FROM docker.io/library/node:22-bookworm-slim AS webapp-builder
WORKDIR /build/webapp
COPY webapp/package*.json ./
RUN npm config set registry https://registry.npmjs.org/
RUN npm ci
COPY webapp/ ./
RUN npm run build

FROM docker.io/library/python:3.11-alpine3.22 AS python-wheel-builder
WORKDIR /build
COPY backend/requirements.txt /build/requirements.txt
RUN apk add --no-cache --virtual .build-deps \
        build-base \
        cargo \
        libffi-dev \
        openssl-dev \
    && python -m pip install --upgrade \
        "pip>=26.1.1" \
        "setuptools>=82.0.0" \
        "wheel>=0.46.2" \
        "jaraco.context>=6.1.0" \
    && python -m pip wheel \
        --wheel-dir /wheels \
        --requirement /build/requirements.txt

FROM docker.io/library/python:3.11-alpine3.22

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

# Install only the prebuilt candidate dependency set. Compilers, Cargo and
# development headers remain in the discarded wheel-builder stage.
COPY backend/requirements.txt /tmp/requirements.txt
COPY --from=python-wheel-builder /wheels /wheels
RUN python -m pip install \
        --no-index \
        --find-links=/wheels \
        --requirement /tmp/requirements.txt \
    && rm -rf /wheels /tmp/requirements.txt

# Keep the runtime image deterministic. Do not COPY the whole repository because
# that can bake local caches, VCS metadata, env files, uploads, or secrets into
# the image when .dockerignore drifts.
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY --from=webapp-builder /build/frontend_dist /app/frontend_dist

# Round B webchat widget static export. Keep embeddable public files outside SPA
# fallback while retaining the non-root runtime boundary.
RUN mkdir -p /app/frontend_dist/static/webchat /app/backend/uploads \
    && cp -r /app/backend/app/static/webchat/. /app/frontend_dist/static/webchat/ \
    && addgroup -S appgroup \
    && adduser -S -D -H -s /sbin/nologin -G appgroup appuser \
    && chown -R appuser:appgroup /app

WORKDIR /app/backend

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).read()" || exit 1

USER appuser

CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8080 --timeout ${WEB_TIMEOUT:-60}"]
