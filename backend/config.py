import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Storage Directories
STORAGE_DIR = BASE_DIR / "storage"
TEMP_DIR = STORAGE_DIR / "temp"
OUTPUT_DIR = STORAGE_DIR / "outputs"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# API Keys & Server Config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free").strip()
PORT = int(os.getenv("PORT", "8000"))

# Auth & Security
def _load_jwt_secret() -> str:
    """JWT secret from env; otherwise generate once and persist (never a hardcoded default)."""
    env_secret = (os.getenv("JWT_SECRET") or os.getenv("JWT_SECRET_KEY") or "").strip()
    if len(env_secret) >= 16:
        return env_secret
    import secrets
    secret_file = STORAGE_DIR / ".jwt_secret"
    try:
        if secret_file.exists():
            saved = secret_file.read_text().strip()
            if len(saved) >= 32:
                return saved
        new_secret = secrets.token_urlsafe(48)
        secret_file.write_text(new_secret)
        return new_secret
    except Exception:
        return secrets.token_urlsafe(48)

JWT_SECRET_KEY = _load_jwt_secret()
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

# Telegram Bot Integration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "tilmoch_bek_bot").strip()

# Public URL, limits, admin (production settings)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://localhost:{PORT}").strip().rstrip("/")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", PUBLIC_BASE_URL).split(",") if o.strip()]
ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()
ADMIN_TELEGRAM_IDS = {int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").replace(" ", "").split(",") if x.isdigit()}
DEMO_LOGIN_ENABLED = os.getenv("DEMO_LOGIN", "0").strip() == "1"
TRUST_PROXY = os.getenv("TRUST_PROXY", "0").strip() == "1"
FILE_TTL_HOURS = float(os.getenv("FILE_TTL_HOURS", "24"))
MAX_PARALLEL_JOBS = max(1, int(os.getenv("MAX_PARALLEL_JOBS", "2")))
PAYMENT_INFO = os.getenv("PAYMENT_INFO", "To'lov uchun admin bilan bog'laning.").strip()
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "").strip()

# Click (Uzbekistan) merchant credentials - payments are enabled only when all three are set
CLICK_SERVICE_ID = os.getenv("CLICK_SERVICE_ID", "").strip()
CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "").strip()
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "").strip()
CLICK_MERCHANT_USER_ID = os.getenv("CLICK_MERCHANT_USER_ID", "").strip()
YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()

# Database
DB_FILE = STORAGE_DIR / "tilmochbek.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_FILE}"
SYNC_DATABASE_URL = f"sqlite:///{DB_FILE}"

# Dynamic FFmpeg Resolution
def get_ffmpeg_executable() -> str:
    # 1. System PATH check
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        return ffmpeg_bin

    # 2. imageio-ffmpeg check
    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_bin and os.path.exists(ffmpeg_bin):
            return ffmpeg_bin
    except Exception:
        pass

    # 3. static-ffmpeg fallback
    try:
        import static_ffmpeg
        ffmpeg_bin = static_ffmpeg.add_paths()
        if ffmpeg_bin:
            return "ffmpeg"
    except Exception:
        pass

    # Default fallback
    return "ffmpeg"

FFMPEG_PATH = get_ffmpeg_executable()

print(f"[Config] Tilmochbek Initialized. FFMPEG_PATH: {FFMPEG_PATH}")
print(f"[Config] Storage: Temp={TEMP_DIR}, Output={OUTPUT_DIR}, DB={DB_FILE}")
print(f"[Config] Gemini: {'Yes' if GEMINI_API_KEY else 'No'} ({GEMINI_MODEL}), Groq: {'Yes' if GROQ_API_KEY else 'No'}, OpenRouter: {'Yes' if OPENROUTER_API_KEY else 'No'}")
print(f"[Config] Public URL: {PUBLIC_BASE_URL} | Admin key: {'set' if ADMIN_KEY else 'NOT set'} | Demo login: {DEMO_LOGIN_ENABLED}")
print(f"[Config] Google Client ID: {'Configured' if GOOGLE_CLIENT_ID else 'Not configured'}")
print(f"[Config] Telegram Bot Token: {'Configured' if TELEGRAM_BOT_TOKEN else 'Disabled (Token not set)'}")
