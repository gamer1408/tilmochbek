import os
import json
import time
import sqlite3
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from backend.config import BASE_DIR

logger = logging.getLogger(__name__)
CACHE_DB_PATH = BASE_DIR / "storage" / "cache.db"

def _get_cache_key(url: str, voice_gender: str) -> str:
    """Computes a normalized SHA-256 hash for a given URL and voice gender."""
    canonical_url = url.strip().lower()
    raw_key = f"{canonical_url}::{voice_gender.strip().lower()}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

def init_cache_db():
    """Initializes the SQLite cache database table if it doesn't exist."""
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(CACHE_DB_PATH)) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dubbing_cache (
                cache_key TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                voice_gender TEXT NOT NULL,
                title TEXT,
                duration REAL,
                thumbnail TEXT,
                video_url TEXT NOT NULL,
                output_path TEXT NOT NULL,
                segments_json TEXT,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()

def get_cached_dub(url: str, voice_gender: str = "male") -> Optional[Dict[str, Any]]:
    """
    Checks if a completed dubbed video exists in the local cache.
    Returns metadata dict if cache hit and file exists on disk, else None.
    """
    try:
        init_cache_db()
        key = _get_cache_key(url, voice_gender)
        with sqlite3.connect(str(CACHE_DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM dubbing_cache WHERE cache_key = ?", (key,))
            row = cursor.fetchone()
            if row:
                output_path = row["output_path"]
                if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
                    segments = []
                    if row["segments_json"]:
                        try:
                            segments = json.loads(row["segments_json"])
                        except Exception:
                            pass
                    logger.info(f"Local Cache HIT for [{url}] ({voice_gender}) -> {output_path}")
                    return {
                        "url": row["url"],
                        "voice_gender": row["voice_gender"],
                        "title": row["title"],
                        "duration": row["duration"],
                        "thumbnail": row["thumbnail"],
                        "video_url": row["video_url"],
                        "output_path": output_path,
                        "segments": segments,
                        "cached": True
                    }
                else:
                    # Output file was deleted, purge stale cache entry
                    cursor.execute("DELETE FROM dubbing_cache WHERE cache_key = ?", (key,))
                    conn.commit()
    except Exception as e:
        logger.warning(f"Cache lookup failed: {e}")
    return None

def save_cached_dub(
    url: str,
    voice_gender: str,
    title: str,
    duration: float,
    thumbnail: str,
    video_url: str,
    output_path: str,
    segments: List[Dict[str, Any]]
):
    """Saves a completed dubbing result to the persistent cache."""
    try:
        init_cache_db()
        key = _get_cache_key(url, voice_gender)
        segments_json = json.dumps(segments, ensure_ascii=False)
        with sqlite3.connect(str(CACHE_DB_PATH)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO dubbing_cache 
                (cache_key, url, voice_gender, title, duration, thumbnail, video_url, output_path, segments_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key,
                url.strip(),
                voice_gender.strip().lower(),
                title,
                duration,
                thumbnail,
                video_url,
                str(output_path),
                segments_json,
                time.time()
            ))
            conn.commit()
        logger.info(f"Saved dub result to local cache: [{key[:12]}] -> {output_path}")
    except Exception as e:
        logger.warning(f"Failed to save to cache: {e}")
