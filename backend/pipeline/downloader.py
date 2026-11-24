import os
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
import yt_dlp
from backend.config import FFMPEG_PATH, YTDLP_COOKIES_FILE

logger = logging.getLogger(__name__)

def _get_ffmpeg_dir() -> Optional[str]:
    return str(Path(FFMPEG_PATH).parent) if os.path.isabs(FFMPEG_PATH) else None

def download_youtube_audio(url: str, job_dir: Path) -> Dict[str, Any]:
    """
    Rapid Audio Ingestion:
    Downloads ONLY the lightweight audio stream from YouTube in 2-3 seconds,
    and converts to 16kHz mono MP3 (64kbps) for Groq Whisper.
    Immune to Windows file locking issues (WinError 32).
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    audio_out = str(job_dir / "audio.mp3")
    ffmpeg_dir = _get_ffmpeg_dir()

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': str(job_dir / 'raw_audio.%(ext)s'),
        'overwrites': True,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web', 'mweb']
            }
        }
    }
    if ffmpeg_dir:
        ydl_opts['ffmpeg_location'] = ffmpeg_dir
    if YTDLP_COOKIES_FILE and os.path.exists(YTDLP_COOKIES_FILE):
        ydl_opts['cookiefile'] = YTDLP_COOKIES_FILE

    logger.info(f"Rapidly extracting audio stream from: {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        title = info.get('title', 'YouTube Video')
        duration = float(info.get('duration') or 0.0)
        thumbnail = info.get('thumbnail', '')

    raw_candidates = list(job_dir.glob("raw_audio.*"))
    if not raw_candidates:
        raise RuntimeError("yt-dlp failed to download audio stream.")

    raw_audio = raw_candidates[0]
    # Fast convert to 16kHz mono 64k mp3 via FFmpeg
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(raw_audio),
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        audio_out
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup raw audio download
    if os.path.exists(str(raw_audio)) and str(raw_audio) != audio_out:
        try:
            os.remove(str(raw_audio))
        except Exception:
            pass

    logger.info(f"Audio stream ready in ~2s: {audio_out} ({os.path.getsize(audio_out)} bytes)")
    return {
        "audio_path": audio_out,
        "title": title,
        "duration": duration,
        "thumbnail": thumbnail,
        "url": url
    }

def download_youtube_video_stream(url: str, job_dir: Path) -> str:
    """
    Background Video Stream Ingestion:
    Downloads the video track concurrently while transcription & translation run.
    Saves clean MP4 video to job_dir / 'video.mp4'.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    video_out = str(job_dir / "video.mp4")
    ffmpeg_dir = _get_ffmpeg_dir()

    ydl_opts = {
        'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/bestvideo[ext=mp4]/best[height<=720]/best[ext=mp4]/best',
        'outtmpl': str(job_dir / 'video_raw.%(ext)s'),
        'overwrites': True,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'web', 'mweb']
            }
        }
    }
    if ffmpeg_dir:
        ydl_opts['ffmpeg_location'] = ffmpeg_dir
    if YTDLP_COOKIES_FILE and os.path.exists(YTDLP_COOKIES_FILE):
        ydl_opts['cookiefile'] = YTDLP_COOKIES_FILE

    logger.info(f"Downloading video stream in background: {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    raw_candidates = list(job_dir.glob("video_raw.*"))
    if not raw_candidates:
        raise RuntimeError("yt-dlp failed to download video stream.")

    raw_video = raw_candidates[0]
    # Fast stream-copy remux to video.mp4
    cmd = [FFMPEG_PATH, "-y", "-i", str(raw_video), "-c:v", "copy", "-c:a", "aac", video_out]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not os.path.exists(video_out):
        cmd = [FFMPEG_PATH, "-y", "-i", str(raw_video), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", video_out]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Cleanup raw video download
    if os.path.exists(str(raw_video)) and str(raw_video) != video_out:
        try:
            os.remove(str(raw_video))
        except Exception:
            pass

    logger.info(f"Video stream ready: {video_out} ({os.path.getsize(video_out)} bytes)")
    return video_out

def download_youtube_video(url: str, job_dir: Path) -> Dict[str, Any]:
    """
    Standard all-in-one downloader (backwards compatible).
    Downloads audio and video synchronously.
    """
    audio_meta = download_youtube_audio(url, job_dir)
    video_path = download_youtube_video_stream(url, job_dir)
    return {
        "video_path": video_path,
        "audio_path": audio_meta["audio_path"],
        "title": audio_meta["title"],
        "duration": audio_meta["duration"],
        "thumbnail": audio_meta["thumbnail"],
    }

class AwaitableDict(dict):
    def __await__(self):
        async def _async_self():
            return self
        return _async_self().__await__()

async def download_audio_and_metadata(url: str, job_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Asynchronous ingestion: downloads audio stream and metadata, along with video stream.
    """
    import uuid
    import asyncio
    from backend.config import TEMP_DIR

    if job_dir is None:
        job_dir = TEMP_DIR / f"job_{uuid.uuid4().hex[:8]}"
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    # Launch video download task concurrently with audio download
    video_task = asyncio.create_task(asyncio.to_thread(download_youtube_video_stream, url, job_dir))
    audio_meta = await asyncio.to_thread(download_youtube_audio, url, job_dir)
    video_path = await video_task

    return AwaitableDict({
        "audio_path": audio_meta["audio_path"],
        "video_path": video_path,
        "title": audio_meta.get("title", "YouTube Video"),
        "duration": audio_meta.get("duration", 0.0),
        "thumbnail": audio_meta.get("thumbnail", ""),
        "url": url,
        "job_dir": job_dir
    })

