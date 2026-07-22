"""Progress callback helpers for user-visible output.

进度回调助手：把 agent 处理过程中的进度（工具提示、推理过程、文件编辑事件等）
转换成 OutboundMessage 发回渠道，让用户看到"agent 正在做什么"。
运行时状态通知（turn 生命周期、模型切换等）见 ``nanobot.bus.runtime_events``。

These helpers convert agent progress callbacks into outbound chat messages.
Runtime state notifications such as turn lifecycle and model changes live in
``nanobot.bus.runtime_events``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus


def build_bus_progress_callback(
    bus: MessageBus,
    msg: InboundMessage,
) -> Callable[..., Awaitable[None]]:
    """Return a callback that publishes progress as outbound messages.

    构造一个进度回调：agent 在处理过程中调用它时，把进度内容包装成
    OutboundMessage（带 _progress 等元数据标记）发布到消息总线，
    最终由渠道发送给用户。
    """

    async def _publish_progress(
        content: str,
        *,
        tool_hint: bool = False,
        tool_events: list[dict[str, Any]] | None = None,
        file_edit_events: list[dict[str, Any]] | None = None,
        reasoning: bool = False,
        reasoning_end: bool = False,
    ) -> None:
        # 复制原始入站消息的元数据，并打上各类进度标记，
        # 渠道/WebUI 据此决定如何渲染（如流式推理、工具卡片、文件编辑提示）。
        meta = dict(msg.metadata or {})
        meta["_progress"] = True
        meta["_tool_hint"] = tool_hint
        if reasoning:
            meta["_reasoning_delta"] = True  # 推理过程增量
        if reasoning_end:
            meta["_reasoning_end"] = True  # 推理过程结束
        if tool_events:
            meta["_tool_events"] = tool_events  # 工具调用事件
        if file_edit_events:
            meta["_file_edit_events"] = file_edit_events  # 文件编辑事件
        await bus.publish_outbound(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
                metadata=meta,
            )
        )

    async def _bus_progress(
        content: str,
        *,
        tool_hint: bool = False,
        tool_events: list[dict[str, Any]] | None = None,
        file_edit_events: list[dict[str, Any]] | None = None,
        reasoning: bool = False,
        reasoning_end: bool = False,
    ) -> None:
        await _publish_progress(
            content,
            tool_hint=tool_hint,
            tool_events=tool_events,
            file_edit_events=file_edit_events,
            reasoning=reasoning,
            reasoning_end=reasoning_end,
        )

    return _bus_progress
