import os
import sqlite3
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine
from backend.config import DATABASE_URL, SYNC_DATABASE_URL, STORAGE_DIR, DB_FILE
from backend.models import Base

logger = logging.getLogger(__name__)

# Async Engine & Sessionmaker
async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

def run_sqlite_migrations():
    """
    Safely synchronizes and recreates SQLite schema to prevent NOT NULL constraints
    on obsolete columns like source_url_or_path.
    """
    if not DB_FILE.exists():
        return
    try:
        conn = sqlite3.connect(str(DB_FILE))
        cursor = conn.cursor()
        
        # Check dub_jobs columns
        cursor.execute("PRAGMA table_info(dub_jobs);")
        columns_info = cursor.fetchall()
        column_names = [row[1] for row in columns_info]
        
        # If obsolete column source_url_or_path exists with NOT NULL constraint, recreate table cleanly
        if "source_url_or_path" in column_names:
            logger.info("Rebuilding dub_jobs table to remove obsolete source_url_or_path NOT NULL constraint...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dub_jobs_clean (
                    id VARCHAR(64) PRIMARY KEY,
                    user_id INTEGER,
                    youtube_url VARCHAR(1000) NOT NULL,
                    video_title VARCHAR(500) DEFAULT 'YouTube Video',
                    video_duration FLOAT DEFAULT 0.0,
                    voice_gender VARCHAR(16) DEFAULT 'male',
                    status VARCHAR(32) DEFAULT 'queued',
                    progress INTEGER DEFAULT 0,
                    step VARCHAR(64) DEFAULT 'Queued',
                    error TEXT,
                    video_url VARCHAR(500),
                    output_path VARCHAR(1000),
                    thumbnail VARCHAR(500),
                    segments_json TEXT,
                    created_at DATETIME,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
            """)
            
            # Copy data across
            cursor.execute("""
                INSERT OR IGNORE INTO dub_jobs_clean (
                    id, user_id, youtube_url, video_title, video_duration, voice_gender,
                    status, progress, step, error, video_url, output_path, thumbnail,
                    segments_json, created_at
                )
                SELECT 
                    id, user_id, 
                    COALESCE(NULLIF(youtube_url, ''), source_url_or_path, 'https://youtu.be/sample'),
                    video_title, video_duration, voice_gender, status, progress, step,
                    error, video_url, output_path, thumbnail, segments_json, created_at
                FROM dub_jobs;
            """)
            
            cursor.execute("DROP TABLE dub_jobs;")
            cursor.execute("ALTER TABLE dub_jobs_clean RENAME TO dub_jobs;")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_dub_jobs_id ON dub_jobs (id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_dub_jobs_user_id ON dub_jobs (user_id);")
            conn.commit()
            logger.info("Successfully rebuilt dub_jobs table with clean schema.")
        
        # Add new user columns for plans/referrals on existing databases
        cursor.execute("PRAGMA table_info(users);")
        user_cols = [row[1] for row in cursor.fetchall()]
        if user_cols:
            for col, ddl in [
                ("plan", "ALTER TABLE users ADD COLUMN plan VARCHAR(16) DEFAULT 'free'"),
                ("plan_expires_at", "ALTER TABLE users ADD COLUMN plan_expires_at DATETIME"),
                ("bonus_videos", "ALTER TABLE users ADD COLUMN bonus_videos INTEGER DEFAULT 0"),
                ("referred_by", "ALTER TABLE users ADD COLUMN referred_by INTEGER"),
            ]:
                if col not in user_cols:
                    cursor.execute(ddl)
                    logger.info(f"Added users.{col} column")
            conn.commit()

        conn.close()
    except Exception as e:
        logger.warning(f"SQLite migration error: {e}", exc_info=True)

async def init_db():
    """Initializes SQLite database tables asynchronously."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    run_sqlite_migrations()
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("SQLAlchemy database schema initialized.")

def init_db_sync():
    """Initializes SQLite database tables synchronously (for pre-flight check in run.py)."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    run_sqlite_migrations()
    sync_engine = create_engine(SYNC_DATABASE_URL, echo=False)
    Base.metadata.create_all(bind=sync_engine)
    logger.info("Synchronous database schema initialized.")

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for accessing database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for standalone background tasks to access database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
