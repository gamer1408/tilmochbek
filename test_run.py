import os
import sys
import asyncio
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Fix Windows console UTF-8 encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from backend.pipeline.downloader import download_audio_and_metadata
from backend.pipeline.transcriber import transcribe_audio
from backend.pipeline.translator import translate_segments
from backend.pipeline.synthesizer import synthesize_voice_track
from backend.pipeline.video_processor import mux_dubbed_video

async def run_benchmark():
    url = "https://youtu.be/kfeytgohT6A?si=OxeMFSNLzLgxvFyv"
    print(f"[*] Starting benchmark on: {url}")
    t0 = time.time()
    
    # 1. Download
    t_start = time.time()
    media_info = await download_audio_and_metadata(url)
    print(f"[+] 1. Download finished in: {time.time() - t_start:.2f}s")
    
    # 2. Transcribe (Groq Whisper)
    t_start = time.time()
    segments = await transcribe_audio(media_info['audio_path'])
    print(f"[+] 2. Transcription finished in: {time.time() - t_start:.2f}s (Segments: {len(segments)})")
    
    # 3. Translation (Gemini 2.5 Flash / Fast LLM)
    t_start = time.time()
    translated_segments = await translate_segments(segments)
    trans_time = time.time() - t_start
    print(f"[+] 3. Translation finished in: {trans_time:.2f}s")
    
    # 4. Synthesis & 5. Muxing
    t_start = time.time()
    dubbed_audio = await synthesize_voice_track(translated_segments, voice_gender="male")
    output_path = await mux_dubbed_video(media_info['video_path'], dubbed_audio)
    print(f"[+] 4-5. Synthesis & Muxing finished in: {time.time() - t_start:.2f}s")
    
    total_time = time.time() - t0
    print(f"\n==========================================")
    print(f"BENCHMARK COMPLETE: Total Pipeline Latency = {total_time:.2f}s")
    print(f"Translation Stage Latency = {trans_time:.2f}s")
    print(f"Output File = {output_path}")
    print(f"==========================================")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
