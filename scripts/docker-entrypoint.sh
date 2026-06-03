#!/usr/bin/env bash
set -euo pipefail

echo "${SYNC_CRON_SCHEDULE} root /bin/bash /app/scripts/daily-sync.sh >> /proc/1/fd/1 2>&1" \
  > /etc/cron.d/macro-pulse-sync
chmod 0644 /etc/cron.d/macro-pulse-sync
cron

if [[ "${RUN_SYNC_ON_START}" == "true" ]]; then
  echo "Running initial MacroPulse sync..."
  /bin/bash /app/scripts/daily-sync.sh
fi

echo "Starting dashboard on port ${PORT}..."
cd /app/dashboard
exec node server.js
