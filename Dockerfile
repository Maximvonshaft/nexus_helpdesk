FROM m.daocloud.io/docker.io/library/node:22-bookworm-slim AS webapp-builder
WORKDIR /build/webapp
COPY webapp/package*.json ./
RUN npm config set registry https://registry.npmmirror.com
RUN npm ci
COPY webapp/ ./
RUN npm run build

FROM m.daocloud.io/docker.io/library/python:3.11-slim

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG IMAGE_TAG=nexusdesk/helpdesk:server
ARG APP_VERSION=server
ARG FRONTEND_BUILD_SHA=unknown

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    GIT_SHA=${GIT_SHA} \
    BUILD_TIME=${BUILD_TIME} \
    IMAGE_TAG=${IMAGE_TAG} \
    APP_VERSION=${APP_VERSION} \
    FRONTEND_BUILD_SHA=${FRONTEND_BUILD_SHA}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt || pip install -r /tmp/requirements.txt || pip install -r /tmp/requirements.txt

COPY . /app
COPY --from=webapp-builder /build/frontend_dist /app/frontend_dist

# Round B webchat widget static export
# Keep embeddable public webchat files outside SPA fallback.
RUN mkdir -p /app/frontend_dist/static/webchat \
    && cp -r /app/backend/app/static/webchat/. /app/frontend_dist/static/webchat/

RUN mkdir -p /app/backend/uploads \
    && addgroup --system appgroup \
    && adduser --system --ingroup appgroup --home /app --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appgroup /app

WORKDIR /app/backend

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
