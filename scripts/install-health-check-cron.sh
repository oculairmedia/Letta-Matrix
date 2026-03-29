#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

CRON_SCHEDULE="${HEALTH_CHECK_CRON_SCHEDULE:-*/15 * * * *}"
LOG_PATH="${HEALTH_CHECK_LOG_PATH:-/var/log/matrix-health-check.log}"
HEALTH_SCRIPT="${PROJECT_DIR}/scripts/health-check-auth.sh"
CRON_CMD="${CRON_SCHEDULE} ${HEALTH_SCRIPT} >> ${LOG_PATH} 2>&1"

if [[ ! -x "$HEALTH_SCRIPT" ]]; then
  chmod +x "$HEALTH_SCRIPT"
fi

CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
FILTERED_CRON="$(printf "%s\n" "$CURRENT_CRON" | grep -v "scripts/health-check-auth.sh" || true)"

{
  printf "%s\n" "$FILTERED_CRON"
  printf "%s\n" "$CRON_CMD"
} | sed '/^$/N;/^\n$/D' | crontab -

echo "Installed health-check cron job:"
echo "$CRON_CMD"
