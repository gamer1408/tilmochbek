import time
import uuid
import secrets
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from jose import jwt, JWTError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from fastapi import Depends, HTTPException, status, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.config import JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_DAYS, GOOGLE_CLIENT_ID
from backend.models import User
from backend.db import get_db

logger = logging.getLogger(__name__)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Generates a signed JWT access token for user sessions."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=JWT_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    """Decodes and validates a JWT access token."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError as e:
        logger.debug(f"JWT decode error: {e}")
        return None

def verify_google_id_token(credential: str) -> Optional[Dict[str, Any]]:
    """Verifies a Google Sign-In ID token cryptographically. Requires GOOGLE_CLIENT_ID."""
    if not GOOGLE_CLIENT_ID:
        logger.error("GOOGLE_CLIENT_ID is not configured; Google sign-in is disabled.")
        return None
    try:
        info = id_token.verify_oauth2_token(credential, google_requests.Request(), GOOGLE_CLIENT_ID)
        if info.get("email") and info.get("email_verified") is False:
            return None
        return info
    except Exception as e:
        logger.warning(f"Google ID token verification failed: {e}")
        return None

async def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """
    Extracts the authenticated User if valid Authorization Bearer header is present.
    Returns None for guest visitors without raising 401 exceptions.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None

    token = authorization.split(" ")[1].strip()
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        return None

    try:
        user_id = int(payload["sub"])
        result = await db.execute(select(User).where(User.id == user_id, User.is_active == 1))
        user = result.scalar_one_or_none()
        return user
    except Exception as e:
        logger.warning(f"Failed to fetch optional user: {e}")
        return None

async def get_current_user_required(
    user: Optional[User] = Depends(get_current_user_optional)
) -> User:
    """Dependency that strictly requires an authenticated user (raises 401 if guest)."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tizimga kirish talab qilinadi. Iltimos, Google orqali kiring.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

def generate_telegram_link_code() -> str:
    """Generates a secure, human-readable link code for Telegram bot binding."""
    return f"link_{secrets.token_hex(6)}"
