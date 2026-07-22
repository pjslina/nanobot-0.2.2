"""Internal turn continuation helpers.

"内部 turn 续接"助手：当一次 turn 因为工具调用次数到达上限（max_iterations）而
被迫中断、但持续目标（sustained goal）尚未完成时，把下一个续接 turn 直接排入
待处理队列，对用户不可见地继续推进，直到目标完成或续接轮数耗尽。

这个模块把"预算边界续接策略"从 AgentLoop 中剥离出来：loop 只调用几个助手函数，
由这些助手决定是否允许内部续接，并在允许时把下一个 turn 入队。

This module keeps budget-boundary continuation policy out of ``AgentLoop``.
The loop calls a small set of helpers; those helpers decide whether an internal
continuation is allowed and, when it is, queue the next turn directly.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Mapping, MutableMapping

from loguru import logger

from nanobot.session.goal_state import (
    goal_state_runtime_lines,
    sustained_goal_active,
    sustained_goal_turn,
)

# 以下是一组元数据键，标记"内部续接"相关的状态，贯穿 inbound 消息与会话元数据。
INTERNAL_CONTINUATION_META = "_internal_continuation"  # 标记本消息由内部续接策略产生
INTERNAL_CONTINUATION_KIND_META = "_internal_continuation_kind"  # 续接类型（如 sustained_goal）
INTERNAL_CONTINUATION_PENDING_META = "_internal_continuation_pending"  # 本 turn 已安排续接切片
INTERNAL_CONTINUATION_RUN_STARTED_AT_META = "_internal_continuation_run_started_at"  # 跨续接切片传递的"用户可见运行开始时间"
SKIP_USER_PERSIST_META = "_skip_user_persist"  # 跳过把本消息作为用户输入持久化

_GOAL_CONTINUATION_KIND = "sustained_goal"
_GOAL_CONTINUATION_SENDER = "system:continuation"  # 续接消息的伪发送者
_GOAL_CONTINUATION_ROUNDS_KEY = "_sustained_goal_continuation_rounds"  # 本会话已续接轮数
_MAX_GOAL_CONTINUATION_ROUNDS = 12  # 单个持续目标最多续接 12 轮，防止无限循环
# 生成续接消息时剥离的入站元数据键（流式标记、续接待处理标记等，不应带入下一轮）。
_STRIPPED_INBOUND_META_KEYS = {
    "_stream_id",
    "_stream_delta",
    "_stream_end",
    "_resuming",
    INTERNAL_CONTINUATION_PENDING_META,
}


def internal_continuation_inbound(metadata: Mapping[str, Any] | None) -> bool:
    """True for an inbound message created by an internal continuation policy."""
    return bool(metadata and metadata.get(INTERNAL_CONTINUATION_META) is True)


def internal_continuation_pending(metadata: Mapping[str, Any] | None) -> bool:
    """True when the current turn scheduled an invisible continuation slice."""
    return bool(metadata and metadata.get(INTERNAL_CONTINUATION_PENDING_META) is True)


def internal_continuation_run_started_at(metadata: Mapping[str, Any] | None) -> float | None:
    """Return the user-visible run start propagated across continuation slices."""
    if not metadata:
        return None
    value = metadata.get(INTERNAL_CONTINUATION_RUN_STARTED_AT_META)
    if not isinstance(value, int | float):
        return None
    started_at = float(value)
    return started_at if started_at > 0 else None


def should_persist_user_message(metadata: Mapping[str, Any] | None) -> bool:
    """Return whether this inbound message should be persisted as user input."""
    if metadata and metadata.get(SKIP_USER_PERSIST_META) is True:
        return False
    return not internal_continuation_inbound(metadata)


def should_stream_budget_response(
    *,
    stop_reason: str,
    pending_queue_available: bool,
    session_metadata: Mapping[str, Any] | None,
    message_metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether the budget-boundary response should be sent to the user."""
    if stop_reason != "max_iterations":
        return True
    return should_finalize_on_max_iterations(
        pending_queue_available=pending_queue_available,
        session_metadata=session_metadata,
        message_metadata=message_metadata,
    )


def should_finalize_on_max_iterations(
    *,
    pending_queue_available: bool,
    session_metadata: Mapping[str, Any] | None,
    message_metadata: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether a max-iteration boundary should produce a final response.

    When a sustained goal can continue internally, the current runner slice
    should stop without spending an extra no-tools finalization call. The next
    queued continuation slice owns the eventual user-visible response.
    """
    return not (
        pending_queue_available
        and _goal_continuation_available(
            session_metadata,
            message_metadata=message_metadata,
        )
    )


async def maybe_continue_turn(ctx: Any) -> bool:
    """Queue an internal continuation for *ctx* when policy allows it.

    当策略允许时，为当前 ctx 安排一个"内部续接"turn（排入待处理队列）。
    返回 True 表示已安排续接。续接消息对用户不可见（suppress_response=True），
    由 system:continuation 发出，带续接提示词，让 agent 在新的预算下继续推进目标。
    """
    if ctx.session is None or ctx.pending_queue is None:
        return False
    if not _continuation_available(
        stop_reason=ctx.stop_reason,
        pending_queue_available=True,
        session_metadata=ctx.session.metadata,
        message_metadata=ctx.msg.metadata,
    ):
        return False

    metadata = _internal_continuation_metadata(
        ctx.msg.metadata,
        run_started_at=getattr(ctx, "visible_run_started_at", None),
    )
    content = _goal_continuation_prompt(ctx.session.metadata)
    messages = _strip_terminal_assistant(ctx.all_messages, ctx.final_content)
    _increment_goal_continuation_round(ctx.session.metadata)

    logger.info("Turn budget reached; scheduling internal continuation")
    # 标记本 turn 已安排续接、抑制本 turn 的用户响应，并把续接消息入队。
    ctx.msg.metadata[INTERNAL_CONTINUATION_PENDING_META] = True
    ctx.final_content = ""
    ctx.all_messages = messages
    ctx.suppress_response = True
    await ctx.pending_queue.put(
        dataclasses.replace(
            ctx.msg,
            sender_id=_GOAL_CONTINUATION_SENDER,
            content=content,
            media=[],
            metadata=metadata,
            session_key_override=ctx.session_key,
        )
    )
    return True


def prepare_save_boundary(ctx: Any) -> None:
    """Prepare continuation bookkeeping and the history append boundary."""
    if ctx.session is not None:
        clear_internal_continuation_state(ctx.session.metadata)

    ctx.save_skip = _save_skip_for_turn(
        message_metadata=ctx.msg.metadata,
        initial_message_count=len(ctx.initial_messages),
        history_count=len(ctx.history),
        user_persisted_early=ctx.user_persisted_early,
    )


def _continuation_available(
    *,
    stop_reason: str,
    pending_queue_available: bool,
    session_metadata: Mapping[str, Any] | None,
    message_metadata: Mapping[str, Any] | None = None,
) -> bool:
    if stop_reason != "max_iterations" or not pending_queue_available:
        return False
    return _goal_continuation_available(
        session_metadata,
        message_metadata=message_metadata,
    )


def clear_internal_continuation_state(metadata: MutableMapping[str, Any]) -> None:
    """Reset policy bookkeeping once its owning runtime mode is inactive."""
    if not sustained_goal_active(metadata):
        metadata.pop(_GOAL_CONTINUATION_ROUNDS_KEY, None)


def _save_skip_for_turn(
    *,
    message_metadata: Mapping[str, Any] | None,
    initial_message_count: int,
    history_count: int,
    user_persisted_early: bool,
) -> int:
    """Return the persisted-message append boundary for this turn.

    返回本 turn 持久化消息时的"追加边界"（即历史中已有多少条，新消息从该位置追加）。
    用于 SAVE 阶段决定从哪条消息开始写入会话历史。
    """
    if message_metadata and message_metadata.get(SKIP_USER_PERSIST_META) is True:
        return initial_message_count
    if internal_continuation_inbound(message_metadata):
        return initial_message_count
    # build_messages 可能把当前消息合并到同角色的历史尾部。
    # 无论哪种形态，runner 追加的消息都从 initial_message_count 开始。
    has_standalone_current = initial_message_count > 1 + history_count
    if has_standalone_current and not user_persisted_early:
        return initial_message_count - 1
    return initial_message_count


def _goal_continuation_available(
    session_metadata: Mapping[str, Any] | None,
    *,
    message_metadata: Mapping[str, Any] | None = None,
    max_rounds: int = _MAX_GOAL_CONTINUATION_ROUNDS,
) -> bool:
    # 是否可续接：本 turn 属于持续目标 + 会话有 active 目标 + 续接轮数未超上限。
    if not sustained_goal_turn(session_metadata, message_metadata=message_metadata):
        return False
    if not sustained_goal_active(session_metadata):
        return False
    try:
        rounds = int((session_metadata or {}).get(_GOAL_CONTINUATION_ROUNDS_KEY) or 0)
    except (TypeError, ValueError):
        rounds = 0
    return rounds < max(0, max_rounds)


def _increment_goal_continuation_round(session_metadata: MutableMapping[str, Any]) -> None:
    try:
        rounds = int(session_metadata.get(_GOAL_CONTINUATION_ROUNDS_KEY) or 0)
    except (TypeError, ValueError):
        rounds = 0
    session_metadata[_GOAL_CONTINUATION_ROUNDS_KEY] = rounds + 1


def _internal_continuation_metadata(
    message_metadata: Mapping[str, Any] | None,
    *,
    run_started_at: float | None = None,
) -> dict[str, Any]:
    metadata = dict(message_metadata or {})
    metadata[INTERNAL_CONTINUATION_META] = True
    metadata[INTERNAL_CONTINUATION_KIND_META] = _GOAL_CONTINUATION_KIND
    if run_started_at is not None:
        metadata[INTERNAL_CONTINUATION_RUN_STARTED_AT_META] = float(run_started_at)
    for key in _STRIPPED_INBOUND_META_KEYS:
        metadata.pop(key, None)
    return metadata


def _goal_continuation_prompt(metadata: Mapping[str, Any] | None) -> str:
    lines = goal_state_runtime_lines(metadata)
    if lines:
        goal = "\n".join(lines)
        return (
            "Continue the active sustained goal after the previous turn reached "
            "its tool-call budget.\n\n"
            f"{goal}\n\n"
            "Continue from the saved context. Do not mention the continuation "
            "boundary to the user. Use tools as needed, and call complete_goal "
            "when the objective is truly finished."
        )
    return (
        "Continue the active sustained goal after the previous turn reached "
        "its tool-call budget. Continue from the saved context. Do not mention "
        "the continuation boundary to the user. Use tools as needed, and call "
        "complete_goal when the objective is truly finished."
    )


def _strip_terminal_assistant(
    messages: list[dict[str, Any]],
    final_content: str | None,
) -> list[dict[str, Any]]:
    """Drop the synthetic max-iteration assistant message before saving history."""
    if not messages:
        return messages
    last = messages[-1]
    if last.get("role") != "assistant":
        return messages
    if final_content is None or last.get("content") != final_content:
        return messages
    if last.get("tool_calls"):
        return messages
    return messages[:-1]
