# OpenClaw Gateway 密码配置与 Python 对话测试

本文说明两件事：

1. 将 OpenClaw Gateway 的连接密码设置为 `zxt2000`
2. 使用 Python 连接 Gateway，并发送一条测试消息验证对话是否正常

## 1. 配置 Gateway 密码

如果你当前是通过 `systemctl --user` 管理 Gateway，推荐直接使用仓库里的脚本：

```bash
bash scripts/start_openclaw_gateway.sh
```

停止 Gateway：

```bash
bash scripts/stop_openclaw_gateway.sh
```

重启 Gateway：

```bash
bash scripts/restart_openclaw_gateway.sh
```

查看状态：

```bash
bash scripts/status_openclaw_gateway.sh
```

查看最近 200 行日志：

```bash
bash scripts/logs_openclaw_gateway.sh
```

查看最近 500 行日志：

```bash
bash scripts/logs_openclaw_gateway.sh 500
```

如果你不是通过 systemd 用户服务启动，也可以直接使用环境变量启动：

```bash
export OPENCLAW_GATEWAY_PASSWORD=zxt2000
openclaw gateway
```

也可以直接通过命令行参数启动：

```bash
openclaw gateway --password zxt2000
```

当前用户服务名为：

```text
openclaw-gateway.service
```

如果 Gateway 默认监听本机端口，则 Python 客户端通常连接到：

```text
ws://127.0.0.1:18789
```

## 2. 初始化 uv 项目依赖

本仓库现在使用 `uv` 管理 Python 客户端和测试依赖。首次使用时执行：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv sync --group dev
```

这会安装运行时依赖 `websockets`，以及测试依赖 `pytest`、`pytest-asyncio`。

## 3. Python 对话测试脚本

已提供可直接运行的脚本：

- `src/openclaw_gateway_chat_test.py`

脚本做的事情如下：

1. 建立到 Gateway 的 WebSocket 连接
2. 处理 Gateway 返回的 `connect.challenge`
3. 优先复用本机 `~/.openclaw/identity/device.json` 和 `~/.openclaw/identity/device-auth.json` 中已有的设备身份与 operator token 完成签名握手
4. 如果没有本机 operator token，则回退为密码 `zxt2000`
5. 向指定会话发送一条测试消息
6. 打印 Gateway 返回的响应和事件

核心调用方式如下：

```bash
uv run python src/openclaw_gateway_chat_test.py
```

如果需要覆盖默认地址、密码或会话 key，可以这样运行：

```bash
uv run python src/openclaw_gateway_chat_test.py \
  --url ws://127.0.0.1:18789 \
  --password zxt2000 \
  --session-key main \
  --message "你好，请回复一条测试消息"
```

## 4. pytest 测试

运行单元测试：

```bash
uv run pytest tests/unit -q
```

运行真实 Gateway 端到端测试：

```bash
RUN_OPENCLAW_E2E=1 uv run pytest tests/e2e/test_real_gateway.py -m e2e -q
```

也可以直接执行仓库脚本：

```bash
bash scripts/test_openclaw_gateway_chat.sh
```

这个脚本会先检查 `openclaw-gateway.service` 是否已启动，然后依次执行：

1. 单元测试
2. 真实 Gateway 的连接、发送、断开端到端测试

## 5. 预期结果

脚本正常时应看到以下信息：

- `connect` 请求返回成功
- `agent` 请求返回成功，或收到相关事件
- 终端中打印出 Gateway 返回的 JSON 数据

如果连接失败，请优先检查：

- Gateway 是否已经启动
- 如果使用 systemd 用户服务，执行 `systemctl --user status openclaw-gateway.service`
- 端口是否正确
- 密码是否与 `OPENCLAW_GATEWAY_PASSWORD` 一致
- 本机是否能访问 `ws://127.0.0.1:18789`

## 6. 说明

这个脚本用于最小化验证 Gateway 认证和消息发送链路，不包含完整的会话管理、重连、流式消费和异常恢复逻辑。如果后续需要，可以再扩展成完整客户端。
