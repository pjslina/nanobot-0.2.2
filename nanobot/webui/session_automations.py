"""Automation payloads for the embedded WebUI.

WebUI 自动化（cron 任务）的序列化层：把 cron 服务中的 ``CronJob`` 转成
前端可消费的 JSON 载荷。提供两种视图--单会话视图（仅该会话绑定的任务）
与全局自动化管理视图（含运行历史、来源等详情）。本模块只负责"读与序列化"，
不创建/修改任务。
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any, Protocol

from nanobot.cron.session_turns import CRON_HISTORY_META
from nanobot.cron.types import CronJob
from nanobot.session.manager import _message_preview_text


# 以下两个 Protocol 用于结构化类型约束（鸭子类型），让本模块可接收 cron
# 服务与会话管理器的"最小子集"而无需导入具体实现，避免循环依赖。
class _CronServiceLike(Protocol):
    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]: ...

    def list_bound_cron_jobs_for_session(
        self,
        session_key: str,
        *,
        include_disabled: bool = True,
    ) -> list[CronJob]: ...


class _SessionManagerLike(Protocol):
    def read_session_file(self, key: str) -> dict[str, Any] | None: ...


def session_automation_jobs(
    cron_service: _CronServiceLike | None,
    session_key: str,
) -> list[CronJob]:
    """Return user automations attached to the WebUI session.

    返回绑定到指定 WebUI 会话的所有自动化任务（含已禁用的）。cron_service
    为 None（未启用 cron）时直接返回空列表。
    """
    if cron_service is None:
        return []
    return cron_service.list_bound_cron_jobs_for_session(
        session_key,
        include_disabled=True,
    )


def session_automations_payload(
    cron_service: _CronServiceLike | None,
    session_key: str,
    *,
    pending_job_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Return user-created automation jobs attached to a WebUI session.

    构造单会话视图的任务载荷：序列化该会话绑定的任务，并用 pending_job_ids
    标记当前正等待首次执行的"待生效"任务（前端据此显示 pending 状态）。
    """
    return {
        "jobs": serialize_automation_jobs(
            session_automation_jobs(cron_service, session_key),
            pending_job_ids=pending_job_ids,
        )
    }


def all_automations_payload(
    cron_service: _CronServiceLike | None,
    *,
    session_manager: _SessionManagerLike | None = None,
    pending_job_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Return all cron jobs visible to the WebUI automation manager.

    构造全局自动化管理视图的载荷：列出所有任务（含已禁用）并附带详情
    （运行历史、来源会话等），供自动化管理页面展示。session_manager 用于
    补充 websocket 来源会话的标题与预览文本。
    """
    jobs = cron_service.list_jobs(include_disabled=True) if cron_service is not None else []
    return {
        "jobs": serialize_automation_jobs(
            jobs,
            pending_job_ids=pending_job_ids,
            include_details=True,
            session_manager=session_manager,
        )
    }


def serialize_automation_jobs(
    jobs: list[CronJob],
    *,
    pending_job_ids: Collection[str] | None = None,
    include_details: bool = False,
    session_manager: _SessionManagerLike | None = None,
) -> list[dict[str, Any]]:
    return [
        _serialize_job(
            job,
            pending=job.id in (pending_job_ids or ()),
            include_details=include_details,
            session_manager=session_manager,
        )
        for job in jobs
    ]


def _serialize_job(
    job: CronJob,
    *,
    pending: bool = False,
    include_details: bool = False,
    session_manager: _SessionManagerLike | None = None,
) -> dict[str, Any]:
    # 基础载荷（单会话视图用）：id/名称/启用/调度/消息/状态。include_details
    # 为真时追加管理视图所需的额外字段（保护标记、运行历史、来源等）。
    payload = {
        "id": job.id,
        "name": job.name,
        "enabled": job.enabled,
        "schedule": {
            "kind": job.schedule.kind,
            "at_ms": job.schedule.at_ms,
            "every_ms": job.schedule.every_ms,
            "expr": job.schedule.expr,
            "tz": job.schedule.tz,
        },
        "payload": {
            "message": job.payload.message,
        },
        "state": {
            "next_run_at_ms": job.state.next_run_at_ms,
            "last_status": job.state.last_status,
            "pending": pending,
        },
    }
    if not include_details:
        return payload

    # system_event 类型的任务为系统内置任务，受保护、禁止用户删除
    # （见 cron.service.remove_job 的 "protected" 分支），前端据此隐藏删除按钮。
    payload["protected"] = job.payload.kind == "system_event"
    payload["delete_after_run"] = job.delete_after_run
    payload["created_at_ms"] = job.created_at_ms
    payload["updated_at_ms"] = job.updated_at_ms
    payload["payload"].update({"kind": job.payload.kind})
    payload["state"].update(
        {
            "last_run_at_ms": job.state.last_run_at_ms,
            "last_error": job.state.last_error,
            # 仅保留最近 5 次运行记录，控制载荷体积。
            "run_history": [
                {
                    "run_at_ms": record.run_at_ms,
                    "status": record.status,
                    "duration_ms": record.duration_ms,
                    "error": record.error,
                }
                for record in job.state.run_history[-5:]
            ],
        }
    )
    payload["origin"] = _origin_payload(job, session_manager)
    return payload


def _origin_payload(
    job: CronJob,
    session_manager: _SessionManagerLike | None,
) -> dict[str, Any] | None:
    # 构造任务的"来源"信息。非 websocket 渠道仅返回渠道名（标题/预览留空）；
    # websocket 渠道则额外读取其来源会话文件，补上 session_key、标题与预览，
    # 便于在自动化管理页中跳转回原会话。
    channel = job.payload.origin_channel
    chat_id = job.payload.origin_chat_id
    if not channel or not chat_id:
        return None
    title = ""
    preview = ""
    if channel != "websocket":
        return {
            "channel": channel,
            "title": title,
            "preview": preview,
        }

    session_key = f"{channel}:{chat_id}"
    if session_manager is not None:
        data = session_manager.read_session_file(session_key)
        if isinstance(data, dict):
            title = str(data.get("title") or "")
            preview = _session_preview(data.get("messages"))

    return {
        "session_key": session_key,
        "channel": channel,
        "chat_id": chat_id,
        "title": title,
        "preview": preview,
    }


def _session_preview(messages: Any) -> str:
    # 从会话消息中提取一段预览文本：优先返回第一条 user 消息内容；若无 user
    # 消息则回退到第一条 assistant 消息。会跳过带 CRON_HISTORY_META（_cron_turn）
    # 标记的消息，因为这些是 cron 自动注入的轮次，不能代表真实对话。
    if not isinstance(messages, list):
        return ""
    fallback_preview = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        # 跳过 cron 触发产生的自动轮次，避免预览被自动化消息占据。
        if message.get(CRON_HISTORY_META) is True:
            continue
        text = _message_preview_text(message)
        if not text:
            continue
        if message.get("role") == "user":
            return text
        if not fallback_preview and message.get("role") == "assistant":
            fallback_preview = text
    return fallback_preview
