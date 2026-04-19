#!/bin/bash

# 启动 OpenClaw 多用户多会话并发测试 chatbot。
# 默认端口 5273（避开单用户 chatbot 的 5173）。

uv run python -m multi_chatbot.server \
    --gateway-token d4d0e1803e2f7e91bc6155fd37782558f60fcb8a5527242f \
    --port 5273 \
    "$@"
