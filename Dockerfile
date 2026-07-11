FROM docker.io/library/node:22-bookworm-slim AS webapp-builder
WORKDIR /build/webapp
COPY webapp/package*.json ./
RUN npm config set registry https://registry.npmjs.org/
RUN npm ci
COPY webapp/ ./
RUN npm run build

FROM docker.io/library/python:3.11-slim-bookworm

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

# Keep packaging tools above known vulnerable releases before installing the
# application lock. No network/debug utility is installed in the runtime image.
COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade \
        "pip>=26.1.1" \
        "setuptools>=82.0.0" \
        "wheel>=0.46.2" \
        "jaraco.context>=6.1.0" \
    && python -m pip install -r /tmp/requirements.txt

# Keep the runtime image deterministic. Do not COPY the whole repository because
# that can bake local caches, VCS metadata, env files, uploads, or secrets into
# the image when .dockerignore drifts.
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
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

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).read()" || exit 1

USER appuser

CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8080 --timeout ${WEB_TIMEOUT:-60}"]
