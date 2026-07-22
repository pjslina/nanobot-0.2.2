"""Runtime context for tool construction.

工具运行时上下文。包含两部分：
1. ``ToolContext``：构造工具时注入的"全局"依赖（工作区、消息总线、子 agent 管理器、
   cron 服务、会话管理器、provider 快照加载器等），工具实例持有它以访问各类服务。
2. ``RequestContext``：每条消息处理时注入的"当前请求"上下文（渠道、chat_id、
   会话 key、消息 id、元数据）。由于工具注册表是跨会话共享的，工具不能把会话信息
   存为实例属性，而是通过 contextvars 在每次 turn 绑定当前请求上下文，工具执行时
   再读取。这样同一个工具实例能安全地被多个并发会话复用。
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable

# 当前请求上下文：用 ContextVar 实现，按 asyncio 任务隔离。
# 每次 turn 开始时 bind_request_context 绑定，结束时 reset。
_CURRENT_REQUEST_CONTEXT: ContextVar["RequestContext | None"] = ContextVar(
    "nanobot_tool_request_context",
    default=None,
)


@dataclass(frozen=True)
class RequestContext:
    """Per-request context injected into tools at message-processing time.

    消息处理时注入工具的"每请求"上下文。frozen=True 保证不可变，避免被工具误改。
    """

    channel: str
    chat_id: str
    message_id: str | None = None
    session_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ContextAware(Protocol):
    # 实现 set_context 的工具会被 AgentLoop 在每条消息处理时注入当前请求上下文。
    def set_context(self, ctx: RequestContext) -> None:
        ...


def bind_request_context(ctx: RequestContext) -> Token[RequestContext | None]:
    # 绑定当前请求上下文，返回 token 供 reset 使用。
    return _CURRENT_REQUEST_CONTEXT.set(ctx)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    # 用 bind 返回的 token 恢复之前的上下文（必须配对调用）。
    _CURRENT_REQUEST_CONTEXT.reset(token)


def current_request_context() -> RequestContext | None:
    # 工具执行时调用，获取当前请求上下文（渠道/会话等）。
    return _CURRENT_REQUEST_CONTEXT.get()


def current_request_session_key() -> str | None:
    # 便捷方法：获取当前请求的会话 key。
    ctx = current_request_context()
    return ctx.session_key if ctx else None


@dataclass
class ToolContext:
    # 工具构造时注入的全局依赖集合（跨会话共享，不随请求变化）。
    config: Any
    workspace: str
    bus: Any | None = None
    subagent_manager: Any | None = None
    cron_service: Any | None = None
    sessions: Any | None = None
    file_state_store: Any = field(default=None)
    provider_snapshot_loader: Callable[[], Any] | None = None
    image_generation_provider_configs: dict[str, Any] | None = None
    timezone: str = "UTC"
    workspace_sandbox: Any | None = None
    runtime_events: Any | None = None
