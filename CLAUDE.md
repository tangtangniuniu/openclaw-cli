# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个 OpenClaw Gateway 的 Python 客户端仓库，职责有两条：

1. 用 `websockets` + `cryptography` 实现 OpenClaw WebSocket 控制协议的最小客
   户端（`src/openclaw_client/`）。
2. 对外说明 Gateway 的四类接口（WS 协议、OpenAI 兼容 HTTP、OpenResponses
   HTTP、Tools Invoke HTTP）并提供可执行的示例脚本（`demo/` 与
   `doc/gateway_interfaces.md`）。

本仓库不负责实现 Gateway 本身——Gateway 作为系统用户服务
(`openclaw-gateway.service`) 独立运行在 `127.0.0.1:18789`。所有集成代码都假设
该服务可达。

## 常用命令

```bash
# 首次安装依赖
UV_CACHE_DIR=/tmp/uv-cache uv sync --group dev

# 单元测试
uv run pytest tests/unit -q

# 单个测试
uv run pytest tests/unit/test_client.py::test_connect_sends_password_and_marks_connected -q

# 端到端测试（需要真实 Gateway 在线）
RUN_OPENCLAW_E2E=1 uv run pytest tests/e2e/test_real_gateway.py -m e2e -q

# 一键跑「单测 + e2e」
bash scripts/test_openclaw_gateway_chat.sh

# 内置的 CLI 入口
uv run python src/openclaw_gateway_chat_test.py
```

Gateway 生命周期脚本（都只是 `systemctl --user` 的封装）：

```bash
bash scripts/start_openclaw_gateway.sh     # 启动
bash scripts/stop_openclaw_gateway.sh      # 停止
bash scripts/restart_openclaw_gateway.sh   # 重启
bash scripts/status_openclaw_gateway.sh    # 状态
bash scripts/logs_openclaw_gateway.sh [N]  # 最近 N 行日志，默认 200
```

Demo 脚本都带 `--help`；鉴权默认使用密码 `zxt2000`，如 Gateway 配置为
`gateway.auth.mode=token` 必须用 `--token <真实 token>` 覆盖。详见
`doc/gateway_interfaces.md`。

## 代理陷阱

本机常设 `http_proxy=http://127.0.0.1:17890`；若保留该变量去访问 `127.0.0.1`
会得到 `503 Service Unavailable`（代理把本地回环也代理了）。调试 HTTP 接口时
请先 `unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY` 或给 curl 加
`--noproxy '*'`。WebSocket 客户端 (`websockets` 库) 不受该变量影响。

## 架构要点

### `OpenClawGatewayClient` 握手流程（`src/openclaw_client/client.py`）

WS 协议有一个非标准但必须严格遵守的认证握手，实现细节：

1. `websockets.connect(url)` 后**立刻**等待 Gateway 推送的
   `connect.challenge` 事件，读取 `payload.nonce` 和 `payload.ts`。
2. `_auth_variants()` 根据用户提供的 `password` / `token` 和本机
   `~/.openclaw/identity/device-auth.json` 中的 operator token，构造一组
   `(auth_payload, signing_secret)` 组合按优先级尝试；operator token 优先于
   密码。
3. `build_signed_device()` 用 `DeviceIdentity` 的 Ed25519 私钥对 v2 payload
   (`v2|deviceId|clientId|mode|role|scopes|signedAt|signingSecret|nonce`) 签
   名，返回 `signatureCandidates` 列表。
4. `_connect_once()` 把 `connect` 请求的 `auth` 和 `device`（含逐个候选签
   名）发上去，直到 Gateway 回 `ok=true` 的 `res` 或耗尽候选为止。
5. 某次失败会 `disconnect()` 再尝试下一组 auth 组合。

如果需要扩展：**不要**把签名逻辑搬进 `client.py`——它应继续走
`device_auth.build_signed_device()`，保持「协议在 client.py，crypto 在
device_auth.py」的边界。官方协议文档 (`doc/gateway_interfaces.md` §3.1) 提到
v3 payload 是推荐格式但 v2 仍被接受；当前实现只签 v2，后续若要升级请扩展
`_build_v*_payload` 并在 `build_signed_device` 里同时返回两种候选签名，由
Gateway 自己挑选。

### `send_message()` 的回复收集策略

Gateway 的 `agent` RPC 既会同步回 `res`，也会异步推送一批 `event` 帧（包括
`agent.delta`、`agent.completed` 等）。没有单一字段能断言「回答结束」，所以
`send_message()` 组合了三种终止条件：

1. **事件终止**：事件名以 `.done / .completed / .complete / .finished /
   .finish` 结尾，或 `payload.done/completed/finished` 为 true。
2. **settle timeout**：在收到 `res` 之后，若 `settle_timeout` 秒内没有新帧，
   认为本轮结束并返回。
3. **历史回退 (`_poll_history_for_reply`)**：当上面都没拿到文本时，用
   `chat.history` RPC 轮询 session 历史，找到比 `acceptedAt` 更新的 assistant
   消息。

任何对「从事件/响应中抽取回复文本」的改动都应继续走
`extract_reply_text()` / `_collect_text_fragments()`：它做的事是在嵌套的
`payload` 中沿着 `text / delta / content / message / reply / response /
output` 这几个键递归收集字符串碎片。这套 heuristic 故意容忍字段名抖动，不
要改成严格的 schema 匹配。

### 鉴权凭据在三处协作

- 密码：`OPENCLAW_GATEWAY_PASSWORD` 环境变量、CLI `--password`、构造
  `OpenClawGatewayClient(password=...)`。仓库脚本默认 `zxt2000`。
- 设备身份：`~/.openclaw/identity/device.json`（Ed25519 PEM 对），由 Gateway
  在首次 pair 时生成；`DeviceIdentity.load()` 会校验 `deviceId` 是否等于公
  钥 SHA-256 指纹。
- operator token：`~/.openclaw/identity/device-auth.json` 的
  `tokens.operator.token`，`load_operator_token()` 读取；存在时优先于密码。

Demo 对应的 HTTP bearer 则必须匹配 `~/.openclaw/openclaw.json` 中的
`gateway.auth.token` 或 `gateway.auth.password`（取决于 `auth.mode`）——这是
跟 WS 完全不同的凭据来源，调试时容易搞混。

### Demo 脚本统一约束

`demo/*_http_demo.py` 只用标准库 `urllib`，避免引入 HTTP 依赖；SSE 流式是
手写逐行 `data:` 解析。保持这个约束：新的 HTTP demo 不应引入 `httpx` /
`requests` / `aiohttp`，除非同步修改 `pyproject.toml` 并有充分理由。

`demo/websocket_protocol_demo.py` 刻意复用 `OpenClawGatewayClient._request`
而不是重新走一遍握手，这样协议变更只需要改一处。

## 文档与回复语言

- 仓库里所有注释、docstring、文档、用户回复必须使用简体中文（`AGENTS.md`
  §其他规则）。代码标识符仍用英文。
- 文档放在 `doc/`，命名使用小写下划线；`doc/gateway_interfaces.md` 是 Gateway
  接口的中心索引，新增对外接口说明应挂在它下面或从它外链。

## 仓库布局速览

```
src/openclaw_client/      WS 客户端（client.py + device_auth.py + cli.py）
src/openclaw_gateway_chat_test.py  最小对话测试入口，作为 uv 脚本使用
demo/                     四类 Gateway 接口的独立演示脚本
doc/                      中文文档（Gateway 接口说明 + 密码/连接指南）
scripts/                  systemctl --user 的封装 + 联调脚本
tests/unit/               单元测试，monkeypatch FakeWebSocket
tests/e2e/                端到端测试，`-m e2e` 标记，连真实 Gateway
```
