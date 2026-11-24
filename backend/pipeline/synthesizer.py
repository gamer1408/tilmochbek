import os
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
import edge_tts
from backend.config import FFMPEG_PATH
from backend.pipeline.transcriber import get_audio_duration

logger = logging.getLogger(__name__)

VOICES = {
    "male": "uz-UZ-SardorNeural",
    "female": "uz-UZ-MadinaNeural"
}

# High-throughput semaphore for Edge-TTS synthesis (6 concurrent streams)
TTS_SEMAPHORE = asyncio.Semaphore(6)

def speed_adjust_audio(input_audio: str, target_max_duration: float) -> float:
    """
    Dynamically aligns synthesized speech duration with the target subtitle segment window
    using the FFmpeg atempo filter. Prevents vocal collision and cross-segment bleed.
    """
    current_duration = get_audio_duration(input_audio)
    if current_duration <= 0 or target_max_duration <= 0:
        return current_duration

    # If speech exceeds allocated segment time by > 5%
    if current_duration > (target_max_duration * 1.05):
        speedup_factor = current_duration / target_max_duration
        # Cap speedup to 1.6x so speech remains natural and intelligible
        speedup_factor = min(1.6, max(1.05, speedup_factor))
        
        temp_output = input_audio + ".sped.mp3"
        filter_str = f"atempo={speedup_factor:.2f}"
        if speedup_factor > 2.0:
            filter_str = f"atempo=2.0,atempo={speedup_factor / 2.0:.2f}"

        cmd = [
            FFMPEG_PATH, "-y",
            "-i", input_audio,
            "-filter:a", filter_str,
            "-vn",
            temp_output
        ]
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if os.path.exists(temp_output) and os.path.getsize(temp_output) > 0:
                os.replace(temp_output, input_audio)
                new_duration = get_audio_duration(input_audio)
                logger.info(f"Speed aligned: {current_duration:.2f}s -> {new_duration:.2f}s (target: {target_max_duration:.2f}s, {speedup_factor:.2f}x)")
                return new_duration
        except Exception as e:
            logger.warning(f"Speed adjustment non-critical warning: {e}")
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception:
                    pass

    return current_duration

async def synthesize_segment_worker(idx: int, seg: Dict[str, Any], voice: str, job_dir: Path) -> Dict[str, Any]:
    """Concurrent worker for synthesizing a single segment with retry and speed sync."""
    text_to_speak = seg.get("translated_text", "").strip()
    if not text_to_speak:
        return {
            **seg,
            "speech_path": None,
            "speech_duration": 0.0
        }

    out_file = str(job_dir / f"speech_{idx}.mp3")
    target_duration = max(0.8, float(seg.get("end", 0.0)) - float(seg.get("start", 0.0)))

    async with TTS_SEMAPHORE:
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text_to_speak, voice)
                await communicate.save(out_file)
                if os.path.exists(out_file) and os.path.getsize(out_file) > 0:
                    # Run speed alignment asynchronously in thread pool to not block event loop
                    final_duration = await asyncio.to_thread(speed_adjust_audio, out_file, target_duration)
                    return {
                        **seg,
                        "speech_path": out_file,
                        "speech_duration": round(final_duration, 2)
                    }
            except Exception as e:
                backoff = 0.5 * (attempt + 1)
                logger.warning(f"Edge-TTS attempt {attempt + 1} for segment {idx} failed: {e}. Retrying in {backoff}s...")
                await asyncio.sleep(backoff)

    logger.error(f"Edge-TTS failed for segment {idx} after 3 attempts.")
    return {
        **seg,
        "speech_path": None,
        "speech_duration": 0.0
    }

async def synthesize_uzbek_speech(
    segments: List[Dict[str, Any]], 
    job_dir: Path, 
    voice_gender: str = "male"
) -> List[Dict[str, Any]]:
    """
    High-Throughput Concurrent Edge-TTS Voice Generation:
    Synthesizes multiple segments in parallel (Semaphore=6) with instant dynamic duration sync.
    """
    voice = VOICES.get(voice_gender.lower(), VOICES["male"])
    logger.info(f"Synthesizing Uzbek speech concurrently (Semaphore=6) using '{voice}' for {len(segments)} segments...")

    tasks = [
        synthesize_segment_worker(idx, seg, voice, job_dir)
        for idx, seg in enumerate(segments)
    ]

    synthesized_segments = await asyncio.gather(*tasks)
    success_count = len([s for s in synthesized_segments if s.get("speech_path")])
    logger.info(f"Rapid synthesis completed: {success_count}/{len(segments)} speech files aligned.")
    return list(synthesized_segments)

async def synthesize_voice_track(
    segments: List[Dict[str, Any]], 
    voice_gender: str = "male", 
    job_dir: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """
    High-Throughput voice track synthesizer for benchmark and pipeline execution.
    """
    import uuid
    from backend.config import TEMP_DIR

    if job_dir is None:
        job_dir = TEMP_DIR / f"tts_{uuid.uuid4().hex[:8]}"
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    return await synthesize_uzbek_speech(segments, job_dir, voice_gender)

