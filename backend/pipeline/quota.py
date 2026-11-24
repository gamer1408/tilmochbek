"""Tariff plans and usage quotas (matches the business model document)."""
import logging
import datetime
from typing import Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.models import User, DubJob

logger = logging.getLogger(__name__)

# videos = number of videos per period, days = period length, max_minutes = max single video length
PLANS: Dict[str, Dict[str, Any]] = {
    "free":    {"key": "free",    "name": "Sinov",          "price": 0,      "videos": 1,   "days": 7,  "max_minutes": 30},
    "weekly":  {"key": "weekly",  "name": "Haftalik",       "price": 29900,  "videos": 14,  "days": 7,  "max_minutes": 60},
    "monthly": {"key": "monthly", "name": "Oylik Standart", "price": 99900,  "videos": 60,  "days": 30, "max_minutes": 60,
                "old_price": 119000},
    "vip":     {"key": "vip",     "name": "Oylik VIP",      "price": 199900, "videos": 150, "days": 30, "max_minutes": 120,
                "old_price": 249000},
}

REFERRAL_BONUS_VIDEOS = 1


class QuotaError(Exception):
    """Raised when a user is not allowed to start/finish a job (user-facing Uzbek message)."""


def utcnow() -> datetime.datetime:
    return datetime.datetime.utcnow()


def effective_plan_key(user: Optional[User]) -> str:
    if not user:
        return "free"
    key = user.plan or "free"
    if key not in PLANS:
        return "free"
    if key != "free" and (not user.plan_expires_at or user.plan_expires_at < utcnow()):
        return "free"
    return key


def window_start(user: User, plan_key: str) -> datetime.datetime:
    plan = PLANS[plan_key]
    if plan_key == "free":
        return utcnow() - datetime.timedelta(days=plan["days"])
    return user.plan_expires_at - datetime.timedelta(days=plan["days"])


def public_plans() -> list:
    return [p for p in PLANS.values()]


def max_seconds_for(user: Optional[User]) -> float:
    return PLANS[effective_plan_key(user)]["max_minutes"] * 60.0


async def get_usage(db: AsyncSession, user: User, in_flight: int = 0) -> Dict[str, Any]:
    """Returns the user's current plan, used/remaining videos and expiry."""
    plan_key = effective_plan_key(user)
    plan = PLANS[plan_key]
    start = window_start(user, plan_key)
    res = await db.execute(
        select(func.count(DubJob.id)).where(
            DubJob.user_id == user.id,
            DubJob.status == "completed",
            DubJob.created_at >= start,
        )
    )
    used = int(res.scalar() or 0) + max(0, in_flight)
    plan_left = max(0, plan["videos"] - used)
    bonus = int(user.bonus_videos or 0)
    return {
        "plan": plan_key,
        "plan_name": plan["name"],
        "videos_total": plan["videos"],
        "videos_used": min(used, plan["videos"]),
        "videos_remaining": plan_left + bonus,
        "bonus_videos": bonus,
        "max_minutes": plan["max_minutes"],
        "expires_at": user.plan_expires_at.isoformat() if plan_key != "free" and user.plan_expires_at else None,
        "resets_at": (start + datetime.timedelta(days=plan["days"])).isoformat() if plan_key == "free" else None,
    }


async def ensure_can_start(db: AsyncSession, user: Optional[User], in_flight: int = 0) -> Dict[str, Any]:
    """Raises QuotaError if the user may not start a new dubbing job."""
    if not user:
        raise QuotaError("Dublyaj qilish uchun tizimga kiring (Google yoki Telegram orqali). Birinchi video bepul!")
    usage = await get_usage(db, user, in_flight)
    if usage["videos_remaining"] <= 0:
        raise QuotaError(
            "Sizning videolar limitingiz tugadi. Davom etish uchun tarifni tanlang yoki do'stingizni taklif qilib "
            "+1 bepul video oling."
        )
    return usage


def check_duration_quota(user: Optional[User], duration_seconds: float) -> None:
    """Raises QuotaError if the video is longer than the plan allows."""
    limit = max_seconds_for(user)
    if duration_seconds and duration_seconds > limit:
        mins = int(limit // 60)
        plan_name = PLANS[effective_plan_key(user)]["name"]
        raise QuotaError(
            f"Video juda uzun ({duration_seconds / 60:.0f} daqiqa). '{plan_name}' tarifida eng ko'pi bilan "
            f"{mins} daqiqalik video qabul qilinadi."
        )


async def deduct_job_credits(db: AsyncSession, user_id: Optional[int], duration_seconds: float) -> Optional[int]:
    """
    Call BEFORE the completed DubJob row is inserted. Records dubbed seconds and consumes one
    bonus (referral) video when the plan allowance for the period is already used up.
    """
    if not user_id:
        return None
    try:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if not user:
            return None
        plan_key = effective_plan_key(user)
        plan = PLANS[plan_key]
        start = window_start(user, plan_key)
        used_before = int(
            (await db.execute(
                select(func.count(DubJob.id)).where(
                    DubJob.user_id == user_id, DubJob.status == "completed", DubJob.created_at >= start
                )
            )).scalar() or 0
        )
        if used_before >= plan["videos"] and (user.bonus_videos or 0) > 0:
            user.bonus_videos = user.bonus_videos - 1
        user.total_dubbed_seconds = (user.total_dubbed_seconds or 0.0) + (duration_seconds or 0.0)
        await db.commit()
        return int(user.bonus_videos or 0)
    except Exception as e:
        logger.error(f"Failed to update usage for user {user_id}: {e}")
        await db.rollback()
        return None


async def activate_plan(db: AsyncSession, user: User, plan_key: str, days: Optional[int] = None) -> User:
    """Activates (or extends) a paid plan. Used by admin tools / payment confirmation."""
    if plan_key not in PLANS or plan_key == "free":
        raise ValueError("Noto'g'ri tarif.")
    plan = PLANS[plan_key]
    base = user.plan_expires_at if (user.plan_expires_at and user.plan_expires_at > utcnow() and user.plan == plan_key) else utcnow()
    user.plan = plan_key
    user.plan_expires_at = base + datetime.timedelta(days=days or plan["days"])
    await db.commit()
    await db.refresh(user)
    return user


async def grant_referral_bonus(db: AsyncSession, inviter_id: int) -> None:
    inviter = (await db.execute(select(User).where(User.id == inviter_id))).scalar_one_or_none()
    if inviter:
        inviter.bonus_videos = (inviter.bonus_videos or 0) + REFERRAL_BONUS_VIDEOS
        await db.commit()
