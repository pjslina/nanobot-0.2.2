"""Append-only WebUI display transcript (JSONL), separate from agent session.

WebUI 显示转录模块：以追加写（append-only）的 JSONL 文件持久化面向前端展示的
事件流，与 agent 会话历史（``nanobot/agent/memory.py``）相互独立。核心职责包括：

- 记录用户消息、流式增量（delta）、推理（reasoning）、工具调用/文件编辑、轮次结束
  等事件，用于 WebUI 回放与刷新续接；
- 当活跃转录文件超过阈值时分段滚动（segment rotation），并维护 manifest 索引，
  支持基于 turn 的游标分页加载历史；
- 将底层 JSONL 事件折叠（replay）成前端 ``UIMessage`` 形态，复刻 ``useNanobotStream.ts``
  中的核心状态机逻辑；
- 支持从会话历史回填缺失的用户消息、分叉（fork）转录、签名媒体 URL 重写等。

本模块只关心"展示用"转录；agent 的真实对话上下文由会话管理器维护。
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple
from urllib.parse import unquote, urlparse

from loguru import logger

from nanobot.config.paths import get_webui_dir
from nanobot.cron.session_turns import CRON_HISTORY_META
from nanobot.session.manager import SessionManager
from nanobot.webui.metadata import WEBUI_MESSAGE_SOURCE_METADATA_KEY, WEBUI_TURN_METADATA_KEY

WEBUI_TRANSCRIPT_SCHEMA_VERSION = 3
WEBUI_FORK_MARKER_EVENT = "fork_marker"
# 单个转录文件（活跃段或归档段）的字节上限，超过即触发滚动/分段。
_MAX_TRANSCRIPT_FILE_BYTES = 8 * 1024 * 1024
# 活跃转录滚动时希望保留的目标大小（上限的一半），为后续追加留出余量。
_TARGET_ACTIVE_TRANSCRIPT_BYTES = _MAX_TRANSCRIPT_FILE_BYTES // 2
_TRANSCRIPT_SEGMENT_MANIFEST_VERSION = 2
# 活跃段在 chunk_id 列表中的固定标识；其余归档段为 6 位数字编号。
_TRANSCRIPT_ACTIVE_CHUNK_ID = "active"
# 归档段文件名形如 000001.jsonl，正则用于校验/枚举磁盘上的段文件。
_TRANSCRIPT_SEGMENT_RE = re.compile(r"^\d{6}\.jsonl$")
_DEFAULT_TRANSCRIPT_PAGE_LIMIT = 160
_MAX_TRANSCRIPT_PAGE_LIMIT = 1000
# turn_id 白名单正则；长度受限且字符集安全，避免作为标识符时引发注入或异常。
_WEBUI_TURN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MARKDOWN_LOCAL_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\((<[^>]+>|[^)\s]+)(\s+(?:\"[^\"]*\"|'[^']*'))?\)"
)
_INLINE_MARKDOWN_IMAGE_EXTS: frozenset[str] = frozenset({
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
})
_INLINE_MARKDOWN_VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4",
    ".mov",
    ".webm",
})
_INLINE_MARKDOWN_MEDIA_EXTS = _INLINE_MARKDOWN_IMAGE_EXTS | _INLINE_MARKDOWN_VIDEO_EXTS
# 这些工具会修改文件，其在工具事件中的提示若已被 file_edit 事件覆盖，则需在回放时去重。
_FILE_EDIT_TOOL_NAMES: frozenset[str] = frozenset({
    "write_file",
    "edit_file",
    "apply_patch",
})
# 转录中参与"一轮对话展示"的事件类型；其余事件（如 fork_marker）仅作标记不直接展示。
_TURN_DISPLAY_EVENTS: frozenset[str] = frozenset({
    "reasoning_delta",
    "reasoning_end",
    "delta",
    "stream_end",
    "message",
    "file_edit",
    "turn_end",
})


def rewrite_local_markdown_images(
    text: str,
    *,
    workspace_path: Path,
    sign_path: Callable[[Path], Mapping[str, Any] | None],
) -> str:
    """Rewrite markdown media paths inside the workspace to signed WebUI media URLs.

    将 markdown 中指向工作区内本地媒体的路径改写为已签名的 WebUI 媒体 URL。
    仅处理相对工作区的本地路径（无 scheme/netloc/query/fragment），并通过
    ``sign_path`` 回调换取带签名的访问 URL，使前端可通过网关安全访问受保护媒体。
    已经是 ``/api/media/`` 前缀、锚点或绝对 URL 的链接保持原样。
    """
    # 快速路径：文本中不含 markdown 图片语法时直接返回，避免正则开销。
    if "![" not in text:
        return text

    def resolve_url(raw_url: str) -> str | None:
        url = raw_url.strip()
        # 形如 ![..](<url>) 时 url 被尖括号包裹，剥去后再处理。
        if url.startswith("<") and url.endswith(">"):
            url = url[1:-1].strip()
        # 跳过已是网关媒体路径、锚点链接；这些无需也无法再次签名。
        if not url or url.startswith(("/api/media/", "#")):
            return None
        parsed = urlparse(url)
        # 仅处理纯本地路径：带 scheme/netloc/query/fragment 的 URL 不改写。
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return None
        path_text = unquote(url)
        if Path(path_text).suffix.lower() not in _INLINE_MARKDOWN_MEDIA_EXTS:
            return None
        candidate = Path(path_text).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_path / candidate
        try:
            # resolve(strict=False) 允许路径中间存在不存在的段；
            # relative_to 用于校验解析后的绝对路径仍在工作区内，防止路径穿越。
            resolved = candidate.resolve(strict=False)
            resolved.relative_to(workspace_path)
        except (OSError, ValueError):
            return None
        if not resolved.is_file():
            return None
        signed = sign_path(resolved)
        return str(signed.get("url")) if signed and signed.get("url") else None

    def replace(match: re.Match[str]) -> str:
        signed_url = resolve_url(match.group(2))
        if not signed_url:
            return match.group(0)
        title = match.group(3) or ""
        return f"![{match.group(1)}]({signed_url}{title})"

    return _MARKDOWN_LOCAL_IMAGE_RE.sub(replace, text)


def _media_kind_from_name(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _INLINE_MARKDOWN_IMAGE_EXTS:
        return "image"
    if ext in _INLINE_MARKDOWN_VIDEO_EXTS:
        return "video"
    return "file"


def webui_transcript_path(session_key: str) -> Path:
    # 活跃转录始终写入 {safe_key}.jsonl；safe_key 将原始 session_key 规整为文件名安全形式。
    stem = SessionManager.safe_key(session_key)
    return get_webui_dir() / f"{stem}.jsonl"


def webui_transcript_segments_dir(session_key: str) -> Path:
    # 归档段与 manifest 存放在 {safe_key}.segments/ 子目录下，与活跃文件分离。
    stem = SessionManager.safe_key(session_key)
    return get_webui_dir() / f"{stem}.segments"


def _webui_transcript_manifest_path(session_key: str) -> Path:
    return webui_transcript_segments_dir(session_key) / "manifest.json"


def _legacy_webui_thread_path(session_key: str) -> Path:
    # 旧版 WebUI 使用 {safe_key}.json 单文件存储线程，删除时需一并清理。
    stem = SessionManager.safe_key(session_key)
    return get_webui_dir() / f"{stem}.json"


class _TranscriptTurnRef(NamedTuple):
    # 选中分页时用到的"轮次引用"：ordinal 为该轮在全局轮次序列中的序号，
    # records 为该轮的全部 JSONL 记录（按时间顺序）。
    ordinal: int
    records: list[dict[str, Any]]


class _TranscriptChunkRef(NamedTuple):
    # "块引用"：chunk_id 为段标识（active 或 6 位编号），start_ordinal 为该段在
    # 全局轮次序列中的起始序号，turn_count/user_count 用于分页计数时避免回读文件。
    chunk_id: str
    start_ordinal: int
    turn_count: int
    user_count: int


def _record_json_line(record: dict[str, Any]) -> str:
    # 紧凑序列化（无多余空白）且保留非 ASCII 字符，保证字节估算与磁盘写入一致。
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


def _read_transcript_file(path: Path) -> list[dict[str, Any]]:
    # 逐行读取 JSONL，跳过空行与解析失败的行（仅告警不抛异常），保证转录损坏时仍可部分加载。
    lines_out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("bad jsonl at {} line {}", path, line_no)
                    continue
                if isinstance(obj, dict):
                    lines_out.append(obj)
    except OSError as e:
        logger.warning("read transcript failed {}: {}", path, e)
        return []
    return lines_out


def _records_bytes(records: list[dict[str, Any]]) -> int:
    # 估算记录序列落盘后的字节数（含每行末尾换行），用于分段滚动时判断是否超限。
    total = 0
    for record in records:
        total += len(_record_json_line(record).encode("utf-8")) + 1
    return total


def _flatten_turns(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [record for turn in turns for record in turn]


def _write_records_to_path(path: Path, rows: list[dict[str, Any]]) -> None:
    # 原子写：先写 .tmp 文件并 fsync 落盘，再 os.replace 原子替换目标文件，
    # 避免崩溃时留下半写文件导致转录损坏。任一异常下删除临时文件并向上抛出。
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            for row in rows:
                raw = _record_json_line(row)
                # 单行体积超限直接报错，防止某条异常记录撑爆单个段文件。
                if len(raw.encode("utf-8")) > _MAX_TRANSCRIPT_FILE_BYTES:
                    raise ValueError("webui transcript line too large")
                f.write(raw + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _segment_file_path(session_key: str, segment_id: str) -> Path:
    return webui_transcript_segments_dir(session_key) / f"{segment_id}.jsonl"


def _segment_ids_on_disk(session_key: str) -> list[str]:
    # 枚举磁盘上 {stem}.segments/ 目录下符合 NNNNNN.jsonl 命名的段，按文件名升序返回。
    directory = webui_transcript_segments_dir(session_key)
    if not directory.is_dir():
        return []
    return sorted(
        path.stem
        for path in directory.iterdir()
        if path.is_file() and _TRANSCRIPT_SEGMENT_RE.fullmatch(path.name)
    )


def _segment_manifest_entry(session_key: str, segment_id: str) -> dict[str, Any]:
    # 通过实际读取段文件计算其 manifest 条目（字节大小、轮次数、用户消息数）。
    # 仅在 manifest 失效/重建时调用，正常路径使用缓存以避免回读所有段。
    path = _segment_file_path(session_key, segment_id)
    lines = _read_transcript_file(path)
    return {
        "id": segment_id,
        "bytes": path.stat().st_size if path.exists() else 0,
        "turn_count": len(_split_transcript_turns(lines)),
        "user_count": sum(1 for line in lines if _is_user_transcript_row(line)),
    }


def _non_negative_int(value: Any) -> int | None:
    # 显式排除 bool（bool 是 int 子类），仅接受非负整数；非法值返回 None。
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _normalize_manifest_entry(session_key: str, entry: Any) -> dict[str, Any] | None:
    # 校验 manifest 中的单个条目：id 命名合规、对应文件存在且字节数与记录一致、
    # 计数字段为非负整数。任一不符返回 None，触发整体 manifest 重建。
    if not isinstance(entry, dict):
        return None
    segment_id = entry.get("id")
    if not isinstance(segment_id, str) or not _TRANSCRIPT_SEGMENT_RE.fullmatch(f"{segment_id}.jsonl"):
        return None
    segment_path = _segment_file_path(session_key, segment_id)
    values = {
        key: _non_negative_int(entry.get(key))
        for key in ("bytes", "turn_count", "user_count")
    }
    # 字节数必须与磁盘文件当前大小一致，否则视为失效（可能被外部改动或部分写入）。
    if not segment_path.is_file() or values["bytes"] != segment_path.stat().st_size:
        return None
    if values["turn_count"] is None or values["user_count"] is None:
        return None
    return {
        "id": segment_id,
        "bytes": values["bytes"],
        "turn_count": values["turn_count"],
        "user_count": values["user_count"],
    }


def _write_segment_manifest(session_key: str, segment_ids: list[str]) -> None:
    # 原子写入 manifest.json（先写 .tmp 再 os.replace），条目由各段实时统计得到。
    directory = webui_transcript_segments_dir(session_key)
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "version": _TRANSCRIPT_SEGMENT_MANIFEST_VERSION,
        "segments": [_segment_manifest_entry(session_key, segment_id) for segment_id in segment_ids],
    }
    path = _webui_transcript_manifest_path(session_key)
    tmp_path = path.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _rebuild_segment_manifest(session_key: str) -> list[str]:
    # 从磁盘重新扫描所有段并重建 manifest；若无段则删除 manifest 文件。返回段 id 列表。
    segment_ids = _segment_ids_on_disk(session_key)
    if segment_ids:
        _write_segment_manifest(session_key, segment_ids)
    else:
        _webui_transcript_manifest_path(session_key).unlink(missing_ok=True)
    return segment_ids


def _rebuilt_segment_manifest_entries(session_key: str) -> list[dict[str, Any]]:
    return [_segment_manifest_entry(session_key, segment_id) for segment_id in _rebuild_segment_manifest(session_key)]


def _read_segment_manifest_entries(session_key: str) -> list[dict[str, Any]]:
    # 读取并校验 manifest：版本不匹配、条目非法、或与磁盘段列表不一致时，回退到重建。
    # 该容错策略保证 manifest 与磁盘始终一致，即使 manifest 损坏也能自愈。
    directory = webui_transcript_segments_dir(session_key)
    if not directory.is_dir():
        return []
    path = _webui_transcript_manifest_path(session_key)
    if not path.is_file():
        return _rebuilt_segment_manifest_entries(session_key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_segments = data.get("segments") if isinstance(data, dict) else None
        if data.get("version") != _TRANSCRIPT_SEGMENT_MANIFEST_VERSION or not isinstance(raw_segments, list):
            return _rebuilt_segment_manifest_entries(session_key)
        entries: list[dict[str, Any]] = []
        for entry in raw_segments:
            normalized = _normalize_manifest_entry(session_key, entry)
            if normalized is None:
                return _rebuilt_segment_manifest_entries(session_key)
            entries.append(normalized)
        # manifest 中的段 id 顺序必须与磁盘实际段列表完全一致，否则重建。
        if [entry["id"] for entry in entries] != _segment_ids_on_disk(session_key):
            return _rebuilt_segment_manifest_entries(session_key)
        return entries
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return _rebuilt_segment_manifest_entries(session_key)


def _read_segment_ids(session_key: str) -> list[str]:
    return [entry["id"] for entry in _read_segment_manifest_entries(session_key)]


def _append_segment_turns(session_key: str, turns: list[list[dict[str, Any]]]) -> None:
    # 将一批完整轮次追加为归档段：按 _MAX_TRANSCRIPT_FILE_BYTES 上限分批，
    # 每批写一个新段（编号自增），最后刷新 manifest。单个 turn 不会跨段拆分。
    if not turns:
        return
    segment_ids = _read_segment_ids(session_key)
    next_id = int(segment_ids[-1]) + 1 if segment_ids else 1
    batch: list[list[dict[str, Any]]] = []
    batch_bytes = 0
    for turn in turns:
        turn_bytes = _records_bytes(turn)
        # 当前批次非空且加入后超限，则先落盘当前批次再开新批次。
        if batch and batch_bytes + turn_bytes > _MAX_TRANSCRIPT_FILE_BYTES:
            segment_id = f"{next_id:06d}"
            _write_records_to_path(_segment_file_path(session_key, segment_id), _flatten_turns(batch))
            segment_ids.append(segment_id)
            next_id += 1
            batch = []
            batch_bytes = 0
        batch.append(turn)
        batch_bytes += turn_bytes
    if batch:
        segment_id = f"{next_id:06d}"
        _write_records_to_path(_segment_file_path(session_key, segment_id), _flatten_turns(batch))
        segment_ids.append(segment_id)
    _write_segment_manifest(session_key, segment_ids)


def _rotate_active_transcript_if_needed(session_key: str) -> None:
    # 活跃文件超限时触发滚动：将较早的完整轮次归档到段文件，仅保留尾部若干轮在活跃文件。
    # 从末尾向前累加，保留总字节不超过 _TARGET_ACTIVE_TRANSCRIPT_BYTES 的最近若干轮
    # （至少保留最后一轮），保证活跃文件始终可追加且体积受控。
    path = webui_transcript_path(session_key)
    if not path.is_file():
        return
    try:
        if path.stat().st_size <= _MAX_TRANSCRIPT_FILE_BYTES:
            return
    except OSError:
        return

    lines = _read_transcript_file(path)
    if not lines:
        return
    turns = _split_transcript_turns(lines)
    # 只有一轮时无法滚动（归档会清空活跃文件），直接返回等待更多轮次。
    if len(turns) <= 1:
        return

    keep_start = len(turns) - 1
    keep_bytes = 0
    # 从最后一轮向前累加，直到再前一轮会超出目标大小为止。
    for idx in range(len(turns) - 1, -1, -1):
        turn_bytes = _records_bytes(turns[idx])
        if idx == len(turns) - 1 or keep_bytes + turn_bytes <= _TARGET_ACTIVE_TRANSCRIPT_BYTES:
            keep_start = idx
            keep_bytes += turn_bytes
            continue
        break

    moved = turns[:keep_start]
    kept = turns[keep_start:]
    if not moved:
        return
    # 先归档较早轮次，再用 kept 重写活跃文件；二者均通过原子写完成。
    _append_segment_turns(session_key, moved)
    _write_records_to_path(path, _flatten_turns(kept))


def _chunk_ids(session_key: str) -> list[str]:
    # 返回全部 chunk id：先触发可能的滚动，再列出归档段，最后追加活跃段（若存在）。
    _rotate_active_transcript_if_needed(session_key)
    ids = _read_segment_ids(session_key)
    if webui_transcript_path(session_key).is_file():
        ids.append(_TRANSCRIPT_ACTIVE_CHUNK_ID)
    return ids


def _read_chunk_turns(session_key: str, chunk_id: str) -> list[list[dict[str, Any]]]:
    # 读取某个 chunk 的全部轮次：active 取活跃文件，其余取对应段文件。
    if chunk_id == _TRANSCRIPT_ACTIVE_CHUNK_ID:
        path = webui_transcript_path(session_key)
    else:
        path = _segment_file_path(session_key, chunk_id)
    if not path.is_file():
        return []
    return _split_transcript_turns(_read_transcript_file(path))


def _encode_page_cursor(before_turn_ordinal: int) -> str:
    # 分页游标为 {"before_turn": N} 的 urlsafe base64 编码（去填充），
    # 表示"加载序号 N 之前的轮次"。前端透传回服务端实现"加载更早历史"。
    raw = json.dumps(
        {"before_turn": before_turn_ordinal},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_page_cursor(value: str | None) -> int | None:
    # 解码游标：补齐 base64 填充后解析 JSON，校验 before_turn 为非负整数；
    # 任何损坏或非法值返回 None（视为从头加载最新一页）。
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    before_turn = data.get("before_turn")
    if (
        isinstance(before_turn, bool)
        or not isinstance(before_turn, int)
        or before_turn < 0
    ):
        return None
    return before_turn


def _coerce_page_limit(limit: int | None) -> int:
    # 将分页 limit 收敛到 [1, _MAX_TRANSCRIPT_PAGE_LIMIT]；None 时用默认值。
    if limit is None:
        return _DEFAULT_TRANSCRIPT_PAGE_LIMIT
    return max(1, min(_MAX_TRANSCRIPT_PAGE_LIMIT, int(limit)))


def _chunk_turn_refs(session_key: str) -> list[_TranscriptChunkRef]:
    # 构建全部 chunk 的引用列表（含全局起始序号与计数），用于分页时按序号区间定位。
    # 归档段直接复用 manifest 缓存的计数；活跃段需实际读取以获得真实计数。
    _rotate_active_transcript_if_needed(session_key)
    refs: list[_TranscriptChunkRef] = []
    ordinal = 0
    for entry in _read_segment_manifest_entries(session_key):
        chunk_id = str(entry["id"])
        turn_count = int(entry["turn_count"])
        if turn_count <= 0:
            continue
        refs.append(_TranscriptChunkRef(chunk_id, ordinal, turn_count, int(entry["user_count"])))
        ordinal += turn_count
    if webui_transcript_path(session_key).is_file():
        active_turns = _read_chunk_turns(session_key, _TRANSCRIPT_ACTIVE_CHUNK_ID)
        active_turn_count = len(active_turns)
        if active_turn_count > 0:
            refs.append(
                _TranscriptChunkRef(
                    _TRANSCRIPT_ACTIVE_CHUNK_ID,
                    ordinal,
                    active_turn_count,
                    sum(1 for turn in active_turns for row in turn if _is_user_transcript_row(row)),
                ),
            )
    return refs


def _count_user_messages_before_ordinal(
    session_key: str,
    chunks: list[_TranscriptChunkRef],
    before_ordinal: int,
) -> int:
    # 统计给定全局轮次序号之前的用户消息数，用于告知前端本页起始的用户消息偏移。
    # 整段覆盖时直接用缓存的 user_count，部分覆盖时才回读该段实际计数。
    total = 0
    for chunk in chunks:
        if before_ordinal <= chunk.start_ordinal:
            break
        local_end = min(chunk.turn_count, before_ordinal - chunk.start_ordinal)
        if local_end <= 0:
            continue
        if local_end >= chunk.turn_count:
            total += chunk.user_count
            continue
        turns = _read_chunk_turns(session_key, chunk.chunk_id)
        total += sum(
            1
            for turn in turns[:local_end]
            for row in turn
            if _is_user_transcript_row(row)
        )
    return total


def _select_transcript_page(
    session_key: str,
    *,
    limit: int | None,
    before: str | None,
    _manifest_rebuilt: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # 分页选取历史转录：按 UI 消息数（而非轮次数）限制每页大小，从最新向最老方向
    # 跨 chunk 收集轮次，直至达到 page_limit。返回该页的全部 JSONL 记录及分页元信息。
    # _manifest_rebuilt 用于防止重建 manifest 后仍不一致时的无限递归。
    page_limit = _coerce_page_limit(limit)
    chunks = _chunk_turn_refs(session_key)
    total_turns = sum(chunk.turn_count for chunk in chunks)
    before_ordinal = _decode_page_cursor(before)
    upper_ordinal = total_turns if before_ordinal is None else min(before_ordinal, total_turns)
    selected: list[_TranscriptTurnRef] = []
    selected_message_count = 0

    # 从最新 chunk 向最老 chunk 倒序遍历，每个 chunk 内同样从后向前取轮次。
    for chunk in reversed(chunks):
        if chunk.start_ordinal >= upper_ordinal:
            continue
        local_upper = min(chunk.turn_count, upper_ordinal - chunk.start_ordinal)
        if local_upper <= 0:
            continue
        turns = _read_chunk_turns(session_key, chunk.chunk_id)
        # 归档段实际轮次数与 manifest 记录不符时，重建 manifest 后重新分页一次。
        if (
            chunk.chunk_id != _TRANSCRIPT_ACTIVE_CHUNK_ID
            and len(turns) != chunk.turn_count
            and not _manifest_rebuilt
        ):
            _rebuild_segment_manifest(session_key)
            return _select_transcript_page(
                session_key,
                limit=limit,
                before=before,
                _manifest_rebuilt=True,
            )
        local_upper = min(local_upper, len(turns))
        for turn_index in range(local_upper - 1, -1, -1):
            ordinal = chunk.start_ordinal + turn_index
            turn = turns[turn_index]
            selected.append(_TranscriptTurnRef(ordinal, turn))
            # 用 replay 后的 UI 消息数衡量页大小，与前端展示的消息粒度一致。
            selected_message_count += len(replay_transcript_to_ui_messages(turn))
            if selected_message_count >= page_limit:
                break
        if selected_message_count >= page_limit:
            break

    # selected 为倒序，反转回时间正序后展平为记录列表。
    selected_chronological = list(reversed(selected))
    lines = [record for ref in selected_chronological for record in ref.records]
    if not selected_chronological:
        return [], {
            "before_cursor": None,
            "has_more_before": False,
            "loaded_message_count": 0,
            "user_message_offset": 0,
        }

    first_ref = selected_chronological[0]
    # 是否还有更早的历史：当本页起始轮次序号 > 0 时存在更早轮次。
    has_more = first_ref.ordinal > 0
    page = {
        # 下一页游标指向本页起始轮次序号，前端再次请求即加载该序号之前的轮次。
        "before_cursor": _encode_page_cursor(first_ref.ordinal) if has_more else None,
        "has_more_before": has_more,
        "loaded_message_count": 0,
        "user_message_offset": _count_user_messages_before_ordinal(
            session_key,
            chunks,
            first_ref.ordinal,
        ),
    }
    return lines, page


def read_transcript_lines(session_key: str) -> list[dict[str, Any]]:
    # 读取整条会话的转录：依次拼接所有归档段与活跃文件的记录，按时间正序返回。
    lines: list[dict[str, Any]] = []
    for chunk_id in _chunk_ids(session_key):
        if chunk_id == _TRANSCRIPT_ACTIVE_CHUNK_ID:
            lines.extend(_read_transcript_file(webui_transcript_path(session_key)))
        else:
            lines.extend(_read_transcript_file(_segment_file_path(session_key, chunk_id)))
    return lines


def _write_transcript_lines(session_key: str, rows: list[dict[str, Any]]) -> None:
    # 全量重写转录：先删除现有活跃文件/段/旧线程文件，再写入新的活跃文件并尝试滚动。
    # 用于分叉（fork）等需要整体替换转录的场景。
    delete_webui_transcript(session_key)
    path = webui_transcript_path(session_key)
    _write_records_to_path(path, rows)
    _rotate_active_transcript_if_needed(session_key)


def _append_to_active_transcript(session_key: str, obj: dict[str, Any]) -> None:
    # 以追加模式写入单条记录到活跃文件，并 fsync 保证持久化。
    # 单行超限直接抛错，避免破坏 JSONL 结构或撑爆文件。
    raw = _record_json_line(obj)
    if len(raw.encode("utf-8")) > _MAX_TRANSCRIPT_FILE_BYTES:
        msg = "webui transcript line too large"
        raise ValueError(msg)
    path = webui_transcript_path(session_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = raw + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def append_transcript_object(session_key: str, obj: dict[str, Any]) -> None:
    # 追加一条事件记录；当事件为 turn_end（一轮结束）时尝试滚动活跃文件，
    # 保证仅在轮次边界归档，避免把一轮拆分到两段。
    _append_to_active_transcript(session_key, obj)
    if obj.get("event") == "turn_end":
        _rotate_active_transcript_if_needed(session_key)


def normalize_webui_turn_id(value: Any) -> str:
    # 规整 turn_id：仅当为合法字符串时原样返回，否则生成随机 UUID。
    # 防止外部传入非法 turn_id 破坏去重/分页逻辑。
    if isinstance(value, str):
        candidate = value.strip()
        if _WEBUI_TURN_ID_RE.fullmatch(candidate):
            return candidate
    return str(uuid.uuid4())


def webui_message_source(metadata: dict[str, Any] | None) -> dict[str, str] | None:
    # 从 metadata 中提取消息来源标记；仅处理 kind="cron"（cron 触发的轮次），
    # 携带可选 label。其余来源不标注，返回 None。
    raw = (metadata or {}).get(WEBUI_MESSAGE_SOURCE_METADATA_KEY)
    if not isinstance(raw, dict) or raw.get("kind") != "cron":
        return None
    source: dict[str, str] = {"kind": "cron"}
    label = raw.get("label")
    if isinstance(label, str) and label.strip():
        source["label"] = label.strip()
    return source


class WebUITranscriptRecorder:
    """Prepare and persist WebUI wire events without leaking UI rules into channels.

    WebUI 转录记录器：负责把会话事件整理并落盘到转录 JSONL，同时为同一轮
    （turn）内的事件打上 ``turn_id`` / ``turn_phase`` / ``turn_seq`` 标记，使前端
    能按轮次分组与排序。其本身不依赖任何渠道逻辑，避免把 UI 展示规则泄漏到
    渠道实现中。
    """

    def __init__(self, log: Any = logger) -> None:
        self._log = log
        # 同一轮内的事件序号生成状态：键为 (chat_id, turn_id)，值为已分配的最大序号。
        # 仅在进程内存中维护，turn 完成后清除；网关重启后会从 1 重新计数。
        self._turn_sequences: dict[tuple[str, str], int] = {}

    def client_turn_metadata(self, value: Any) -> dict[str, str]:
        # 将外部传入的 turn 标识规整后包装为 metadata，供后续 annotate 使用。
        return {WEBUI_TURN_METADATA_KEY: normalize_webui_turn_id(value)}

    def prepare_event(
        self,
        chat_id: str,
        event: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
        phase: str | None = None,
        include_source: bool = False,
    ) -> None:
        # 在原地（mutate event）补全来源标记与轮次标注；不落盘。
        if include_source and (source := webui_message_source(metadata)):
            event["source"] = source
        self._annotate_turn(chat_id, event, metadata, phase)

    def prepare_and_append(
        self,
        chat_id: str,
        event: dict[str, Any],
        *,
        metadata: dict[str, Any] | None = None,
        phase: str | None = None,
        include_source: bool = False,
        transcript_overrides: dict[str, Any] | None = None,
    ) -> None:
        # 先标注事件，再合并转录专用字段（transcript_overrides，不发给前端），最后落盘。
        self.prepare_event(
            chat_id,
            event,
            metadata=metadata,
            phase=phase,
            include_source=include_source,
        )
        record = dict(event)
        if transcript_overrides:
            record.update(transcript_overrides)
        self.append(chat_id, record)

    def append_user_message(
        self,
        chat_id: str,
        text: str,
        *,
        metadata: dict[str, Any],
        media_paths: list[str] | None = None,
        cli_apps: list[dict[str, Any]] | None = None,
        mcp_presets: list[dict[str, Any]] | None = None,
    ) -> None:
        # "/stop" 指令（无附件）仅作控制信号，不入转录，避免在历史中留下噪声。
        if text.strip() == "/stop" and not media_paths:
            return
        payload = build_user_transcript_event(
            chat_id,
            text,
            media_paths=media_paths,
            cli_apps=cli_apps,
            mcp_presets=mcp_presets,
        )
        if payload is None:
            return
        self.prepare_and_append(chat_id, payload, metadata=metadata, phase="user")

    def append(self, chat_id: str, event: dict[str, Any]) -> None:
        try:
            # 深拷贝事件后再落盘，避免后续对 event 的改动影响已持久化的记录。
            dup = json.loads(json.dumps(event, ensure_ascii=False))
            append_transcript_object(f"websocket:{chat_id}", dup)
        except (OSError, ValueError, TypeError) as e:
            # 转录写入失败不应中断主流程，仅告警。
            self._log.warning("webui transcript append failed: {}", e)

    def _next_turn_seq(self, chat_id: str, turn_id: str) -> int:
        # 为同一 (chat_id, turn_id) 分配递增序号，保证一轮内事件可按序排列。
        key = (chat_id, turn_id)
        seq = self._turn_sequences.get(key, 0) + 1
        self._turn_sequences[key] = seq
        return seq

    def _annotate_turn(
        self,
        chat_id: str,
        event: dict[str, Any],
        metadata: dict[str, Any] | None,
        phase: str | None,
    ) -> None:
        # 仅当给定 phase 且 metadata 中有合法 turn_id 时才标注轮次信息。
        # phase == "complete" 表示轮次结束，弹出该 turn 的序号状态以释放内存。
        if phase is None:
            return
        turn_id = (metadata or {}).get(WEBUI_TURN_METADATA_KEY)
        if not isinstance(turn_id, str) or not turn_id:
            return
        event["turn_id"] = turn_id
        event["turn_phase"] = phase
        event["turn_seq"] = self._next_turn_seq(chat_id, turn_id)
        if phase == "complete":
            self._turn_sequences.pop((chat_id, turn_id), None)


def _chat_id_from_session_key(session_key: str) -> str | None:
    # 从 "websocket:{chat_id}" 形式的 session_key 中还原 chat_id；
    # 非该前缀或 chat_id 为空时返回 None。
    if not session_key.startswith("websocket:"):
        return None
    chat_id = session_key.split(":", 1)[1].strip()
    return chat_id or None


def _is_user_transcript_row(row: dict[str, Any]) -> bool:
    # 判定一行是否为用户消息：兼容新旧两种写法（event="user" 或 role="user"）。
    return row.get("event") == "user" or row.get("role") == "user"


def fork_transcript_before_user_index(
    source_key: str,
    target_key: str,
    before_user_index: int,
) -> bool:
    """Copy transcript rows before a zero-based global user-message index.

    ``before_user_index == user_count`` copies the full transcript prefix. WebUI
    uses that when forking from an assistant reply at the end of a chat.

    中文说明：在源会话转录中定位第 ``before_user_index`` 条用户消息，将其之前
    （不含该条）的全部记录复制到目标会话，用于"从此处分叉新对话"。复制时跳过
    fork_marker 标记，并把 chat_id 改写为目标会话的 chat_id。若用户消息序号
    越界则返回 False 表示分叉失败。
    """
    if before_user_index < 0:
        return False
    lines = read_transcript_lines(source_key)
    if not lines:
        return False

    target_chat_id = _chat_id_from_session_key(target_key)
    copied: list[dict[str, Any]] = []
    user_index = 0
    found_target = False
    for row in lines:
        # 已存在的 fork_marker 不复制，避免新会话中残留旧的分叉边界。
        if row.get("event") == WEBUI_FORK_MARKER_EVENT:
            continue
        if _is_user_transcript_row(row):
            # 命中目标用户消息：停止复制，该条及之后的内容不进入新会话。
            if user_index == before_user_index:
                found_target = True
                break
            user_index += 1
        # 深拷贝避免源与目标共享可变对象。
        dup = json.loads(json.dumps(row, ensure_ascii=False))
        if target_chat_id is not None:
            dup["chat_id"] = target_chat_id
        copied.append(dup)
    # 处理目标恰好在转录末尾（无后续行）的情况：循环结束后再判定一次。
    if user_index == before_user_index:
        found_target = True

    if not found_target:
        return False

    _write_transcript_lines(target_key, copied)
    return True


def append_fork_marker(session_key: str) -> None:
    """Mark the UI-only boundary where a WebUI fork starts accepting new turns.

    中文说明：在转录中追加一个 ``fork_marker`` 事件，标记此后为分叉新会话接续
    的新轮次起点。该标记仅用于 UI 展示边界，不参与回放为消息。
    """
    append_transcript_object(
        session_key,
        {
            "event": WEBUI_FORK_MARKER_EVENT,
            "chat_id": _chat_id_from_session_key(session_key),
        },
    )


def write_session_messages_as_transcript(
    target_key: str,
    messages: list[dict[str, Any]],
) -> None:
    """Write a minimal WebUI transcript from already-truncated session messages.

    中文说明：从会话历史（已截断）构造一份最小化的 WebUI 转录。仅保留 user 与
    assistant 文本及媒体路径，丢弃工具调用等中间过程；用于无转录时从会话历史
    回填一条可展示的线程。``media`` 字段在 user 行存为 ``media_paths``，在
    assistant 行存为 ``media``，与转录事件 schema 保持一致。
    """
    target_chat_id = _chat_id_from_session_key(target_key)
    rows: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        text = content if isinstance(content, str) else ""
        if role == "user":
            row: dict[str, Any] = {"event": "user", "chat_id": target_chat_id, "text": text}
            media = msg.get("media")
            if isinstance(media, list) and media:
                row["media_paths"] = [str(p) for p in media if isinstance(p, str) and p]
            for key in ("cli_apps", "mcp_presets"):
                value = msg.get(key)
                if isinstance(value, list) and value:
                    row[key] = json.loads(json.dumps(value, ensure_ascii=False))
        elif role == "assistant" and text.strip():
            # 跳过空文本的 assistant 消息，避免出现空白气泡。
            row = {"event": "message", "chat_id": target_chat_id, "text": text}
            media = msg.get("media")
            if isinstance(media, list) and media:
                row["media"] = [str(p) for p in media if isinstance(p, str) and p]
        else:
            continue
        rows.append(row)
    _write_transcript_lines(target_key, rows)


def delete_webui_transcript(session_key: str) -> bool:
    # 删除指定会话的全部转录产物：活跃 JSONL、旧版 .json 线程文件、归档段目录。
    # 任一文件删除失败仅告警不抛异常，尽量清理其余文件。
    removed = False
    for path in (webui_transcript_path(session_key), _legacy_webui_thread_path(session_key)):
        if not path.is_file():
            continue
        try:
            path.unlink()
            removed = True
        except OSError as e:
            logger.warning("Failed to delete webui transcript {}: {}", path, e)
    segments_dir = webui_transcript_segments_dir(session_key)
    if segments_dir.is_dir():
        try:
            shutil.rmtree(segments_dir)
            removed = True
        except OSError as e:
            logger.warning("Failed to delete webui transcript segments {}: {}", segments_dir, e)
    return removed


def build_user_transcript_event(
    chat_id: str,
    text: str,
    *,
    media_paths: list[Any] | None = None,
    cli_apps: list[Any] | None = None,
    mcp_presets: list[Any] | None = None,
) -> dict[str, Any] | None:
    # 构造一条 user 事件记录；文本与媒体均为空时返回 None（不入转录）。
    # cli_apps / mcp_presets 仅在为 Mapping 时浅拷贝为 dict，过滤非法项。
    paths = [str(path) for path in (media_paths or []) if path]
    if not text and not paths:
        return None
    event: dict[str, Any] = {
        "event": "user",
        "chat_id": chat_id,
        "text": text,
    }
    if paths:
        event["media_paths"] = paths
    apps = [dict(app) for app in (cli_apps or []) if isinstance(app, Mapping)]
    if apps:
        event["cli_apps"] = apps
    presets = [dict(preset) for preset in (mcp_presets or []) if isinstance(preset, Mapping)]
    if presets:
        event["mcp_presets"] = presets
    return event


def _session_user_event(
    session_key: str,
    message: dict[str, Any],
) -> dict[str, Any] | None:
    # 从会话历史中的一条消息提取 user 事件；非 user 消息或 cron 历史元消息返回 None。
    # chat_id 取 session_key 中冒号后的部分（与转录事件 schema 对齐）。
    if message.get("role") != "user":
        return None
    if message.get(CRON_HISTORY_META) is True:
        return None
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    media = message.get("media")
    cli_apps = message.get("cli_apps")
    mcp_presets = message.get("mcp_presets")
    chat_id = session_key.split(":", 1)[1] if ":" in session_key else session_key
    return build_user_transcript_event(
        chat_id,
        text,
        media_paths=media if isinstance(media, list) else None,
        cli_apps=cli_apps if isinstance(cli_apps, list) else None,
        mcp_presets=mcp_presets if isinstance(mcp_presets, list) else None,
    )


def _assistant_text_signature(value: Any) -> str:
    # 取 assistant 消息文本的去除首尾空白形式作为"签名"，用于跨会话/转录匹配同一轮。
    return value.strip() if isinstance(value, str) else ""


def _session_backfill_turns(
    session_key: str,
    session_messages: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    # 把会话历史折叠成 (user_event, assistant_texts) 列表，每项代表一轮。
    # 一轮 = 一条 user 消息 + 其后零或多条 assistant 消息；用于回填缺失用户消息时
    # 通过 assistant 文本签名与转录轮次对齐。
    turns: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    current_user: dict[str, Any] | None = None
    assistant_texts: list[str] = []

    def flush() -> None:
        # 结束当前轮：仅当存在 user 且至少一条非空 assistant 文本时才记录。
        if current_user is None:
            return
        signature = tuple(text for text in assistant_texts if text)
        if signature:
            turns.append((current_user, signature))

    for message in session_messages:
        role = message.get("role")
        if role == "user":
            # 遇到新 user：先 flush 上一轮，再开启新一轮。
            flush()
            current_user = _session_user_event(session_key, message)
            assistant_texts = []
            continue
        if role == "assistant" and current_user is not None:
            text = _assistant_text_signature(message.get("content"))
            if text:
                assistant_texts.append(text)
    flush()
    return turns


def _split_transcript_turns(lines: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    # 按 turn_end 事件把转录记录切成多轮；末尾若没有 turn_end 也作为一轮返回。
    # 一轮包含其内部全部事件（user/delta/reasoning/tool/...）直到并含 turn_end。
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for rec in lines:
        current.append(rec)
        if rec.get("event") == "turn_end":
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


def _transcript_turn_signature(records: list[dict[str, Any]]) -> tuple[str, ...]:
    # 把转录中一轮 replay 成 UI 消息后，取全部非 trace 的 assistant 文本签名元组，
    # 用于与会话历史中的轮次签名匹配（见 _find_unique_session_turn）。
    texts: list[str] = []
    for message in replay_transcript_to_ui_messages(records):
        if message.get("role") != "assistant" or message.get("kind") == "trace":
            continue
        text = _assistant_text_signature(message.get("content"))
        if text:
            texts.append(text)
    return tuple(texts)


def _find_unique_session_turn(
    session_turns: list[tuple[dict[str, Any], tuple[str, ...]]],
    signature: tuple[str, ...],
    start: int,
) -> int | None:
    # 在 session_turns 的 start 起始位置之后查找与 signature 完全匹配的轮次。
    # 仅当匹配唯一时返回索引；匹配零次或多次均返回 None（无法可靠对齐时跳过）。
    if not signature:
        return None
    found: int | None = None
    for index in range(start, len(session_turns)):
        if session_turns[index][1] != signature:
            continue
        if found is not None:
            # 出现第二个匹配，无法唯一确定，放弃。
            return None
        found = index
    return found


def _with_backfilled_user(
    records: list[dict[str, Any]],
    user_event: dict[str, Any],
) -> list[dict[str, Any]]:
    # 在一轮记录的首个"展示事件"之前插入回填的 user 事件，使旧转录（缺 user 行）
    # 也能正确显示用户提问。展示事件集合见 _TURN_DISPLAY_EVENTS。
    for index, rec in enumerate(records):
        if rec.get("event") in _TURN_DISPLAY_EVENTS:
            return [*records[:index], dict(user_event), *records[index:]]
    return records


def inject_missing_user_events_from_session(
    session_key: str,
    lines: list[dict[str, Any]],
    session_messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Backfill user rows for legacy WebUI transcripts that only stored assistant streams.

    中文说明：旧版 WebUI 转录只存了 assistant 流式事件、未存 user 消息。本函数
    通过 assistant 文本签名把会话历史中的用户消息与转录轮次一一匹配，对缺失
    user 行的轮次插入回填的 user 事件。匹配失败（签名不唯一/无匹配）的轮次
    保持原样，避免错位。``session_cursor`` 单调推进以保证 O(n) 对齐。
    """
    if not lines or not session_messages:
        return lines
    session_turns = _session_backfill_turns(session_key, session_messages)
    if not session_turns:
        return lines

    out: list[dict[str, Any]] = []
    session_cursor = 0
    for turn in _split_transcript_turns(lines):
        has_user = any(rec.get("event") == "user" for rec in turn)
        signature = _transcript_turn_signature(turn)
        match_index = _find_unique_session_turn(session_turns, signature, session_cursor)
        if match_index is None:
            # 无法唯一对齐：原样保留该轮，避免错位回填。
            out.extend(turn)
            continue
        # 已有 user 行则保留；否则在轮首插入回填的 user 事件。
        out.extend(turn if has_user else _with_backfilled_user(turn, session_turns[match_index][0]))
        session_cursor = match_index + 1
    return out


def _format_tool_call_trace(call: Any) -> str | None:
    # 把一次工具调用格式化为 "name(args)" 形式的可读 trace 行；无有效名称返回 None。
    # 兼容 OpenAI 风格 {function:{name,arguments}} 与扁平 {name,arguments} 两种结构。
    if not call or not isinstance(call, dict):
        return None
    fn = call.get("function")
    name = fn.get("name") if isinstance(fn, dict) else None
    if not isinstance(name, str) or not name:
        raw_name = call.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
    if not name:
        return None
    args = (fn.get("arguments") if isinstance(fn, dict) else None) or call.get("arguments")
    # 字符串参数直接拼接（通常是已序列化的 JSON）；dict 参数序列化为 JSON。
    if isinstance(args, str) and args.strip():
        return f"{name}({args})"
    if args and isinstance(args, dict):
        return f"{name}({json.dumps(args, ensure_ascii=False)})"
    return f"{name}()"


def tool_trace_lines_from_events(events: Any) -> list[str]:
    # 从一组工具事件生成去重后的 trace 行列表。按 call_id 去重（同一调用只保留首次），
    # 无 call_id 的事件不做去重。仅处理 phase 为 start/end/error 的事件。
    if not isinstance(events, list):
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for event in events:
        if not event or not isinstance(event, dict):
            continue
        if event.get("phase") not in {"start", "end", "error"}:
            continue
        call_id = event.get("call_id")
        if isinstance(call_id, str) and call_id:
            if call_id in seen:
                continue
            seen.add(call_id)
        t = _format_tool_call_trace(event)
        if t:
            lines.append(t)
    return lines


# 工具事件阶段的前后顺序权重：end/error 视为更"晚期"，合并时覆盖 start。
_PHASE_RANK = {"start": 1, "end": 2, "error": 3}


def _normalize_tool_events(events: Any) -> list[dict[str, Any]]:
    # 过滤并浅拷贝工具事件：仅保留 phase∈{start,end,error} 且有有效名称的事件。
    # 兼容 OpenAI 风格 {function:{name}} 与扁平 {name} 两种来源。
    if not isinstance(events, list):
        return []
    out: list[dict[str, Any]] = []
    for event in events:
        if not event or not isinstance(event, dict):
            continue
        if event.get("phase") not in {"start", "end", "error"}:
            continue
        if not isinstance(event.get("name"), str):
            fn = event.get("function")
            if not (isinstance(fn, dict) and isinstance(fn.get("name"), str)):
                continue
        out.append(dict(event))
    return out


def _tool_event_key(event: dict[str, Any]) -> str:
    # 工具事件的去重键：优先用 call_id；无 call_id 时退化为 trace 文本，
    # 再不行用整体 JSON。保证同一调用的多次事件能归并到同一键。
    call_id = event.get("call_id")
    if isinstance(call_id, str) and call_id:
        return f"call:{call_id}"
    return _format_tool_call_trace(event) or json.dumps(event, sort_keys=True, ensure_ascii=False)


def _tool_event_file_edit_key(event: dict[str, Any]) -> str | None:
    # 仅对文件编辑类工具事件生成键（call_id|tool_name），用于与 file_edit 事件对齐去重。
    # 非文件编辑工具或缺少 call_id 返回 None。
    call_id = event.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        return None
    name = event.get("name")
    if not isinstance(name, str) or not name:
        fn = event.get("function")
        name = fn.get("name") if isinstance(fn, dict) else ""
    if not isinstance(name, str) or name not in _FILE_EDIT_TOOL_NAMES:
        return None
    return f"{call_id}|{name}"


def _merge_tool_events(previous: Any, incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 合并已有工具事件与新事件：按 _tool_event_key 归并，新事件阶段权重 >= 旧事件时覆盖。
    # 这使得同一调用的 start -> end -> error 演进能正确更新而非重复堆积。
    if not isinstance(previous, list) or not previous:
        return incoming
    if not incoming:
        return [dict(event) for event in previous if isinstance(event, dict)]
    merged = [dict(event) for event in previous if isinstance(event, dict)]
    index_by_key = {_tool_event_key(event): idx for idx, event in enumerate(merged)}
    for event in incoming:
        key = _tool_event_key(event)
        existing_index = index_by_key.get(key)
        if existing_index is None:
            # 新调用：追加。
            index_by_key[key] = len(merged)
            merged.append(event)
            continue
        existing = merged[existing_index]
        incoming_rank = _PHASE_RANK.get(str(event.get("phase")), 0)
        existing_rank = _PHASE_RANK.get(str(existing.get("phase")), 0)
        # 仅当新事件阶段不早于旧事件时合并覆盖，避免用 start 倒退覆盖已 end 的事件。
        if incoming_rank >= existing_rank:
            merged[existing_index] = {**existing, **event}
    return merged


def _file_edit_key(edit: dict[str, Any]) -> str:
    # 文件编辑的去重键：优先 call_id|tool，否则 tool|path。
    # 同一调用的多次编辑（如多次 patch 同一文件）应归并到同一条 trace。
    call_id = str(edit.get("call_id") or "")
    tool = str(edit.get("tool") or "")
    if call_id:
        return f"{call_id}|{tool}"
    return f"{tool}|{edit.get('path') or ''}"


def _message_has_file_edit_for_tool_event(
    message: dict[str, Any],
    event: dict[str, Any],
) -> bool:
    # 判定某条 trace 消息已包含与该工具事件对应的 file_edit（按 call_id|tool 对齐）。
    # 用于在回放时剔除已被 file_edit 事件覆盖的工具提示，避免重复展示。
    key = _tool_event_file_edit_key(event)
    if not key:
        return False
    edits = message.get("fileEdits")
    if not isinstance(edits, list):
        return False
    return any(isinstance(edit, dict) and _file_edit_key(edit) == key for edit in edits)


def _filter_covered_file_edit_tool_events(
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # 过滤掉那些已被既有消息中 file_edit 覆盖的工具事件，避免在 trace 中重复展示
    # 同一文件编辑的"调用提示"与"编辑结果"。
    if not events:
        return events
    return [
        event
        for event in events
        if not any(_message_has_file_edit_for_tool_event(message, event) for message in messages)
    ]


def _strip_covered_file_edit_tool_hints(
    message: dict[str, Any],
    edits: list[dict[str, Any]],
) -> dict[str, Any]:
    # 当新的 file_edit 事件到达时，从既有的 trace 消息中移除被其覆盖的工具事件提示，
    # 同时清理对应的 trace 文本行，避免同一编辑同时以"工具调用"和"文件编辑"两种形态出现。
    incoming_keys = {
        _file_edit_key(edit)
        for edit in edits
        if isinstance(edit, dict)
    }
    events = message.get("toolEvents")
    if not incoming_keys or not isinstance(events, list):
        return message

    kept_events: list[dict[str, Any]] = []
    removed_trace_lines: set[str] = set()
    changed = False
    for event in events:
        if not isinstance(event, dict):
            continue
        key = _tool_event_file_edit_key(event)
        if key and key in incoming_keys:
            # 该工具事件被新到的 file_edit 覆盖，移除并记录其 trace 行以便后续剔除。
            changed = True
            removed_trace_lines.update(tool_trace_lines_from_events([event]))
            continue
        kept_events.append(event)
    if not changed:
        return message

    # 同步清理 traces 列表与 content（content 取最后一条 trace 文本）。
    raw_traces = message.get("traces")
    if isinstance(raw_traces, list):
        previous_traces = [trace for trace in raw_traces if isinstance(trace, str)]
    else:
        content = message.get("content")
        previous_traces = [content] if isinstance(content, str) and content else []
    next_traces = [trace for trace in previous_traces if trace not in removed_trace_lines]
    next_message = {
        **message,
        "traces": next_traces,
        "content": next_traces[-1] if next_traces else "",
    }
    if kept_events:
        next_message["toolEvents"] = kept_events
    else:
        # 无剩余工具事件时移除该字段，保持消息整洁。
        next_message.pop("toolEvents", None)
    return next_message


def _merge_unique_tool_trace_lines(
    previous_traces: list[str],
    lines: list[str],
) -> tuple[list[str], bool]:
    # 把新 trace 行按顺序追加到 previous_traces，跳过已存在的行（按文本去重）。
    # 返回合并后的列表与是否有新增；调用方据此判断是否需要更新消息。
    seen_lines = set(previous_traces)
    traces = list(previous_traces)
    added = False
    for line in lines:
        if line in seen_lines:
            continue
        seen_lines.add(line)
        traces.append(line)
        added = True
    return traces, added


def _media_from_signed_urls(value: Any) -> list[dict[str, Any]]:
    # 从 media_urls 列表构造前端期望的 media 对象列表（含 kind/url/name）。
    # kind 由文件扩展名推断；仅保留含 url 的项。
    media: list[dict[str, Any]] = []
    urls = value if isinstance(value, list) else []
    for m in urls:
        if isinstance(m, dict) and m.get("url"):
            name = str(m.get("name") or "")
            media.append(
                {
                    "kind": _media_kind_from_name(name),
                    "url": str(m["url"]),
                    "name": name,
                },
            )
    return media


def replay_transcript_to_ui_messages(
    lines: list[dict[str, Any]],
    *,
    augment_user_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_text: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Fold JSONL records into ``UIMessage``-shaped dicts for the WebUI.

    Mirrors the core fold in ``useNanobotStream.ts`` (delta, reasoning,
    message+kind, turn_end). ``augment_user_media`` maps persisted filesystem
    paths to ``{url, name?}`` / attachment dicts the client expects. Assistant
    media gets a separate hook so replay can re-sign outbound attachments after
    a gateway restart instead of reusing stale process-local signed URLs.

    中文说明：把追加式的 JSONL 转录事件"折叠"成前端 ``UIMessage`` 列表。这是
    转录模块的核心状态机，刻意与前端 ``useNanobotStream.ts`` 中的实时折叠逻辑
    保持一致，使"历史回放"与"实时接收"呈现一致的结果。关键状态：

    - ``buffer_message_id`` / ``buffer_parts``：流式 delta 累积缓冲。多个 delta 分块
      先拼到 ``buffer_parts``，整体写入对应的占位 assistant 消息，避免每帧都重建大字符串。
    - ``suppress_until_turn_end``：一旦本轮已发出带媒体的最终 message，则抑制其后到
      turn_end 之前的进度/工具提示，防止媒体消息被后续噪声覆盖。
    - ``active_activity_segment_id`` / ``active_file_edit_segment_id``：当前"活动段"标识，
      用于把同一轮内的多个工具调用/文件编辑/推理归到同一前端折叠组（activitySegmentId）。
    - ``closed_turn_ids`` / ``replay_turn_aliases``：处理同一转录中出现重复 turn_id 的
      情况（如分叉后回放），为重复的 turn_id 生成别名以避免前端按 turnId 错误合并。
    """
    messages: list[dict[str, Any]] = []
    buffer_message_id: str | None = None
    buffer_parts: list[str] = []
    suppress_until_turn_end = False
    active_activity_segment_id: str | None = None
    active_file_edit_segment_id: str | None = None
    activity_segment_counter = 0
    # 时间戳基准：用当前毫秒时间 + 记录索引构造单调递增的 createdAt，保证回放消息顺序稳定。
    _ts_base = int(time.time() * 1000)
    closed_turn_ids: set[str] = set()
    replay_turn_aliases: dict[str, str] = {}

    def _new_id(prefix: str, idx: int) -> str:
        # 生成带前缀与索引的稳定 id，末尾追加随机短串避免同索引碰撞。
        return f"{prefix}-{idx}-{uuid.uuid4().hex[:8]}"

    def _new_activity_segment(*, activate: bool = True) -> str:
        nonlocal active_activity_segment_id, activity_segment_counter
        activity_segment_counter += 1
        segment_id = f"activity-{activity_segment_counter}"
        if activate:
            active_activity_segment_id = segment_id
        return segment_id

    def _turn_fields(rec: dict[str, Any], fallback_phase: str | None = None) -> dict[str, Any]:
        # 提取记录上的轮次字段（turnId/turnPhase/turnSeq），供前端按轮次分组与排序。
        # 对已关闭的重复 turn_id 生成别名，避免不同轮次因 turnId 相同被前端误合并。
        fields: dict[str, Any] = {}
        turn_id = rec.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            if turn_id in closed_turn_ids:
                fields["turnId"] = replay_turn_aliases.setdefault(
                    turn_id,
                    f"{turn_id}:replay:{idx}",
                )
            else:
                fields["turnId"] = turn_id
        phase = rec.get("turn_phase")
        if isinstance(phase, str) and phase:
            fields["turnPhase"] = phase
        elif fallback_phase:
            fields["turnPhase"] = fallback_phase
        seq = rec.get("turn_seq")
        if isinstance(seq, (int, float)):
            fields["turnSeq"] = int(seq)
        return fields

    def _source_fields(rec: dict[str, Any]) -> dict[str, Any]:
        # 提取 cron 来源标记字段（与 webui_message_source 对应）。
        source = rec.get("source")
        if not isinstance(source, dict) or source.get("kind") != "cron":
            return {}
        out: dict[str, Any] = {"source": {"kind": "cron"}}
        label = source.get("label")
        if isinstance(label, str) and label.strip():
            out["source"]["label"] = label.strip()
        return out

    def _same_turn(message: dict[str, Any], turn_fields: dict[str, Any]) -> bool:
        # 判定消息与当前轮次是否属于同一轮：任一方缺 turnId 视为同轮（保守合并），
        # 否则要求 turnId 相等。
        turn_id = turn_fields.get("turnId")
        message_turn_id = message.get("turnId")
        return not turn_id or not message_turn_id or turn_id == message_turn_id

    def _ensure_activity_segment() -> str:
        # 复用当前活动段；无则新建并激活，保证一组相关事件归到同一 segment。
        return active_activity_segment_id or _new_activity_segment()

    def close_activity_for_answer() -> None:
        # assistant 开始产出正式回答（delta/message）时，结束此前的工作活动段，
        # 使工具调用等归到前一段、回答归到新段。
        nonlocal active_activity_segment_id, active_file_edit_segment_id
        active_activity_segment_id = None
        active_file_edit_segment_id = None

    def close_file_edit_phase_before_activity() -> None:
        # 推理块到达时若处于文件编辑段，先结束该段，使后续推理/活动归到新段。
        nonlocal active_activity_segment_id, active_file_edit_segment_id
        if active_file_edit_segment_id:
            active_activity_segment_id = None
            active_file_edit_segment_id = None

    def attach_reasoning_chunk(
        prev: list[dict[str, Any]],
        chunk: str,
        idx: int,
        turn_fields: dict[str, Any] | None = None,
    ) -> None:
        # 把一块推理文本追加到合适的 assistant 消息上。从末尾向前寻找可承接的候选：
        # 遇到 user/trace 或跨轮消息即停止；找到正在流式推理/已有推理/有正文/流式中的
        # assistant 消息就追加到其 reasoning；否则新建一条纯推理占位消息。
        turn_fields = turn_fields or {}
        for i in range(len(prev) - 1, -1, -1):
            candidate = prev[i]
            if candidate.get("role") == "user":
                break
            if candidate.get("kind") == "trace":
                break
            if candidate.get("role") != "assistant":
                continue
            if not _same_turn(candidate, turn_fields):
                break
            content = str(candidate.get("content") or "")
            has_answer = len(content) > 0
            if (
                candidate.get("reasoningStreaming")
                or candidate.get("reasoning") is not None
                or has_answer
                or candidate.get("isStreaming")
            ):
                # 追加到既有 reasoning，并标记 reasoningStreaming 以便后续 close。
                prev[i] = {
                    **candidate,
                    "reasoning": (str(candidate.get("reasoning") or "")) + chunk,
                    "reasoningStreaming": True,
                    "activitySegmentId": candidate.get("activitySegmentId") or _ensure_activity_segment(),
                    **turn_fields,
                }
                return
            if not has_answer and candidate.get("isStreaming"):
                # 占位流式消息（无正文）转为承载推理。
                prev[i] = {
                    **candidate,
                    "reasoning": chunk,
                    "reasoningStreaming": True,
                    "activitySegmentId": candidate.get("activitySegmentId") or _ensure_activity_segment(),
                    **turn_fields,
                }
                return
            break
        # 无可承接的候选：新建一条纯推理 assistant 消息。
        segment = _ensure_activity_segment()
        prev.append(
            {
                "id": _new_id("as", idx),
                "role": "assistant",
                "content": "",
                "isStreaming": True,
                "reasoning": chunk,
                "reasoningStreaming": True,
                "activitySegmentId": segment,
                **turn_fields,
                "createdAt": _ts_base + idx,
            },
        )

    def find_active_placeholder(
        prev: list[dict[str, Any]],
        turn_fields: dict[str, Any] | None = None,
    ) -> str | None:
        # 寻找可复用的"活跃占位"assistant 消息：必须是同轮、无正文、仍在流式中的
        # 非 trace 消息。存在则返回其 id，使后续 delta 复用而非新建消息。
        turn_fields = turn_fields or {}
        last = prev[-1] if prev else None
        if not last:
            return None
        if last.get("role") != "assistant" or last.get("kind") == "trace":
            return None
        if str(last.get("content") or ""):
            return None
        if not last.get("isStreaming"):
            return None
        if not _same_turn(last, turn_fields):
            return None
        return str(last.get("id"))

    def demote_interrupted_assistant(segment: str) -> None:
        # 当工具调用/文件编辑到达时，把上一条"被中断"的流式 assistant 占位（仅有正文、
        # 无媒体、仍在流式）降级：正文并入 reasoning，清空 content 并停止流式标记。
        # 这样工具活动不会附在被截断的回答气泡上，而是独立成段。
        nonlocal buffer_message_id, buffer_parts
        for i in range(len(messages) - 1, -1, -1):
            candidate = messages[i]
            if candidate.get("role") == "user":
                break
            content = candidate.get("content")
            if (
                candidate.get("role") != "assistant"
                or candidate.get("kind") == "trace"
                or not candidate.get("isStreaming")
                or not isinstance(content, str)
                or not content.strip()
                or candidate.get("media")
            ):
                continue
            reasoning_parts = [
                part
                for part in (candidate.get("reasoning"), content)
                if isinstance(part, str) and part.strip()
            ]
            messages[i] = {
                **candidate,
                "content": "",
                "reasoning": "\n\n".join(reasoning_parts),
                "reasoningStreaming": False,
                "isStreaming": False,
                "activitySegmentId": candidate.get("activitySegmentId") or segment,
            }
            # 该被降级的消息正是当前 delta 缓冲所指向的占位：重置缓冲，避免后续 delta 写入。
            if buffer_message_id == candidate.get("id"):
                buffer_message_id = None
                buffer_parts = []
            return

    def close_reasoning(prev: list[dict[str, Any]]) -> None:
        # reasoning_end：从末尾向前找到第一条仍在 reasoningStreaming 的消息并关闭标记。
        for i in range(len(prev) - 1, -1, -1):
            if prev[i].get("reasoningStreaming"):
                prev[i] = {**prev[i], "reasoningStreaming": False}
                return

    def is_reasoning_only_placeholder(m: dict[str, Any]) -> bool:
        # 判定是否为"纯推理占位"：assistant、非 trace、无正文、有推理、已停止流式、无媒体。
        # 这类消息若后续没有工具 trace 跟进，将被剪除（见 prune_reasoning_only）。
        return (
            m.get("role") == "assistant"
            and m.get("kind") != "trace"
            and not str(m.get("content") or "").strip()
            and bool(m.get("reasoning"))
            and not m.get("reasoningStreaming")
            and not m.get("media")
        )

    def is_tool_trace_at(index: int) -> bool:
        m = messages[index] if 0 <= index < len(messages) else None
        return bool(m and m.get("kind") == "trace")

    def prune_reasoning_only() -> None:
        # turn_end 时剪除"孤立"的纯推理占位：若其后没有工具 trace 跟进，则该推理
        # 未引出任何可见活动，删除以保持消息列表整洁。
        nonlocal messages
        kept: list[dict[str, Any]] = []
        for i, m in enumerate(messages):
            if is_reasoning_only_placeholder(m) and not is_tool_trace_at(i + 1):
                continue
            kept.append(m)
        messages = kept

    def stamp_latency(latency_ms: int) -> None:
        # 把本轮延迟盖到最近一条非 trace 的 assistant 消息上，并关闭其流式标记。
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and messages[i].get("kind") != "trace":
                messages[i] = {
                    **messages[i],
                    "latencyMs": latency_ms,
                    "isStreaming": False,
                }
                return

    def absorb_complete(extra: dict[str, Any], idx: int) -> None:
        # 处理一条完整 assistant 消息（message 事件）：若末尾是同轮的纯推理占位，
        # 则就地吸收（把推理与正文合到同一条）；否则追加新消息。完成后结束活动段。
        nonlocal active_activity_segment_id, active_file_edit_segment_id
        last = messages[-1] if messages else None
        if last and is_reasoning_only_placeholder(last) and _same_turn(last, extra):
            messages[-1] = {
                **last,
                **extra,
                "isStreaming": False,
                "reasoningStreaming": False,
            }
        else:
            messages.append(
                {
                    "id": _new_id("as", idx),
                    "role": "assistant",
                    "createdAt": _ts_base + idx,
                    **extra,
                },
            )
        active_activity_segment_id = None
        active_file_edit_segment_id = None

    def find_file_edit_trace_index(
        segment: str | None,
        edits: list[dict[str, Any]],
    ) -> int | None:
        # 从末尾向前查找应承接本次 file_edit 的既有 trace 消息：遇到 user 即止；
        # 同 segment 的 trace、或含相同 file_edit / 工具事件键的 trace 视为同一组，
        # 返回其索引以便合并，而非新建一条。
        incoming_keys = {_file_edit_key(edit) for edit in edits if isinstance(edit, dict)}
        for i in range(len(messages) - 1, -1, -1):
            candidate = messages[i]
            if candidate.get("role") == "user":
                break
            if candidate.get("kind") != "trace":
                continue
            if segment and candidate.get("activitySegmentId") == segment:
                return i
            existing_edits = candidate.get("fileEdits")
            if isinstance(existing_edits, list):
                for existing in existing_edits:
                    if isinstance(existing, dict) and _file_edit_key(existing) in incoming_keys:
                        return i
            existing_tool_events = candidate.get("toolEvents")
            if isinstance(existing_tool_events, list):
                for event in existing_tool_events:
                    if not isinstance(event, dict):
                        continue
                    key = _tool_event_file_edit_key(event)
                    if key and key in incoming_keys:
                        return i
        return None

    def upsert_file_edits(
        edits: list[dict[str, Any]],
        idx: int,
        turn_fields: dict[str, Any] | None = None,
    ) -> None:
        # 插入或合并一批文件编辑：先降级被中断的 assistant 占位，再查找可合并的既有
        # trace；找不到则新建一条 tool/trace 消息。随后按 _file_edit_key 合并各编辑项，
        # 已有项就地更新（path 已知且非 pending 时清除 pending 标记），新项追加。
        nonlocal active_file_edit_segment_id
        turn_fields = turn_fields or {}
        if not edits:
            return
        segment = active_file_edit_segment_id
        if not segment:
            segment = _new_activity_segment(activate=False)
            active_file_edit_segment_id = segment
        demote_interrupted_assistant(segment)
        target_index = find_file_edit_trace_index(segment, edits)
        if target_index is not None:
            last = messages[target_index]
            segment = str(last.get("activitySegmentId") or segment or _new_activity_segment(activate=False))
            active_file_edit_segment_id = segment
            # 移除被本次 file_edit 覆盖的旧工具事件提示，避免重复展示。
            last = _strip_covered_file_edit_tool_hints(last, edits)
        else:
            if not segment:
                segment = _new_activity_segment(activate=False)
            active_file_edit_segment_id = segment
            messages.append(
                {
                    "id": _new_id("tr", idx),
                    "role": "tool",
                    "kind": "trace",
                    "content": "",
                    "traces": [],
                    "fileEdits": [],
                    "activitySegmentId": segment,
                    **turn_fields,
                    "createdAt": _ts_base + idx,
                },
            )
            target_index = len(messages) - 1
            last = messages[target_index]
        if not segment:
            segment = _new_activity_segment(activate=False)
            active_file_edit_segment_id = segment
        existing = list(last.get("fileEdits") or [])
        index_by_key = {
            _file_edit_key(edit): pos
            for pos, edit in enumerate(existing)
            if isinstance(edit, dict)
        }
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            key = _file_edit_key(edit)
            if key in index_by_key:
                pos = index_by_key[key]
                merged = {**existing[pos], **edit}
                # 编辑已落盘（有 path 且非 pending）时清除 pending 占位标记。
                if edit.get("path") and not edit.get("pending"):
                    merged.pop("pending", None)
                existing[pos] = merged
            else:
                index_by_key[key] = len(existing)
                existing.append(dict(edit))
        messages[target_index] = {
            **last,
            "fileEdits": existing,
            "activitySegmentId": last.get("activitySegmentId") or segment,
            **turn_fields,
        }

    # 主事件循环：按事件类型把 JSONL 记录折叠进 messages。每类事件对应前端一种展示形态。
    for idx, rec in enumerate(lines):
        ev = rec.get("event")
        if ev == "user":
            # 用户消息：重置活动段（新一轮开始），构造 user 消息并可选挂载媒体/CLI/MCP 预设。
            active_activity_segment_id = None
            active_file_edit_segment_id = None
            text = rec.get("text")
            text_s = text if isinstance(text, str) else ""
            media_paths = rec.get("media_paths")
            paths: list[str] = []
            if isinstance(media_paths, list):
                paths = [str(p) for p in media_paths if p]
            media_att: list[dict[str, Any]] | None = None
            if paths and augment_user_media is not None:
                media_att = augment_user_media(paths)
            row: dict[str, Any] = {
                "id": _new_id("u", idx),
                "role": "user",
                "content": text_s,
                **_turn_fields(rec, "user"),
                "createdAt": _ts_base + idx,
            }
            if media_att:
                row["media"] = media_att
                # 全部为图片时额外填充 images 字段，供前端走图片渲染分支。
                if all(m.get("kind") == "image" for m in media_att):
                    row["images"] = [{"url": m.get("url"), "name": m.get("name")} for m in media_att]
            cli_apps = rec.get("cli_apps")
            if isinstance(cli_apps, list) and cli_apps:
                row["cliApps"] = [dict(app) for app in cli_apps if isinstance(app, dict)]
            mcp_presets = rec.get("mcp_presets")
            if isinstance(mcp_presets, list) and mcp_presets:
                row["mcpPresets"] = [
                    dict(preset) for preset in mcp_presets if isinstance(preset, dict)
                ]
            messages.append(row)
            continue

        if ev == "file_edit":
            # 文件编辑事件：交给 upsert_file_edits 合并到（或新建）一条 tool/trace 消息。
            raw_edits = rec.get("edits")
            if isinstance(raw_edits, list):
                upsert_file_edits(
                    [e for e in raw_edits if isinstance(e, dict)],
                    idx,
                    _turn_fields(rec, "activity"),
                )
            continue

        if ev == "delta":
            # 流式增量：累积到 buffer_parts，整体写入占位 assistant 消息。
            if suppress_until_turn_end:
                continue
            chunk = rec.get("text")
            if not isinstance(chunk, str):
                continue
            close_activity_for_answer()
            turn_fields = _turn_fields(rec, "answer")
            # 无缓冲时优先复用活跃占位；否则新建一条流式 assistant 消息作为缓冲目标。
            adopted = find_active_placeholder(messages, turn_fields) if buffer_message_id is None else None
            if buffer_message_id is None:
                if adopted:
                    buffer_message_id = adopted
                else:
                    buffer_message_id = _new_id("buf", idx)
                    messages.append(
                        {
                            "id": buffer_message_id,
                            "role": "assistant",
                            "content": "",
                            "isStreaming": True,
                            **_turn_fields(rec, "answer"),
                            "createdAt": _ts_base + idx,
                        },
                    )
            buffer_parts.append(chunk)
            combined = "".join(buffer_parts)
            # 把累积文本一次性写入缓冲目标消息；按 id 线性查找（消息数有限，开销可接受）。
            for i, m in enumerate(messages):
                if m.get("id") == buffer_message_id:
                    messages[i] = {
                        **m,
                        "content": combined,
                        "isStreaming": True,
                        **_turn_fields(rec, "answer"),
                    }
                    break
            continue

        if ev == "stream_end":
            # 流式结束：用最终文本替换缓冲内容（若有），随后清空缓冲。
            # 注意保留 isStreaming=True，由后续 turn_end 统一关闭，便于前端识别"刚到达"状态。
            if suppress_until_turn_end:
                buffer_message_id = None
                buffer_parts = []
                continue
            final_text = rec.get("text")
            if isinstance(final_text, str):
                if buffer_message_id is None:
                    buffer_message_id = _new_id("buf", idx)
                    messages.append(
                        {
                            "id": buffer_message_id,
                            "role": "assistant",
                            "content": final_text,
                            "isStreaming": True,
                            **_turn_fields(rec, "answer"),
                            "createdAt": _ts_base + idx,
                        },
                    )
                else:
                    for i, m in enumerate(messages):
                        if m.get("id") == buffer_message_id:
                            messages[i] = {
                                **m,
                                "content": final_text,
                                "isStreaming": True,
                                **_turn_fields(rec, "answer"),
                            }
                            break
            buffer_message_id = None
            buffer_parts = []
            continue

        if ev == "reasoning_delta":
            # 推理增量：追加到合适的 assistant 消息的 reasoning 字段。
            if suppress_until_turn_end:
                continue
            chunk = rec.get("text")
            if not isinstance(chunk, str) or not chunk:
                continue
            close_file_edit_phase_before_activity()
            attach_reasoning_chunk(messages, chunk, idx, _turn_fields(rec, "reasoning"))
            continue

        if ev == "reasoning_end":
            # 推理结束：关闭最近一条 reasoningStreaming 标记。
            if suppress_until_turn_end:
                continue
            close_reasoning(messages)
            continue

        if ev == "message":
            # 完整消息事件：按 kind 分流为 reasoning / tool_hint+progress / 最终回答。
            # suppress 期间仍允许最终回答（非 tool_hint/progress/reasoning）通过。
            if suppress_until_turn_end and rec.get("kind") in (
                "tool_hint",
                "progress",
                "reasoning",
            ):
                continue
            kind = rec.get("kind")
            if kind == "reasoning":
                # 整段推理文本：追加后立即关闭流式标记（非增量）。
                line = rec.get("text")
                if not isinstance(line, str) or not line:
                    continue
                close_file_edit_phase_before_activity()
                attach_reasoning_chunk(messages, line, idx, _turn_fields(rec, "reasoning"))
                close_reasoning(messages)
                continue
            if kind in ("tool_hint", "progress"):
                # 工具提示/进度：转为 tool/trace 消息。优先用结构化 tool_events 生成 trace 行，
                # 并剔除已被 file_edit 覆盖的事件，避免重复展示。
                structured_events = _normalize_tool_events(rec.get("tool_events"))
                visible_structured_events = _filter_covered_file_edit_tool_events(messages, structured_events)
                structured = tool_trace_lines_from_events(visible_structured_events)
                text = rec.get("text")
                if structured:
                    trace_lines = structured
                elif structured_events:
                    # 有结构化事件但无法格式化为 trace 行：保留消息但不显示文本行。
                    trace_lines = []
                elif isinstance(text, str) and text:
                    trace_lines = [text]
                else:
                    trace_lines = []
                if not trace_lines:
                    continue
                segment = _ensure_activity_segment()
                demote_interrupted_assistant(segment)
                last = messages[-1] if messages else None
                # 末尾是同段非流式 trace 时合并到该 trace，避免每条工具提示各占一条消息。
                if (
                    last
                    and last.get("kind") == "trace"
                    and not last.get("isStreaming")
                    and (last.get("activitySegmentId") in (None, segment))
                ):
                    prev_traces = list(last.get("traces") or [last.get("content")])
                    if structured:
                        merged_traces, added = _merge_unique_tool_trace_lines(prev_traces, structured)
                        # 既无新增 trace 行、又无结构化事件可合并时跳过，避免无意义更新。
                        if not added and not visible_structured_events:
                            continue
                    else:
                        merged_traces = prev_traces + trace_lines
                    merged = {
                        **last,
                        "traces": merged_traces,
                        "content": merged_traces[-1],
                        "toolEvents": _merge_tool_events(last.get("toolEvents"), visible_structured_events)
                        if visible_structured_events
                        else last.get("toolEvents"),
                        "activitySegmentId": last.get("activitySegmentId") or segment,
                        **_turn_fields(rec, "activity"),
                    }
                    messages[-1] = merged
                else:
                    messages.append(
                        {
                            "id": _new_id("tr", idx),
                            "role": "tool",
                            "kind": "trace",
                            "content": trace_lines[-1],
                            "traces": trace_lines,
                            **({"toolEvents": visible_structured_events} if visible_structured_events else {}),
                            "activitySegmentId": segment,
                            **_turn_fields(rec, "activity"),
                            "createdAt": _ts_base + idx,
                        },
                    )
                continue

            # 最终回答（无 kind 或其它 kind）：清空缓冲后构造完整 assistant 消息。
            buffer_message_id = None
            buffer_parts = []
            text = rec.get("text")
            content_s = text if isinstance(text, str) else ""
            media: list[dict[str, Any]] = []
            raw_media = rec.get("media")
            raw_media_list = raw_media if isinstance(raw_media, list) else []
            media_paths = [path for path in raw_media_list if isinstance(path, str) and path]
            # 优先用 augment_assistant_media 重新签名媒体路径（网关重启后旧签名失效）。
            if media_paths and augment_assistant_media is not None:
                media = augment_assistant_media(media_paths)
            # 无可签名路径时回退到 media_urls（已是签名 URL，如转录中固化的）。
            if not media and (not media_paths or augment_assistant_media is None):
                media = _media_from_signed_urls(rec.get("media_urls"))
            extra: dict[str, Any] = {"content": content_s}
            if media:
                extra["media"] = media
            lat = rec.get("latency_ms")
            if isinstance(lat, (int, float)) and lat >= 0:
                extra["latencyMs"] = int(lat)
            extra.update(_turn_fields(rec, "answer"))
            extra.update(_source_fields(rec))
            absorb_complete(extra, idx)
            # 带媒体的最终回答后抑制本轮剩余进度/工具提示，避免媒体消息被噪声覆盖。
            if media:
                suppress_until_turn_end = True
            continue

        if ev == "turn_end":
            # 轮次结束：重置 suppress 与活动段；登记 turn_id 以便后续重复 turn_id 别名化；
            # 关闭所有流式标记；剪除孤立纯推理占位；盖延迟；清空缓冲。
            suppress_until_turn_end = False
            active_activity_segment_id = None
            active_file_edit_segment_id = None
            turn_id = rec.get("turn_id")
            if isinstance(turn_id, str) and turn_id:
                # 若该 turn_id 已被别名化（本次回放中先前出现过），清除别名记录；
                # 否则记入 closed_turn_ids，使后续相同 turn_id 走别名分支。
                if turn_id in replay_turn_aliases:
                    replay_turn_aliases.pop(turn_id, None)
                else:
                    closed_turn_ids.add(turn_id)
            for i, m in enumerate(messages):
                if m.get("isStreaming"):
                    messages[i] = {**m, "isStreaming": False}
            prune_reasoning_only()
            lat = rec.get("latency_ms")
            if isinstance(lat, (int, float)) and lat >= 0:
                stamp_latency(int(lat))
            buffer_message_id = None
            buffer_parts = []
            continue

    # 收尾：对非 trace 的 assistant 文本应用 augment_assistant_text（如重写本地图片路径），
    # 并移除所有 isStreaming/reasoningStreaming 临时标记（回放结果应为静态终态）。
    for i, m in enumerate(messages):
        if (
            augment_assistant_text is not None
            and m.get("role") == "assistant"
            and m.get("kind") != "trace"
            and isinstance(m.get("content"), str)
        ):
            messages[i] = {**m, "content": augment_assistant_text(m["content"])}
        m.pop("isStreaming", None)
        m.pop("reasoningStreaming", None)
    return messages


def fork_boundary_message_count(lines: list[dict[str, Any]]) -> int | None:
    """Return the replayed UI message count before the first fork marker, if any.

    中文说明：找到转录中第一个 fork_marker，返回该标记之前的内容回放后的 UI 消息数。
    前端据此知道分叉点在第几条消息，从而在分叉会话中正确渲染"旧消息 + 新轮次"的分界。
    """
    for idx, rec in enumerate(lines):
        if rec.get("event") != WEBUI_FORK_MARKER_EVENT:
            continue
        return len(replay_transcript_to_ui_messages(lines[:idx]))
    return None


def has_pending_tool_calls(lines: list[dict[str, Any]]) -> bool:
    """Return True when the selected transcript tail looks like an unfinished turn.

    中文说明：从转录末尾向前扫描，判断当前是否处于"未完成的一轮"中。遇到 turn_end 或
    新的 user 消息即表示上一轮已结束（返回 False）；遇到 delta/stream_end/reasoning/
    file_edit 等事件表示正在进行中（返回 True）；message 事件仅当 kind 为 tool_hint/
    progress/reasoning 时视为进行中（这些是中间过程，最终回答出现后视为可继续）。
    前端据此决定是否显示"工具仍在运行"等指示。fork_marker 跳过不影响判定。
    """
    for rec in reversed(lines):
        ev = rec.get("event")
        if ev == "turn_end":
            return False
        if ev == "user":
            return False
        if ev == "message":
            return rec.get("kind") in {"tool_hint", "progress", "reasoning"}
        if ev in {
            "delta",
            "stream_end",
            "reasoning_delta",
            "reasoning_end",
            "file_edit",
        }:
            return True
        if ev in {WEBUI_FORK_MARKER_EVENT}:
            continue
    return False


def build_webui_thread_response(
    session_key: str,
    *,
    augment_user_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_media: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    augment_assistant_text: Callable[[str], str] | None = None,
    session_messages: list[dict[str, Any]] | None = None,
    limit: int | None = None,
    direction: str | None = None,
    before: str | None = None,
) -> dict[str, Any] | None:
    """Return a payload compatible with ``WebuiThreadPersistedPayload``.

    中文说明：组装前端线程接口的完整响应。流程为：
    1. 若带分页参数则按游标分页选取记录，否则读取整条转录；
    2. 从会话历史回填缺失的用户消息（兼容旧转录）；
    3. 计算分叉边界消息数；
    4. replay 为 ``UIMessage`` 列表，并附加 schema 版本、是否有未完成工具调用、
       分页元信息（含本次实际加载消息数）与分叉边界。无任何记录时返回 None。
    ``augment_*`` 回调用于在 replay 时重新签名媒体 URL 与重写本地图片路径，
    保证网关重启后旧签名失效仍可正常访问。
    """
    paginated = limit is not None or direction is not None or before is not None
    page: dict[str, Any] | None = None
    if paginated:
        lines, page = _select_transcript_page(session_key, limit=limit, before=before)
    else:
        lines = read_transcript_lines(session_key)
    if not lines:
        return None
    lines = inject_missing_user_events_from_session(session_key, lines, session_messages)
    fork_boundary = fork_boundary_message_count(lines)
    msgs = replay_transcript_to_ui_messages(
        lines,
        augment_user_media=augment_user_media,
        augment_assistant_media=augment_assistant_media,
        augment_assistant_text=augment_assistant_text,
    )
    payload = {
        "schemaVersion": WEBUI_TRANSCRIPT_SCHEMA_VERSION,
        "sessionKey": session_key,
        "messages": msgs,
        "has_pending_tool_calls": has_pending_tool_calls(lines),
    }
    if page is not None:
        # 回填本次实际加载的 UI 消息数，供前端判断是否已填满一页。
        page["loaded_message_count"] = len(msgs)
        payload["page"] = page
    if fork_boundary is not None:
        payload["fork_boundary_message_count"] = fork_boundary
    return payload
