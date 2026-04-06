#!/usr/bin/env bash

set -euo pipefail

SERVICE_NAME="openclaw-gateway.service"
GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:18789}"
GATEWAY_PASSWORD="${OPENCLAW_GATEWAY_PASSWORD:-zxt2000}"
SESSION_KEY="main"
TEST_MESSAGE="你好，请回复一条测试消息,爸爸的爷爷的儿子叫什么"

echo "检查 ${SERVICE_NAME} 状态 ..."
systemctl --user is-active --quiet "${SERVICE_NAME}" || {
  echo "${SERVICE_NAME} 未运行，请先执行: bash scripts/start_openclaw_gateway.sh" >&2
  exit 1
}

echo "使用密码连接 Gateway 并发送测试消息 ..."
echo "  URL: ${GATEWAY_URL}"
echo "  密码: ${GATEWAY_PASSWORD}"
echo "  会话: ${SESSION_KEY}"
echo ""

uv run python src/openclaw_gateway_chat_test.py \
  --url "${GATEWAY_URL}" \
  --password "${GATEWAY_PASSWORD}" \
  --session-key "${SESSION_KEY}" \
  --message "${TEST_MESSAGE}"
