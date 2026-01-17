FROM node:22-slim AS builder

# Install build dependencies for native modules
RUN apt-get update && apt-get install -y \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package*.json ./
COPY xmcp.config.ts ./
COPY tsconfig.json ./
RUN npm ci

# Build arg to bust cache when source changes
ARG CACHEBUST=1
COPY src ./src

RUN npm run build

# Prune dev dependencies after build
RUN npm prune --production

FROM node:22-slim

WORKDIR /app

# Copy the built dist and pruned node_modules from builder
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/node_modules ./node_modules

# Hoist nested native module to root for bundler compatibility
RUN mkdir -p /app/node_modules/@matrix-org && \
    cp -r /app/node_modules/@vector-im/matrix-bot-sdk/node_modules/@matrix-org/matrix-sdk-crypto-nodejs \
          /app/node_modules/@matrix-org/

# Create minimal package.json without "type": "module" since xmcp outputs CommonJS
RUN echo '{"name":"matrix-identity-bridge","private":true}' > package.json

RUN mkdir -p /app/data

ENV NODE_ENV=production
ENV DATA_DIR=/app/data

EXPOSE 3100

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD node -e "fetch('http://localhost:3100/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

CMD ["node", "dist/http.js"]
# Cache bust: 1768676484
