#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

: "${TURSO_DATABASE_URL:?Set TURSO_DATABASE_URL in the environment or .env}"

PYTHONPATH="$ROOT" python3 -m scraper.scrape_pulse sync
