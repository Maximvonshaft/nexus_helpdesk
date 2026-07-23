FROM docker.io/library/node:22.23.1-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS webapp-builder
WORKDIR /build/webapp
COPY webapp/package*.json ./
RUN npm config set registry https://registry.npmjs.org/
RUN npm ci
COPY webapp/ ./
RUN npm run build

# LiveKit Agents and native media/tokenization dependencies publish manylinux
# wheels. Build them once on the same glibc generation as the distroless runtime.
FROM docker.io/library/python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba AS python-wheel-builder
WORKDIR /build
COPY backend/requirements.txt /build/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cargo \
        libffi-dev \
        libssl-dev \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade \
        "pip==26.1.1" \
        "setuptools==82.0.0" \
        "wheel==0.46.2" \
        "jaraco.context==6.1.0" \
    && python -m pip wheel \
        --wheel-dir /wheels \
        --requirement /build/requirements.txt

FROM docker.io/library/python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba AS python-runtime-builder
COPY backend/requirements.txt /tmp/requirements.txt
COPY --from=python-wheel-builder /wheels /wheels

# The pinned Python base predates the current Bookworm OpenSSL security release.
# Install and vendor the exact Debian-fixed runtime package, including its complete
# file set and Distroless-compatible package metadata. The final scanner therefore
# evaluates the exact bytes shipped, without a CVE exception or forged version.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libssl3=3.0.20-1~deb12u2; \
    mkdir -p \
        /tmp/libssl-package \
        /tmp/libssl-control \
        /runtime-debian-root \
        /runtime-package-metadata; \
    cd /tmp/libssl-package; \
    apt-get download libssl3=3.0.20-1~deb12u2; \
    package="$(find . -maxdepth 1 -type f -name 'libssl3_*.deb' -print -quit)"; \
    test -n "$package"; \
    dpkg-deb --extract "$package" /runtime-debian-root; \
    dpkg-deb --field "$package" > /runtime-package-metadata/libssl3; \
    dpkg-deb --control "$package" /tmp/libssl-control; \
    cp /tmp/libssl-control/md5sums /runtime-package-metadata/libssl3.md5sums; \
    grep -F 'Version: 3.0.20-1~deb12u2' /runtime-package-metadata/libssl3; \
    rm -rf /tmp/libssl-package /tmp/libssl-control /var/lib/apt/lists/*

RUN python -m pip install \
        --no-index \
        --find-links=/wheels \
        --requirement /tmp/requirements.txt \
    && python -m pip uninstall -y \
        autocommand \
        jaraco.context \
        setuptools \
        wheel \
        pip \
    && rm -rf /wheels /tmp/requirements.txt /root/.cache \
    # Nexus does not ship a desktop UI. Removing the optional Tk modules prevents
    # the server image from acquiring unused Tcl/Tk libraries and attack surface.
    && rm -rf \
        /usr/local/lib/python3.11/tkinter \
        /usr/local/lib/python3.11/idlelib \
        /usr/local/lib/python3.11/turtledemo \
    && rm -f /usr/local/lib/python3.11/lib-dynload/_tkinter*.so

# Distroless cc contains the base C runtime but not every system library used by
# CPython and accepted native wheels. Treat Python and importable extension modules
# as dependency roots. Wheel-private *.libs files are already copied with
# /usr/local; scanning them independently would discard their $ORIGIN context and
# report false missing siblings. Instead expose every wheel-private library
# directory while resolving the complete transitive system-library closure.
RUN set -eux; \
    mkdir -p /runtime-libs; \
    wheel_library_path="$(find /usr/local/lib/python3.11/site-packages -type d -name '*.libs' -print | sort -u | paste -sd: -)"; \
    { \
      printf '%s\n' /usr/local/bin/python3.11; \
      find /usr/local/lib/python3.11/lib-dynload -type f -name '*.so' ! -name '_tkinter*.so' -print; \
      find /usr/local/lib/python3.11/site-packages -type f -name '*.so' ! -path '*/*.libs/*' -print; \
    } | sort -u > /tmp/native-targets; \
    : > /tmp/ldd-report; \
    while IFS= read -r target; do \
      output="$(LD_LIBRARY_PATH="$wheel_library_path" ldd "$target" 2>&1)" && status=0 || status=$?; \
      if [ "$status" -eq 0 ]; then \
        printf 'target %s\n%s\n' "$target" "$output" >> /tmp/ldd-report; \
      elif printf '%s\n' "$output" | grep -Eq 'not a dynamic executable|statically linked'; then \
        printf 'static-or-nondynamic %s\n' "$target" >> /tmp/ldd-report; \
      else \
        printf 'ldd failed for %s: %s\n' "$target" "$output" >&2; \
        exit "$status"; \
      fi; \
    done < /tmp/native-targets; \
    if grep -F 'not found' /tmp/ldd-report; then \
      echo 'unresolved native runtime dependency' >&2; \
      exit 1; \
    fi; \
    awk \
      '/=> \/[^ ]+/ {print $3} \
       /^[[:space:]]*\/[^ ]+[[:space:]]+\(0x/ {print $1}' \
      /tmp/ldd-report \
      | sort -u \
      | grep -v '^/usr/local/' \
      > /tmp/runtime-library-paths; \
    while IFS= read -r library; do \
      test -f "$library"; \
      cp -L --parents "$library" /runtime-libs; \
    done < /tmp/runtime-library-paths; \
    test -s /tmp/native-targets; \
    test -s /tmp/runtime-library-paths; \
    LD_LIBRARY_PATH="$wheel_library_path" /usr/local/bin/python -c "import alembic, av, cryptography, gunicorn, livekit, livekit.agents, psycopg, sqlalchemy, uvicorn"

# Assemble the immutable application tree before entering the shell-less runtime.
FROM docker.io/library/python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba AS runtime-layout
COPY backend/ /layout/app/backend/
COPY scripts/ /layout/app/scripts/
COPY THIRD_PARTY_NOTICES.md /layout/app/THIRD_PARTY_NOTICES.md
COPY --from=webapp-builder /build/frontend_dist /layout/app/frontend_dist
RUN mkdir -p \
        /layout/app/frontend_dist/static/webchat \
        /layout/app/backend/uploads \
        /layout/var/run/nexus-prometheus \
    && cp -r /layout/app/backend/app/static/webchat/. /layout/app/frontend_dist/static/webchat/ \
    && chown -R 65532:65532 /layout/app /layout/var/run/nexus-prometheus

FROM gcr.io/distroless/cc-debian12:nonroot@sha256:8f02d47496256aca25168c508024a60d81af996bbd2d89db4cf5cdf13dff2821

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
    PATH=/usr/local/bin:/usr/bin \
    PYTHONPATH=/app/backend \
    GIT_SHA=${GIT_SHA} \
    BUILD_TIME=${BUILD_TIME} \
    IMAGE_TAG=${IMAGE_TAG} \
    APP_VERSION=${APP_VERSION} \
    FRONTEND_BUILD_SHA=${FRONTEND_BUILD_SHA}

COPY --from=python-runtime-builder /usr/local/ /usr/local/
COPY --from=python-runtime-builder /runtime-debian-root/ /
COPY --from=python-runtime-builder /runtime-package-metadata/libssl3 /var/lib/dpkg/status.d/libssl3
COPY --from=python-runtime-builder /runtime-package-metadata/libssl3.md5sums /var/lib/dpkg/status.d/libssl3.md5sums
COPY --from=python-runtime-builder /runtime-libs/ /
COPY --from=runtime-layout --chown=65532:65532 /layout/ /

WORKDIR /app/backend
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/usr/local/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).read()"]

USER 65532:65532

# Execute the real final image, as its fixed non-root identity, during build. This
# proves the copied CPython/native closure rather than only the discarded builder.
RUN ["/usr/local/bin/python", "-c", "import alembic, av, cryptography, gunicorn, livekit, livekit.agents, psycopg, sqlalchemy, ssl, uvicorn; assert ssl.OPENSSL_VERSION.startswith('OpenSSL 3.0.20')"]

CMD ["/usr/local/bin/python", "-m", "gunicorn", "app.main:app", "--config", "/app/backend/gunicorn.conf.py", "-k", "uvicorn.workers.UvicornWorker", "--workers", "2", "--bind", "0.0.0.0:8080", "--timeout", "60"]
