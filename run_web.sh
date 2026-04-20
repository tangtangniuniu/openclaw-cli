#!/bin/bash

# 默认用密码 zxt2000 连接 Gateway；如需 token，可传 --gateway-token <token> 覆盖。
uv run python -m chatbot.server --gateway-password zxt2000 --port 5173 "$@"
