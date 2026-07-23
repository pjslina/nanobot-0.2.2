"""Legacy WebUI JSON snapshot path helpers (JSON file); transcripts use transcript.

本模块负责管理 WebUI 旧版线程快照（JSON 文件）的路径定位与删除：
- `webui_thread_file_path` 根据 session_key 计算对应的快照文件路径；
- `delete_webui_thread` 同时清理旧版 JSON 快照与追加式转录文件，
  供会话重置 / 清理流程调用。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from nanobot.config.paths import get_webui_dir
from nanobot.session.manager import SessionManager
from nanobot.webui.transcript import delete_webui_transcript


def webui_thread_file_path(session_key: str) -> Path:
    # safe_key 将 session_key 中的危险字符替换为安全形式，确保可作为文件名使用
    stem = SessionManager.safe_key(session_key)
    return get_webui_dir() / f"{stem}.json"


def delete_webui_thread(session_key: str) -> bool:
    """Remove legacy WebUI JSON snapshot and append-only transcript for *session_key*.

    同时删除两类文件：旧版 JSON 快照与转录文件。只要任一删除成功即返回 True；
    JSON 文件删除失败时仅记录警告而不抛出，避免阻塞后续清理流程。
    """
    removed = False
    path = webui_thread_file_path(session_key)
    if path.is_file():
        try:
            path.unlink()
            removed = True
        except OSError as e:
            logger.warning("Failed to delete webui thread file {}: {}", path, e)
    # 无论 JSON 快照是否存在，都尝试删除转录文件，二者互不依赖
    if delete_webui_transcript(session_key):
        removed = True
    return removed
