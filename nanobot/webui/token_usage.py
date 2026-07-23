"""Workspace-scoped token usage telemetry for WebUI overview surfaces.

工作区级别的 token 用量统计，按本地日期聚合并持久化到 ``token-usage.json``。
``TokenUsageHook`` 在 agent 每轮迭代后采集 provider 上报的用量，
``token_usage_payload`` 为 WebUI 概览页汇总最近 30/365 天的总量、峰值与连续
使用天数等指标。写入采用临时文件 + ``fsync`` 的原子落盘，读取带体积保护，
避免损坏文件影响网关。
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.config.paths import get_webui_dir

TOKEN_USAGE_SCHEMA_VERSION = 1
# 状态文件最大字节数，超过则视为异常并忽略，防止损坏/超大文件拖垮读取。
_MAX_STATE_FILE_BYTES = 512 * 1024
# 最多保留的天数，超出时按日期排序裁剪最旧的记录。
_MAX_DAYS_RETAINED = 400
_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "total_tokens",
    "provider_tokens",
    "estimated_tokens",
)
_REQUEST_KEYS = ("requests", "provider_requests", "estimated_requests")
# 用量来源分类：user=对话、api=HTTP API、cron=定时任务、dream=记忆整理、system=系统。
_SOURCE_KEYS = ("user", "api", "cron", "dream", "system")
# 写入串行化锁，避免并发记录相互覆盖。
_WRITE_LOCK = threading.Lock()


def token_usage_state_path() -> Path:
    return get_webui_dir() / "token-usage.json"


def default_token_usage_state() -> dict[str, Any]:
    return {
        "schema_version": TOKEN_USAGE_SCHEMA_VERSION,
        "days": {},
        "updated_at": None,
    }


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _zone(timezone_name: str | None) -> timezone | ZoneInfo:
    # 解析时区名，无效或缺失时回退到 UTC，保证按本地日聚合时不报错。
    if not timezone_name:
        return timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _local_day(now: datetime | None = None, *, timezone_name: str | None = None) -> str:
    # 将时刻转换到指定时区后取日期，用于把用量归入用户当地的"自然日"。
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_zone(timezone_name)).date().isoformat()


def _clean_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clean_source(value: str | None) -> str:
    return value if value in _SOURCE_KEYS else "system"


def _source_from_session_key(session_key: str | None) -> str:
    # 按 session key 前缀推断用量来源，用于在 hook 中自动归类而非依赖显式传入。
    key = session_key or ""
    if key.startswith("dream:"):
        return "dream"
    if key == "heartbeat" or key.startswith("cron:"):
        return "cron"
    if key.startswith("api:"):
        return "api"
    if key.startswith("system:"):
        return "system"
    return "user"


def _normalize_usage(raw: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    usage = {key: _clean_int(raw.get(key)) for key in _USAGE_KEYS}
    # total 缺失时用 prompt+completion 兜底，保证总有可展示的总量。
    fallback_total = usage["prompt_tokens"] + usage["completion_tokens"]
    if usage["total_tokens"] <= 0:
        usage["total_tokens"] = fallback_total
    # provider_tokens 与 estimated_tokens 互为兜底：分别代表"真实计费"与"估算"两类口径，
    # 只在缺失时用 total 补齐，且不得超过 total，避免双计数。
    if usage["estimated_tokens"] <= 0 and usage["provider_tokens"] <= 0:
        usage["provider_tokens"] = usage["total_tokens"]
    elif usage["estimated_tokens"] > 0 and usage["provider_tokens"] <= 0:
        usage["estimated_tokens"] = min(usage["estimated_tokens"], usage["total_tokens"])
    elif usage["provider_tokens"] > 0 and usage["estimated_tokens"] <= 0:
        usage["provider_tokens"] = min(usage["provider_tokens"], usage["total_tokens"])
    # 没有任何 token 则视为无效记录，返回空以便调用方跳过。
    return usage if usage["total_tokens"] > 0 else {}


def _normalize_usage_row(row: dict[str, Any]) -> dict[str, int]:
    cleaned = {key: _clean_int(row.get(key)) for key in _USAGE_KEYS}
    if cleaned["total_tokens"] <= 0:
        cleaned["total_tokens"] = cleaned["prompt_tokens"] + cleaned["completion_tokens"]
    if cleaned["provider_tokens"] <= 0 and cleaned["estimated_tokens"] <= 0:
        cleaned["provider_tokens"] = cleaned["total_tokens"]
    requests = {key: _clean_int(row.get(key)) for key in _REQUEST_KEYS}
    # 请求次数同样在 provider/estimated 两类间兜底：依据 token 口径决定归入哪一类，
    # 使请求计数与 token 计数的口径保持一致。
    if (
        requests["requests"] > 0
        and requests["provider_requests"] <= 0
        and requests["estimated_requests"] <= 0
    ):
        if cleaned["estimated_tokens"] > 0 and cleaned["provider_tokens"] <= 0:
            requests["estimated_requests"] = requests["requests"]
        else:
            requests["provider_requests"] = requests["requests"]
    return {**cleaned, **requests}


def _normalize_sources(raw: Any, fallback: dict[str, int]) -> dict[str, dict[str, int]]:
    sources: dict[str, dict[str, int]] = {}
    if isinstance(raw, dict):
        for source, row in raw.items():
            if not isinstance(row, dict):
                continue
            normalized = _normalize_usage_row(row)
            if normalized["total_tokens"] <= 0 and normalized["requests"] <= 0:
                continue
            source_key = _clean_source(str(source))
            # 同一分区可能出现多次（历史数据），累加而非覆盖。
            current = sources.get(source_key)
            if current is None:
                sources[source_key] = normalized
            else:
                for key in (*_USAGE_KEYS, *_REQUEST_KEYS):
                    current[key] = _clean_int(current.get(key)) + normalized[key]
    # 缺失来源细分时，把整行用量归入 user 分区，保证总有来源维度可用。
    if not sources and (fallback["total_tokens"] > 0 or fallback["requests"] > 0):
        sources["user"] = {key: fallback[key] for key in (*_USAGE_KEYS, *_REQUEST_KEYS)}
    return sources


def normalize_token_usage_state(raw: Any) -> dict[str, Any]:
    state = default_token_usage_state()
    if not isinstance(raw, dict):
        return state
    days_raw = raw.get("days")
    if not isinstance(days_raw, dict):
        return state

    days: dict[str, dict[str, Any]] = {}
    # 按日期排序后只保留最近 _MAX_DAYS_RETAINED 天，丢弃超期记录实现滚动清理。
    for date, row in sorted(days_raw.items())[-_MAX_DAYS_RETAINED:]:
        if not isinstance(date, str) or len(date) != 10 or not isinstance(row, dict):
            continue
        normalized = _normalize_usage_row(row)
        if normalized["total_tokens"] <= 0 and normalized["requests"] <= 0:
            continue
        days[date] = {
            "date": date,
            **normalized,
            "sources": _normalize_sources(row.get("sources"), normalized),
        }

    state["days"] = days
    updated_at = raw.get("updated_at")
    state["updated_at"] = updated_at if isinstance(updated_at, str) else None
    return state


def read_token_usage_state() -> dict[str, Any]:
    path = token_usage_state_path()
    if not path.is_file():
        return default_token_usage_state()
    try:
        # 体积超限直接放弃读取，防止异常文件阻塞或污染内存。
        if path.stat().st_size > _MAX_STATE_FILE_BYTES:
            logger.warning("token usage state too large, ignoring: {}", path)
            return default_token_usage_state()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read token usage state failed {}: {}", path, e)
        return default_token_usage_state()
    return normalize_token_usage_state(raw)


def write_token_usage_state(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_token_usage_state(raw)
    state["updated_at"] = _utc_now_iso()
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise ValueError("token usage state is too large")

    path = token_usage_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 原子写入：先写临时文件并 fsync 数据，再 os.replace 原子替换，避免崩溃产生半写文件。
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    # 同步目录元数据，确保 replace 操作本身落盘（Linux 需要，Windows 上尽力而为）。
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return state
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return state


def record_token_usage(
    usage: dict[str, Any] | None,
    *,
    source: str = "user",
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = _normalize_usage(usage)
    if not normalized:
        # 无有效用量则不记录，直接返回当前状态。
        return read_token_usage_state()

    with _WRITE_LOCK:
        state = read_token_usage_state()
        day = _local_day(now, timezone_name=timezone_name)
        row = dict(state["days"].get(day) or {"date": day, "requests": 0})
        for key in _USAGE_KEYS:
            row[key] = _clean_int(row.get(key)) + normalized.get(key, 0)
        row["requests"] = _clean_int(row.get("requests")) + 1
        # 请求计数按 token 口径分流到 provider_requests 或 estimated_requests，
        # 与 _normalize_usage_row 的兜底逻辑保持一致。
        if normalized.get("estimated_tokens", 0) > 0 and normalized.get("provider_tokens", 0) <= 0:
            row["estimated_requests"] = _clean_int(row.get("estimated_requests")) + 1
        else:
            row["provider_requests"] = _clean_int(row.get("provider_requests")) + 1

        source_key = _clean_source(source)
        sources = dict(row.get("sources") or {})
        source_row = dict(sources.get(source_key) or {"requests": 0})
        for key in _USAGE_KEYS:
            source_row[key] = _clean_int(source_row.get(key)) + normalized.get(key, 0)
        source_row["requests"] = _clean_int(source_row.get("requests")) + 1
        if normalized.get("estimated_tokens", 0) > 0 and normalized.get("provider_tokens", 0) <= 0:
            source_row["estimated_requests"] = _clean_int(source_row.get("estimated_requests")) + 1
        else:
            source_row["provider_requests"] = _clean_int(source_row.get("provider_requests")) + 1
        sources[source_key] = source_row
        row["sources"] = sources

        state["days"][day] = row
        # 超出保留窗口时裁剪最旧的天，维持文件体积上限。
        if len(state["days"]) > _MAX_DAYS_RETAINED:
            kept = dict(sorted(state["days"].items())[-_MAX_DAYS_RETAINED:])
            state["days"] = kept
        return write_token_usage_state(state)


def record_response_token_usage(
    response: Any,
    *,
    source: str,
    timezone_name: str | None = None,
) -> None:
    # 从 provider 响应对象提取 usage 并记录；任何异常都吞掉只记日志，不影响主流程。
    try:
        record_token_usage(
            getattr(response, "usage", None),
            source=source,
            timezone_name=timezone_name,
        )
    except Exception:
        logger.exception("failed to record {} token usage", source)


def token_usage_payload(
    *,
    days: int = 371,
    timezone_name: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    state = read_token_usage_state()
    today = datetime.fromisoformat(_local_day(now, timezone_name=timezone_name)).date()
    start = today - timedelta(days=max(1, days) - 1)
    day_rows = [
        row
        for date, row in sorted(state["days"].items())
        if start.isoformat() <= date <= today.isoformat()
    ]
    last_30_start = today - timedelta(days=29)
    last_30 = [
        row
        for date, row in state["days"].items()
        if last_30_start.isoformat() <= date <= today.isoformat()
    ]
    last_365_start = today - timedelta(days=364)
    last_365 = [
        row
        for date, row in state["days"].items()
        if last_365_start.isoformat() <= date <= today.isoformat()
    ]
    active_dates = {
        datetime.fromisoformat(date).date()
        for date, row in state["days"].items()
        if _clean_int(row.get("total_tokens")) > 0
    }
    # 当前连续使用天数：从今天起向前回溯，遇到无用量的一天即停止。
    current_streak = 0
    cursor = today
    while cursor in active_dates:
        current_streak += 1
        cursor -= timedelta(days=1)

    # 最长连续使用天数：按日期顺序遍历所有活跃日，与前一天相邻则累加，否则重置为 1。
    longest_streak = 0
    running_streak = 0
    for cursor in sorted(active_dates):
        if cursor - timedelta(days=1) in active_dates:
            running_streak += 1
        else:
            running_streak = 1
        longest_streak = max(longest_streak, running_streak)

    all_rows = list(state["days"].values())
    return {
        "days": day_rows,
        "total_tokens": sum(_clean_int(row.get("total_tokens")) for row in all_rows),
        "total_tokens_30d": sum(_clean_int(row.get("total_tokens")) for row in last_30),
        "total_tokens_365d": sum(_clean_int(row.get("total_tokens")) for row in last_365),
        "peak_day_tokens": max([_clean_int(row.get("total_tokens")) for row in all_rows] or [0]),
        "current_streak_days": current_streak,
        "longest_streak_days": longest_streak,
        "active_days_30d": sum(1 for row in last_30 if _clean_int(row.get("total_tokens")) > 0),
        "requests_30d": sum(_clean_int(row.get("requests")) for row in last_30),
        "updated_at": state.get("updated_at"),
    }


class TokenUsageHook(AgentHook):
    """Persist provider-reported token usage without coupling it to chat messages.

    作为 ``AgentHook`` 注入 agent 循环，在每轮迭代结束后采集 ``context.usage``
    并按 session key 自动归类来源后落盘。用量统计与聊天消息解耦，即使无出站
    消息也能记录消耗。"""

    def __init__(self, *, timezone_name: str | None = None) -> None:
        super().__init__()
        self._timezone_name = timezone_name

    async def after_iteration(self, context: AgentHookContext) -> None:
        # 记录失败仅记日志，不影响 agent 主循环。
        try:
            record_token_usage(
                context.usage,
                source=_source_from_session_key(context.session_key),
                timezone_name=self._timezone_name,
            )
        except Exception:
            logger.exception("failed to record token usage")
