FROM node:22-bookworm-slim AS dashboard-deps
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci

FROM node:22-bookworm-slim AS dashboard-builder
WORKDIR /dashboard
COPY --from=dashboard-deps /dashboard/node_modules ./node_modules
COPY dashboard/ .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-bookworm-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash \
      python3 \
      python3-pip \
      python-is-python3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-scraper.txt .
RUN pip3 install --no-cache-dir -r requirements-scraper.txt --break-system-packages \
    && playwright install --with-deps chromium

COPY scraper/ ./scraper/
COPY db/ ./db/
COPY scripts/daily-sync.sh ./scripts/daily-sync.sh
RUN chmod +x ./scripts/daily-sync.sh

COPY --from=dashboard-builder /dashboard/public ./dashboard/public
COPY --from=dashboard-builder /dashboard/.next/standalone ./dashboard
COPY --from=dashboard-builder /dashboard/.next/static ./dashboard/.next/static

EXPOSE 3000

CMD ["node", "dashboard/server.js"]
