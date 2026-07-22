"""Base channel interface for chat platforms.

所有平台渠道（Telegram、Discord、Slack 等）的抽象基类，定义了与
nanobot 消息总线对接的统一生命周期与消息收发协议。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.pairing import (
    PAIRING_CODE_META_KEY,
    format_pairing_reply,
    generate_code,
    is_approved,
)


class BaseChannel(ABC):
    """
    Abstract base class for chat channel implementations.

    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the nanobot message bus.

    渠道抽象基类：定义连接平台、监听消息、发送回复、流式输出与
    权限校验的标准接口。子类只需实现 start/stop/send 等核心方法。
    """

    name: str = "base"
    display_name: str = "Base"
    send_progress: bool = True
    send_tool_hints: bool = False
    show_reasoning: bool = True

    def __init__(self, config: Any, bus: MessageBus):
        """
        Initialize the channel.

        Args:
            config: Channel-specific configuration.
            bus: The message bus for communication.
        """
        self.config = config
        self.logger = logger.bind(channel=self.name)
        self.bus = bus
        self._running = False

    async def transcribe_audio(self, file_path: str | Path) -> str:
        """Transcribe an audio file via Whisper (OpenAI or Groq). Returns empty string on failure."""
        try:
            from nanobot.audio.transcription import (
                resolve_transcription_config,
                transcribe_audio_file,
            )
            from nanobot.config.loader import load_config

            return await transcribe_audio_file(file_path, resolve_transcription_config(load_config()))
        except Exception:
            self.logger.exception("Audio transcription failed")
            return ""

    async def login(self, force: bool = False) -> bool:
        """
        Perform channel-specific interactive login (e.g. QR code scan).

        Args:
            force: If True, ignore existing credentials and force re-authentication.

        Returns True if already authenticated or login succeeds.
        Override in subclasses that support interactive login.

        执行渠道特定的交互式登录（如扫码）。默认返回 True，表示无需登录；
        需要扫码/验证码等交互登录的子类应重写此方法。
        """
        return True

    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.

        This should be a long-running async task that:
        1. Connects to the chat platform
        2. Listens for incoming messages
        3. Forwards messages to the bus via _handle_message()

        渠道启动入口：必须为长期运行的异步任务，连接平台、监听消息，
        并通过 _handle_message() 将消息转发到消息总线。
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources.

        停止渠道并清理资源（连接、会话等）。
        """
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        Send a message through this channel.

        Args:
            msg: The message to send.

        Implementations should raise on delivery failure so the channel manager
        can apply any retry policy in one place.

        发送完整消息。实现方应在投递失败时抛出异常，以便
        ChannelManager 统一执行重试策略。
        """
        pass

    async def send_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None) -> None:
        """Deliver a streaming text chunk.

        Override in subclasses to enable streaming. Implementations should
        raise on delivery failure so the channel manager can retry.

        Streaming contract: ``_stream_delta`` is a chunk, ``_stream_end`` ends
        the current segment, and stateful implementations must key buffers by
        ``_stream_id`` rather than only by ``chat_id``.

        流式文本分块投递。子类重写此方法以启用流式输出。
        流式协议：``_stream_delta`` 表示一个分块，``_stream_end`` 表示
        当前片段结束；有状态的实现应按 ``_stream_id`` 而非仅 ``chat_id`` 维护缓冲区。
        """
        pass

    async def send_reasoning_delta(
        self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Stream a chunk of model reasoning/thinking content.

        Default is no-op. Channels with a native low-emphasis primitive
        (Slack context block, Telegram expandable blockquote, Discord
        subtext, WebUI italic bubble, ...) override to render reasoning
        as a subordinate trace that updates in place as the model thinks.

        Streaming contract mirrors :meth:`send_delta`: ``_reasoning_delta``
        is a chunk, ``_reasoning_end`` ends the current reasoning segment,
        and stateful implementations should key buffers by ``_stream_id``
        rather than only by ``chat_id``.
        """
        return

    async def send_reasoning_end(
        self, chat_id: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Mark the end of a reasoning stream segment.

        Default is no-op. Channels that buffer ``send_reasoning_delta``
        chunks for in-place updates use this signal to flush and freeze
        the rendered group; one-shot channels can ignore it entirely.
        """
        return

    async def send_file_edit_events(
        self,
        chat_id: str,
        edits: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Deliver structured live file-edit events.

        Default is no-op. Channels with a rich activity surface can override
        this to render editing progress without receiving empty text messages.
        """
        return

    async def send_reasoning(self, msg: OutboundMessage) -> None:
        """Deliver a complete reasoning block.

        Default implementation reuses the streaming pair so plugins only
        need to override the delta/end methods. Equivalent to one delta
        with the full content followed immediately by an end marker —
        keeps a single rendering path for both streamed and one-shot
        reasoning (e.g. DeepSeek-R1's final-response ``reasoning_content``).
        """
        if not msg.content:
            return
        meta = dict(msg.metadata or {})
        meta.setdefault("_reasoning_delta", True)
        await self.send_reasoning_delta(msg.chat_id, msg.content, meta)
        end_meta = dict(meta)
        end_meta.pop("_reasoning_delta", None)
        end_meta["_reasoning_end"] = True
        await self.send_reasoning_end(msg.chat_id, end_meta)

    @property
    def supports_streaming(self) -> bool:
        """True when config enables streaming AND this subclass implements send_delta.

        当且仅当配置启用流式输出且子类重写了 send_delta 时返回 True。
        """
        cfg = self.config
        streaming = cfg.get("streaming", False) if isinstance(cfg, dict) else getattr(cfg, "streaming", False)
        return bool(streaming) and type(self).send_delta is not BaseChannel.send_delta

    def is_allowed(self, sender_id: str) -> bool:
        """Check sender permission: star > allowlist > pairing store > deny.

        发送者权限校验，优先级依次为：
        1. allow_from 含 "*" -> 允许所有人；
        2. allow_from 精确匹配 -> 允许；
        3. 配对存储（pairing store）中已批准 -> 允许；
        4. 其余一律拒绝。
        """
        if isinstance(self.config, dict):
            allow_list = self.config.get("allow_from") or self.config.get("allowFrom") or []
        else:
            allow_list = getattr(self.config, "allow_from", None) or []
        if "*" in allow_list:
            return True
        # allowFrom entries are opaque tokens — must match exactly.
        if str(sender_id) in allow_list:
            return True
        if is_approved(self.name, str(sender_id)):
            return True
        return False

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
        is_dm: bool = False,
    ) -> None:
        """Handle an incoming message: check permissions, issue pairing codes in DMs, or forward to bus."""
        if not self.is_allowed(sender_id):
            if is_dm:
                code = generate_code(self.name, str(sender_id))
                await self.send(
                    OutboundMessage(
                        channel=self.name,
                        chat_id=str(chat_id),
                        content=format_pairing_reply(code),
                        metadata={PAIRING_CODE_META_KEY: code},
                    )
                )
                self.logger.info(
                    "Sent pairing code {} to sender {} in chat {}",
                    code, sender_id, chat_id,
                )
            else:
                self.logger.warning(
                    "Access denied for sender {}. "
                    "Add them to allowFrom list in config to grant access.",
                    sender_id,
                )
            return

        meta = metadata or {}
        if self.supports_streaming:
            meta = {**meta, "_wants_stream": True}

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=meta,
            session_key_override=session_key,
        )

        await self.bus.publish_inbound(msg)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """Return default config for onboard. Override in plugins to auto-populate config.json."""
        return {"enabled": False}

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return self._running
