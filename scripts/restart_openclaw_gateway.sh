#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="openclaw-gateway.service"

echo "重启 ${SERVICE_NAME} ..."
systemctl --user restart "${SERVICE_NAME}"

echo "当前状态:"
systemctl --user --no-pager --full status "${SERVICE_NAME}"
