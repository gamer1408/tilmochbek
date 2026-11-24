import os
import re
import uuid
import time
import json
import hmac
import asyncio
import logging
from typing import Dict, Any, Optional, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from backend.config import (
    BASE_DIR, OUTPUT_DIR, GOOGLE_CLIENT_ID, TELEGRAM_BOT_USERNAME, PUBLIC_BASE_URL, ALLOWED_ORIGINS,
    ADMIN_KEY, DEMO_LOGIN_ENABLED, PAYMENT_INFO, SUPPORT_CONTACT, FILE_TTL_HOURS,
)
from backend.models import User, DubJob
from backend.db import init_db, get_db, get_db_context
from backend.auth import (
    create_access_token, verify_google_id_token, get_current_user_optional, get_current_user_required,
    generate_telegram_link_code,
)
from backend.pipeline.quota import (
    PLANS, QuotaError, public_plans, get_usage, ensure_can_start, check_duration_quota, activate_plan,
)
from backend.pipeline.cache_manager import get_cached_dub
from backend.service import execute_dubbing, friendly_error
from backend.runtime import rate_limit, cleanup_loop
from backend.billing import router as billing_router, click_enabled
from backend.telegram_bot import start_telegram_bot, stop_telegram_bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Tilmochbek-Backend")

YOUTUBE_RE = re.compile(
    r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/)|youtu\.be/)[\w\-]{6,}", re.IGNORECASE
)

_background: Set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Tilmochbek...")
    await init_db()
    cleanup_task = asyncio.create_task(cleanup_loop())
    await start_telegram_bot()
    yield
    logger.info("Shutting down background services...")
    cleanup_task.cancel()
    await stop_telegram_bot()


app = FastAPI(title="Tilmochbek.uz - YouTube videolarni o'zbekchaga dublyaj qilish", version="7.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)

app.include_router(billing_router)

# In-memory progress tracker (finished jobs are pruned after one hour)
jobs: Dict[str, Dict[str, Any]] = {}


class DubRequest(BaseModel):
    url: str = Field(..., max_length=500)
    voice_gender: str = "male"


class GoogleAuthRequest(BaseModel):
    credential: str = Field(..., max_length=4096)


class DemoLoginRequest(BaseModel):
    name: Optional[str] = "Demo Foydalanuvchi"


class ActivatePlanRequest(BaseModel):
    plan: str
    days: Optional[int] = Field(None, ge=1, le=366)
    user_id: Optional[int] = None
    telegram_id: Optional[int] = None
    email: Optional[str] = None


def prune_jobs() -> None:
    now = time.time()
    for jid in [j for j, d in jobs.items() if d.get("status") in ("completed", "failed") and now - d.get("_ts", now) > 3600]:
        jobs.pop(jid, None)


def update_job_status(job_id: str, step: str, progress: int, status: str = "processing", **extra):
    if job_id in jobs:
        jobs[job_id].update({"step": step, "progress": progress, "status": status, **extra})


def public_job(job: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in job.items() if not k.startswith("_") and k != "user_id"}


def active_jobs_for(user_id: int) -> int:
    return sum(1 for j in jobs.values() if j.get("user_id") == user_id and j.get("status") in ("queued", "processing"))


async def run_dubbing_pipeline(job_id: str, url: str, voice_gender: str, user_id: Optional[int]):
    async def progress(step: str, pct: int, message: str):
        update_job_status(job_id, step, pct, message=message)

    try:
        user = None
        if user_id:
            async with get_db_context() as db:
                user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        result = await execute_dubbing(
            job_id=job_id, source=url, is_youtube=True, voice_gender=voice_gender, user=user, progress=progress
        )
        update_job_status(
            job_id, "Completed", 100, status="completed", video_url=result["video_url"], title=result["title"],
            duration=result["duration"], thumbnail=result["thumbnail"], segments=result["segments"],
            elapsed_time=result["elapsed"], message=f"Dublyaj {result['elapsed']:.0f} soniyada tayyor bo'ldi!",
        )
    except Exception as e:
        update_job_status(job_id, "Error", 0, status="failed", error=friendly_error(e),
                          message="Dublyaj jarayonida xatolik yuz berdi.")
    finally:
        if job_id in jobs:
            jobs[job_id]["_ts"] = time.time()


# --- HEALTH / CONFIG / SEO ---

@app.get("/api/health")
async def health():
    return {"status": "ok", "active_jobs": sum(1 for j in jobs.values() if j.get("status") in ("queued", "processing"))}


@app.get("/api/config")
async def get_public_config():
    return {
        "google_client_id": GOOGLE_CLIENT_ID,
        "telegram_bot": TELEGRAM_BOT_USERNAME,
        "platform_name": "Tilmochbek",
        "unlimited": False,
        "demo_login": DEMO_LOGIN_ENABLED,
        "click_enabled": click_enabled(),
        "plans": public_plans(),
        "payment_info": PAYMENT_INFO,
        "support_contact": SUPPORT_CONTACT,
        "file_ttl_hours": FILE_TTL_HOURS,
    }


@app.get("/api/plans")
async def get_plans():
    return {"plans": public_plans(), "payment_info": PAYMENT_INFO}


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /outputs/\nSitemap: {PUBLIC_BASE_URL}/sitemap.xml\n"


@app.get("/sitemap.xml")
async def sitemap():
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f"<url><loc>{PUBLIC_BASE_URL}/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url></urlset>")
    return PlainTextResponse(xml, media_type="application/xml")


# --- AUTH & USER ---

@app.post("/api/auth/google")
async def google_auth(req: GoogleAuthRequest, request: Request, db: AsyncSession = Depends(get_db)):
    rate_limit(request, "auth", 15)
    id_info = verify_google_id_token(req.credential)
    if not id_info:
        raise HTTPException(status_code=400, detail="Google token tasdiqlanmadi.")

    google_id = id_info.get("sub")
    email = id_info.get("email")
    full_name = id_info.get("name") or "Google Foydalanuvchi"
    avatar_url = id_info.get("picture")

    user = (await db.execute(select(User).where((User.google_id == google_id) | (User.email == email)))).scalar_one_or_none()
    if not user:
        user = User(email=email, full_name=full_name, avatar_url=avatar_url, google_id=google_id)
        db.add(user)
    else:
        user.full_name = full_name
        if avatar_url:
            user.avatar_url = avatar_url
        if not user.google_id:
            user.google_id = google_id
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": str(user.id), "email": user.email})
    return {"access_token": token, "token_type": "bearer", "user": user.to_dict()}


@app.post("/api/auth/demo-login")
async def demo_login(req: DemoLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    if not DEMO_LOGIN_ENABLED:
        raise HTTPException(status_code=404, detail="Mavjud emas.")
    rate_limit(request, "auth", 15)
    demo_email = "demo@tilmochbek.uz"
    user = (await db.execute(select(User).where(User.email == demo_email))).scalar_one_or_none()
    if not user:
        user = User(email=demo_email, full_name=req.name or "Foydalanuvchi", google_id="demo_tilmoch_12345")
        db.add(user)
        await db.commit()
        await db.refresh(user)
    token = create_access_token({"sub": str(user.id), "email": user.email})
    return {"access_token": token, "token_type": "bearer", "user": user.to_dict()}


@app.get("/api/user/me")
async def get_my_profile(user: Optional[User] = Depends(get_current_user_optional), db: AsyncSession = Depends(get_db)):
    if not user:
        return {"authenticated": False, "guest": True, "user": None, "usage": None}
    usage = await get_usage(db, user, active_jobs_for(user.id))
    return {
        "authenticated": True, "guest": False, "user": user.to_dict(), "usage": usage,
        "telegram_bot": TELEGRAM_BOT_USERNAME,
        "referral_url": f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=ref_{user.id}",
    }


@app.post("/api/user/telegram-link")
async def get_telegram_link(user: User = Depends(get_current_user_required), db: AsyncSession = Depends(get_db)):
    link_code = generate_telegram_link_code()
    user.telegram_link_code = link_code
    await db.commit()
    return {"link_code": link_code, "telegram_bot_url": f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={link_code}"}


@app.get("/api/user/history")
@app.get("/api/history")
async def get_user_history(user: Optional[User] = Depends(get_current_user_optional), db: AsyncSession = Depends(get_db)):
    """History of the signed-in user only (guests get an empty list: other users' videos are private)."""
    if not user:
        return {"history": []}
    stmt = (select(DubJob).where(DubJob.user_id == user.id, DubJob.status == "completed")
            .order_by(desc(DubJob.created_at)).limit(50))
    rows = (await db.execute(stmt)).scalars().all()
    return {"history": [j.to_dict() for j in rows]}


# --- DUBBING ---

@app.post("/api/dub")
async def start_dubbing(
    req: DubRequest, request: Request,
    user: Optional[User] = Depends(get_current_user_optional), db: AsyncSession = Depends(get_db),
):
    rate_limit(request, "dub", 6)
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="YouTube havolasini kiriting.")
    if not YOUTUBE_RE.match(url):
        raise HTTPException(status_code=400, detail="Faqat YouTube havolalari qabul qilinadi (youtube.com yoki youtu.be).")
    voice_gender = req.voice_gender.lower().strip()
    if voice_gender not in ("male", "female"):
        voice_gender = "male"

    try:
        await ensure_can_start(db, user, active_jobs_for(user.id) if user else 0)
    except QuotaError as e:
        raise HTTPException(status_code=402 if user else 401, detail=str(e))
    user_id = user.id
    prune_jobs()

    cached = get_cached_dub(url, voice_gender)
    if cached:
        try:
            check_duration_quota(user, cached.get("duration", 0))
        except QuotaError as e:
            raise HTTPException(status_code=402, detail=str(e))
        job_id = uuid.uuid4().hex
        segments = cached.get("segments", [])
        jobs[job_id] = {
            "job_id": job_id, "user_id": user_id, "status": "completed", "progress": 100, "step": "Completed",
            "title": cached.get("title", "Dubbed Video"), "duration": cached.get("duration", 0),
            "thumbnail": cached.get("thumbnail", ""), "video_url": cached.get("video_url"), "segments": segments,
            "cached": True, "message": "Natija tayyor (avval dublyaj qilingan)!", "_ts": time.time(),
        }
        db.add(DubJob(
            id=job_id, user_id=user_id, youtube_url=url, video_title=cached.get("title", "YouTube Video"),
            video_duration=cached.get("duration", 0), voice_gender=voice_gender, status="completed", progress=100,
            step="Completed", video_url=cached.get("video_url"), output_path=cached.get("output_path"),
            thumbnail=cached.get("thumbnail"), segments_json=json.dumps(segments, ensure_ascii=False),
        ))
        await db.commit()
        return {"job_id": job_id, "status": "completed", "cached": True}

    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "job_id": job_id, "user_id": user_id, "status": "queued", "progress": 5, "step": "Queued",
        "title": "Yuklanmoqda...", "video_url": None, "error": None, "message": "Navbatga qo'yildi...",
        "_ts": time.time(),
    }
    task = asyncio.create_task(run_dubbing_pipeline(job_id, url, voice_gender, user_id))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"job_id": job_id, "status": "queued", "cached": False}


@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str, sse: bool = False, request: Request = None):
    if job_id not in jobs:
        async with get_db_context() as db:
            db_job = (await db.execute(select(DubJob).where(DubJob.id == job_id))).scalar_one_or_none()
        if db_job:
            segments = []
            if db_job.segments_json:
                try:
                    segments = json.loads(db_job.segments_json)
                except Exception:
                    pass
            expired = bool(db_job.output_path) and not os.path.exists(db_job.output_path)
            return JSONResponse({
                "job_id": db_job.id, "status": "expired" if (db_job.status == "completed" and expired) else db_job.status,
                "progress": db_job.progress, "step": db_job.step, "title": db_job.video_title,
                "duration": db_job.video_duration, "video_url": None if expired else db_job.video_url,
                "thumbnail": db_job.thumbnail, "segments": segments, "error": db_job.error,
                "message": "Fayl muddati tugagan (24 soatdan keyin o'chiriladi). Qayta dublyaj qiling." if expired else None,
            })
        raise HTTPException(status_code=404, detail="Job topilmadi.")

    if not sse:
        return JSONResponse(public_job(jobs[job_id]))

    async def event_generator():
        while True:
            if request and await request.is_disconnected():
                break
            data = public_job(jobs.get(job_id, {}))
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            if data.get("status") in ("completed", "failed"):
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


# --- ADMIN (protected by ADMIN_KEY) ---

def require_admin(x_admin_key: Optional[str] = Header(None)):
    if not ADMIN_KEY or not x_admin_key or not hmac.compare_digest(x_admin_key, ADMIN_KEY):
        raise HTTPException(status_code=403, detail="Ruxsat yo'q.")


@app.post("/api/admin/activate", dependencies=[Depends(require_admin)])
async def admin_activate(req: ActivatePlanRequest, db: AsyncSession = Depends(get_db)):
    if req.plan not in PLANS or req.plan == "free":
        raise HTTPException(status_code=400, detail="Noto'g'ri tarif.")
    stmt = select(User)
    if req.user_id:
        stmt = stmt.where(User.id == req.user_id)
    elif req.telegram_id:
        stmt = stmt.where(User.telegram_id == req.telegram_id)
    elif req.email:
        stmt = stmt.where(User.email == req.email)
    else:
        raise HTTPException(status_code=400, detail="user_id, telegram_id yoki email kerak.")
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi.")
    user = await activate_plan(db, user, req.plan, req.days)
    return {"ok": True, "user": user.to_dict()}


@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
async def admin_stats(db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    done = (await db.execute(select(func.count(DubJob.id)).where(DubJob.status == "completed"))).scalar() or 0
    failed = (await db.execute(select(func.count(DubJob.id)).where(DubJob.status == "failed"))).scalar() or 0
    paid = (await db.execute(select(func.count(User.id)).where(User.plan != "free"))).scalar() or 0
    return {"users": users, "paid_users": paid, "jobs_completed": done, "jobs_failed": failed}


# --- STATIC ---

frontend_dir = BASE_DIR / "frontend"
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")
if frontend_dir.exists():
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


@app.middleware("http")
async def security_and_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif path.startswith("/outputs"):
        response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.get("/")
async def root():
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "Tilmochbek.uz is live."}
