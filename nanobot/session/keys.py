"""Shared session key constants and helpers.

会话 key 的共享常量与助手。
"会话 key" 是 agent 识别"同一段连续对话"的唯一标识：同一个 key 共享历史与记忆。
默认按 "渠道:chat_id" 区分会话；开启 unified_session 时所有渠道/聊天共享同一个
会话（即 agent 不区分来源，记忆全局贯通）。
"""

from __future__ import annotations

# 统一会话模式下的固定 key：所有渠道与聊天共用这一个会话。
UNIFIED_SESSION_KEY = "unified:default"


def session_key_for_channel(channel: str, chat_id: str, *, unified_session: bool = False) -> str:
    """Return the session key for a channel/chat pair.

    返回某渠道/聊天对应的会话 key。
    unified_session=True 时返回统一 key（全局共享会话），
    否则返回 "{渠道}:{chat_id}"（按来源隔离会话）。
    """
    if unified_session:
        return UNIFIED_SESSION_KEY
    return f"{channel}:{chat_id}"
