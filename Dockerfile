FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# JS runtime used by yt-dlp to solve YouTube challenges
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend frontend
COPY run.py migrate_db.py ./
ENV PYTHONUNBUFFERED=1 PORT=10000 TRUST_PROXY=1
EXPOSE 10000
CMD ["python", "run.py"]
