"""Coordination for scheduled cron turns.

定时 cron turn 的协调器。cron 任务触发的 turn 与普通用户消息不同：
它需要一个"提交-等待响应"的同步语义（submit 返回该 turn 的响应），
同时不能与正在进行的实时用户 turn 混在一起（避免抢占或重复注入）。
本协调器管理这些：用 future 让 submit 等待对应 turn 的响应；
当目标会话正忙时把 cron turn 延迟到会话空闲后再投递。
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable, Iterable

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.cron.session_turns import (
    cron_run_id,
    cron_trigger,
    defer_cron_until_session_idle,
)


class CronTurnCoordinator:
    """Manage scheduled cron turns without mixing them into live injections.

    管理定时 cron turn，避免与实时用户注入混淆。
    """

    def __init__(
        self,
        *,
        publish_inbound: Callable[[InboundMessage], Awaitable[None]],
        dispatch: Callable[[InboundMessage], Awaitable[object]],
        is_running: Callable[[], bool],
    ) -> None:
        self._publish_inbound = publish_inbound
        self._dispatch = dispatch
        self._is_running = is_running
        self.deferred_queues: dict[str, list[InboundMessage]] = {}  # 会话 key -> 延迟待投递的 cron 消息
        self._waiters: dict[str, asyncio.Future[OutboundMessage | None]] = {}  # run_id -> 等待响应的 future
        self._pending_messages_by_run_id: dict[str, InboundMessage] = {}  # run_id -> 正在处理的 cron 消息

    async def submit(self, msg: InboundMessage) -> OutboundMessage | None:
        """Submit a scheduled cron turn and wait for its session response.

        提交一个定时 cron turn 并等待其会话响应。
        agent 主循环运行中则走 publish_inbound（正常入队），否则直接 dispatch。
        用 future 阻塞等待 complete() 把响应回填。
        """
        run_id = cron_run_id(msg.metadata)
        if not run_id:
            raise ValueError("cron turn metadata must include a run_id")
        if run_id in self._waiters:
            raise RuntimeError(f"cron run {run_id!r} is already pending")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[OutboundMessage | None] = loop.create_future()
        self._waiters[run_id] = future
        self._pending_messages_by_run_id[run_id] = msg
        try:
            if self._is_running():
                await self._publish_inbound(msg)
            else:
                await self._dispatch(msg)
            return await future
        finally:
            self._waiters.pop(run_id, None)
            self._pending_messages_by_run_id.pop(run_id, None)

    def should_defer(
        self,
        msg: InboundMessage,
        *,
        session_key: str,
        active_session_keys: Iterable[str],
    ) -> bool:
        return (
            defer_cron_until_session_idle(msg.metadata)
            and session_key in active_session_keys
        )

    def defer_if_active(
        self,
        msg: InboundMessage,
        *,
        session_key: str,
        active_session_keys: Iterable[str],
    ) -> bool:
        """Defer a cron turn when its target session is already active."""
        if not self.should_defer(
            msg,
            session_key=session_key,
            active_session_keys=active_session_keys,
        ):
            return False
        pending_msg = msg
        if session_key != msg.session_key:
            pending_msg = dataclasses.replace(
                msg,
                session_key_override=session_key,
            )
        self.defer(session_key, pending_msg)
        return True

    def complete(
        self,
        msg: InboundMessage,
        *,
        response: OutboundMessage | None = None,
        error: BaseException | None = None,
    ) -> None:
        # 把某个 cron turn 的响应/异常回填给等待它的 submit()（通过 run_id 关联 future）。
        run_id = cron_run_id(msg.metadata)
        if not run_id:
            return
        future = self._waiters.get(run_id)
        if future is None or future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(response)

    def defer(self, session_key: str, msg: InboundMessage) -> None:
        self.deferred_queues.setdefault(session_key, []).append(msg)

    def pending_job_ids_for_session(self, session_key: str) -> set[str]:
        """Return cron jobs that are waiting for or running in *session_key*."""
        job_ids: set[str] = set()
        for msg in self.deferred_queues.get(session_key, []):
            job_id = _cron_job_id(msg)
            if job_id:
                job_ids.add(job_id)
        for msg in self._pending_messages_by_run_id.values():
            if msg.session_key != session_key:
                continue
            job_id = _cron_job_id(msg)
            if job_id:
                job_ids.add(job_id)
        return job_ids

    async def publish_next_deferred(self, session_key: str) -> None:
        queue = self.deferred_queues.get(session_key)
        if not queue:
            return
        msg = queue.pop(0)
        if not queue:
            self.deferred_queues.pop(session_key, None)
        await self._publish_inbound(msg)


def _cron_job_id(msg: InboundMessage) -> str | None:
    trigger = cron_trigger(msg.metadata)
    if not trigger:
        return None
    value = trigger.get("job_id")
    return value if isinstance(value, str) and value else None
