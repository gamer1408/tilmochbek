# GapirAI.uz - Zero-Cost AI YouTube-to-Uzbek Dubbing Application

GapirAI.uz is a modern, reactive, zero-cost AI web application that automatically ingests YouTube videos, transcribes speech, translates content into natural Uzbek, synthesizes Uzbek neural voices (`uz-UZ-SardorNeural` and `uz-UZ-MadinaNeural`), and applies FFmpeg background audio ducking (15% volume preservation) + vocal overlay.

---

## ⚡ Key Features

1. **YouTube Video & Audio Ingestion**: High-reliability extraction via `yt-dlp`.
2. **Speech-to-Text (STT)**: Powered by `groq` SDK (`whisper-large-v3`) with automated offline silence-aware fallback segmentation if `GROQ_API_KEY` is omitted.
3. **Uzbek Translation**: Clean text normalization and translation via `deep-translator`.
4. **Uzbek Neural Text-to-Speech (TTS)**: Microsoft Edge Neural Voices (`uz-UZ-SardorNeural` for Male, `uz-UZ-MadinaNeural` for Female) using `edge-tts` (100% zero-cost).
5. **FFmpeg Background Audio Ducking**: Lowers background music/audio to 15% volume while overlaying synthesized Uzbek audio aligned to segment timestamps.
6. **Real-Time Progress Tracker**: Server-Sent Events (SSE) streaming updates across 5 pipeline stages.
7. **Modern Glassmorphism UI**: Reactive single-page UI with glowing dark mode aesthetics, built-in video player preview, and subtitle segment breakdown.

---

## 🏗️ Project Architecture

```
tarjimavdai/
├── backend/
│   ├── app.py                     # FastAPI entry point & static file hosting
│   ├── config.py                  # Environment config & auto-detect binaries
│   ├── pipeline/
│   │   ├── downloader.py          # yt-dlp wrapper with error handling
│   │   ├── transcriber.py         # Groq Whisper / Fallback STT logic
│   │   ├── translator.py          # Uzbek translation with text cleanup
│   │   ├── synthesizer.py         # Async edge-tts audio generator
│   │   └── video_processor.py     # FFmpeg audio ducking and video muxer
│   └── requirements.txt
├── frontend/
│   ├── index.html                 # Modern glassmorphism UI
│   ├── style.css
│   └── app.js                     # Real-time SSE progress tracker
├── tests/
│   └── test_pipeline.py           # Automated end-to-end integration test
├── storage/
│   ├── temp/                      # Temporary job processing files
│   └── outputs/                   # Generated dubbed MP4 videos
├── run.py                         # Startup script with self-test
└── README.md
```

---

## 🚀 Quickstart Guide

### 1. Install Dependencies
```bash
pip install -r backend/requirements.txt
```

### 2. Configuration (Optional)
Create a `.env` file in the root directory if you want to use Groq API for Whisper transcription:
```env
GROQ_API_KEY=gsk_your_groq_api_key_here
```
*(If no API key is provided, the application automatically uses the offline segmenter fallback mode).*

### 3. Run Self-Test & Boot Web Application
```bash
python run.py
```

Open your browser and navigate to:
👉 **[http://localhost:8000](http://localhost:8000)**

---

## 🧪 Automated Testing

To run the automated integration test directly:
```bash
python tests/test_pipeline.py
```

This generates a synthetic video, transcribes, translates, synthesizes Uzbek speech, applies FFmpeg audio ducking, and verifies output `.mp4` file integrity.

---

## v7 — Commercial release notes (2026-11-24)

- **Tariffs & quotas** (`backend/pipeline/quota.py`): Sinov (1 video / 7 kun, 30 daq), Haftalik 29 900 (14 / 7 kun), Oylik Standart 99 900 (60 / 30 kun), Oylik VIP 199 900 (150 / 30 kun, 120 daq). Referral: +1 video per invited friend (`/taklif` in the bot).
- **Payments are manual**: user pays by card, admin activates in the bot: `/activate <telegram_id> <weekly|monthly|vip> [days]` (needs `ADMIN_TELEGRAM_IDS`), or `POST /api/admin/activate` with header `X-Admin-Key`.
- **Storage**: outputs deleted after 24 h (`FILE_TTL_HOURS`), stale temp dirs after 3 h; working dir removed after every job.
- **Security**: no hardcoded JWT secret, mock Google tokens removed, demo login off unless `DEMO_LOGIN=1`, rate limits, CORS restricted to `ALLOWED_ORIGINS`, history is private per user, XSS escaping in the UI.
- **New .env keys**: `PUBLIC_BASE_URL` (https public URL, required for bot buttons and large-file links), `ADMIN_KEY`, `ADMIN_TELEGRAM_IDS`, `PAYMENT_INFO`, `SUPPORT_CONTACT`, `MAX_PARALLEL_JOBS` (default 2), `TRUST_PROXY=1` behind nginx.
- **Tests**: `python -m pytest tests/test_commercial.py` (offline, no API keys needed).
- Backup of the previous version: `_backup_2026-11-24/`.
