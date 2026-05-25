FROM docker.io/library/node:22-bookworm-slim AS webapp-builder
WORKDIR /build/webapp
COPY webapp/package*.json ./
RUN npm config set registry https://registry.npmjs.org/
RUN npm ci
COPY webapp/ ./
RUN npm run build

FROM docker.io/library/node:22-bookworm-slim AS nexus-codex-runtime-builder
WORKDIR /build/nexus-codex-runtime
COPY tools/nexus-codex-runtime/package*.json ./
RUN npm config set registry https://registry.npmjs.org/ \
    && npm ci
COPY tools/nexus-codex-runtime/ ./
RUN npm run build \
    && npm prune --omit=dev

FROM docker.io/library/node:22-bookworm-slim AS openclaw-runtime
RUN npm config set registry https://registry.npmjs.org/ \
    && npm install -g openclaw @openclaw/codex \
    && node --version \
    && npm --version \
    && openclaw --version \
    && npm list -g --depth=0 openclaw @openclaw/codex \
    && test -e /usr/local/lib/node_modules/openclaw/dist/entry.mjs -o -e /usr/local/lib/node_modules/openclaw/dist/entry.js -o -e /usr/local/lib/node_modules/openclaw/openclaw.mjs

FROM docker.io/library/python:3.11-slim

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

# Keep the runtime image deterministic. Do not COPY the whole repository because
# that can bake local caches, VCS metadata, env files, uploads, or secrets into
# the image when .dockerignore drifts.
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY deploy/codex_app_server_bridge_proxy.py /app/deploy/
COPY deploy/codex_app_server_private_upstream_proxy.py /app/deploy/
COPY deploy/codex_private_reply_engine.py /app/deploy/
COPY deploy/codex_openclaw_codex_harness_adapter.py /app/deploy/
COPY --from=nexus-codex-runtime-builder /build/nexus-codex-runtime /app/tools/nexus-codex-runtime
COPY --from=webapp-builder /build/frontend_dist /app/frontend_dist
COPY --from=openclaw-runtime /usr/local/ /usr/local/

RUN node --version \
    && npm --version \
    && openclaw --version \
    && npm list -g --depth=0 openclaw @openclaw/codex \
    && test -e /usr/local/lib/node_modules/openclaw/dist/entry.mjs -o -e /usr/local/lib/node_modules/openclaw/dist/entry.js -o -e /usr/local/lib/node_modules/openclaw/openclaw.mjs

# Round B webchat widget static export
# Keep embeddable public webchat files outside SPA fallback.
RUN mkdir -p /app/frontend_dist/static/webchat \
    && cp -r /app/backend/app/static/webchat/. /app/frontend_dist/static/webchat/

RUN mkdir -p /app/backend/uploads /home/appuser/.openclaw \
    && addgroup --system appgroup \
    && adduser --system --ingroup appgroup --home /app --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appgroup /app /home/appuser

WORKDIR /app/backend

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

USER appuser

CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8080 --timeout ${WEB_TIMEOUT:-60}"]
