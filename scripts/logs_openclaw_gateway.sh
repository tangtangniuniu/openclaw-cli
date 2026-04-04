#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="openclaw-gateway.service"
LINES="${1:-200}"

echo "查看 ${SERVICE_NAME} 最近 ${LINES} 行日志 ..."
journalctl --user -u "${SERVICE_NAME}" -n "${LINES}" --no-pager
