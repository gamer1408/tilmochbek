"""Shared dubbing service used by both the web API and the Telegram bot."""
import os
import json
import time
import shutil
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.config import TEMP_DIR, OUTPUT_DIR, FFMPEG_PATH, GROQ_API_KEY
from backend.db import get_db_context
from backend.models import User, DubJob
from backend.runtime import pipeline_semaphore
from backend.pipeline.quota import QuotaError, check_duration_quota, deduct_job_credits
from backend.pipeline.cache_manager import save_cached_dub
from backend.pipeline.downloader import download_youtube_audio, download_youtube_video_stream
from backend.pipeline.transcriber import transcribe_audio, get_audio_duration
from backend.pipeline.translator import translate_segments
from backend.pipeline.synthesizer import synthesize_uzbek_speech
from backend.pipeline.video_processor import process_dubbed_video

logger = logging.getLogger("Tilmochbek-Service")

ProgressCb = Callable[[str, int, str], Awaitable[None]]

_PRIVATE_KEYS = ("speech_path", "audio_path")


def public_segments(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Segments without server-side file paths (safe to return to clients / store in history)."""
    return [{k: v for k, v in s.items() if k not in _PRIVATE_KEYS} for s in segments]


def friendly_error(exc: Exception) -> str:
    """Maps internal exceptions to short Uzbek messages (never leaks tracebacks/paths/keys)."""
    if isinstance(exc, QuotaError):
        return str(exc)
    text = str(exc).lower()
    if any(k in text for k in ("private video", "video unavailable", "this video is not available", "removed")):
        return "Video mavjud emas yoki yopiq (private). Boshqa havola yuboring."
    if "sign in to confirm" in text or "confirm you" in text or "bot" in text and "youtube" in text:
        return "YouTube hozircha bu videoni yuklashga ruxsat bermadi. Birozdan keyin qayta urinib ko'ring."
    if "unsupported url" in text or "is not a valid url" in text:
        return "Havola noto'g'ri. To'liq YouTube havolasini yuboring."
    if "live" in text and "stream" in text:
        return "Jonli efirni dublyaj qilib bo'lmaydi. Efir tugagach urinib ko'ring."
    if "no speech" in text or "bo'sh" in text or "empty" in text:
        return "Videoda nutq topilmadi."
    return "Dublyaj jarayonida kutilmagan xatolik yuz berdi. Iltimos, birozdan keyin qayta urinib ko'ring."


async def _record_failed(job_id: str, user_id: Optional[int], source: str, voice: str, message: str) -> None:
    try:
        async with get_db_context() as db:
            db.add(DubJob(
                id=job_id, user_id=user_id, youtube_url=source[:1000], video_title="Xatolik",
                voice_gender=voice, status="failed", progress=0, step="Error", error=message[:1000],
            ))
            await db.commit()
    except Exception as e:  # never mask the original error
        logger.warning(f"Could not store failed job {job_id}: {e}")


async def execute_dubbing(
    *,
    job_id: str,
    source: str,
    is_youtube: bool,
    voice_gender: str,
    user: Optional[User],
    progress: ProgressCb,
) -> Dict[str, Any]:
    """
    Full pipeline: download -> STT -> translate -> TTS -> mix -> persist.
    Raises QuotaError (user-facing) or any other Exception (use friendly_error()).
    """
    sem = pipeline_semaphore()
    if sem.locked():
        await progress("Navbat", 6, "Serverda band, navbatda kutilmoqda...")
    async with sem:
        return await _run(job_id, source, is_youtube, voice_gender, user, progress)


async def _run(job_id, source, is_youtube, voice_gender, user, progress) -> Dict[str, Any]:
    started = time.time()
    user_id = user.id if user else None
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"{job_id}.mp4"
    video_task: Optional[asyncio.Task] = None
    thumbnail = ""

    try:
        await progress("Yuklab olish", 15, "Video va audio oqimlari tayyorlanmoqda...")
        if is_youtube:
            video_task = asyncio.create_task(asyncio.to_thread(download_youtube_video_stream, source, job_dir))
            audio_res = await asyncio.to_thread(download_youtube_audio, source, job_dir)
            audio_path = audio_res["audio_path"]
            title = audio_res["title"]
            duration = float(audio_res["duration"] or 0.0)
            thumbnail = audio_res.get("thumbnail", "") or ""
        else:
            audio_path = str(job_dir / "audio.mp3")
            await asyncio.to_thread(
                subprocess.run,
                [FFMPEG_PATH, "-y", "-i", source, "-vn", "-ar", "16000", "-ac", "1", "-b:a", "64k", audio_path],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            duration = float(await asyncio.to_thread(get_audio_duration, audio_path) or 0.0)
            title = Path(source).stem.split("_", 1)[-1] or "Video"

        # Plan limit on single-video length (checked before the expensive steps)
        check_duration_quota(user, duration)

        await progress("AI Transkripsiya", 35, "Nutq matnga aylantirilmoqda (Whisper)...")
        segments = await asyncio.to_thread(transcribe_audio, audio_path, GROQ_API_KEY)
        if not segments:
            raise RuntimeError("no speech detected")

        await progress("AI Tarjima", 55, "O'zbek tiliga tabiiy tarjima qilinmoqda...")
        translated = await asyncio.to_thread(translate_segments, segments, "uz")

        await progress("Ovoz Sintezi", 75, "O'zbekcha neyron ovoz sintezlanmoqda...")
        synthesized = await synthesize_uzbek_speech(translated, job_dir, voice_gender)

        await progress("Miksing", 90, "Audio va video birlashtirilmoqda...")
        video_path = await video_task if video_task else source
        await asyncio.to_thread(process_dubbed_video, video_path, synthesized, str(out_file), 0.08, 2.2, job_dir)

        elapsed = round(time.time() - started, 2)
        video_url = f"/outputs/{job_id}.mp4"
        clean_segments = public_segments(synthesized)

        if is_youtube:
            save_cached_dub(
                url=source, voice_gender=voice_gender, title=title, duration=duration, thumbnail=thumbnail,
                video_url=video_url, output_path=str(out_file), segments=clean_segments,
            )

        async with get_db_context() as db:
            if user_id:
                await deduct_job_credits(db, user_id, duration)
            db.add(DubJob(
                id=job_id, user_id=user_id, youtube_url=source[:1000] if is_youtube else f"upload:{title}"[:1000],
                video_title=title[:500], video_duration=duration, voice_gender=voice_gender,
                status="completed", progress=100, step="Completed", video_url=video_url,
                output_path=str(out_file), thumbnail=thumbnail[:500],
                segments_json=json.dumps(clean_segments, ensure_ascii=False),
            ))
            await db.commit()

        return {
            "title": title, "duration": duration, "thumbnail": thumbnail, "video_url": video_url,
            "out_file": str(out_file), "segments": clean_segments, "elapsed": elapsed,
        }
    except Exception as exc:
        if not isinstance(exc, QuotaError):
            logger.error(f"Job [{job_id}] failed: {exc}", exc_info=True)
        await _record_failed(job_id, user_id, source, voice_gender, friendly_error(exc))
        if out_file.exists():
            try:
                out_file.unlink()
            except OSError:
                pass
        raise
    finally:
        # Always wait for the background video download, then remove the working directory
        if video_task is not None:
            try:
                await video_task
            except BaseException:
                pass
        shutil.rmtree(job_dir, ignore_errors=True)
        if not is_youtube:
            try:
                os.remove(source)
            except OSError:
                pass
