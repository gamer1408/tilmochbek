import os
import sys
import asyncio
import logging
import uvicorn
from pathlib import Path

# Fix Windows console stdout encoding for unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from backend.config import FFMPEG_PATH, TEMP_DIR, OUTPUT_DIR, PORT
from backend.db import init_db_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Tilmochbek-Runner")

def run_preflight_checks():
    logger.info("Performing pre-flight system checks...")
    logger.info(f"FFmpeg binary detected at: {FFMPEG_PATH}")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    init_db_sync()
    logger.info("Storage directories & SQLite database schema initialized.")

def main():
    print("=" * 65)
    print("   Tilmochbek.uz - Aqlli YouTube Tarjima va Ovozlashtirish Studio  ")
    print("=" * 65)

    run_preflight_checks()

    print("=" * 65)
    print(f"  Server is starting! Access the Web UI at:")
    print(f"  URL: http://localhost:{PORT}")
    print("=" * 65)
    print()

    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
        log_level="info"
    )

if __name__ == "__main__":
    main()
