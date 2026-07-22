# WebSocket 服务器通道

Nanobot 可以作为 WebSocket 服务器，让外部客户端（Web 应用、CLI、脚本）通过持久连接与 agent 实时交互。

## 特性

- 基于 WebSocket 的双向实时通信
- 流式支持 - 逐 token 接收 agent 回复
- 基于令牌的认证（静态令牌和短期签发令牌）
- 多聊天多路复用 - 一条连接可并发运行多个 `chat_id`
- TLS/SSL 支持（WSS），强制最低 TLSv1.2
- 通过 `allowFrom` 实现客户端允许列表
- 自动清理失效连接

## 快速开始

### 1. 配置

在 `config.json` 的 `channels.websocket` 下添加：

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 8765,
      "path": "/",
      "tokenIssueSecret": "your-webui-password",
      "websocketRequiresToken": true,
      "allowFrom": ["*"],
      "streaming": true
    }
  }
}
```

### 2. 启动 nanobot

```bash
nanobot gateway
```

你应看到：

```text
WebSocket server listening on ws://127.0.0.1:8765/
```

### 3. 连接客户端

```bash
# 使用 websocat
websocat ws://127.0.0.1:8765/?client_id=alice

# 使用 Python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8765/?client_id=alice") as ws:
        ready = json.loads(await ws.recv())
        print(ready)  # {"event": "ready", "chat_id": "...", "client_id": "alice"}
        await ws.send(json.dumps({"content": "Hello nanobot!"}))
        reply = json.loads(await ws.recv())
        print(reply["text"])

asyncio.run(main())
```

## 连接 URL

```text
ws://{host}:{port}{path}?client_id={id}&token={token}
```

| 参数 | 必需 | 说明 |
|-----------|----------|-------------|
| `client_id` | 否 | 用于 `allowFrom` 授权的标识符。省略时自动生成为 `anon-xxxxxxxxxxxx`。截断为 128 字符。 |
| `token` | 条件性 | 认证令牌。当 `websocketRequiresToken` 为 `true` 或配置了 `token`（静态密钥）时必需。 |

## 线路协议

所有帧均为 JSON 文本。每条消息都有一个 `event` 字段。

### 服务器 -> 客户端

**`ready`** - 连接建立后立即发送：

```json
{
  "event": "ready",
  "chat_id": "uuid-v4",
  "client_id": "alice"
}
```

**`message`** - 完整 agent 回复：

```json
{
  "event": "message",
  "chat_id": "uuid-v4",
  "text": "Hello! How can I help?",
  "media": ["/tmp/image.png"],
  "reply_to": "msg-id"
}
```

`media` 和 `reply_to` 仅在适用时出现。

**`delta`** - 流式文本块（仅当 `streaming: true`）：

```json
{
  "event": "delta",
  "chat_id": "uuid-v4",
  "text": "Hello",
  "stream_id": "s1"
}
```

**`stream_end`** - 标识一个流式片段结束：

```json
{
  "event": "stream_end",
  "chat_id": "uuid-v4",
  "stream_id": "s1"
}
```

**`reasoning_delta`** - 当前助手回合的增量模型推理 / 思考块。镜像 `delta`，但目标是答案上方的推理气泡而非答案正文：

```json
{
  "event": "reasoning_delta",
  "chat_id": "uuid-v4",
  "text": "Let me decompose ",
  "stream_id": "r1"
}
```

**`reasoning_end`** - 当前推理流的关闭标记。WebUI 用它来锁定就地气泡，并从闪烁标题切换为静态折叠状态：

```json
{
  "event": "reasoning_end",
  "chat_id": "uuid-v4",
  "stream_id": "r1"
}
```

推理帧仅在通道的 `showReasoning` 为 `true`（默认）且模型返回推理内容时（DeepSeek-R1 / Kimi / MiMo / OpenAI 推理模型、Anthropic 扩展思考，或内联 `<think>` / `<thought>` 标签）才会流动。不带推理的模型不会产生任何 `reasoning_delta` 帧。

**`runtime_model_updated`** - 在 gateway 运行时模型变更时广播，例如执行 `/model <preset>` 之后：

```json
{
  "event": "runtime_model_updated",
  "model_name": "openai/gpt-4.1-mini",
  "model_preset": "fast"
}
```

当没有具名预设处于活动状态时省略 `model_preset`。WebUI 客户端使用该事件，在斜杠命令、配置重载和设置变更之间保持显示的模型徽章同步。

**`attached`** - 对 `new_chat` / `attach` 入站信封的确认（见 [多聊天多路复用](#多聊天多路复用)）：

```json
{"event": "attached", "chat_id": "uuid-v4"}
```

**`error`** - 格式错误入站信封的软错误。连接保持打开：

```json
{"event": "error", "detail": "invalid chat_id"}
```

### 客户端 -> 服务器

**遗留方式（默认聊天）：** 发送纯字符串，或带被识别文本字段的 JSON 对象：

```json
"Hello nanobot!"
```

```json
{"content": "Hello nanobot!"}
```

被识别字段：`content`、`text`、`message`（按此顺序检查）。无效 JSON 被视为纯文本。这些帧路由到连接的默认 `chat_id`（在 `ready` 中通告的那个）。

**类型化信封（多聊天）：** 任何带字符串 `type` 字段的 JSON 对象都是类型化信封：

| `type` | 字段 | 效果 |
|--------|--------|--------|
| `new_chat` | - | 服务器铸造新的 `chat_id`，订阅此连接，回复 `attached`。 |
| `attach` | `chat_id` | 订阅已存在的 `chat_id`（例如页面重载后）。回复 `attached`。 |
| `message` | `chat_id`, `content` | 在 `chat_id` 上发送 `content`。首次使用会自动附加；无需显式 `attach`。 |

完整流程见 [多聊天多路复用](#多聊天多路复用)。

## 配置参考

所有字段都位于 `config.json` 的 `channels.websocket` 下。

### 连接

| 字段 | 类型 | 默认值 | 说明 |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | 启用 WebSocket 服务器。 |
| `host` | string | `"127.0.0.1"` | 绑定地址。使用 `"0.0.0.0"` 以接受外部连接。 |
| `port` | int | `8765` | 监听端口。 |
| `path` | string | `"/"` | WebSocket 升级路径。末尾斜杠会被规范化（根路径 `/` 保留）。 |
| `maxMessageBytes` | int | `37748736` | 入站消息最大字节数（1 KB – 40 MB）。默认值（36 MB）的尺寸设定为可接受最多 4 个 8 MB 的 base64 编码图片附件；若通道仅承载文本，可调低。 |

### 认证

| 字段 | 类型 | 默认值 | 说明 |
|-------|------|---------|-------------|
| `token` | string | `""` | 静态共享密钥。设置后，客户端必须提供与此密钥匹配的 `?token=<value>`（时序安全比较）。签发令牌也作为回退被接受。 |
| `websocketRequiresToken` | bool | `true` | 当为 `true` 且未配置静态 `token` 时，客户端仍须出示有效的签发令牌。设为 `false` 可允许未认证连接（仅对本地/可信网络安全）。 |
| `tokenIssuePath` | string | `""` | 用于签发短期令牌的 HTTP 路径。必须不同于 `path`。见 [令牌签发](#令牌签发)。 |
| `tokenIssueSecret` | string | `""` | 通过签发端点获取令牌所需的密钥。如为空，任何客户端都可获取令牌（会记录为告警）。 |
| `tokenTtlS` | int | `300` | 签发令牌的存活时间，单位秒（30 – 86,400）。 |

### 访问控制

| 字段 | 类型 | 默认值 | 说明 |
|-------|------|---------|-------------|
| `allowFrom` | list of string | `["*"]` | 允许的 `client_id` 值。`"*"` 允许全部；`[]` 拒绝全部。 |

### 流式

| 字段 | 类型 | 默认值 | 说明 |
|-------|------|---------|-------------|
| `streaming` | bool | `true` | 启用流式模式。agent 发送 `delta` + `stream_end` 帧而非单条 `message`。 |

### 保活

| 字段 | 类型 | 默认值 | 说明 |
|-------|------|---------|-------------|
| `pingIntervalS` | float | `20.0` | WebSocket ping 间隔，单位秒（5 – 300）。 |
| `pingTimeoutS` | float | `20.0` | 关闭连接前等待 pong 的时间（5 – 300）。 |

### TLS/SSL

| 字段 | 类型 | 默认值 | 说明 |
|-------|------|---------|-------------|
| `sslCertfile` | string | `""` | TLS 证书文件（PEM）路径。必须同时设置 `sslCertfile` 和 `sslKeyfile` 才能启用 WSS。 |
| `sslKeyfile` | string | `""` | TLS 私钥文件（PEM）路径。最低 TLS 版本强制为 TLSv1.2。 |

## 令牌签发

对于 `websocketRequiresToken: true` 的生产部署，请使用短期令牌，而非在客户端中嵌入静态密钥。

### 工作原理

1. 客户端发送 `GET {tokenIssuePath}`，附带 `Authorization: Bearer {tokenIssueSecret}`（或 `X-Nanobot-Auth` 头）。
2. 服务器响应一个一次性令牌：

```json
{"token": "nbwt_aBcDeFg...", "expires_in": 300}
```

3. 客户端以 `?token=nbwt_aBcDeFg...&client_id=...` 打开 WebSocket。
4. 该令牌被消费（单次使用），无法再次使用。

### 示例设置

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "port": 8765,
      "path": "/ws",
      "tokenIssuePath": "/auth/token",
      "tokenIssueSecret": "your-secret-here",
      "tokenTtlS": 300,
      "websocketRequiresToken": true,
      "allowFrom": ["*"],
      "streaming": true
    }
  }
}
```

客户端流程：

```bash
# 1. 获取令牌
curl -H "Authorization: Bearer your-secret-here" http://127.0.0.1:8765/auth/token

# 2. 使用令牌连接
websocat "ws://127.0.0.1:8765/ws?client_id=alice&token=nbwt_aBcDeFg..."
```

### 限制

- 签发令牌为一次性 - 每个令牌只能完成一次握手。
- 未消费令牌上限为 10,000。超出此限制的请求返回 HTTP 429。
- 过期令牌在每次签发或校验请求时惰性清除。

## 多聊天多路复用

单条 WebSocket 可承载多个并发聊天。服务器以 `chat_id -> {connections}` 作为扇出集合跟踪，因此同一个聊天也可在多个连接之间镜像（例如两个浏览器标签页）。

### 典型流程（带侧边栏的 Web UI）

```text
client                                server
  | --- connect -------------------->  |
  | <-- {"event":"ready",              |
  |      "chat_id":"d3..."}   (default)|
  |                                     |
  | --- {"type":"new_chat"} --------->  |
  | <-- {"event":"attached",            |
  |      "chat_id":"a1..."}             |
  |                                     |
  | --- {"type":"message",              |
  |      "chat_id":"a1...",             |
  |      "content":"hi"} ------------>  |
  | <-- {"event":"delta", ...}          |
  | <-- {"event":"stream_end", ...}     |
  |                                     |
  | --- {"type":"attach",               |  # 页面重载后
  |      "chat_id":"a1..."} --------->  |
  | <-- {"event":"attached", ...}       |
```

### 规则

- 每个出站事件都带 `chat_id`。客户端必须按该字段分发。
- `chat_id` 格式：`^[A-Za-z0-9_:-]{1,64}$`。不匹配的值返回 `error`。
- `message` 首次使用时自动附加 - 对于服务器在同一连接上通过 `new_chat` 铸造的聊天，无需单独 `attach`。
- 错误（无效信封、未知 `type`、错误 `chat_id`）为软错误：服务器回复 `{"event":"error","detail":"..."}` 并保持连接打开。

### 向后兼容

仅发送纯文本或 `{"content": ...}` 的遗留客户端保持不变地工作：这些帧路由到连接的默认 `chat_id`（来自 `ready` 的那个）。无需任何配置开关。

### 安全边界

`chat_id` 是一种*能力*：任何持有有效 WebSocket 认证凭据和该 chat_id 的人都能附加到该会话并查看其输出。这对 nanobot 的本地单用户模型是安全的。多租户部署应按用户对 chat_id 做命名空间隔离（或引入按租户的认证门）- nanobot 当前不这样做。

## 安全说明

- **时序安全比较**：静态令牌校验使用 `hmac.compare_digest` 以防范时序攻击。
- **纵深防御**：`allowFrom` 在 HTTP 握手层和消息层都做检查。
- **chat_id 作为能力**：见 [多聊天多路复用](#多聊天多路复用)。WebSocket 握手上的认证是唯一防线；通过它的调用者可附加到他们知道的任何 chat_id。
- **TLS 强制**：启用 SSL 时，TLSv1.2 为允许的最低版本。
- **默认安全**：`websocketRequiresToken` 默认为 `true`。仅在可信网络上显式设为 `false`。

## 媒体文件

出站 `message` 事件可能包含带本地文件系统路径的 `media` 字段。远程客户端无法直接访问这些文件 - 它们需要：

- 共享的文件系统挂载，或
- 一个提供 nanobot 媒体目录服务的 HTTP 文件服务器

## 常见模式

### 可信本地网络（无认证）

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8765,
      "websocketRequiresToken": false,
      "allowFrom": ["*"],
      "streaming": true
    }
  }
}
```

### 静态令牌（简单认证）

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "token": "my-shared-secret",
      "allowFrom": ["alice", "bob"]
    }
  }
}
```

客户端以 `?token=my-shared-secret&client_id=alice` 连接。

### 带签发令牌的公共端点

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8765,
      "path": "/ws",
      "tokenIssuePath": "/auth/token",
      "tokenIssueSecret": "production-secret",
      "websocketRequiresToken": true,
      "sslCertfile": "/etc/ssl/certs/server.pem",
      "sslKeyfile": "/etc/ssl/private/server-key.pem",
      "allowFrom": ["*"]
    }
  }
}
```

### 自定义路径

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "path": "/chat/ws",
      "allowFrom": ["*"]
    }
  }
}
```

客户端连接到 `ws://127.0.0.1:8765/chat/ws?client_id=...`。末尾斜杠会被规范化，因此 `/chat/ws/` 的效果相同。
