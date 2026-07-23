"""Persisted WebUI project workspace state.

持久化 WebUI 项目工作区的访问范围状态。管理两件事：全局默认访问模式
（``default_access_mode``：default / full，决定文件访问边界）与每个会话独立的
工作区范围（``WorkspaceScope``，存于会话 metadata）。状态文件用原子写 + fsync
保证崩溃安全；范围解析由 ``WebUIWorkspaceController`` 统一处理，并对 localhost
专属的"完全访问"控件做权限门控。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.paths import get_webui_dir
from nanobot.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScope,
    WorkspaceScopeError,
    build_workspace_scope,
    default_workspace_scope,
    validate_workspace_scope_payload,
)

WEBUI_WORKSPACE_STATE_SCHEMA_VERSION = 1
_MAX_STATE_FILE_BYTES = 128 * 1024
_DEFAULT_ACCESS_MODES = {"default", "full"}
_LEGACY_RESTRICTED_DEFAULT_ACCESS_MODE = "restricted"
_WEBUI_SCOPE_CHANNEL = "websocket"


def webui_workspace_state_path() -> Path:
    return get_webui_dir() / "workspace-state.json"


def default_webui_workspace_state() -> dict[str, Any]:
    return {
        "schema_version": WEBUI_WORKSPACE_STATE_SCHEMA_VERSION,
        "default_access_mode": "default",
        "updated_at": None,
    }


def normalize_webui_workspace_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    state = default_webui_workspace_state()
    updated_at = raw.get("updated_at")
    state["updated_at"] = updated_at if isinstance(updated_at, str) else None
    default_access_mode = raw.get("default_access_mode")
    if default_access_mode in _DEFAULT_ACCESS_MODES:
        state["default_access_mode"] = default_access_mode
    return state


def read_webui_workspace_state() -> dict[str, Any]:
    """读取工作区状态文件；过大或解析失败时回退到默认状态（拒绝异常输入）。"""
    path = webui_workspace_state_path()
    if not path.is_file():
        return default_webui_workspace_state()
    try:
        # 体积超限视为异常/损坏，直接忽略以防读入超大数据
        if path.stat().st_size > _MAX_STATE_FILE_BYTES:
            logger.warning("webui workspace state too large, ignoring: {}", path)
            return default_webui_workspace_state()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read webui workspace state failed {}: {}", path, e)
        return default_webui_workspace_state()
    return normalize_webui_workspace_state(raw)


def write_webui_workspace_state(raw: dict[str, Any]) -> dict[str, Any]:
    """原子写入工作区状态：先写 .tmp、fsync 文件、os.replace，再 fsync 目录。

    对文件和所在目录都做 fsync，确保 rename 这一原子动作本身也被持久化，
    从而在崩溃后不会出现回退到旧文件或空文件的中间态。"""
    state = normalize_webui_workspace_state(raw)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise ValueError("workspace state is too large")

    path = webui_workspace_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
        f.write(b"\n")
        f.flush()
        # fsync 文件内容，确保数据落盘后再 rename
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return state
    try:
        # fsync 目录以持久化 rename 的目录条目变更（POSIX 崩溃安全要求）
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return state


def read_webui_default_access_mode() -> str:
    state = read_webui_workspace_state()
    mode = state.get("default_access_mode")
    return mode if mode in _DEFAULT_ACCESS_MODES else "default"


def write_webui_default_access_mode(mode: str) -> bool:
    """设置默认访问模式，返回是否真的发生变化。仅在变化时才写盘。

    旧的 ``restricted`` 模式已废弃，统一映射为 ``default`` 以保持向前兼容。"""
    # 旧版 restricted 模式语义已被 default 取代，静默归一
    if mode == _LEGACY_RESTRICTED_DEFAULT_ACCESS_MODE:
        mode = "default"
    if mode not in _DEFAULT_ACCESS_MODES:
        raise ValueError("default access mode must be default or full")
    state = read_webui_workspace_state()
    changed = state.get("default_access_mode") != mode
    if changed:
        state["default_access_mode"] = mode
        write_webui_workspace_state(state)
    return changed


def default_scope_for_webui(
    default_workspace: Path,
    default_restrict_to_workspace: bool,
) -> WorkspaceScope:
    mode = read_webui_default_access_mode()
    if mode == "default":
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
            source_channel=_WEBUI_SCOPE_CHANNEL,
        )
    return build_workspace_scope(default_workspace, mode, source_channel=_WEBUI_SCOPE_CHANNEL)


def workspaces_payload(
    *,
    default_workspace: Path,
    default_restrict_to_workspace: bool,
    controls_available: bool,
) -> dict[str, Any]:
    default_access_mode = read_webui_default_access_mode()
    default_scope = (
        default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
            source_channel=_WEBUI_SCOPE_CHANNEL,
        )
        if default_access_mode == "default"
        else build_workspace_scope(default_workspace, default_access_mode, source_channel=_WEBUI_SCOPE_CHANNEL)
    )
    return {
        "schema_version": WEBUI_WORKSPACE_STATE_SCHEMA_VERSION,
        "default_access_mode": default_access_mode,
        "default_scope": default_scope.payload(),
        "controls": {
            "can_change_project": controls_available,
            "can_use_full_access": controls_available,
        },
    }


class WebUIWorkspaceController:
    """Own WebUI project scope persistence and validation.

    统一负责 WebUI 工作区范围的解析、校验与持久化：按会话从 metadata 读取已存
    范围，缺失时回退到默认范围；并对仅限 localhost 的"完全访问"控件做门控。"""

    def __init__(
        self,
        *,
        session_manager: Any | None,
        default_workspace: Path,
        default_restrict_to_workspace: bool,
    ) -> None:
        self._sessions = session_manager
        self._default_workspace = default_workspace
        self._default_restrict_to_workspace = default_restrict_to_workspace

    def default_scope(self) -> WorkspaceScope:
        return default_scope_for_webui(
            self._default_workspace,
            self._default_restrict_to_workspace,
        )

    def scope_for_session_key(self, session_key: str) -> WorkspaceScope:
        """返回某会话的工作区范围：优先用其 metadata 中持久化的范围，否则回退默认。

        优先调用轻量的 ``read_session_metadata``，不可用才退回读取整份会话文件；
        持久化范围缺失或校验失败时一律回退默认范围，避免脏数据阻塞对话。"""
        if self._sessions is None:
            return self.default_scope()
        # 优先用只读元数据的轻量接口，避免读取整份会话历史
        metadata_reader = getattr(self._sessions, "read_session_metadata", None)
        if callable(metadata_reader):
            data = metadata_reader(session_key)
        else:
            data = self._sessions.read_session_file(session_key)
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        if not isinstance(metadata, dict) or WORKSPACE_SCOPE_METADATA_KEY not in metadata:
            return self.default_scope()
        try:
            return validate_workspace_scope_payload(
                metadata.get(WORKSPACE_SCOPE_METADATA_KEY),
                default_workspace=self._default_workspace,
                default_restrict_to_workspace=self._default_restrict_to_workspace,
                source_channel=_WEBUI_SCOPE_CHANNEL,
            )
        except WorkspaceScopeError:
            return self.default_scope()

    def payload(self, *, controls_available: bool) -> dict[str, Any]:
        return workspaces_payload(
            default_workspace=self._default_workspace,
            default_restrict_to_workspace=self._default_restrict_to_workspace,
            controls_available=controls_available,
        )

    def scope_from_envelope(
        self,
        envelope: dict[str, Any],
        *,
        session_key: str | None,
        controls_available: bool,
    ) -> WorkspaceScope:
        """从请求信封解析工作区范围：信封带范围则校验之，否则按会话/默认回退。

        解析优先级：信封显式携带 > 会话已持久化范围 > 默认范围。
        ``controls_available`` 为 False（非 localhost）时，若解析出的范围与默认
        不同（即请求"完全访问"等高权限），拒绝并返回 403--高权限控件仅本地可用。"""
        raw = envelope.get(WORKSPACE_SCOPE_METADATA_KEY)
        if raw is None and session_key:
            # 信封未带范围但有会话：沿用该会话已持久化的范围
            scope = self.scope_for_session_key(session_key)
        elif raw is None:
            scope = self.default_scope()
        else:
            scope = validate_workspace_scope_payload(
                raw,
                default_workspace=self._default_workspace,
                default_restrict_to_workspace=self._default_restrict_to_workspace,
                source_channel=_WEBUI_SCOPE_CHANNEL,
            )
        # 非本地连接不得使用超出默认的访问范围，防止远程提权
        if not controls_available and scope.metadata() != self.default_scope().metadata():
            raise WorkspaceScopeError("workspace controls are localhost-only", status=403)
        return scope

    def scope_for_new_chat(
        self,
        envelope: dict[str, Any],
        *,
        controls_available: bool,
    ) -> WorkspaceScope:
        return self.scope_from_envelope(
            envelope,
            session_key=None,
            controls_available=controls_available,
        )

    def scope_for_set_request(
        self,
        envelope: dict[str, Any],
        *,
        chat_id: str,
        chat_running: bool,
        controls_available: bool,
    ) -> WorkspaceScope:
        """处理显式"设置工作区"请求：会话进行中拒绝修改（409），否则按信封解析。"""
        # 运行中的会话不允许切换工作区范围，避免中途改变文件访问边界
        if chat_running:
            raise WorkspaceScopeError("chat_running", status=409)
        return self.scope_from_envelope(
            envelope,
            session_key=f"websocket:{chat_id}",
            controls_available=controls_available,
        )

    def scope_for_message(
        self,
        envelope: dict[str, Any],
        *,
        chat_id: str,
        chat_running: bool,
        controls_available: bool,
    ) -> WorkspaceScope:
        """为普通消息解析范围：运行中允许沿用已存范围，但禁止借消息改成不同范围。

        与 ``scope_for_set_request`` 的区别：发消息时若信封未带范围则沿用会话已存
        范围（含运行中）；但若信封显式带了范围且与会话当前范围不同，则视为中途
        篡改访问边界，返回 409。"""
        scope = self.scope_from_envelope(
            envelope,
            session_key=f"websocket:{chat_id}",
            controls_available=controls_available,
        )
        if (
            WORKSPACE_SCOPE_METADATA_KEY in envelope
            and chat_running
            and scope.metadata() != self.scope_for_session_key(f"websocket:{chat_id}").metadata()
        ):
            # 运行中试图通过消息改写工作区范围 -> 冲突
            raise WorkspaceScopeError("chat_running", status=409)
        return scope

    def persist_scope(self, chat_id: str, scope: WorkspaceScope) -> None:
        """将工作区范围写入对应会话的 metadata，供后续请求沿用。"""
        if self._sessions is not None:
            session = self._sessions.get_or_create(f"websocket:{chat_id}")
            session.metadata["webui"] = True
            session.metadata[WORKSPACE_SCOPE_METADATA_KEY] = scope.metadata()
            self._sessions.save(session)
