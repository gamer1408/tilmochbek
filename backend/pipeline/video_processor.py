import os
import sys
import logging
import subprocess
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional
from backend.config import FFMPEG_PATH
from backend.pipeline.transcriber import get_audio_duration

logger = logging.getLogger(__name__)

# Ensure audioop compatibility for Python 3.13 / 3.14 (PEP 594)
try:
    import audioop_lts as audioop
    sys.modules['audioop'] = audioop
except ImportError:
    pass

try:
    from pydub import AudioSegment
    AudioSegment.converter = FFMPEG_PATH
    AudioSegment.ffmpeg = FFMPEG_PATH
    PYDUB_AVAILABLE = True
except Exception as pydub_err:
    logger.warning(f"Pydub import warning: {pydub_err}")
    PYDUB_AVAILABLE = False


def assemble_full_uzbek_audio(
    video_path: str,
    segments: List[Dict[str, Any]],
    output_audio_path: str,
    speech_gain: float = 2.0
) -> str:
    """
    Rapid In-Memory / Python Track Assembly:
    Creates a silent base track of exact video length and overlays
    all synthesized speech segments at their millisecond offsets.
    Avoids Windows CLI 8191-character limits ([WinError 206]).
    """
    total_duration_s = get_audio_duration(video_path)
    total_duration_ms = int((total_duration_s + 2.0) * 1000)

    logger.info(f"Assembling full Uzbek audio track (duration: {total_duration_s:.2f}s, segments: {len(segments)})...")

    # 24kHz mono/stereo base track
    full_track = AudioSegment.silent(duration=total_duration_ms, frame_rate=24000)

    for idx, seg in enumerate(segments):
        speech_path = seg.get("speech_path")
        if not speech_path or not os.path.exists(speech_path):
            continue
        try:
            seg_audio = AudioSegment.from_file(speech_path)
            start_ms = int(max(0, float(seg.get("start", 0.0))) * 1000)
            full_track = full_track.overlay(seg_audio, position=start_ms)
        except Exception as seg_err:
            logger.warning(f"Failed to overlay segment {idx}: {seg_err}")

    full_track.export(output_audio_path, format="wav")
    logger.info(f"Uzbek vocal track exported: {output_audio_path} ({os.path.getsize(output_audio_path)} bytes)")
    return output_audio_path


def cleanup_temp_files(
    segments: List[Dict[str, Any]], 
    full_audio_path: str = None, 
    job_dir: Path = None
):
    """Prunes individual segment audio files and intermediate buffers after rendering."""
    for seg in segments:
        sp = seg.get("speech_path")
        if sp and os.path.exists(sp):
            try:
                os.remove(sp)
            except Exception:
                pass

    if full_audio_path and os.path.exists(full_audio_path):
        try:
            os.remove(full_audio_path)
        except Exception:
            pass

    if job_dir and os.path.exists(job_dir):
        for extra in ["filter_script.txt", "audio.compressed.mp3"]:
            extra_path = job_dir / extra
            if extra_path.exists():
                try:
                    os.remove(extra_path)
                except Exception:
                    pass


def process_dubbed_video(
    video_path: str,
    segments: List[Dict[str, Any]],
    output_path: str,
    bg_volume: float = 0.08,
    speech_gain: float = 2.2,
    job_dir: Path = None
) -> str:
    """
    Zero-Reencoding Instant Video Muxing:
    1. Pre-assembles Uzbek speech track into full_uzbek_audio.wav.
    2. Runs 2-input FFmpeg with stream copy (-c:v copy -c:a aac) for < 2s muxing.
    3. Mixes background (0.08) and Uzbek voice (2.2) via amix filter.
    """
    # Guard against positional argument mismatch where job_dir is passed as speech_gain
    if isinstance(speech_gain, (Path, str)):
        try:
            speech_gain = float(speech_gain)
        except (ValueError, TypeError):
            job_dir = Path(speech_gain)
            speech_gain = 2.2

    try:
        bg_volume = float(bg_volume)
    except Exception:
        bg_volume = 0.08

    try:
        speech_gain = float(speech_gain)
    except Exception:
        speech_gain = 2.2

    logger.info(f"Instant muxing dubbed video (bg: {bg_volume*100:.0f}%, speech: {speech_gain}x gain)...")
    
    if not job_dir:
        job_dir = Path(output_path).parent

    valid_speech_segments = [
        s for s in segments 
        if s.get("speech_path") and os.path.exists(s["speech_path"])
    ]

    if not valid_speech_segments:
        logger.warning("No speech segments available. Copying original video.")
        cmd = [FFMPEG_PATH, "-y", "-i", video_path, "-c", "copy", output_path]
        subprocess.run(cmd, check=True)
        return output_path

    # Step 1: Pre-assemble audio in Python
    full_uzbek_audio_path = str(job_dir / "full_uzbek_audio.wav")
    assemble_full_uzbek_audio(video_path, valid_speech_segments, full_uzbek_audio_path, speech_gain=speech_gain)

    # Step 2: 2-Input FFmpeg Stream Copy Muxing
    filter_complex = (
        f"[0:a]volume={bg_volume}[bg];"
        f"[1:a]volume={speech_gain}[voice];"
        f"[bg][voice]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    ffmpeg_cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", video_path,
        "-i", full_uzbek_audio_path,
        "-filter_complex", filter_complex,
        "-map", "0:v?",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path
    ]

    logger.info("Executing instant 2-input zero-reencode muxing...")
    res = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if res.returncode != 0:
        logger.warning(f"Stream copy failed ({res.stderr}). Re-encoding with ultrafast preset...")
        ffmpeg_cmd_reencode = [
            FFMPEG_PATH,
            "-y",
            "-i", video_path,
            "-i", full_uzbek_audio_path,
            "-filter_complex", filter_complex,
            "-map", "0:v?",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path
        ]
        subprocess.run(ffmpeg_cmd_reencode, check=True)

    logger.info(f"Dubbed video successfully generated in record time: {output_path} ({os.path.getsize(output_path)} bytes)")

    # Step 3: Cleanup segment temp files
    try:
        cleanup_temp_files(valid_speech_segments, full_uzbek_audio_path, job_dir)
    except Exception as cleanup_err:
        logger.warning(f"Cleanup non-critical: {cleanup_err}")

    return output_path

class AwaitableStr(str):
    def __await__(self):
        async def _async_self():
            return self
        return _async_self().__await__()

async def mux_dubbed_video(
    video_path: str,
    dubbed_audio: Any,
    output_path: Optional[str] = None,
    job_dir: Optional[Path] = None
) -> str:
    """
    Asynchronously muxes the dubbed speech track or segments with the original video.
    """
    import uuid
    from backend.config import OUTPUT_DIR, TEMP_DIR

    if output_path is None:
        output_path = str(OUTPUT_DIR / f"dubbed_{uuid.uuid4().hex[:8]}.mp4")

    if job_dir is None:
        job_dir = TEMP_DIR / f"mux_{uuid.uuid4().hex[:8]}"
        job_dir.mkdir(parents=True, exist_ok=True)
    job_dir = Path(job_dir)

    if isinstance(dubbed_audio, list):
        segments = dubbed_audio
    else:
        segments = []

    res_path = await asyncio.to_thread(
        process_dubbed_video,
        video_path,
        segments,
        output_path,
        0.08,
        2.2,
        job_dir
    )
    return AwaitableStr(res_path)

