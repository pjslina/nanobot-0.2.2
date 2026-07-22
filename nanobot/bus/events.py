"""Event types for the message bus.

消息总线的事件类型定义。这是整个 nanobot 数据流的"数据载体"：
渠道（Channels）产生 InboundMessage 喂给 agent，agent 处理后产生
OutboundMessage 回传给渠道。两者都通过 MessageBus 异步传递。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Optional ``OutboundMessage.metadata`` key for structured, channel-agnostic UI
# payloads. Value is JSON-serializable with at least ``kind``; rich clients may
# render it and other channels may ignore unknown keys.
# 可选的 OutboundMessage.metadata 键：用于携带与渠道无关的结构化 UI 载荷。
# 富客户端（如 WebUI）可据此渲染特殊 UI，其他渠道可忽略未知键。
OUTBOUND_META_AGENT_UI = "_agent_ui"

# Internal-only inbound metadata used by in-process channels to ask the agent
# loop to update runtime state without going through a user session.
# 仅内部使用的入站元数据：进程内渠道用它请求 agent loop 更新运行时状态，
# 而无需走一次完整的用户会话 turn。
INBOUND_META_RUNTIME_CONTROL = "_runtime_control"
RUNTIME_CONTROL_ACK = "_ack"
RUNTIME_CONTROL_MCP_RELOAD = "mcp_reload"


@dataclass
class InboundMessage:
    """Message received from a chat channel.

    从聊天渠道收到的消息。由 Channels 构造并发布到 MessageBus.inbound，
    AgentLoop 消费它来驱动一次 turn。
    """

    channel: str  # telegram, discord, slack, whatsapp  # 渠道名（telegram/discord/slack/whatsapp 等）
    sender_id: str  # User identifier  # 发送者标识
    chat_id: str  # Chat/channel identifier  # 聊天/群组标识，用于路由回复
    content: str  # Message text  # 消息文本
    timestamp: datetime = field(default_factory=datetime.now)
    media: list[str] = field(default_factory=list)  # Media URLs  # 媒体（图片/音频等）URL 列表
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data  # 渠道特定数据
    session_key_override: str | None = None  # Optional override for thread-scoped sessions  # 可选：覆盖默认会话 key（用于线程级会话隔离）

    @property
    def session_key(self) -> str:
        """Unique key for session identification.

        会话唯一标识。默认按 "渠道:chat_id" 区分会话；
        若设置了 session_key_override 则用它（实现线程级/话题级会话隔离）。
        """
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """Message to send to a chat channel.

    ``metadata`` can carry routing (``message_id``, …), trace flags (``_progress``),
    and optional ``OUTBOUND_META_AGENT_UI`` blobs for rich clients; non-WebUI
    channels may ignore unknown keys.
    """

    channel: str
    chat_id: str
    content: str
    reply_to: str | None = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    buttons: list[list[str]] = field(default_factory=list)
