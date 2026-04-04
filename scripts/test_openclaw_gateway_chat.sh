#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="openclaw-gateway.service"
export OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:18789}"
export OPENCLAW_GATEWAY_PASSWORD="${OPENCLAW_GATEWAY_PASSWORD:-zxt2000}"
export OPENCLAW_SESSION_KEY="${OPENCLAW_SESSION_KEY:-main}"
export OPENCLAW_TEST_MESSAGE="${OPENCLAW_TEST_MESSAGE:-你好，请回复一条来自 shell 脚本的测试消息。}"
export RUN_OPENCLAW_E2E=1

echo "检查 ${SERVICE_NAME} 状态 ..."
systemctl --user is-active --quiet "${SERVICE_NAME}" || {
  echo "${SERVICE_NAME} 未运行，请先执行: bash scripts/start_openclaw_gateway.sh" >&2
  exit 1
}

echo "运行单元测试 ..."
uv run pytest tests/unit -q

echo "运行真实 Gateway 端到端测试 ..."
uv run pytest tests/e2e/test_real_gateway.py -m e2e -q
