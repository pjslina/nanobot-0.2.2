# 渠道插件指南

只需三步即可构建自定义 nanobot 渠道：子类化、打包、安装。

> **注意：** 我们建议基于 nanobot 的源码检出（`python -m pip install -e .`）而非 PyPI 发布版来开发渠道插件，这样你始终可以使用最新的基础渠道功能和 API。

## 工作原理

nanobot 通过 Python [入口点（entry points）](https://packaging.python.org/en/latest/specifications/entry-points/)发现渠道插件。当 `nanobot gateway` 启动时，它会扫描：

1. `nanobot/channels/` 中的内置渠道
2. 在 `nanobot.channels` 入口点组下注册的外部包

如果匹配的配置段中 `"enabled": true`，该渠道将被实例化并启动。

## 快速开始

我们将构建一个最小化的 webhook 渠道，通过 HTTP POST 接收消息并回送回复。

### 项目结构

```text
nanobot-channel-webhook/
├── nanobot_channel_webhook/
│   ├── __init__.py          # 重新导出 WebhookChannel
│   └── channel.py           # 渠道实现
└── pyproject.toml
```

### 1. 创建你的渠道

```python
# nanobot_channel_webhook/__init__.py
from nanobot_channel_webhook.channel import WebhookChannel

__all__ = ["WebhookChannel"]
```

```python
# nanobot_channel_webhook/channel.py
import asyncio
from typing import Any

from aiohttp import web
from loguru import logger
from pydantic import Field

from nanobot.channels.base import BaseChannel
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Base


class WebhookConfig(Base):
    """Webhook 渠道配置。"""
    enabled: bool = False
    port: int = 9000
    allow_from: list[str] = Field(default_factory=list)


class WebhookChannel(BaseChannel):
    name = "webhook"
    display_name = "Webhook"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WebhookConfig(**config)
        super().__init__(config, bus)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WebhookConfig().model_dump(by_alias=True)

    async def start(self) -> None:
        """启动一个监听传入消息的 HTTP 服务器。

        重要：start() 必须永久阻塞（或直到 stop() 被调用）。
        如果它返回，该渠道被视为已死亡。
        """
        self._running = True
        port = self.config.port

        app = web.Application()
        app.router.add_post("/message", self._on_request)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info("Webhook listening on :{}", port)

        # 阻塞直到停止
        while self._running:
            await asyncio.sleep(1)

        await runner.cleanup()

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """投递一条出站消息。

        msg.content  - markdown 文本（按需转换为平台格式）
        msg.media    - 要附加的本地文件路径列表
        msg.chat_id  - 接收者（即你传给 _handle_message 的同一 chat_id）
        msg.metadata - 可能包含流式分片的 "_progress": True
        """
        logger.info("[webhook] -> {}: {}", msg.chat_id, msg.content[:80])
        # 在真实插件中：POST 到回调 URL、通过 SDK 发送等。

    async def _on_request(self, request: web.Request) -> web.Response:
        """处理传入的 HTTP POST。"""
        body = await request.json()
        sender = body.get("sender", "unknown")
        chat_id = body.get("chat_id", sender)
        text = body.get("text", "")
        media = body.get("media", [])       # URL 列表

        # 这是关键调用：校验 allowFrom，然后将消息放入总线供 agent 处理。
        await self._handle_message(
            sender_id=sender,
            chat_id=chat_id,
            content=text,
            media=media,
        )

        return web.json_response({"ok": True})
```

### 2. 注册入口点

```toml
# pyproject.toml
[project]
name = "nanobot-channel-webhook"
version = "0.1.0"
dependencies = ["nanobot-ai", "aiohttp"]

[project.entry-points."nanobot.channels"]
webhook = "nanobot_channel_webhook:WebhookChannel"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["nanobot_channel_webhook"]
```

键名（`webhook`）成为配置段名。值指向你的 `BaseChannel` 子类。

### 3. 安装与配置

```bash
python -m pip install -e .
nanobot plugins list      # 验证 "Webhook" 显示为 "plugin"
nanobot onboard           # 自动为检测到的插件添加默认配置
```

编辑 `~/.nanobot/config.json`：

```json
{
  "channels": {
    "webhook": {
      "enabled": true,
      "port": 9000,
      "allowFrom": ["*"]
    }
  }
}
```

### 4. 运行与测试

```bash
nanobot gateway
```

在另一个终端中：

```bash
curl -X POST http://localhost:9000/message \
  -H "Content-Type: application/json" \
  -d '{"sender": "user1", "chat_id": "user1", "text": "Hello!"}'
```

agent 接收消息并处理它。回复会到达你的 `send()` 方法。

## BaseChannel API

### 必需（抽象）

| 方法 | 说明 |
|--------|-------------|
| `async start()` | **必须永久阻塞。** 连接到平台，监听消息，对每条消息调用 `_handle_message()`。如果该方法返回，则渠道已死亡。 |
| `async stop()` | 设置 `self._running = False` 并清理资源。在网关关闭时被调用。 |
| `async send(msg: OutboundMessage)` | 将一条出站消息投递到平台。 |

### 交互式登录

如果你的渠道需要交互式认证（例如二维码扫描），请重写 `login(force=False)`：

```python
async def login(self, force: bool = False) -> bool:
    """
    执行渠道特定的交互式登录。

    Args:
        force: 若为 True，忽略现有凭据并重新认证。

    若已认证或登录成功则返回 True。
    """
    # 对于基于二维码的登录：
    # 1. 若 force，清除已保存的凭据
    # 2. 检查是否已认证（从磁盘/状态加载）
    # 3. 若未认证，展示二维码并轮询确认
    # 4. 成功后保存 token
```

不需要交互式登录的渠道（例如带 bot token 的 Telegram、带 bot token 的 Discord）会继承默认的 `login()`，该方法直接返回 `True`。

用户通过以下命令触发交互式登录：
```bash
nanobot channels login <channel_name>
nanobot channels login <channel_name> --force  # 重新认证
```

### 基类提供

| 方法 / 属性 | 说明 |
|-------------------|-------------|
| `_handle_message(sender_id, chat_id, content, media?, metadata?, session_key?)` | **当你收到消息时调用此方法。** 检查 `is_allowed()`，然后发布到总线。若 `supports_streaming` 为真，则自动设置 `_wants_stream`。 |
| `is_allowed(sender_id)` | 对照 `config.allow_from` 检查；`"*"` 允许全部，`[]` 拒绝全部。 |
| `default_config()`（类方法） | 为 `nanobot onboard` 返回默认配置字典。重写以声明你的字段。 |
| `transcribe_audio(file_path)` | 通过共享的顶层 `transcription` 配置转录音频（若已配置）。 |
| `supports_streaming`（属性） | 当配置含 `"streaming": true` **且**子类重写了 `send_delta()` 时为 `True`。 |
| `is_running` | 返回 `self._running`。 |
| `login(force=False)` | 执行交互式登录（例如二维码扫描）。若已认证或登录成功则返回 `True`。在支持交互式登录的子类中重写。 |
| `send_reasoning_delta(chat_id, delta, metadata?)` | 用于流式模型推理/思考内容的可选钩子。默认为空操作。 |
| `send_reasoning_end(chat_id, metadata?)` | 标记一个推理块结束的可选钩子。默认为空操作。 |
| `send_reasoning(msg)` | 可选的一次性推理回退方案。默认实现转换为 `send_reasoning_delta()` + `send_reasoning_end()`。 |

### 可选（流式）

| 方法 | 说明 |
|--------|-------------|
| `async send_delta(chat_id, delta, metadata?)` | 重写以接收流式分片。详见[流式支持](#流式支持)。 |

### 消息类型

```python
@dataclass
class OutboundMessage:
    channel: str        # 你的渠道名
    chat_id: str        # 接收者（即你传给 _handle_message 的同一值）
    content: str        # markdown 文本 - 按需转换为平台格式
    media: list[str]    # 要附加的本地文件路径（图片、音频、文档）
    metadata: dict      # 可能包含：流式分片的 "_progress"（bool），
                        #              用于回复串接的 "message_id"
```

## 流式支持

渠道可以选择开启实时流式传输——agent 逐 token 发送内容，而不是一次性发送最终消息。这完全可选；不开启流式的渠道也能正常工作。

### 工作原理

当**同时**满足两个条件时，agent 会通过你的渠道流式传输内容：

1. 配置含 `"streaming": true`
2. 你的子类重写了 `send_delta()`

若任一缺失，agent 回退到常规的一次性 `send()` 路径。

### 实现 `send_delta`

重写 `send_delta` 以处理两类调用：

```python
async def send_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None) -> None:
    meta = metadata or {}

    if meta.get("_stream_end"):
        # 流式结束 - 执行最终格式化、清理等
        return

    # 常规 delta - 追加文本，更新屏幕上的消息
    # delta 包含一小段文本（几个 token）
```

**Metadata 标志：**

| 标志 | 含义 |
|------|---------|
| `_stream_delta: True` | 一个内容分片（delta 包含新文本） |
| `_stream_end: True` | 流式结束（delta 为空） |

### 示例：带流式的 Webhook

```python
class WebhookChannel(BaseChannel):
    name = "webhook"
    display_name = "Webhook"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WebhookConfig(**config)
        super().__init__(config, bus)
        self._buffers: dict[str, str] = {}

    async def send_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None) -> None:
        meta = metadata or {}
        if meta.get("_stream_end"):
            text = self._buffers.pop(chat_id, "")
            # 最终投递 - 格式化并发送完整消息
            await self._deliver(chat_id, text, final=True)
            return

        self._buffers.setdefault(chat_id, "")
        self._buffers[chat_id] += delta
        # 增量更新 - 将部分文本推送到客户端
        await self._deliver(chat_id, self._buffers[chat_id], final=False)

    async def send(self, msg: OutboundMessage) -> None:
        # 非流式路径 - 保持不变
        await self._deliver(msg.chat_id, msg.content, final=True)
```

### 配置

按渠道启用流式：

```json
{
  "channels": {
    "webhook": {
      "enabled": true,
      "streaming": true,
      "allowFrom": ["*"]
    }
  }
}
```

当 `streaming` 为 `false`（默认）或被省略时，只会调用 `send()`——没有流式开销。

### BaseChannel 流式 API

| 方法 / 属性 | 说明 |
|-------------------|-------------|
| `async send_delta(chat_id, delta, metadata?)` | 重写以处理流式分片。默认为空操作。 |
| `supports_streaming`（属性） | 当配置含 `streaming: true` **且**子类重写了 `send_delta` 时返回 `True`。 |

## 进度、工具提示与推理

除普通助手文本外，nanobot 还可以发出低强调的 trace 块。这些块用于 UI 中的状态行、可折叠的"已使用工具"分组，或推理/思考块等展示。没有合适展示位置的平台可以安全地忽略它们。

### 进度与工具提示

进度与工具提示通过常规的 `send(msg)` 路径到达。在渲染前检查 `msg.metadata`：

```python
async def send(self, msg: OutboundMessage) -> None:
    meta = msg.metadata or {}

    if meta.get("_tool_hint"):
        # 一条简短的工具面包屑，例如 read_file("config.json")
        await self._send_trace(msg.chat_id, msg.content, kind="tool")
        return

    if meta.get("_progress"):
        # 通用的非最终状态，例如 "Thinking..." 或 "Running command..."
        await self._send_trace(msg.chat_id, msg.content, kind="progress")
        return

    await self._send_message(msg.chat_id, msg.content, media=msg.media)
```

大多数渠道默认关闭工具提示。用户可以全局或按渠道启用：

```json
{
  "channels": {
    "sendToolHints": true,
    "webhook": {
      "enabled": true,
      "sendToolHints": true
    }
  }
}
```

### 推理块

推理通过专用的可选钩子投递，而非 `send()`。如果你的平台能以低视觉强调/可折叠块的形式展示模型推理，请重写 `send_reasoning_delta()` 和 `send_reasoning_end()`。默认实现为空操作，因此不支持的渠道会直接丢弃推理内容。

```python
class WebhookChannel(BaseChannel):
    name = "webhook"
    display_name = "Webhook"

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WebhookConfig(**config)
        super().__init__(config, bus)
        self._reasoning_buffers: dict[str, str] = {}

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        meta = metadata or {}
        stream_id = str(meta.get("_stream_id") or chat_id)
        self._reasoning_buffers[stream_id] = self._reasoning_buffers.get(stream_id, "") + delta
        await self._update_reasoning_block(chat_id, self._reasoning_buffers[stream_id], final=False)

    async def send_reasoning_end(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        meta = metadata or {}
        stream_id = str(meta.get("_stream_id") or chat_id)
        text = self._reasoning_buffers.pop(stream_id, "")
        if text:
            await self._update_reasoning_block(chat_id, text, final=True)
```

**推理 metadata 标志：**

| 标志 | 含义 |
|------|---------|
| `_reasoning_delta: True` | 一个推理/思考分片；`delta` 包含新文本。 |
| `_reasoning_end: True` | 当前推理块已完成；`delta` 为空。 |
| `_reasoning: True` | 旧式一次性推理。`BaseChannel.send_reasoning()` 会将其转换为 delta + end。 |
| `_stream_id` | 此助手轮次/片段的稳定 id。用它作为缓冲区的键，而不仅使用 `chat_id`。 |

推理可见性由全局或按渠道的 `showReasoning` 控制：

```json
{
  "channels": {
    "showReasoning": true,
    "webhook": {
      "enabled": true,
      "showReasoning": true
    }
  }
}
```

推荐的渲染方式：

- 将工具提示和进度渲染为 trace/状态 UI，而非普通助手回复。
- 以较低的视觉强调渲染推理，并在完成后折叠（当平台支持时）。
- 保持推理与最终答案文本分离。最终答案仍通过 `send()` 或 `send_delta()` 到达。

## 配置

### 为什么需要 Pydantic 模型

`BaseChannel.is_allowed()` 通过 `getattr(self.config, "allow_from", [])` 读取权限列表。这对 `allow_from` 是真实 Python 属性的 Pydantic 模型有效，但**对普通 `dict` 会静默失败**——`dict` 没有 `allow_from` 属性，因此 `getattr` 总是返回默认值 `[]`，导致所有消息被拒绝。

内置渠道使用 Pydantic 配置模型（继承自 `nanobot.config.schema` 中的 `Base`）。插件渠道**必须同样如此**。

### 模式

1. 定义一个继承自 `nanobot.config.schema.Base` 的 Pydantic 模型：

```python
from pydantic import Field
from nanobot.config.schema import Base

class WebhookConfig(Base):
    """Webhook 渠道配置。"""
    enabled: bool = False
    port: int = 9000
    allow_from: list[str] = Field(default_factory=list)
```

`Base` 配置了 `alias_generator=to_camel` 和 `populate_by_name=True`，因此像 `"allowFrom"` 和 `"allow_from"` 这样的 JSON 键均可被接受。

2. 在 `__init__` 中将 `dict` 转换为模型：

```python
from typing import Any
from nanobot.bus.queue import MessageBus

class WebhookChannel(BaseChannel):
    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = WebhookConfig(**config)
        super().__init__(config, bus)
```

3. 以属性方式访问配置（而非 `.get()`）：

```python
async def start(self) -> None:
    port = self.config.port
    token = self.config.token
```

`allowFrom` 由 `_handle_message()` 自动处理——你无需自行检查。

重写 `default_config()` 以便 `nanobot onboard` 自动填充 `config.json`：

```python
@classmethod
def default_config(cls) -> dict[str, Any]:
    return WebhookConfig().model_dump(by_alias=True)
```

> **注意：** `default_config()` 返回普通 `dict`（而非 Pydantic 模型），因为它用于序列化到 `config.json`。推荐方式是实例化你的配置模型并调用 `model_dump(by_alias=True)`——这会自动使用 camelCase 键（`allowFrom`），并将默认值保持在单一真相来源中。

若不重写，基类返回 `{"enabled": false}`。

## 命名约定

| 对象 | 格式 | 示例 |
|------|--------|---------|
| PyPI 包 | `nanobot-channel-{name}` | `nanobot-channel-webhook` |
| 入口点键 | `{name}` | `webhook` |
| 配置段 | `channels.{name}` | `channels.webhook` |
| Python 包 | `nanobot_channel_{name}` | `nanobot_channel_webhook` |

## 本地开发

```bash
git clone https://github.com/you/nanobot-channel-webhook
cd nanobot-channel-webhook
python -m pip install -e .
nanobot plugins list    # 应显示 "Webhook" 为 "plugin"
nanobot gateway         # 端到端测试
```

## 验证

```bash
$ nanobot plugins list

  Name       Source    Enabled
  telegram   builtin  yes
  discord    builtin  no
  webhook    plugin   yes
```
