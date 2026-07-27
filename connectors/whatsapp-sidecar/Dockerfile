FROM docker.io/library/node:22.23.1-alpine3.24@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS builder

WORKDIR /build
COPY package.json package-lock.json ./
RUN npm config set registry https://registry.npmjs.org/ \
    && npm ci --ignore-scripts --no-audit --no-fund
COPY tsconfig.json ./
COPY src/ ./src/
RUN npm run typecheck \
    && npm test \
    && npm run build \
    && npm prune --omit=dev \
    && rm -rf /root/.npm /tmp/*

FROM docker.io/library/node:22.23.1-alpine3.24@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2

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
COPY --from=builder --chown=node:node /build/package.json ./package.json
COPY --from=builder --chown=node:node /build/node_modules ./node_modules
COPY --from=builder --chown=node:node /build/dist ./dist

# The production process needs only the Node runtime. Removing npm, Corepack and
# Yarn eliminates package-manager CVEs and prevents runtime dependency mutation.
RUN rm -rf \
      /usr/local/lib/node_modules/npm \
      /usr/local/lib/node_modules/corepack \
      /usr/local/bin/npm \
      /usr/local/bin/npx \
      /usr/local/bin/corepack \
      /usr/local/bin/yarn \
      /usr/local/bin/yarnpkg \
      /opt/yarn-* \
      /root/.npm \
      /tmp/* \
    && mkdir -p /data/whatsapp-sessions /data/whatsapp-callback-spool \
    && chown -R node:node /data /app \
    && chmod 0700 /data/whatsapp-sessions /data/whatsapp-callback-spool

EXPOSE 18793
USER node

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:18793/readyz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"]

CMD ["node", "dist/index.js"]
