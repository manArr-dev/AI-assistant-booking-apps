# syntax=docker/dockerfile:1.7
# Multi-stage build: React UI -> Python dependencies -> slim, non-root runtime.
#   docker build -t booking-assistant .
#   docker run -p 8080:8080 -e MFA_ENCRYPTION_KEY=... booking-assistant

# ---------- 1. build the React frontend
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 2. install Python dependencies into a virtualenv
FROM python:3.12-slim AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements-server.txt ./
RUN pip install -r requirements-server.txt

# ---------- 3. runtime
FROM python:3.12-slim AS runtime
ARG VERSION=dev
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="booking-assistant" \
      org.opencontainers.image.description="Booking assistant API, web UI and MCP servers" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/manArr-dev/booking-assistant"

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_VERSION=${VERSION} \
    FRONTEND_DIST=/app/frontend/dist \
    BOOKING_DB=/data/bookings.db \
    PORT=8080

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown app:app /data

WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
COPY --chown=app:app *.py ./
COPY --chown=app:app travel/ ./travel/
COPY --from=web --chown=app:app /web/dist ./frontend/dist

USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz', timeout=2)"

# --proxy-headers: trust X-Forwarded-For from the ingress so rate limits see real client IPs.
CMD ["sh", "-c", "exec uvicorn api_server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips=${FORWARDED_ALLOW_IPS:-127.0.0.1} --workers ${WEB_CONCURRENCY:-2}"]
