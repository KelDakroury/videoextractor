FROM denoland/deno:bin-2.9.7 AS deno

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_SCRAPER_HOST=0.0.0.0 \
    MEDIA_SCRAPER_JOB_ROOT=/tmp/videoextractor \
    PORT=8000

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app

COPY requirements-web.txt .
RUN python3 -m pip install --no-cache-dir --disable-pip-version-check \
    --requirement requirements-web.txt

RUN useradd --create-home --uid 10001 app \
    && install -d -o app -g app /tmp/videoextractor
COPY --chown=app:app . .

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python3", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:{}/api/health'.format(os.environ.get('PORT', '8000')), timeout=3)"]

CMD ["python3", "webapp.py"]
