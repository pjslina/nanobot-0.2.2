"""Auto compact: proactive compression of idle sessions to reduce token cost and latency.

"自动压缩"：对长时间空闲的会话主动做记忆巩固（压缩成摘要），以降低后续 turn 的
token 开销与延迟。基于 TTL（session_ttl_minutes）：会话超过该时长未被活动时，
在后台调度压缩；活跃中或有在途 agent 任务的会话会被跳过。
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Coroutine

from loguru import logger

from nanobot.session.manager import Session, SessionManager

if TYPE_CHECKING:
    from nanobot.agent.memory import Consolidator


class AutoCompact:
    _RECENT_SUFFIX_MESSAGES = 8  # 压缩时保留最近多少条消息不归档（保留近期上下文）
    _INTERNAL_SESSION_PREFIXES = ("dream:",)  # 内部会话前缀（如 dream 记忆巩固会话），不参与自动压缩

    def __init__(self, sessions: SessionManager, consolidator: Consolidator,
                 session_ttl_minutes: int = 0):
        self.sessions = sessions
        self.consolidator = consolidator
        self._ttl = session_ttl_minutes  # 会话空闲过期阈值（分钟），<=0 表示禁用
        self._archiving: set[str] = set()  # 正在后台压缩中的会话 key，避免重复调度
        self._summaries: dict[str, tuple[str, datetime]] = {}  # 热路径缓存：会话 key -> (摘要文本, 最后活跃时间)

    def _is_expired(self, ts: datetime | str | None,
                    now: datetime | None = None) -> bool:
        # 判断会话是否已空闲超过 TTL。
        if self._ttl <= 0 or not ts:
            return False
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return ((now or datetime.now()) - ts).total_seconds() >= self._ttl * 60

    @staticmethod
    def _format_summary(text: str, last_active: datetime) -> str:
        return f"Previous conversation summary (last active {last_active.isoformat()}):\n{text}"

    @classmethod
    def _is_internal_session(cls, key: str) -> bool:
        return key.startswith(cls._INTERNAL_SESSION_PREFIXES)

    def check_expired(self, schedule_background: Callable[[Coroutine], None],
                      active_session_keys: Collection[str] = ()) -> None:
        """Schedule archival for idle sessions, skipping those with in-flight agent tasks.

        为空闲过期的会话调度后台归档（压缩），跳过有在途 agent 任务的会话。
        """
        now = datetime.now()
        for info in self.sessions.list_sessions():
            key = info.get("key", "")
            # 跳过：无 key、内部会话、已在压缩中、当前活跃的会话。
            if not key or self._is_internal_session(key) or key in self._archiving:
                continue
            if key in active_session_keys:
                continue
            if self._is_expired(info.get("updated_at"), now):
                self._archiving.add(key)
                schedule_background(self._archive(key))

    async def _archive(self, key: str) -> None:
        if self._is_internal_session(key):
            self._archiving.discard(key)
            return
        try:
            summary = await self.consolidator.compact_idle_session(
                key, self._RECENT_SUFFIX_MESSAGES,
            )
            if summary and summary != "(nothing)":
                session = self.sessions.get_or_create(key)
                meta = session.metadata.get("_last_summary")
                if isinstance(meta, dict):
                    self._summaries[key] = (
                        meta["text"],
                        datetime.fromisoformat(meta["last_active"]),
                    )
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
        finally:
            self._archiving.discard(key)

    def prepare_session(self, session: Session, key: str) -> tuple[Session, str | None]:
        # 在 turn 开始前准备会话：若正在压缩或已过期，重新加载最新会话状态；
        # 并返回上一次压缩产生的摘要（热路径优先内存缓存，冷路径回退到会话元数据）。
        if self._is_internal_session(key):
            self._archiving.discard(key)
            self._summaries.pop(key, None)
            return session, None
        if key in self._archiving or self._is_expired(session.updated_at):
            logger.info("Auto-compact: reloading session {} (archiving={})", key, key in self._archiving)
            session = self.sessions.get_or_create(key)
        # 热路径：进程未重启，摘要仍在内存缓存中。
        entry = self._summaries.pop(key, None)
        if entry:
            return session, self._format_summary(entry[0], entry[1])
        # 冷路径：进程已重启，摘要持久化在会话元数据 _last_summary 中。
        meta = session.metadata.get("_last_summary")
        if isinstance(meta, dict):
            return session, self._format_summary(meta["text"], datetime.fromisoformat(meta["last_active"]))
        return session, None
