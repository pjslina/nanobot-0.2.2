"""On-demand version checker for nanobot-ai releases.

Checks PyPI for newer versions when explicitly requested (no background polling).

按需检查 nanobot-ai 在 PyPI 上是否有更新版本，不做后台轮询。通过模块级
``_cache`` 元组缓存最近一次查询结果（默认 5 分钟 TTL），避免短时间反复
请求 PyPI。`check_for_update` 为阻塞调用，应在后台线程或任务中调用。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from nanobot import __version__

logger = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/nanobot-ai/json"
_CACHE_TTL_S = 300  # 5 minutes cache to avoid hammering PyPI

# 模块级缓存：(缓存写入的 monotonic 时间戳, 最新版本号或 None)。用元组而非可变对象，
# 便于通过 global 赋值整体替换，避免多线程读到半更新状态。
_cache: tuple[float, str | None] = (0.0, None)


def check_for_update() -> dict[str, Any] | None:
    """Check PyPI for a newer version. Returns update info dict or None if up-to-date.

    Uses a short cache to avoid repeated requests within the TTL window.
    This is a blocking call — invoke from a thread or background task.

    返回更新信息字典（含当前版本、最新版本、PyPI 链接）；已是最新或查询失败时
    返回 None。失败时静默返回 None（仅 debug 日志），不影响调用方。
    """
    global _cache
    now = time.monotonic()
    cached_at, cached_val = _cache
    # 仅在 TTL 内且缓存值非空时复用，否则发起网络请求刷新缓存
    if now - cached_at < _CACHE_TTL_S and cached_val is not None:
        latest = cached_val
    else:
        try:
            resp = httpx.get(_PYPI_URL, timeout=5.0, follow_redirects=True)
            resp.raise_for_status()
            latest = resp.json().get("info", {}).get("version")
        except Exception:
            # 任何异常都视为查询失败，不更新缓存，返回 None 保持静默
            logger.debug("PyPI version check failed", exc_info=True)
            return None
        _cache = (now, latest)

    if not latest or latest == __version__:
        return None
    return {
        "currentVersion": __version__,
        "latestVersion": latest,
        "pypiUrl": "https://pypi.org/project/nanobot-ai/",
    }
