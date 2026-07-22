"""Session metadata helpers for sustained goals (e.g. ``long_task`` / ``complete_goal``).

"持续目标"（sustained goal）的会话元数据助手。
持续目标让 agent 能够跨多个 turn 持续推进一个长期任务（如 ``long_task``/
``complete_goal`` 工具）：目标状态写在会话元数据 ``metadata[GOAL_STATE_KEY]`` 中，
agent 每轮都会读到它，从而"记住"自己正在完成什么。

工具把状态写入 ``metadata[GOAL_STATE_KEY]``。读取时兼容旧版本的会话 key
``thread_goal``。调用方通过 ``goal_state_runtime_lines``、``goal_state_ws_blob``、
``runner_wall_llm_timeout_s`` 使用，无需 import 具体工具实现。

Tools set ``metadata[GOAL_STATE_KEY]``. Reads accept the legacy session key ``thread_goal``
for older sessions. Callers use ``goal_state_runtime_lines``, ``goal_state_ws_blob``, and
``runner_wall_llm_timeout_s`` without importing tool implementations.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, MutableMapping

from nanobot.session.manager import SessionManager

GOAL_STATE_KEY = "goal_state"
# Older builds stored the same JSON blob under this key.
# 旧版本把同一份 JSON 存在这个 key 下，读取时需兼容。
_LEGACY_GOAL_STATE_SESSION_KEY = "thread_goal"
_MAX_OBJECTIVE_IN_RUNTIME = 4000  # 注入运行时上下文的目标文本最大长度
_MAX_OBJECTIVE_WS = 600  # 推送给 WebUI 的目标文本最大长度（更短，避免前端过载）


def _session_goal_raw(metadata: Mapping[str, Any] | None) -> Any:
    # 读取原始目标 blob：优先新 key，回退到旧 key（兼容老会话）。
    if not metadata:
        return None
    if GOAL_STATE_KEY in metadata:
        return metadata.get(GOAL_STATE_KEY)
    return metadata.get(_LEGACY_GOAL_STATE_SESSION_KEY)


def discard_legacy_goal_state_key(metadata: MutableMapping[str, Any]) -> None:
    """Remove legacy metadata key after migrating writes to :data:`GOAL_STATE_KEY`.

    把目标状态迁移到新 key 后，删除旧 key（清理老会话遗留数据）。
    """
    metadata.pop(_LEGACY_GOAL_STATE_SESSION_KEY, None)


def goal_state_raw(metadata: Mapping[str, Any] | None) -> Any:
    """Return the session goal blob under :data:`GOAL_STATE_KEY` or the legacy key. 返回目标状态原始 blob。"""
    return _session_goal_raw(metadata)


def sustained_goal_active(metadata: Mapping[str, Any] | None) -> bool:
    """True when this session has an active sustained objective (``long_task`` bookkeeping).

    当本会话有一个"进行中"的持续目标时返回 True（status == "active"）。
    """
    goal = parse_goal_state(goal_state_raw(metadata))
    return isinstance(goal, dict) and goal.get("status") == "active"


def sustained_goal_turn(
    metadata: Mapping[str, Any] | None,
    *,
    message_metadata: Mapping[str, Any] | None = None,
) -> bool:
    """True when this turn should use sustained-goal runtime limits.

    判断"本 turn 是否应启用持续目标的运行时限额"：
    会话已有 active 目标，或本 turn 由 /goal 命令触发时为 True。
    """
    if sustained_goal_active(metadata):
        return True
    if not message_metadata:
        return False
    return str(message_metadata.get("original_command") or "").strip() == "/goal"


def parse_goal_state(blob: Any) -> dict[str, Any] | None:
    # 把目标 blob 解析成 dict：接受 dict 或 JSON 字符串，非法则返回 None。
    if blob is None:
        return None
    if isinstance(blob, dict):
        return blob
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def goal_state_runtime_lines(metadata: Mapping[str, Any] | None) -> list[str]:
    """Lines appended inside the Runtime Context block when a goal is active.

    当目标处于 active 时，拼接到"运行时上下文"块里的文本行，
    让 LLM 在每轮都能看到当前正在推进的目标及其摘要。
    """
    if not metadata:
        return []
    goal = parse_goal_state(_session_goal_raw(metadata))
    if not isinstance(goal, dict) or goal.get("status") != "active":
        return []
    objective = str(goal.get("objective") or "").strip()
    if not objective:
        return ["Goal: active (no objective text stored)."]
    if len(objective) > _MAX_OBJECTIVE_IN_RUNTIME:
        objective = objective[:_MAX_OBJECTIVE_IN_RUNTIME].rstrip() + "\n… (truncated)"
    out = ["Goal (active):", objective]
    hint = str(goal.get("ui_summary") or "").strip()
    if hint:
        out.append(f"Summary: {hint}")
    return out


def goal_state_ws_blob(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """JSON-safe snapshot for WebSocket ``goal_state`` events (one chat_id per frame).

    生成可安全序列化的目标快照，通过 WebSocket ``goal_state`` 事件推给 WebUI
    （每帧一个 chat_id）。目标文本按更短的阈值截断以适应前端展示。
    """
    goal = parse_goal_state(_session_goal_raw(metadata)) if metadata else None
    if isinstance(goal, dict) and goal.get("status") == "active":
        objective = str(goal.get("objective") or "").strip()
        if len(objective) > _MAX_OBJECTIVE_WS:
            objective = objective[:_MAX_OBJECTIVE_WS].rstrip() + "…"
        summary = str(goal.get("ui_summary") or "").strip()[:120]
        blob: dict[str, Any] = {"active": True}
        if summary:
            blob["ui_summary"] = summary
        if objective:
            blob["objective"] = objective
        return blob
    return {"active": False}


def runner_wall_llm_timeout_s(
    sessions: SessionManager,
    session_key: str | None,
    *,
    metadata: Mapping[str, Any] | None = None,
    message_metadata: Mapping[str, Any] | None = None,
) -> float | None:
    """Wall-clock cap for :class:`~nanobot.agent.runner.AgentRunner` when streaming an LLM.

    返回 AgentRunner 流式调用 LLM 时的"挂钟超时"上限：
    - 持续目标 turn 返回 ``0.0``：表示禁用 ``asyncio.wait_for`` 包裹（长任务不设超时）；
    - 否则返回 ``None``：表示使用环境变量 ``NANOBOT_LLM_TIMEOUT_S`` 配置的超时。
    当调用方已持有本 turn 的会话元数据时，可直接传入 in-memory ``metadata`` 以免重复读盘。

    Returns ``0.0`` to disable ``asyncio.wait_for`` around the request when this is a
    sustained-goal turn; ``None`` means use ``NANOBOT_LLM_TIMEOUT_S``. Pass in-memory
    ``metadata`` when the caller already holds :attr:`~nanobot.session.manager.Session.metadata`
    for this turn.
    """
    meta: Mapping[str, Any] | None = metadata
    if meta is None and session_key:
        meta = sessions.get_or_create(session_key).metadata
    return 0.0 if sustained_goal_turn(meta, message_metadata=message_metadata) else None
