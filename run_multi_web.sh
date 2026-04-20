#!/bin/bash

# 启动 OpenClaw 多用户多会话并发测试 chatbot。
# 默认端口 5273（避开单用户 chatbot 的 5173）。
# 默认用密码 zxt2000 连接 Gateway；如需 token，可传 --gateway-token <token> 覆盖。

uv run python -m multi_chatbot.server \
    --gateway-password zxt2000 \
    --port 5273 \
    "$@"
