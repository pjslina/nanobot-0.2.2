"""CLI Apps helpers for the WebUI HTTP and message surfaces."""

# 为 WebUI 的 HTTP 接口与消息通道提供 CLI 应用（cli apps）相关的辅助逻辑：包括目录缓存
# 的后台异步刷新、前端传入的 CLI 应用提及（mentions）清洗、列表负载组装与安装/更新/
# 卸载/测试等动作分发。本模块仅做编排与输入净化，真正的 CLI 应用管理由 `CliAppManager` 完成。

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from nanobot.apps.cli import CliAppError, CliAppManager, CliAppsRuntimeConfig
from nanobot.config.loader import load_config

QueryParams = dict[str, list[str]]

_CLI_APP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)
_CLI_APP_ATTACHMENT_KEYS = (
    "name",
    "display_name",
    "category",
    "entry_point",
    "logo_url",
    "brand_color",
)
_CATALOG_REFRESH_RETRY_SECONDS = 60.0
# 以下两个模块级变量共同实现目录刷新的去重与限流：同一进程内同一时刻最多只有一个
# 刷新任务在跑，且失败后至少间隔 _CATALOG_REFRESH_RETRY_SECONDS 才会再次尝试。
_catalog_refresh_task: asyncio.Task[None] | None = None
_catalog_refresh_last_started = 0.0


async def _refresh_catalog(manager: CliAppManager) -> None:
    # 后台刷新目录缓存。异常被吞掉：刷新是尽力而为的优化，失败不应影响主请求流程。
    try:
        await manager.refresh_catalog_cache(force_refresh=True)
    except Exception:
        pass


def _start_catalog_refresh(manager: CliAppManager) -> bool:
    # 触发一次后台目录刷新，返回是否成功启动。已存在运行中的任务则直接返回 True（合并请求）；
    # 距上次启动不足冷却时间则返回 False（限流），避免短时间反复刷新拖慢远程目录源。
    global _catalog_refresh_last_started, _catalog_refresh_task
    now = time.monotonic()
    if _catalog_refresh_task is not None and not _catalog_refresh_task.done():
        return True
    if now - _catalog_refresh_last_started < _CATALOG_REFRESH_RETRY_SECONDS:
        return False
    _catalog_refresh_last_started = now
    _catalog_refresh_task = asyncio.create_task(_refresh_catalog(manager))
    return True


def _clip_ws_string(value: Any, limit: int = 240) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def normalize_cli_app_mentions(raw: Any) -> list[dict[str, str]]:
    """Sanitize structured CLI app mentions sent by the WebUI."""
    # 清洗前端提交的 CLI 应用提及列表，防止注入并控制规模：最多保留 8 条，名称须匹配
    # `^[a-z0-9][a-z0-9_-]{0,63}$` 的命名规则，按名称小写去重，并对各附加字段做长度截断。
    # 这些净化的 mentions 会随消息进入 agent 上下文，因此必须严格收窄输入。
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    # 仅取前 8 条，限制单条消息能引用的 CLI 应用数量，避免上下文被大量提及撑爆。
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        name = _clip_ws_string(item.get("name"), 64)
        if not name or _CLI_APP_NAME_RE.match(name) is None:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        row: dict[str, str] = {"name": key}
        for field in _CLI_APP_ATTACHMENT_KEYS[1:]:
            # logo_url 允许更长（URL 较长），其余字段统一限 160 字符。
            value = _clip_ws_string(item.get(field), 512 if field == "logo_url" else 160)
            if value:
                row[field] = value
        out.append(row)
    return out


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _manager() -> CliAppManager:
    # 每次调用都重新加载配置并新建 manager，不缓存实例。代价是少量重复构造，好处是
    # 配置变更后立即生效、避免跨请求共享可变状态。
    config = load_config()
    cli_cfg = config.tools.cli_apps
    return CliAppManager(
        workspace=config.workspace_path,
        runtime=CliAppsRuntimeConfig(
            install_timeout=cli_cfg.install_timeout,
            run_timeout=cli_cfg.run_timeout,
            catalog_ttl_seconds=cli_cfg.catalog_ttl_seconds,
        ),
    )


async def cli_apps_payload(*, installed_only: bool = False) -> dict[str, Any]:
    # 组装返回给前端的 CLI 应用列表负载。策略是"先返回缓存、后台刷新"：
    # - installed_only 时直接返回已安装应用，不触碰远程目录；
    # - 否则用缓存即时返回，若缓存已陈旧则异步触发刷新（不阻塞请求），并在负载中标记
    #   catalog_refresh_pending 让前端知道稍后可重取；
    # - 缓存为空时退回已安装列表，保证首次访问也有内容可展示。
    manager = _manager()
    if installed_only:
        return manager.installed_payload()
    payload = manager.payload(cache_only=True)
    refresh_pending = False
    if not manager.catalog_cache_fresh(include_optional=True):
        refresh_pending = _start_catalog_refresh(manager)
    if not payload["apps"]:
        installed = manager.installed_payload()
        if installed["apps"]:
            payload = installed
    payload["catalog_refresh_pending"] = refresh_pending
    return payload


def cli_apps_action(action: str, query: QueryParams) -> dict[str, Any]:
    # 分发 CLI 应用的管理动作（install/update/uninstall/test）到 `CliAppManager`。
    # 名称缺失或动作未知时抛出 CliAppError，由上层转换为对应的 HTTP 错误响应。
    name = (_query_first(query, "name") or "").strip()
    if not name:
        raise CliAppError("missing CLI app name")
    manager = _manager()
    if action == "install":
        return manager.install(name)
    if action == "update":
        return manager.update(name)
    if action == "uninstall":
        return manager.uninstall(name)
    if action == "test":
        return manager.test(name)
    raise CliAppError(f"unknown CLI app action '{action}'", status=404)
