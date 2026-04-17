# OpenClaw Gateway 接口 Demo 与说明

本文汇总仓库里 `demo/` 目录下四类 Gateway 接口演示脚本，对应 OpenClaw 官方
Gateway 的四个对外协议：

| 接口 | 协议 | Demo | 官方文档 |
| --- | --- | --- | --- |
| OpenAI 兼容 Chat/Models/Embeddings | HTTP | `demo/openai_http_demo.py` | [openai-http-api](https://docs.openclaw.ai/gateway/openai-http-api) |
| OpenResponses | HTTP | `demo/openresponses_http_demo.py` | [openresponses-http-api](https://docs.openclaw.ai/gateway/openresponses-http-api) |
| WebSocket 控制面 / 节点传输协议 | WS | `demo/websocket_protocol_demo.py` | [protocol](https://docs.openclaw.ai/gateway/protocol) |
| 单工具直调 | HTTP | `demo/tools_invoke_demo.py` | [tools-invoke-http-api](https://docs.openclaw.ai/gateway/tools-invoke-http-api) |

> 所有 HTTP/WebSocket 接口都走同一个 Gateway 端口（默认 `18789`）；即 HTTP 与 WS
> 是同端口多路复用的。

## 0. 准备

- 确认 Gateway 已启动：`systemctl --user status openclaw-gateway.service` 或仓库
  的 `scripts/status_openclaw_gateway.sh`。
- 获取鉴权凭证。依 `gateway.auth.mode` 不同：
  - `token`：`gateway.auth.token` 或环境变量 `OPENCLAW_GATEWAY_TOKEN`
  - `password`：`gateway.auth.password` 或 `OPENCLAW_GATEWAY_PASSWORD`
  - `trusted-proxy` / `none`：依部署边界决定，参见下文各接口的鉴权说明。

本仓库自带的 `OpenClawGatewayClient` 默认使用密码 `zxt2000`；如果 Gateway 采
用其它 token（例如安装时随机生成的 `gateway.auth.token`），Demo 的 `--token`
参数必须使用真实的 token。

- 运行环境：`UV_CACHE_DIR=/tmp/uv-cache uv sync --group dev`。
- 绕过代理：如果机器设置了 `http_proxy` / `https_proxy`，调用本地 Gateway 前需要
  `unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY` 或在 curl 中加
  `--noproxy '*'`。

---

## 1. OpenAI 兼容 HTTP 接口（`demo/openai_http_demo.py`）

Gateway 暴露一套与 OpenAI 同形态的 HTTP 接口，方便 Open WebUI / LobeChat /
LibreChat 等前端直接接入：

- `GET  /v1/models`
- `GET  /v1/models/{id}`
- `POST /v1/chat/completions`
- `POST /v1/embeddings`
- `POST /v1/responses`（同时出现在 OpenResponses 文档里）

### 1.1 启用开关

该端点**默认关闭**，需要写入配置：

```json5
{
  gateway: {
    http: {
      endpoints: {
        chatCompletions: { enabled: true },
      },
    },
  },
}
```

禁用则改为 `enabled: false`。

### 1.2 鉴权

- `Authorization: Bearer <token-or-password>` （共享密钥模式）
- 可选请求头：
  - `x-openclaw-model: <provider/model-or-id>` 覆盖后端模型。
  - `x-openclaw-agent-id: <agentId>` 或 `model: "openclaw/<agentId>"` 指定 Agent。
  - `x-openclaw-session-key: <sessionKey>` 强制会话路由。
  - `x-openclaw-message-channel: <channel>` 模拟 channel 上下文。

**重要安全边界**：共享密钥鉴权下该端点等价于 gateway 的完整 operator 权限，
且调用一律被视为「owner」发起，窄化的 `x-openclaw-scopes` 会被忽略，不要把
token/password 分发给外部或公网。

### 1.3 使用 Demo

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

# 列出可用 Agent
python3 demo/openai_http_demo.py --token $OPENCLAW_TOKEN --mode models

# 同步 chat 调用
python3 demo/openai_http_demo.py \
  --token $OPENCLAW_TOKEN \
  --mode chat \
  --model openclaw/default \
  --prompt "你好，请一句话介绍你自己。"

# SSE 流式
python3 demo/openai_http_demo.py \
  --token $OPENCLAW_TOKEN \
  --mode chat --stream \
  --prompt "给我三条使用 OpenClaw 的建议"

# 指定后端模型（只覆盖该请求）
python3 demo/openai_http_demo.py \
  --token $OPENCLAW_TOKEN \
  --mode chat \
  -H "x-openclaw-model: openai/gpt-5.4" \
  --prompt "用一句话总结 OpenClaw 的定位。"

# 生成 embeddings
python3 demo/openai_http_demo.py \
  --token $OPENCLAW_TOKEN \
  --mode embeddings \
  -H "x-openclaw-model: openai/text-embedding-3-small" \
  --embedding-input "alpha" --embedding-input "beta"
```

Demo 使用标准库 `urllib`，不引入额外依赖。SSE 流式解析直接逐行读取 `data:` 记
录，并把 `delta.content` 拼接到标准输出。

### 1.4 注意事项

- `model` 字段是「Agent target」，不是后端 Provider 模型 id。固定别名：
  - `openclaw` / `openclaw/default`：默认 Agent
  - `openclaw/<agentId>`：特定 Agent
  - 兼容别名：`openclaw:<agentId>`、`agent:<agentId>`
- 默认**每次请求都会生成新 session key**；若希望同一个 user 复用会话，传
  OpenAI 的 `user` 字段即可（demo 可用 `--prompt` + 自定义 body 扩展）。
- `/v1/models` 只展示 Agent 目标，不列 provider 原始模型或 sub-agent。

---

## 2. OpenResponses HTTP 接口（`demo/openresponses_http_demo.py`）

`POST /v1/responses` 是 OpenClaw 对「OpenResponses」协议的兼容实现，支持
item-based input、流式事件、function tool、`input_image` 与 `input_file`。

### 2.1 启用开关

默认关闭，开启方式：

```json5
{
  gateway: {
    http: {
      endpoints: {
        responses: { enabled: true },
      },
    },
  },
}
```

### 2.2 请求结构

目前支持：

- `model`：Agent target，与 Chat Completions 共享约束
- `input`：字符串或 item 对象数组（`message` / `function_call_output` / `input_image` / `input_file` / `reasoning` / `item_reference`）
- `instructions`：附加到 system prompt
- `tools` + `tool_choice`：客户端 function tools 定义
- `stream`：启用 SSE
- `max_output_tokens`、`user`、`previous_response_id`

被接受但当前忽略的字段：`max_tool_calls`、`reasoning`、`metadata`、`store`、
`truncation`。

`message` 的 role 支持 `system` / `developer` / `user` / `assistant`；其中
`system` 和 `developer` 都会合并到 system prompt，最新的 `user` 或
`function_call_output` 会被视为「当前消息」。

### 2.3 流式事件类型

启用 `stream: true` 时 Gateway 按 SSE 发送下列事件，结束以 `data: [DONE]` 标
记：

```
response.created
response.in_progress
response.output_item.added
response.content_part.added
response.output_text.delta
response.output_text.done
response.content_part.done
response.output_item.done
response.completed
response.failed   # 发生错误时
```

Demo 会把 `response.output_text.delta` 合并输出为人类可读文本，其它事件按
`event` / `data` 原样打印。

### 2.4 使用 Demo

```bash
# 字符串 input
python3 demo/openresponses_http_demo.py \
  --token $OPENCLAW_TOKEN \
  --prompt "请用一句话总结 OpenClaw 的核心能力。"

# 复杂 item-based input
python3 demo/openresponses_http_demo.py \
  --token $OPENCLAW_TOKEN \
  --input demo/example_responses_input.json

# 流式
python3 demo/openresponses_http_demo.py \
  --token $OPENCLAW_TOKEN \
  --stream --prompt "列出 OpenResponses 支持的 3 种 input item 类型。"

# 指定 Agent
python3 demo/openresponses_http_demo.py \
  --token $OPENCLAW_TOKEN \
  -H "x-openclaw-agent-id: main" \
  --model "openclaw" --prompt "hi"
```

### 2.5 文件/图片输入的注意点

- `input_file` 默认被解码后作为**不可信外部内容**注入 system prompt，使用明确
  的 `<<<EXTERNAL_UNTRUSTED_CONTENT id="...">>>` 边界标记，文本不会进入会话
  历史。
- PDF 优先走文本抽取，若抽取到的文本很少，首几页会被栅格化为图像再送给模型。
- 允许的 MIME / 大小 / URL 允许列表等可在
  `gateway.http.endpoints.responses.{files,images}` 下细调，默认：
  body 20MB，file 5MB，image 10MB，URL 部分最多 8 个，file 超时 10s。

---

## 3. WebSocket 协议（`demo/websocket_protocol_demo.py`）

Gateway 的原生协议是 WebSocket JSON 帧，负责 **全量控制面 + 节点传输**。CLI、
Web UI、iOS/Android 节点都使用同一条 WS 接入。

### 3.1 帧模型

```
连接 → Gateway: {type:"event", event:"connect.challenge", payload:{nonce, ts}}
请求   → Gateway: {type:"req",   id, method, params}
响应   ← Gateway: {type:"res",   id, ok, payload | error}
事件   ← Gateway: {type:"event", event, payload, seq?, stateVersion?}
```

- 首帧必须是 `connect` 请求，`params` 中需要：
  - `minProtocol` / `maxProtocol`（协议版本，当前 `3`）
  - `client`、`role`、`scopes`、`caps`、`commands`、`permissions`
  - `auth`（`token` / `password`）
  - `device`：含 `id`、`publicKey`、`signature`、`signedAt`、`nonce`
- 服务端 `hello-ok` 响应会包含 `features`、`snapshot`、`policy`、可能的
  `auth.deviceToken`，客户端应按 `policy.tickIntervalMs` 续活。
- 对所有有副作用的方法都要带 **幂等键**（见 schema）。

### 3.2 角色与 scope

- `operator`：控制面客户端（CLI / UI / 自动化）。常用 scope：
  `operator.read`、`operator.write`、`operator.admin`、`operator.approvals`、
  `operator.pairing`、`operator.talk.secrets`。
- `node`：能力宿主（camera / screen / canvas / system.run 等）。节点通过
  `caps` / `commands` / `permissions` 声明能力，Gateway 仍然会做服务端白名单校验。

### 3.3 Demo 做了什么

`demo/websocket_protocol_demo.py` 复用仓库里 `openclaw_client.OpenClawGatewayClient`：

1. 建立 WebSocket 连接
2. 等待 `connect.challenge` 事件，读取服务端 `nonce`
3. 用本机 `~/.openclaw/identity/device.json` 的设备密钥签名 v2/v3 payload
4. 发送 `connect` 请求（支持密码或 operator token 两种鉴权）
5. 选择 `agent` / `chat.history` / 任意 RPC 调用，打印帧内容

支持三种子模式：

```bash
# 发送一轮 agent 对话并打印完整 res+事件
uv run python demo/websocket_protocol_demo.py \
  --token $OPENCLAW_TOKEN --mode send \
  --message "请帮我列出当前的 operator scope"

# 拉取某个 session 的历史（chat.history 的 WS 版本）
uv run python demo/websocket_protocol_demo.py \
  --token $OPENCLAW_TOKEN --mode history --session-key main

# 任意 RPC 直调
uv run python demo/websocket_protocol_demo.py \
  --token $OPENCLAW_TOKEN --mode raw --method health --params '{}'
uv run python demo/websocket_protocol_demo.py \
  --token $OPENCLAW_TOKEN --mode raw --method models.list --params '{}'
```

### 3.4 常用 RPC 方法家族

`hello-ok.features.methods` 会返回一个保守的探测列表，下面是官方文档列出的几个
主要家族（完整列表见 `protocol` 文档）：

- 系统：`health`、`status`、`gateway.identity.get`、`system-presence`
- 模型/用量：`models.list`、`usage.status`、`usage.cost`、`sessions.usage.*`
- 会话：`sessions.list`、`sessions.create`、`sessions.send`、`sessions.abort`、
  `sessions.messages.subscribe` 以及 `chat.history` / `chat.send` / `chat.abort`
- 设备/节点配对：`device.pair.*`、`device.token.*`、`node.pair.*`、`node.invoke*`
- 审批：`exec.approval.*`、`exec.approvals.*`、`plugin.approval.*`
- 配置/Secret：`config.get|set|patch|apply|schema`、`secrets.reload|resolve`
- 工具/技能：`commands.list`、`tools.catalog`、`tools.effective`、`skills.*`
- 自动化：`wake`、`cron.*`

事件方面常见：`chat`、`session.message`、`session.tool`、`sessions.changed`、
`presence`、`tick`、`health`、`heartbeat`、`cron`、`exec.approval.*`、
`node.pair.*`。

### 3.5 鉴权与设备签名注意项

- 所有 WS 客户端必须在 `connect.params.device` 里带上设备身份，并对
  `connect.challenge` 返回的 `nonce` 做签名（推荐 v3 payload，v2 仍兼容）。
- 配对成功后 `hello-ok.auth.deviceToken` 是下次连接的首选凭证，应持久化保存。
- `AUTH_TOKEN_MISMATCH` 时 Gateway 会在 `error.details` 附 `canRetryWithDeviceToken`
  与 `recommendedNextStep`，客户端可据此决定自动重试还是提示人工处理。

---

## 4. Tools Invoke HTTP 接口（`demo/tools_invoke_demo.py`）

`POST /tools/invoke` 用于**单工具直调**。相比 WS 的 `node.invoke` / `sessions.*`
链路，它是最小、最直白的一次性调用接口，默认开启，不需要额外配置。

### 4.1 鉴权与安全

- 同 OpenAI 兼容端点：共享密钥即全量 operator 权限；`x-openclaw-scopes` 在共
  享密钥模式下会被忽略并恢复 operator 默认 scope。
- Gateway 会对危险工具保持**硬拒绝**（即使 session 策略允许）：
  `exec`、`spawn`、`shell`、`fs_write`、`fs_delete`、`fs_move`、`apply_patch`、
  `sessions_spawn`、`sessions_send`、`cron`、`gateway`、`nodes`、`whatsapp_login`。
- 默认最大请求体 2 MB。
- 访问不在 allowlist 的工具 → 返回 `404`；参数错误 → `400`；未授权 → `401`；
  认证限流 → `429`；方法错误 → `405`；工具执行异常 → `500`（信息已脱敏）。

可通过以下配置微调：

```json5
{
  gateway: {
    tools: {
      deny: ["browser"],   // 再额外屏蔽
      allow: ["gateway"],  // 从默认 deny 列表中放行
    },
  },
}
```

### 4.2 请求体字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool` | string | **必填**。工具名。 |
| `action` | string | 可选。工具 schema 支持 `action` 且 `args` 里未提供时使用。 |
| `args` | object | 可选。工具参数。 |
| `sessionKey` | string | 可选。默认 `main`（遵循 `session.mainKey` 与默认 Agent）。 |
| `dryRun` | bool | 保留字段，目前被忽略。 |

可选请求头：`x-openclaw-message-channel`、`x-openclaw-account-id`，辅助组/频
道策略解析。

### 4.3 使用 Demo

```bash
# 列出当前会话（最常用的健康检查）
python3 demo/tools_invoke_demo.py --token $OPENCLAW_TOKEN --tool sessions_list

# 带 action 字段
python3 demo/tools_invoke_demo.py --token $OPENCLAW_TOKEN \
  --tool sessions_list --action json

# 调用任意工具 + 自定义 args
python3 demo/tools_invoke_demo.py --token $OPENCLAW_TOKEN \
  --tool web_search --args-json '{"query":"openclaw"}'

# 指定 sessionKey
python3 demo/tools_invoke_demo.py --token $OPENCLAW_TOKEN \
  --tool sessions_list --session-key main
```

返回值固定形状：

```json
{ "ok": true, "result": { "content": [...], "details": { ... } } }
```

错误形状：

```json
{ "ok": false, "error": { "type": "unauthorized", "message": "..." } }
```

---

## 5. 常见排障清单

- **`{"error":{"message":"Unauthorized"}}`**：`Authorization: Bearer` 的值与
  `gateway.auth.token` / `gateway.auth.password` 不一致，或使用了密码模式但没
  启用。
- **HTML 页面被返回**：当 `/v1/...` 端点未启用时，Gateway 的 HTTP 多路复用会回
  落到 Control UI。把 `gateway.http.endpoints.*.enabled` 打开后即可。
- **`404` `Tool not available`**：工具未在默认 allowlist 中，或命中硬 deny
  列表，或 session 策略禁止。参考「硬拒绝」小节并调整 `gateway.tools` 配置。
- **连不上 127.0.0.1:18789**：多数情况下是本机设置了 `http_proxy`，请先
  `unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY` 或给 curl 加
  `--noproxy '*'`。
- **`connect.challenge` 之后报 `DEVICE_AUTH_*`**：设备签名问题，参考 `protocol`
  文档的迁移表：检查 nonce、`signedAt`、publickey/deviceId 一致性，优先使用 v3
  payload。
