# Agentic Job Seeker backend: FastAPI + MCP + headless/remote Chromium for the apply bots.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/app/.browsers \
    JOBHUB_BUN=/usr/local/bin/bun \
    JOBHUB_PROFILE=/app/data/profile.yaml \
    JOBHUB_PYTHON=/usr/local/bin/python \
    DISPLAY=:99 \
    TZ=Asia/Kolkata

# Virtual display + VNC/noVNC so you can log in to LinkedIn/Naukri/Indeed from your phone's browser.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify fluxbox tini ca-certificates curl git tzdata fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Bun runs the ai-job-search LinkedIn public-search CLI (zero runtime deps).
COPY --from=oven/bun:1.4.4 /usr/local/bin/bun /usr/local/bin/bun

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# pdftotext reads the resume for job scoring (kept after the heavy layers so they stay cached).
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY . .
RUN ./scripts/fetch_vendored.sh

RUN useradd --create-home --uid 1000 jobhub \
    && mkdir -p /app/data /app/runs /app/.secrets /app/.profiles \
    && chmod 700 /app/.secrets \
    && chown -R jobhub:jobhub /app
USER jobhub

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
