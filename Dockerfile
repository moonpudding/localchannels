FROM python:3.12-slim

# ffmpeg provides ffmpeg + ffprobe, used for metadata and thumbnails.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app ./app

# Project metadata for the About page and the update check. Copied after the
# code so bumping the version doesn't invalidate the app layer. app/about.py
# resolves this relative to the package parent, so it must sit next to app/.
COPY about.json ./

# Container defaults. Override any of these in docker-compose or `docker run -e`.
#   LOCALCHANNELS_ROOT_PATH  where your videos are mounted (read/write: needed
#                            for file deletion and writing tags into filenames)
#   LOCALCHANNELS_DATA_DIR   where the SQLite DB + thumbnails are persisted
ENV LOCALCHANNELS_ROOT_PATH=/videos \
    LOCALCHANNELS_DATA_DIR=/data \
    LOCALCHANNELS_HOST=0.0.0.0 \
    LOCALCHANNELS_PORT=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Persisted data (database + cached thumbnails).
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["python", "-m", "app.main"]
