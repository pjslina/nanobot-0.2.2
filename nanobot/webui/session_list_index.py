"""Cache-only WebUI session list index.

The core ``SessionManager`` owns durable conversation history. This module owns
the WebUI sidebar optimization so core session writes stay independent from UI
presentation caches.

WebUI 侧边栏会话列表的缓存层。核心的 ``SessionManager`` 负责持久化对话历史，
本模块维护一份可重建的展示缓存（``.webui_session_index.json``），让侧边栏渲染
无需每次全量扫描会话文件。通过文件签名（mtime/size）做增量校验：仅当会话文件
或 WebUI 活动文件发生变化时才重新读取，否则直接复用缓存行。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.paths import get_webui_dir
from nanobot.cron.session_turns import CRON_HISTORY_META
from nanobot.session.manager import (
    _SESSION_LIST_PREVIEW_MAX_CHARS,
    _SESSION_LIST_PREVIEW_MAX_RECORDS,
    Session,
    SessionManager,
    _message_preview_text,
    _metadata_title,
)

_INDEX_VERSION = 1
_INDEX_FILENAME = ".webui_session_index.json"
_WEBUI_ACTIVITY_MTIME_NS = "webui_activity_mtime_ns"
_WEBUI_ACTIVITY_SIZE = "webui_activity_size"


def list_webui_sessions(session_manager: SessionManager) -> list[dict[str, Any]]:
    """Return session rows for the WebUI sidebar, backed by a rebuildable cache.

    返回侧边栏所需的会话行。先与磁盘会话文件做增量对账（``_reconcile_index``），
    仅在缓存失效时才落盘新索引；最后按 ``updated_at`` 倒序输出供前端展示。"""
    rows, changed = _reconcile_index(session_manager)
    if changed:
        try:
            _write_index_rows(session_manager.sessions_dir, rows)
        except Exception as e:
            logger.debug("Failed to write WebUI session list index: {}", e)
    sessions = [_public_row(session_manager.sessions_dir, row) for row in rows]
    return sorted(sessions, key=lambda row: row.get("updated_at", ""), reverse=True)


def _reconcile_index(session_manager: SessionManager) -> tuple[list[dict[str, Any]], bool]:
    """将缓存索引与磁盘会话文件做增量对账，返回 (最新行列表, 是否有变化)。

    对账策略：用文件签名（mtime_ns/size）+ WebUI 活动文件签名判断每个缓存行
    是否仍有效，有效则直接复用、无需重读文件；失效或新增的文件才重新扫描。
    ``changed`` 为 True 时调用方会落盘新索引。"""
    existing_rows = _read_index_rows(session_manager.sessions_dir)
    existing_by_file = {
        row.get("file"): row
        for row in existing_rows or []
        if isinstance(row.get("file"), str)
    }
    paths = sorted(session_manager.sessions_dir.glob("*.jsonl"))
    rows: list[dict[str, Any]] = []
    # 缓存文件不存在时必然要重建，初始即标记为已变化
    changed = existing_rows is None

    for path in paths:
        row = existing_by_file.get(path.name)
        if row is not None and _indexed_row_matches_file(row, path):
            # 签名未变，复用缓存行，跳过昂贵的文件读取
            rows.append(row)
            continue

        changed = True
        scanned = _scan_session_row(session_manager, path)
        if scanned is not None:
            rows.append(scanned)

    # 文件集合有增删（新建或归档删除的会话）时需要刷新索引
    if set(existing_by_file) != {path.name for path in paths}:
        changed = True
    if existing_rows is not None and rows != existing_rows:
        changed = True
    return rows, changed


def _index_path(sessions_dir: Path) -> Path:
    return sessions_dir / _INDEX_FILENAME


def _read_index_rows(sessions_dir: Path) -> list[dict[str, Any]] | None:
    """读取并校验缓存索引；任何结构异常都返回 None 以触发全量重建。"""
    path = _index_path(sessions_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    # 版本不匹配（索引格式升级过）则废弃旧缓存
    if not isinstance(data, dict) or data.get("version") != _INDEX_VERSION:
        return None
    rows = data.get("sessions")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None
    return rows


def _write_index_rows(sessions_dir: Path, rows: list[dict[str, Any]]) -> None:
    """原子写入索引：先写 .tmp 再 os.replace，避免并发读取到半写文件。"""
    path = _index_path(sessions_dir)
    tmp_path = path.with_suffix(".json.tmp")
    data = {"version": _INDEX_VERSION, "sessions": rows}
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        # os.replace 在同文件系统下为原子操作，读者要么看到旧文件要么看到新文件
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _file_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def _indexed_row_matches_file(row: dict[str, Any], path: Path) -> bool:
    """判断缓存行是否仍然有效：需同时匹配会话文件签名与 WebUI 活动文件签名。

    会话文件签名（mtime_ns/size）反映对话历史变更；WebUI 活动文件签名反映
    UI 侧的活动更新（二者共同决定 ``updated_at``），任一变化都需重新扫描。"""
    if not all(isinstance(row.get(key), str) for key in ("key", "created_at", "updated_at")):
        return False
    if not isinstance(row.get("title", ""), str) or not isinstance(row.get("preview", ""), str):
        return False
    if row.get("file") != path.name:
        return False
    try:
        signature = _file_signature(path)
    except OSError:
        return False
    activity_signature = _webui_activity_signature(str(row.get("key")))
    return (
        row.get("mtime_ns") == signature["mtime_ns"]
        and row.get("size") == signature["size"]
        and row.get(_WEBUI_ACTIVITY_MTIME_NS) == activity_signature[_WEBUI_ACTIVITY_MTIME_NS]
        and row.get(_WEBUI_ACTIVITY_SIZE) == activity_signature[_WEBUI_ACTIVITY_SIZE]
    )


def _public_row(sessions_dir: Path, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": row.get("key"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "title": row.get("title", ""),
        "preview": row.get("preview", ""),
        "path": str(sessions_dir / str(row.get("file", ""))),
    }


def _preview_from_messages(messages: list[dict[str, Any]]) -> str:
    """从内存消息生成侧边栏预览：优先返回第一条 user 消息，否则取首条 assistant。

    跳过 cron 历史元数据记录；并用记录数/字符数双上限避免对超大历史做全量扫描。"""
    fallback_preview = ""
    scanned_records = 0
    scanned_chars = 0
    for item in messages:
        scanned_records += 1
        scanned_chars += len(json.dumps(item, ensure_ascii=False)) + 1
        if (
            scanned_records > _SESSION_LIST_PREVIEW_MAX_RECORDS
            or scanned_chars > _SESSION_LIST_PREVIEW_MAX_CHARS
        ):
            break
        # cron 历史是系统注入的元数据行，不作为对话预览
        if item.get(CRON_HISTORY_META) is True:
            continue
        text = _message_preview_text(item)
        if not text:
            continue
        if item.get("role") == "user":
            return text
        if not fallback_preview and item.get("role") == "assistant":
            fallback_preview = text
    return fallback_preview


def _webui_activity_paths(session_key: str) -> list[Path]:
    """返回某会话在 WebUI 目录下的活动文件（.jsonl 流式活动与 .json 快照）。

    这些文件独立于会话历史，记录 UI 侧的交互活动，用于让侧边栏的 ``updated_at``
    能反映用户在 WebUI 上的最新操作，而不仅是会话历史写入时间。"""
    stem = SessionManager.safe_key(session_key)
    webui_dir = get_webui_dir()
    return [
        webui_dir / f"{stem}.jsonl",
        webui_dir / f"{stem}.json",
    ]


def _webui_activity_signature(session_key: str) -> dict[str, int]:
    latest_mtime_ns = 0
    total_size = 0
    for path in _webui_activity_paths(session_key):
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
        total_size += stat.st_size
    return {
        _WEBUI_ACTIVITY_MTIME_NS: latest_mtime_ns,
        _WEBUI_ACTIVITY_SIZE: total_size,
    }


def _webui_activity_updated_at(signature: dict[str, int]) -> str | None:
    """将活动签名的 mtime（纳秒）转为 ISO 时间字符串；无活动文件时返回 None。"""
    mtime_ns = signature.get(_WEBUI_ACTIVITY_MTIME_NS, 0)
    if mtime_ns <= 0:
        return None
    # mtime_ns 为纳秒，除以 1e9 得到秒
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000).isoformat()


def _timestamp(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _latest_updated_at(stored: str | None, activity: str | None) -> str | None:
    """取会话历史更新时间与 WebUI 活动时间的较新者作为侧边栏排序依据。"""
    if _timestamp(activity) > _timestamp(stored):
        return activity
    return stored


def _indexed_row_for_session(session: Session, path: Path) -> dict[str, Any]:
    signature = _file_signature(path)
    activity_signature = _webui_activity_signature(session.key)
    activity_updated_at = _webui_activity_updated_at(activity_signature)
    return {
        "key": session.key,
        "created_at": session.created_at.isoformat(),
        "updated_at": _latest_updated_at(session.updated_at.isoformat(), activity_updated_at),
        "title": _metadata_title(session.metadata),
        "preview": _preview_from_messages(session.messages),
        "file": path.name,
        "mtime_ns": signature["mtime_ns"],
        "size": signature["size"],
        **activity_signature,
    }


def _scan_session_row(session_manager: SessionManager, path: Path) -> dict[str, Any] | None:
    """从磁盘会话文件流式扫描出一条缓存行：首行为元数据，其余每行一条消息。

    预览策略同 ``_preview_from_messages``（优先首条 user，回退首条 assistant），
    并用记录数/字符数上限避免读取超大文件。任何解析异常都交由 ``_repair``
    修复后重试，修复失败则返回 None（该会话从侧边栏隐去）。"""
    # 文件名由会话键把首个 ':' 替换为 '_' 得到（规避非法文件名字符），这里逆向还原
    fallback_key = path.stem.replace("_", ":", 1)
    try:
        with open(path, encoding="utf-8") as f:
            first_line = f.readline().strip()
            if not first_line:
                return None
            data = json.loads(first_line)
            if data.get("_type") != "metadata":
                return None
            preview = ""
            fallback_preview = ""
            scanned_records = 0
            scanned_chars = 0
            for line in f:
                if not line.strip():
                    continue
                scanned_records += 1
                scanned_chars += len(line)
                if (
                    scanned_records > _SESSION_LIST_PREVIEW_MAX_RECORDS
                    or scanned_chars > _SESSION_LIST_PREVIEW_MAX_CHARS
                ):
                    break
                item = json.loads(line)
                if item.get("_type") == "metadata":
                    continue
                if item.get(CRON_HISTORY_META) is True:
                    continue
                text = _message_preview_text(item)
                if not text:
                    continue
                if item.get("role") == "user":
                    preview = text
                    break
                if not fallback_preview and item.get("role") == "assistant":
                    fallback_preview = text
            signature = _file_signature(path)
            key = data.get("key") or fallback_key
            activity_signature = _webui_activity_signature(key)
            activity_updated_at = _webui_activity_updated_at(activity_signature)
            return {
                "key": key,
                "created_at": data.get("created_at"),
                "updated_at": _latest_updated_at(data.get("updated_at"), activity_updated_at),
                "title": _metadata_title(data.get("metadata", {})),
                "preview": preview or fallback_preview,
                "file": path.name,
                "mtime_ns": signature["mtime_ns"],
                "size": signature["size"],
                **activity_signature,
            }
    except Exception:
        # 文件损坏/格式异常时尝试修复会话，再由内存对象重建行
        repaired = session_manager._repair(fallback_key)
        if repaired is None:
            return None
        return _indexed_row_for_session(repaired, path)
