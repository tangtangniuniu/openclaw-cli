#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="openclaw-gateway.service"

echo "查询 ${SERVICE_NAME} 状态 ..."
systemctl --user --no-pager --full status "${SERVICE_NAME}"
