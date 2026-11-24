import os
import datetime
from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, index=True, nullable=True)
    full_name = Column(String(255), nullable=False, default="Foydalanuvchi")
    avatar_url = Column(String(500), nullable=True)
    google_id = Column(String(255), unique=True, index=True, nullable=True)
    telegram_id = Column(BigInteger, unique=True, index=True, nullable=True)
    telegram_username = Column(String(255), nullable=True)
    telegram_link_code = Column(String(64), unique=True, index=True, nullable=True)
    total_dubbed_seconds = Column(Float, default=0.0)
    is_active = Column(Integer, default=1)
    plan = Column(String(16), default="free")
    plan_expires_at = Column(DateTime, nullable=True)
    bonus_videos = Column(Integer, default=0)
    referred_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    jobs = relationship("DubJob", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "avatar_url": self.avatar_url,
            "google_id": self.google_id,
            "telegram_id": self.telegram_id,
            "telegram_username": self.telegram_username,
            "total_dubbed_seconds": round(self.total_dubbed_seconds or 0.0, 1),
            "plan": self.plan or "free",
            "plan_expires_at": self.plan_expires_at.isoformat() if self.plan_expires_at else None,
            "bonus_videos": self.bonus_videos or 0,
            "referral_code": f"ref_{self.id}",
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

class DubJob(Base):
    __tablename__ = "dub_jobs"

    id = Column(String(64), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    youtube_url = Column(String(1000), nullable=False)
    video_title = Column(String(500), default="YouTube Video")
    video_duration = Column(Float, default=0.0)
    voice_gender = Column(String(16), default="male")  # "male" | "female"
    status = Column(String(32), default="queued")  # "queued" | "processing" | "completed" | "failed"
    progress = Column(Integer, default=0)
    step = Column(String(64), default="Queued")
    error = Column(Text, nullable=True)
    video_url = Column(String(500), nullable=True)
    output_path = Column(String(1000), nullable=True)
    thumbnail = Column(String(500), nullable=True)
    segments_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="jobs")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "youtube_url": self.youtube_url,
            "video_title": self.video_title,
            "video_duration": round(self.video_duration or 0.0, 1),
            "voice_gender": self.voice_gender,
            "status": self.status,
            "progress": self.progress,
            "step": self.step,
            "error": self.error,
            "video_url": self.video_url,
            "thumbnail": self.thumbnail,
            "expired": bool(self.output_path) and not os.path.exists(self.output_path),
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Payment(Base):
    __tablename__ = "payments"

    id = Column(String(32), primary_key=True)  # = merchant_trans_id sent to Click
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan = Column(String(16), nullable=False)
    amount = Column(Integer, nullable=False)  # so'm
    status = Column(String(16), default="pending")  # pending | prepared | paid | cancelled
    provider = Column(String(16), default="click")
    click_trans_id = Column(String(64), unique=True, nullable=True, index=True)
    prepare_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    paid_at = Column(DateTime, nullable=True)
