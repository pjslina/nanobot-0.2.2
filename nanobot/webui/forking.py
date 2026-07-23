"""WebUI chat fork orchestration.

WebUI 会话分叉（fork）的编排层。分叉指：从某个已有 WebUI 会话的指定
用户消息之前切出一份副本，生成新的 chat_id 与会话，保留切点之前的全部
历史。本模块负责校验请求、克隆会话与 transcript 状态、把新会话挂接到
连接并向前端注水（hydrate）。``websocket.py`` 仅负责传输，分叉语义集中于此。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from nanobot.session.manager import SessionManager
from nanobot.session.webui_turns import WEBUI_TITLE_METADATA_KEY, clean_generated_title
from nanobot.webui.transcript import (
    append_fork_marker,
    delete_webui_transcript,
    fork_transcript_before_user_index,
    write_session_messages_as_transcript,
)

# WebUI chat_id 合法字符集：字母数字及 _:- ，长度 1~64。用于校验分叉来源 ID。
_WEBUI_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,64}$")


def _valid_webui_chat_id(value: Any) -> bool:
    return isinstance(value, str) and _WEBUI_CHAT_ID_RE.match(value) is not None


def create_webui_chat_fork(
    session_manager: SessionManager,
    *,
    source_chat_id: str,
    before_user_index: int,
    title: str | None = None,
) -> tuple[str, str] | None:
    """Return ``(chat_id, session_key)`` for a new fork, or ``None`` for bad input.

    创建一个 WebUI 会话分叉，返回新会话的 ``(chat_id, session_key)``；
    输入非法（来源或切点无效）时返回 ``None``。流程：生成新 UUID 作为
    chat_id，从源会话切点之前克隆消息得到新会话，再同步克隆 transcript；
    若 transcript 克隆失败则用克隆出的消息重建 transcript。任何异常都会
    触发回滚--删除已创建的部分 transcript 与会话--再向上抛出，保证不会
    留下半成品分叉。
    """
    new_id = str(uuid.uuid4())
    # WebUI 会话的 session_key 固定为 "websocket:<chat_id>" 形式。
    source_key = f"websocket:{source_chat_id}"
    target_key = f"websocket:{new_id}"
    try:
        # 克隆源会话中"第 before_user_index 条 user 消息"之前的所有内容到目标会话。
        forked = session_manager.fork_session_before_user_index(
            source_key,
            target_key,
            before_user_index,
        )
        if forked is None:
            return None

        # 同步分叉 WebUI 的 transcript（与 session 并行存储的独立转写记录）。
        transcript_ok = fork_transcript_before_user_index(
            source_key,
            target_key,
            before_user_index,
        )
        # transcript 分叉失败时退而求其次：用克隆出的会话消息重建一份 transcript。
        if not transcript_ok:
            write_session_messages_as_transcript(target_key, forked.messages)
        # 在新 transcript 末尾追加分叉标记，便于 UI 区分这是分叉出的会话。
        append_fork_marker(target_key)

        fork_title = clean_generated_title(title)
        if fork_title:
            forked.metadata[WEBUI_TITLE_METADATA_KEY] = fork_title
            # 带标题更新时强制 fsync 落盘，确保分叉元数据持久化。
            session_manager.save(forked, fsync=True)
    except Exception:
        # 回滚：删除可能已创建的 transcript 与会话，避免残留半成品，再向上抛出。
        delete_webui_transcript(target_key)
        session_manager.delete_session(target_key)
        raise
    return new_id, target_key


async def handle_webui_fork_chat(channel: Any, connection: Any, envelope: Mapping[str, Any]) -> None:
    """Handle the WebUI ``fork_chat`` websocket command.

    ``websocket.py`` owns the transport. This module owns WebUI fork semantics:
    validate the request, clone session/transcript state, attach the new chat,
    and hydrate the client.
    """
    source_chat_id = envelope.get("source_chat_id")
    raw_index = envelope.get("before_user_index")
    if not _valid_webui_chat_id(source_chat_id):
        await channel._send_event(connection, "error", detail="invalid source_chat_id")
        return
    # 注意先判 bool 再判 int：Python 中 True/False 也是 int 的实例，
    # 若不先排除布尔值，True 会被当成 1 通过校验，导致切点错误。
    if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index < 0:
        await channel._send_event(connection, "error", detail="invalid before_user_index")
        return

    session_manager = channel.gateway.session_manager
    if session_manager is None:
        await channel._send_event(connection, "error", detail="session_manager_unavailable")
        return

    try:
        forked = create_webui_chat_fork(
            session_manager,
            source_chat_id=source_chat_id,
            before_user_index=raw_index,
            title=envelope.get("title") if isinstance(envelope.get("title"), str) else None,
        )
        if forked is None:
            await channel._send_event(connection, "error", detail="invalid fork source or index")
            return
        fork_id, fork_key = forked
    except Exception as exc:
        channel.logger.warning("fork_chat failed: {}", exc)
        await channel._send_event(connection, "error", detail="fork_chat_failed")
        return

    # 计算新会话所属的工作区作用域，并把该连接挂接到新 chat（后续消息归属此会话）。
    scope = channel._workspaces.scope_for_session_key(fork_key)
    channel._attach(connection, fork_id)
    await channel._send_event(connection, "attached", chat_id=fork_id)
    await channel._send_event(
        connection,
        "session_updated",
        chat_id=fork_id,
        scope="metadata",
        workspace_scope=scope.payload(),
    )
    # 注水：把新会话的完整内容（历史消息等）推送给前端，使其立即呈现分叉结果。
    await channel._hydrate_after_subscribe(fork_id)
