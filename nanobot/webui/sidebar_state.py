"""Persisted WebUI sidebar workspace state.

This state is UI-only metadata, scoped to the active nanobot instance data
directory (the directory containing the current config.json). It deliberately
does not modify agent sessions.

侧边栏工作区状态的持久化模块。该状态仅为 UI 元数据（置顶/归档/标题覆盖/
标签/折叠分组/视图偏好等），作用域限定于当前 nanobot 实例数据目录（含
config.json 的目录）。刻意不触碰 agent 会话内容，因此读写失败或状态丢失
不会影响会话本身。所有写入均经过归一化与体积限制，防止恶意/异常客户端
写入超大或畸形状态文件。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.config.paths import get_webui_dir

WEBUI_SIDEBAR_STATE_SCHEMA_VERSION = 1
# 以下各项上限用于防御性归一化：防止客户端写入超大状态文件导致内存/磁盘
# 膨胀或解析缓慢。状态文件超过 _MAX_STATE_FILE_BYTES 时整体丢弃。
_MAX_STATE_FILE_BYTES = 256 * 1024
_MAX_LIST_ITEMS = 2_000
_MAX_MAP_ITEMS = 2_000
_MAX_KEY_LEN = 512
_MAX_TITLE_LEN = 160
_MAX_TAG_LEN = 40
# density/sort 为枚举型视图选项，归一化时仅接受白名单内的值，其余回退默认。
_ALLOWED_DENSITIES = {"comfortable", "compact"}
_ALLOWED_SORTS = {"updated_desc", "created_desc", "title_asc"}


def webui_sidebar_state_path() -> Path:
    return get_webui_dir() / "sidebar-state.json"


def default_webui_sidebar_state() -> dict[str, Any]:
    # 返回一份完整的默认侧边栏状态：空置顶/归档列表、空映射、默认视图偏好。
    # schema_version 用于未来结构迁移时识别旧格式。
    return {
        "schema_version": WEBUI_SIDEBAR_STATE_SCHEMA_VERSION,
        "pinned_keys": [],
        "archived_keys": [],
        "title_overrides": {},
        "project_name_overrides": {},
        "tags_by_key": {},
        "collapsed_groups": {},
        "view": {
            "density": "comfortable",
            "show_previews": False,
            "show_timestamps": False,
            "show_archived": False,
            "sort": "updated_desc",
        },
        "updated_at": None,
    }


def _clean_string(value: Any, *, max_len: int = _MAX_KEY_LEN) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _clean_string_list(value: Any, *, max_len: int = _MAX_KEY_LEN) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    # 用 seen 集合对结果去重，同时保留首次出现的顺序。
    seen: set[str] = set()
    # 仅取前 _MAX_LIST_ITEMS 项，防止超长列表拖慢归一化。
    for item in value[:_MAX_LIST_ITEMS]:
        cleaned = _clean_string(item, max_len=max_len)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _clean_bool_map(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, bool] = {}
    for key, raw in list(value.items())[:_MAX_MAP_ITEMS]:
        cleaned_key = _clean_string(key)
        if cleaned_key is None:
            continue
        out[cleaned_key] = bool(raw)
    return out


def _clean_title_overrides(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw_title in list(value.items())[:_MAX_MAP_ITEMS]:
        cleaned_key = _clean_string(key)
        cleaned_title = _clean_string(raw_title, max_len=_MAX_TITLE_LEN)
        if cleaned_key is None or cleaned_title is None:
            continue
        out[cleaned_key] = cleaned_title
    return out


def _clean_tags_by_key(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, raw_tags in list(value.items())[:_MAX_MAP_ITEMS]:
        cleaned_key = _clean_string(key)
        if cleaned_key is None:
            continue
        # 每个会话最多保留 12 个标签，避免标签视图膨胀。
        tags = _clean_string_list(raw_tags, max_len=_MAX_TAG_LEN)[:12]
        if tags:
            out[cleaned_key] = tags
    return out


def _clean_view(value: Any) -> dict[str, Any]:
    # 归一化视图偏好：density/sort 必须命中白名单，否则回退默认；布尔项强转。
    default = default_webui_sidebar_state()["view"]
    if not isinstance(value, dict):
        return dict(default)
    density = value.get("density")
    sort = value.get("sort")
    return {
        # 仅接受白名单内的枚举值，未知值统一回退默认，避免前端传入脏数据。
        "density": density if density in _ALLOWED_DENSITIES else default["density"],
        "show_previews": bool(value.get("show_previews", default["show_previews"])),
        "show_timestamps": bool(value.get("show_timestamps", default["show_timestamps"])),
        "show_archived": bool(value.get("show_archived", default["show_archived"])),
        "sort": sort if sort in _ALLOWED_SORTS else default["sort"],
    }


def normalize_webui_sidebar_state(raw: Any) -> dict[str, Any]:
    """Return a schema-v1 sidebar state from any older/partial input.

    将任意（旧版/残缺/畸形）输入归一化为 schema-v1 的合法状态。各字段经
    对应 ``_clean_*`` 清洗：去重、截断、枚举校验、类型强转，确保落盘与
    返回给前端的数据始终结构稳定。非 dict 输入视作空状态。
    """
    if not isinstance(raw, dict):
        raw = {}
    state = default_webui_sidebar_state()
    state["pinned_keys"] = _clean_string_list(raw.get("pinned_keys"))
    state["archived_keys"] = _clean_string_list(raw.get("archived_keys"))
    state["title_overrides"] = _clean_title_overrides(raw.get("title_overrides"))
    state["project_name_overrides"] = _clean_title_overrides(
        raw.get("project_name_overrides")
    )
    state["tags_by_key"] = _clean_tags_by_key(raw.get("tags_by_key"))
    state["collapsed_groups"] = _clean_bool_map(raw.get("collapsed_groups"))
    state["view"] = _clean_view(raw.get("view"))
    updated_at = raw.get("updated_at")
    state["updated_at"] = updated_at if isinstance(updated_at, str) else None
    return state


def read_webui_sidebar_state() -> dict[str, Any]:
    # 读取侧边栏状态文件；任何异常（文件缺失/超大/JSON 解析失败）均回退默认
    # 状态而非抛错，保证 UI 永远能拿到一份合法状态。
    path = webui_sidebar_state_path()
    if not path.is_file():
        return default_webui_sidebar_state()
    try:
        # 先按字节大小拦截超大文件，避免误读入超大 JSON 拖垮进程。
        if path.stat().st_size > _MAX_STATE_FILE_BYTES:
            logger.warning("webui sidebar state too large, ignoring: {}", path)
            return default_webui_sidebar_state()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read webui sidebar state failed {}: {}", path, e)
        return default_webui_sidebar_state()
    return normalize_webui_sidebar_state(raw)


def write_webui_sidebar_state(raw: dict[str, Any]) -> dict[str, Any]:
    # 原子写入侧边栏状态：先归一化并打时间戳，序列化后做体积校验，再用
    # "写临时文件 + fsync + os.replace + 目录 fsync" 的模式落盘。该模式保证
    # 任何时刻磁盘上的文件都是完整的——即使进程在写入中途崩溃，读者也只会
    # 看到旧版完整文件或新版完整文件，不会读到半截 JSON。
    state = normalize_webui_sidebar_state(raw)
    # 使用 UTC 时间戳（Z 后缀）记录本次更新时刻。
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise ValueError("sidebar state is too large")

    path = webui_sidebar_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 写入同目录下的临时文件，再原子替换目标文件（os.replace 在同文件系统上原子）。
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
        f.write(b"\n")
        # flush + fsync 把数据强制刷到磁盘，避免停留在页缓存中。
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # 对目录做 fsync 以持久化目录项（文件名与新 inode 的关联），否则掉电后
    # os.replace 的结果可能未落盘。打开目录失败时（如不支持）则跳过。
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return state
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return state
