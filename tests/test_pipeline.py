import os
import sys
import asyncio
import logging
import subprocess
from pathlib import Path

# Add workspace root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from backend.config import FFMPEG_PATH, TEMP_DIR, OUTPUT_DIR, GROQ_API_KEY, GEMINI_API_KEY, GEMINI_MODEL, OPENROUTER_API_KEY
from backend.pipeline.downloader import download_youtube_video
from backend.pipeline.transcriber import transcribe_audio
from backend.pipeline.translator import translate_segments
from backend.pipeline.synthesizer import synthesize_uzbek_speech
from backend.pipeline.video_processor import process_dubbed_video

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("TestPipeline")

def create_synthetic_test_video(job_dir: Path) -> tuple[str, str]:
    """Generates a 6-second synthetic test MP4 video with a 440Hz sine audio track."""
    job_dir.mkdir(parents=True, exist_ok=True)
    video_path = str(job_dir / "test_video.mp4")
    audio_path = str(job_dir / "test_audio.wav")

    logger.info("Generating synthetic test video via FFmpeg...")
    cmd = [
        FFMPEG_PATH, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "128k",
        video_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    cmd_audio = [
        FFMPEG_PATH, "-y",
        "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        audio_path
    ]
    subprocess.run(cmd_audio, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    assert os.path.exists(video_path), "Failed to generate synthetic test video."
    assert os.path.exists(audio_path), "Failed to extract test audio."
    return video_path, audio_path

async def run_end_to_end_test(use_real_youtube: bool = True):
    """Runs end-to-end integration test across all 5 pipeline stages."""
    test_job_id = "test_run_e2e"
    job_dir = TEMP_DIR / test_job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    out_video_path = str(OUTPUT_DIR / f"{test_job_id}.mp4")

    logger.info("==========================================")
    logger.info("STARTING END-TO-END PIPELINE SELF-TEST")
    logger.info("==========================================")

    # Stage 1: Download & Audio Extraction
    if use_real_youtube:
        logger.info("Stage 1: Downloading Real YouTube Sample Video...")
        sample_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        dl_res = download_youtube_video(sample_url, job_dir)
        video_path = dl_res["video_path"]
        audio_path = dl_res["audio_path"]
        print(f"[OK] Stage 1 Passed: Real YouTube video downloaded ({dl_res['title']}).")
    else:
        logger.info("Stage 1: Generating Synthetic Test Video & Audio...")
        video_path, audio_path = create_synthetic_test_video(job_dir)
        print("[OK] Stage 1 Passed: Video & Audio files initialized.")

    # Stage 2: STT Transcription (Groq Whisper)
    logger.info("Stage 2: Speech-to-Text Transcription via Groq Whisper...")
    segments = transcribe_audio(audio_path, GROQ_API_KEY)
    assert len(segments) > 0, "STT transcription produced no segments."
    print(f"[OK] Stage 2 Passed: {len(segments)} segments transcribed with real timestamps.")
    for s in segments:
        print(f"     [{s['start']}s - {s['end']}s] {s['text']}")

    # Stage 3: Uzbek Translation (Gemini 2.5 Flash / Groq / Deep-Translator)
    logger.info("Stage 3: Uzbek Translation (Gemini 2.5 Flash / Groq / Deep-Translator)...")
    translated_segments = translate_segments(segments, "uz")
    for seg in translated_segments:
        assert "translated_text" in seg and len(seg["translated_text"]) > 0
    print(f"[OK] Stage 3 Passed: All segments translated to Uzbek.")
    for s in translated_segments:
        print(f"     -> {s.get('translated_text')}")

    # Stage 4: Edge TTS Synthesis
    logger.info("Stage 4: Edge TTS Uzbek Neural Synthesis (Voice: Male)...")
    synthesized_segments = await synthesize_uzbek_speech(translated_segments, job_dir, "male")
    valid_speech = [s for s in synthesized_segments if s.get("speech_path")]
    assert len(valid_speech) > 0, "No speech segments were synthesized by Edge TTS."
    print(f"[OK] Stage 4 Passed: {len(valid_speech)} Uzbek TTS audio files generated.")

    # Stage 5: FFmpeg Audio Ducking & Video Muxing
    logger.info("Stage 5: FFmpeg Audio Ducking & Video Muxing...")
    final_output = process_dubbed_video(
        video_path=video_path,
        segments=synthesized_segments,
        output_path=out_video_path,
        bg_volume=0.08,
        speech_gain=2.2,
        job_dir=job_dir
    )
    assert os.path.exists(final_output), "Final dubbed video output file missing."
    file_size = os.path.getsize(final_output)
    assert file_size > 10000, f"Generated video file is too small ({file_size} bytes)."
    print(f"[OK] Stage 5 Passed: Final dubbed video created successfully ({file_size / 1024:.1f} KB).")

    logger.info("==========================================")
    logger.info("ALL PIPELINE INTEGRATION TESTS PASSED 100%!")
    logger.info("==========================================")

if __name__ == "__main__":
    asyncio.run(run_end_to_end_test(use_real_youtube=True))
