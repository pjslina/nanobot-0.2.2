"""Composition helpers for the embedded WebUI gateway."""

# 内嵌 WebUI 网关（gateway）的组合辅助模块。将网关运行所需的各项依赖（HTTP 处理器、
# 令牌存储、媒体网关、会话转录、工作区控制器等）集中装配为一个 `GatewayServices`
# 容器，供 WebSocket 传输层与 HTTP 路由层共享同一组实例，避免在各处分散构造与传递。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger as default_logger

from nanobot.webui.gateway_tokens import GatewayTokenStore
from nanobot.webui.media_gateway import WebUIMediaGateway
from nanobot.webui.transcript import WebUITranscriptRecorder
from nanobot.webui.workspaces import WebUIWorkspaceController
from nanobot.webui.ws_http import GatewayHTTPHandler


@dataclass(frozen=True)
class GatewayServices:
    """Explicit dependencies shared by WebSocket transport and HTTP routes."""
    # 网关共享依赖容器：frozen=True 保证装配后不可变，所有字段在进程生命周期内固定。
    # WebSocket 传输与 HTTP 路由都持有同一实例，从而共享令牌校验、媒体、转录等状态。

    http: GatewayHTTPHandler
    tokens: GatewayTokenStore
    media: WebUIMediaGateway
    transcripts: WebUITranscriptRecorder
    workspaces: WebUIWorkspaceController
    session_manager: Any | None
    cron_service: Any | None
    # 返回指定会话下待执行的 cron 任务 id 集合；为可空回调，仅在 cron 服务启用时提供。
    cron_pending_job_ids: Callable[[str], set[str]] | None


def build_gateway_services(
    *,
    config: Any,
    bus: Any,
    session_manager: Any | None,
    static_dist_path: Path | None,
    workspace_path: Path,
    default_restrict_to_workspace: bool,
    runtime_model_name: Any | None,
    runtime_surface: str,
    runtime_capabilities_overrides: dict[str, Any] | None,
    disabled_skills: set[str] | None = None,
    cron_service: Any | None = None,
    cron_pending_job_ids: Callable[[str], set[str]] | None = None,
    logger: Any = default_logger,
) -> GatewayServices:
    # 网关依赖装配工厂：按"被依赖者先构造"的顺序创建各组件，再统一打包返回。
    # 构造顺序有依赖关系：tokens 必须先于 http（http 校验令牌需引用 tokens），
    # workspaces 也先于 http（http 通过 workspaces 解析会话工作区）。
    tokens = GatewayTokenStore()
    media = WebUIMediaGateway(
        workspace_path=workspace_path,
        logger=logger,
    )
    transcripts = WebUITranscriptRecorder(log=logger)
    workspaces = WebUIWorkspaceController(
        session_manager=session_manager,
        default_workspace=workspace_path,
        default_restrict_to_workspace=default_restrict_to_workspace,
    )
    http = GatewayHTTPHandler(
        config=config,
        session_manager=session_manager,
        static_dist_path=static_dist_path,
        runtime_model_name=runtime_model_name,
        runtime_surface=runtime_surface,
        runtime_capabilities_overrides=runtime_capabilities_overrides,
        bus=bus,
        tokens=tokens,
        media=media,
        workspaces=workspaces,
        skills_workspace_path=workspace_path,
        disabled_skills=disabled_skills,
        cron_service=cron_service,
        cron_pending_job_ids=cron_pending_job_ids,
        log=logger,
    )
    return GatewayServices(
        http=http,
        tokens=tokens,
        media=media,
        transcripts=transcripts,
        workspaces=workspaces,
        session_manager=session_manager,
        cron_service=cron_service,
        cron_pending_job_ids=cron_pending_job_ids,
    )
