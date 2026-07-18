FROM docker.io/library/node:22.23.1-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS webapp-builder
WORKDIR /build/webapp
COPY webapp/package*.json ./
RUN npm config set registry https://registry.npmjs.org/
RUN npm ci
COPY webapp/ ./
RUN npm run build

FROM docker.io/library/python:3.11.15-alpine3.22@sha256:a4fc589b32e824f3f02ed9d7e7be19518aa47e105b80416336af9f202275a489 AS python-wheel-builder
WORKDIR /build
COPY backend/requirements.txt /build/requirements.txt
RUN apk add --no-cache --virtual .build-deps \
        build-base \
        cargo \
        libffi-dev \
        openssl-dev \
    && python -m pip install --upgrade \
        "pip==26.1.1" \
        "setuptools==82.0.0" \
        "wheel==0.46.2" \
        "jaraco.context==6.1.0" \
    && python -m pip wheel \
        --wheel-dir /wheels \
        --requirement /build/requirements.txt

FROM docker.io/library/python:3.11.15-alpine3.22@sha256:a4fc589b32e824f3f02ed9d7e7be19518aa47e105b80416336af9f202275a489

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG IMAGE_TAG=nexusdesk/helpdesk:server
ARG APP_VERSION=server
ARG FRONTEND_BUILD_SHA=unknown

LABEL org.opencontainers.image.revision=${GIT_SHA}
LABEL org.opencontainers.image.created=${BUILD_TIME}
LABEL org.opencontainers.image.version=${APP_VERSION}

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

# Security updates are performed by reviewing and advancing the immutable base
# digest. The build itself must not mutate package resolution with apk upgrade.
# Install only prebuilt wheels; compilers, Cargo and development headers remain
# in the discarded wheel-builder stage. Packaging/build tools are removed from
# the runtime after installation because the service never builds packages.
COPY backend/requirements.txt /tmp/requirements.txt
COPY --from=python-wheel-builder /wheels /wheels
RUN python -m pip install \
        --no-index \
        --find-links=/wheels \
        --requirement /tmp/requirements.txt \
    && python -m pip uninstall -y \
        autocommand \
        jaraco.context \
        setuptools \
        wheel \
    && rm -rf /wheels /tmp/requirements.txt /root/.cache

# Keep the runtime image deterministic. Do not COPY the whole repository because
# that can bake local caches, VCS metadata, env files, uploads, or secrets into
# the image when .dockerignore drifts.
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY THIRD_PARTY_NOTICES.md /app/THIRD_PARTY_NOTICES.md
COPY --from=webapp-builder /build/frontend_dist /app/frontend_dist

# Round B webchat widget static export. Keep embeddable public files outside SPA
# fallback while retaining the non-root runtime boundary. The metrics directory
# is a runtime mount point shared only by processes in one controlled release.
RUN mkdir -p \
        /app/frontend_dist/static/webchat \
        /app/backend/uploads \
        /var/run/nexus-prometheus \
    && cp -r /app/backend/app/static/webchat/. /app/frontend_dist/static/webchat/ \
    && addgroup -S appgroup \
    && adduser -S -D -H -s /sbin/nologin -G appgroup appuser \
    && chown -R appuser:appgroup /app /var/run/nexus-prometheus

WORKDIR /app/backend

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).read()" || exit 1

USER appuser

CMD ["sh", "-c", "gunicorn app.main:app -c /app/backend/gunicorn.conf.py -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-2} -b 0.0.0.0:8080 --timeout ${WEB_TIMEOUT:-60}"]
