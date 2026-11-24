"""Runtime helpers: concurrency limiter, rate limiting, storage cleanup daemon."""
import time
import shutil
import asyncio
import logging
from collections import defaultdict, deque
from typing import Deque, Dict, Optional

from fastapi import Request, HTTPException

from backend.config import TEMP_DIR, OUTPUT_DIR, FILE_TTL_HOURS, MAX_PARALLEL_JOBS, TRUST_PROXY

logger = logging.getLogger("Tilmochbek-Runtime")

# ---- Concurrency: at most N dubbing pipelines at once (protects CPU, RAM and API quotas) ----
_pipeline_sem: Optional[asyncio.Semaphore] = None


def pipeline_semaphore() -> asyncio.Semaphore:
    global _pipeline_sem
    if _pipeline_sem is None:
        _pipeline_sem = asyncio.Semaphore(MAX_PARALLEL_JOBS)
    return _pipeline_sem


# ---- Simple in-memory sliding-window rate limiter ----
_hits: Dict[str, Deque[float]] = defaultdict(deque)


def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request, bucket: str, limit: int, window_s: int = 60) -> None:
    key = f"{bucket}:{client_ip(request)}"
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(status_code=429, detail="Juda ko'p so'rov yuborildi. Birozdan keyin urinib ko'ring.")
    q.append(now)
    if len(_hits) > 5000:  # keep memory bounded
        for k in [k for k, v in _hits.items() if not v or now - v[-1] > window_s]:
            _hits.pop(k, None)


# ---- Storage cleanup (24h TTL for outputs, stale temp dirs) ----
def cleanup_storage_once(ttl_hours: float = FILE_TTL_HOURS) -> Dict[str, int]:
    now = time.time()
    removed_out = removed_tmp = 0
    ttl_s = ttl_hours * 3600
    try:
        for f in OUTPUT_DIR.glob("*.mp4"):
            try:
                if now - f.stat().st_mtime > ttl_s:
                    f.unlink()
                    removed_out += 1
            except OSError:
                pass
    except Exception as e:
        logger.warning(f"Output cleanup error: {e}")
    try:
        for d in TEMP_DIR.iterdir():
            try:
                # temp working dirs older than 3 hours are stale (pipeline normally removes them itself)
                if now - d.stat().st_mtime > 3 * 3600:
                    if d.is_dir():
                        shutil.rmtree(d, ignore_errors=True)
                    else:
                        d.unlink()
                    removed_tmp += 1
            except OSError:
                pass
    except Exception as e:
        logger.warning(f"Temp cleanup error: {e}")
    if removed_out or removed_tmp:
        logger.info(f"Cleanup: removed {removed_out} expired outputs, {removed_tmp} stale temp items")
    return {"outputs": removed_out, "temp": removed_tmp}


async def cleanup_loop(interval_s: int = 1800):
    while True:
        try:
            await asyncio.to_thread(cleanup_storage_once)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Cleanup loop error: {e}")
        await asyncio.sleep(interval_s)
