FROM docker.io/library/node:22.23.1-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS builder

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmjs.org/ \
    && npm ci
COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run typecheck \
    && npm test \
    && npm run build \
    && npm prune --omit=dev

FROM docker.io/library/node:22.23.1-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ARG APP_VERSION=whatsapp-sidecar

LABEL org.opencontainers.image.title="NexusDesk WhatsApp Baileys Sidecar" \
      org.opencontainers.image.revision=${GIT_SHA} \
      org.opencontainers.image.created=${BUILD_TIME} \
      org.opencontainers.image.version=${APP_VERSION}

ENV NODE_ENV=production \
    APP_ENV=production \
    WA_SIDECAR_CONNECTOR_MODE=baileys \
    WA_SIDECAR_PORT=18793 \
    WHATSAPP_SESSION_ROOT=/data/whatsapp-sessions \
    WHATSAPP_CALLBACK_SPOOL_ROOT=/data/whatsapp-callback-spool

WORKDIR /app
COPY --from=builder --chown=node:node /build/package.json /build/package-lock.json ./
COPY --from=builder --chown=node:node /build/node_modules ./node_modules
COPY --from=builder --chown=node:node /build/dist ./dist

RUN mkdir -p /data/whatsapp-sessions /data/whatsapp-callback-spool \
    && chown -R node:node /data /app \
    && chmod 0700 /data/whatsapp-sessions /data/whatsapp-callback-spool

EXPOSE 18793
USER node

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:18793/readyz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]

CMD ["node", "dist/index.js"]
