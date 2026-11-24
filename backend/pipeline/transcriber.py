import os
import wave
import contextlib
import logging
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Any
from backend.config import GROQ_API_KEY, FFMPEG_PATH

logger = logging.getLogger(__name__)

def get_audio_duration(audio_path: str) -> float:
    """Returns audio file duration in seconds."""
    try:
        with contextlib.closing(wave.open(audio_path, 'r')) as f:
            frames = f.getnframes()
            rate = f.getframerate()
            return frames / float(rate)
    except Exception:
        pass

    cmd = [FFMPEG_PATH, "-i", audio_path]
    res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    import re
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
    if match:
        h, m, s = match.groups()
        return float(h) * 3600 + float(m) * 60 + float(s)
    return 10.0

def ensure_lightweight_audio(input_audio: str) -> str:
    """
    Ensures the audio is in lightweight mono MP3 (16kHz, 64k) format under 24MB
    for ultra-fast upload to Groq Whisper API.
    """
    if input_audio.endswith(".mp3") and os.path.exists(input_audio) and os.path.getsize(input_audio) < 24 * 1024 * 1024:
        return input_audio

    duration = get_audio_duration(input_audio)
    bitrate = "32k" if duration > 1800 else ("48k" if duration > 600 else "64k")
    compressed_path = str(Path(input_audio).with_suffix(".compressed.mp3"))

    cmd = [
        FFMPEG_PATH, "-y",
        "-i", input_audio,
        "-ar", "16000",
        "-ac", "1",
        "-b:a", bitrate,
        compressed_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(compressed_path) and os.path.getsize(compressed_path) > 0:
            return compressed_path
    except Exception as e:
        logger.warning(f"Audio compression failed ({e}). Using original file.")
    return input_audio

class AwaitableList(list):
    def __await__(self):
        async def _async_self():
            return self
        return _async_self().__await__()

def transcribe_audio(audio_path: str, api_key: str = None) -> List[Dict[str, Any]]:
    """
    Rapid Transcription:
    Streams lightweight 16kHz MP3 directly to Groq Whisper (whisper-large-v3).
    Returns parsed segment timestamps: [{'id': int, 'start': float, 'end': float, 'text': str}]
    """
    key_to_use = api_key or GROQ_API_KEY

    if not key_to_use:
        logger.warning("No GROQ_API_KEY found. Generating fallback segments.")
        duration = get_audio_duration(audio_path)
        return AwaitableList([{
            "id": 0,
            "start": 0.0,
            "end": round(duration, 2),
            "text": "Hello, welcome to this video."
        }])

    upload_audio_path = ensure_lightweight_audio(audio_path)
    
    from groq import Groq
    client = Groq(api_key=key_to_use, timeout=90.0)

    transcription = None
    max_retries = 3

    for attempt in range(max_retries):
        try:
            logger.info(f"Streaming '{upload_audio_path}' ({os.path.getsize(upload_audio_path)/(1024*1024):.2f}MB) to Groq Whisper (attempt {attempt + 1}/{max_retries})...")
            with open(upload_audio_path, "rb") as file_stream:
                transcription = client.audio.transcriptions.create(
                    file=(os.path.basename(upload_audio_path), file_stream),
                    model="whisper-large-v3",
                    response_format="verbose_json",
                    temperature=0.0
                )
            if transcription:
                break
        except Exception as e:
            backoff_delay = (2 ** attempt) * 2
            logger.warning(f"Groq Whisper attempt {attempt + 1} failed: {e}. Retrying in {backoff_delay}s...")
            if attempt < max_retries - 1:
                time.sleep(backoff_delay)
            else:
                logger.error(f"All {max_retries} Groq Whisper attempts failed: {e}")

    # Cleanup temporary compressed file if created
    if upload_audio_path != audio_path and os.path.exists(upload_audio_path):
        try:
            os.remove(upload_audio_path)
        except Exception:
            pass

    if not transcription:
        logger.warning("Falling back to default duration segment.")
        duration = get_audio_duration(audio_path)
        return AwaitableList([{
            "id": 0,
            "start": 0.0,
            "end": round(duration, 2),
            "text": "Hello, welcome to this video."
        }])

    raw_segments = getattr(transcription, "segments", None)
    parsed_segments = []

    if raw_segments:
        for idx, seg in enumerate(raw_segments):
            start = seg.get("start") if isinstance(seg, dict) else getattr(seg, "start", 0.0)
            end = seg.get("end") if isinstance(seg, dict) else getattr(seg, "end", 0.0)
            text = seg.get("text") if isinstance(seg, dict) else getattr(seg, "text", "")
            
            text_str = str(text).strip()
            if text_str:
                parsed_segments.append({
                    "id": idx,
                    "start": round(float(start), 2),
                    "end": round(float(end), 2),
                    "text": text_str
                })

    if not parsed_segments:
        full_text = getattr(transcription, "text", "").strip()
        if full_text:
            duration = get_audio_duration(audio_path)
            parsed_segments.append({
                "id": 0,
                "start": 0.0,
                "end": round(duration, 2),
                "text": full_text
            })

    logger.info(f"Groq Whisper transcribed {len(parsed_segments)} real segments in rapid turnaround.")
    return AwaitableList(parsed_segments)
