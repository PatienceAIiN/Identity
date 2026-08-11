# Identity — API + web app + fusion encoder in one container.
#
# Sized from measurement, not guesswork: a single encode peaks at ~181 MB RSS
# (OpenCV + YuNet + the placement search), so the service runs with 512Mi and
# concurrency 4. See docs/deploy.md.
FROM python:3.12-slim

# libzbar1 gives us pyzbar as a second validation decoder; libGL/glib are
# OpenCV's runtime deps. Nothing else is installed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libzbar0 \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first so layer caching survives source edits.
COPY requirements-deploy.txt ./
RUN pip install --no-cache-dir --retries 8 --timeout 120 -r requirements-deploy.txt

# Local packages (encoder is imported by the API for server-side fusion).
COPY packages/encoder ./packages/encoder
COPY packages/binding ./packages/binding
RUN pip install --no-cache-dir --retries 8 --timeout 120 ./packages/encoder ./packages/binding

COPY apps/api ./apps/api
COPY apps/web/static ./apps/web/static

# Drop privileges — nothing here needs root at runtime.
RUN useradd --create-home --uid 10001 identity && chown -R identity /srv
USER identity

# Cloud Run provides $PORT; default for local `docker run`.
ENV PORT=8080
WORKDIR /srv/apps/api
# --no-proxy-headers: the app decides which forwarded values to believe
# (trial.py), rather than uvicorn silently rewriting request.client from a
# header any caller can set.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} \
    --workers 1 --timeout-keep-alive 65 --no-proxy-headers --log-level info
