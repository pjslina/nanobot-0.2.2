"""HTTP API handler extracted from WebSocketChannel.

Handles all non-WebSocket HTTP routes: bootstrap, sessions, settings,
media, commands, sidebar state, static file serving, and token management.

Also houses shared HTTP utility functions used by both this module and
``websocket.py`` to avoid circular imports.
"""

# 本模块是 WebUI 网关的 HTTP 路由层：网关在同一端口上同时承载 WebSocket
# 多路复用协议（见 ``websocket.py``）与一组 HTTP 接口，由本模块负责后者。
# 主要职责包括：
#   * 引导接口（``/webui/bootstrap``）：向 WebUI 前端下发连接令牌与 WebSocket 地址。
#   * 会话相关 REST 接口（``/api/sessions/...``）：列出、读取、删除会话历史等。
#   * 自动化（cron）管理接口（``/api/webui/automations``）：增删改查、立即运行。
#   * 媒体网关（``/api/media/...``）：以签名 URL 提供会话内图片/音频等静态媒体。
#   * 静态资源服务：当 ``static_dist_path`` 存在时回退到 SPA 的 index.html。
# 所有有状态工作均委托给组合层注入的服务（``SessionManager``、``CronService``、
# ``WebUIMediaGateway``、``WebUIWorkspaceController`` 等），本类只做路由与鉴权。
# 共享的 HTTP 工具函数集中放在 ``http_utils.py``，避免与 ``websocket.py`` 循环导入。

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.command.builtin import builtin_command_palette
from nanobot.cron.session_turns import is_bound_cron_job
from nanobot.cron.types import CronJob, CronSchedule
from nanobot.utils.subagent_channel_display import scrub_subagent_messages_for_channel
from nanobot.webui.file_preview import WebUIFilePreviewError, file_preview_payload
from nanobot.webui.gateway_tokens import GatewayTokenStore, token_response_payload
from nanobot.webui.http_utils import (
    case_insensitive_header as _case_insensitive_header,
)
from nanobot.webui.http_utils import (
    host_for_url as _host_for_url,
)
from nanobot.webui.http_utils import (
    http_error as _http_error,
)
from nanobot.webui.http_utils import (
    http_json_response as _http_json_response,
)
from nanobot.webui.http_utils import (
    http_response as _http_response,
)
from nanobot.webui.http_utils import (
    is_localhost as _is_localhost,
)
from nanobot.webui.http_utils import (
    issue_route_secret_matches as _issue_route_secret_matches,
)
from nanobot.webui.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from nanobot.webui.http_utils import (
    parse_query as _parse_query,
)
from nanobot.webui.http_utils import (
    parse_request_path as _parse_request_path,
)
from nanobot.webui.http_utils import (
    query_first as _query_first,
)
from nanobot.webui.http_utils import (
    safe_host_header as _safe_host_header,
)
from nanobot.webui.media_gateway import WebUIMediaGateway
from nanobot.webui.session_automations import (
    all_automations_payload,
    serialize_automation_jobs,
    session_automation_jobs,
    session_automations_payload,
)
from nanobot.webui.session_list_index import list_webui_sessions
from nanobot.webui.sidebar_state import (
    read_webui_sidebar_state,
    write_webui_sidebar_state,
)
from nanobot.webui.skills_api import webui_skill_detail_payload, webui_skills_payload
from nanobot.webui.thread_disk import delete_webui_thread
from nanobot.webui.transcript import build_webui_thread_response
from nanobot.webui.workspaces import WebUIWorkspaceController

_SLOW_WEBUI_HTTP_LOG_MS = 1_000
_AUTOMATION_VALUES_HEADER = "X-Nanobot-Automation-Values"

if TYPE_CHECKING:
    from nanobot.bus.queue import MessageBus
    from nanobot.cron.service import CronService
    from nanobot.session.manager import SessionManager


def _decode_api_key(raw_key: str) -> str | None:
    # URL 解码后再用正则校验，只允许长度<=128 的安全字符集，防止会话键注入异常路径
    key = unquote(raw_key)
    _api_key_re = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
    if _api_key_re.match(key) is None:
        return None
    return key


def _default_model_name_from_config() -> str | None:
    # 从配置预设解析默认模型名；任何异常都吞掉并返回 None，保证 bootstrap 不因配置问题失败
    try:
        from nanobot.config.loader import load_config
        model = load_config().resolve_preset().model.strip()
        return model or None
    except Exception as e:
        logger.debug("bootstrap model_name could not load from config: {}", e)
        return None


def _resolve_bootstrap_model_name(
    runtime_name: Callable[[], str | None] | None,
) -> str:
    """解析 bootstrap 接口返回的 ``model_name``。

    优先调用运行时回调（``runtime_name``），它可能反映当前实际加载的模型；
    回调不可用或返回空时再回退到配置文件中的默认模型。返回值始终为字符串
    （可能为空串），便于前端直接渲染。"""
    if runtime_name is not None:
        try:
            raw = runtime_name()
        except Exception as e:
            logger.debug("bootstrap runtime model resolver failed: {}", e)
        else:
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped:
                    return stripped
    return _default_model_name_from_config() or ""


# ---------------------------------------------------------------------------
# GatewayHTTPHandler
# ---------------------------------------------------------------------------


class GatewayHTTPHandler:
    """Handles all HTTP routes served alongside the WebSocket endpoint.

    Routes HTTP requests and delegates stateful work to explicit gateway
    services owned by the composition layer.

    本类是 WebUI 网关的 HTTP 路由处理器，与 ``websocket.py`` 中的多路复用
    WebSocket 端点共用同一端口。它本身是无状态的路由层：所有有状态副作用
    （会话读写、cron 调度、媒体签名、工作区作用域等）都通过构造函数注入的
    服务对象完成，便于测试与替换。入口方法 :meth:`dispatch` 负责解析路径、
    依次尝试各路由分组并记录慢请求。
    """

    def __init__(
        self,
        *,
        config: Any,  # WebSocketConfig
        session_manager: SessionManager | None,
        static_dist_path: Path | None,
        runtime_model_name: Callable[[], str | None] | None,
        runtime_surface: str,
        runtime_capabilities_overrides: dict[str, Any] | None,
        bus: MessageBus,
        tokens: GatewayTokenStore,
        media: WebUIMediaGateway,
        workspaces: WebUIWorkspaceController,
        skills_workspace_path: Path,
        disabled_skills: set[str] | None = None,
        cron_service: CronService | None = None,
        cron_pending_job_ids: Callable[[str], set[str]] | None = None,
        log: Any = logger,
    ) -> None:
        self.config = config
        self.session_manager = session_manager
        self.static_dist_path = static_dist_path
        self.runtime_model_name = runtime_model_name
        self.bus = bus
        self.tokens = tokens
        self.media = media
        self.workspaces = workspaces
        self.skills_workspace_path = skills_workspace_path
        self.disabled_skills = disabled_skills or set()
        self.cron_service = cron_service
        self.cron_pending_job_ids = cron_pending_job_ids
        self._log = log
        self._runtime_surface = runtime_surface

        # 延迟导入以避免在模块加载期产生循环依赖；运行时再构造设置路由器与能力描述。
        from nanobot.webui.settings_api import runtime_capabilities as _rc
        from nanobot.webui.settings_routes import WebUISettingsRouter

        # 根据运行时"表面"（native/浏览器等）计算前端能力位，决定 WebUI 可见的功能集。
        self._capabilities = _rc(runtime_surface, runtime_capabilities_overrides or {})
        self.settings_routes = WebUISettingsRouter(
            bus=bus,
            logger=self._log,
            check_api_token=self.check_api_token,
            parse_query=_parse_query,
            json_response=_http_json_response,
            error_response=_http_error,
            runtime_surface=runtime_surface,
            runtime_capabilities=self._capabilities,
        )

    def workspace_controls_available(self, connection: Any) -> bool:
        # 原生 surface（如桌面端）或来自本机的连接才允许使用工作区控制功能，
        # 远程浏览器连接默认关闭，避免在不可信网络上暴露文件系统操作。
        return self._runtime_surface == "native" or _is_localhost(connection)

    # -- Token management ---------------------------------------------------

    def check_api_token(self, request: WsRequest) -> bool:
        # 委托给令牌存储校验请求中的 API 令牌；所有受保护路由都先调用本方法做鉴权。
        return self.tokens.check_api_token(request)

    # -- Main dispatch ------------------------------------------------------

    async def dispatch(self, connection: Any, request: WsRequest) -> Any | None:
        """Route an HTTP request. Returns Response or None.

        HTTP 路由总入口：解析规范化路径，调用 :meth:`_dispatch_resolved`
        进行实际分发，并在 ``finally`` 中记录耗时超过阈值的慢请求。"""
        got, _ = _parse_request_path(request.path)
        started = time.perf_counter()
        response: Any | None = None

        try:
            response = await self._dispatch_resolved(connection, request, got)
            return response
        finally:
            self._log_slow_http(got, response, started)

    async def _dispatch_resolved(
        self,
        connection: Any,
        request: WsRequest,
        got: str,
    ) -> Any | None:
        # 路由分发顺序有讲究：专用端点优先，再依次尝试设置/会话/媒体/自动化/杂项路由。
        # Token issue endpoint
        if self.config.token_issue_path:
            issue_expected = _normalize_config_path(self.config.token_issue_path)
            if got == issue_expected:
                return self._handle_token_issue(connection, request)

        # Bootstrap
        if got == "/webui/bootstrap":
            return self._handle_bootstrap(connection, request)

        # Settings routes (delegated)
        response = await self.settings_routes.dispatch(request, got)
        if response is not None:
            return response

        # Session routes
        response = await self._dispatch_session_routes(request, got)
        if response is not None:
            return response

        # Media routes
        response = self._dispatch_media_routes(request, got)
        if response is not None:
            return response

        # Automation routes
        response = await self._dispatch_automation_routes(request, got)
        if response is not None:
            return response

        # Misc routes
        response = await self._dispatch_misc_routes(connection, request, got)
        if response is not None:
            return response

        # API 404 (never serve SPA for /api/ routes)
        # 对 /api/ 下的未匹配路径直接返回 404，绝不回退到 SPA，避免把 API 错误
        # 误当成前端路由处理，同时防止泄露前端 HTML 给 API 调用方。
        if got.startswith("/api/"):
            return _http_error(404, "API route not found")

        # Static SPA serving
        # 非 API 路径才尝试静态资源/SPA 回退，最终未命中返回 404。
        if self.static_dist_path is not None:
            response = self._serve_static(got)
            if response is not None:
                return response

        return connection.respond(404, "Not Found")

    def _log_slow_http(self, path: str, response: Any | None, started: float) -> None:
        # 仅对 API 与 bootstrap 路由记录慢请求日志，静态资源不计入，避免噪声。
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms < _SLOW_WEBUI_HTTP_LOG_MS:
            return
        if not (path.startswith("/api/") or path == "/webui/bootstrap"):
            return
        status = getattr(response, "status_code", None)
        self._log.warning(
            "slow webui http route path={} status={} duration_ms={}",
            path,
            status if status is not None else "none",
            elapsed_ms,
        )

    # -- Token issue --------------------------------------------------------

    def _handle_token_issue(self, connection: Any, request: Any) -> Any:
        # 签发连接令牌的专用端点：优先用 token_issue_secret 校验，缺失时回退到主令牌；
        # 两者都为空则告警并放行（生产环境必须配置 secret，否则任何人都能领令牌）。
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return connection.respond(401, "Unauthorized")
        else:
            self._log.warning(
                "token_issue_path is set but token_issue_secret is empty; "
                "any client can obtain connection tokens — set token_issue_secret for production."
            )
        # 令牌发放速率限制：未领取的令牌过多时直接拒绝，防止令牌洪泛耗尽配额。
        if not self.tokens.can_issue():
            self._log.error(
                "too many outstanding issued tokens ({}), rejecting issuance",
                len(self.tokens.issued_tokens),
            )
            return _http_json_response({"error": "too many outstanding tokens"}, status=429)
        token_value = self.tokens.issue_token(self.config.token_ttl_s)
        return _http_json_response(token_response_payload(token_value, self.config.token_ttl_s))

    # -- Bootstrap ----------------------------------------------------------

    def _handle_bootstrap(self, connection: Any, request: Any) -> Response:
        # Bootstrap 是 WebUI 首屏加载时调用的接口，返回连接所需的令牌与 WebSocket 地址。
        # 鉴权策略：配置了 secret 时用 secret 校验；否则仅允许本机访问，远程直接 403。
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        if secret:
            if not _issue_route_secret_matches(request.headers, secret):
                return _http_error(401, "Unauthorized")
        elif not _is_localhost(connection):
            return _http_error(403, "bootstrap is localhost-only")

        # include_api_token=True 表示本次发放的令牌可作为 API 令牌使用（权限更高），
        # 因此同样受未领取令牌上限约束，超限返回 429。
        if not self.tokens.can_issue(include_api_token=True):
            return _http_response(
                json.dumps({"error": "too many outstanding tokens"}).encode("utf-8"),
                status=429,
                content_type="application/json; charset=utf-8",
            )
        token = self.tokens.issue_token(self.config.token_ttl_s, api_token=True)

        # 拼接前端实际应连接的 WebSocket URL（考虑反向代理协议与配置路径）。
        ws_url = self._bootstrap_ws_url(request)
        expected_path = _normalize_config_path(self.config.path)
        return _http_json_response(
            {
                "token": token,
                "ws_path": expected_path,
                "ws_url": ws_url,
                "expires_in": self.config.token_ttl_s,
                "model_name": _resolve_bootstrap_model_name(self.runtime_model_name),
                "runtime_surface": self._runtime_surface,
                "runtime_capabilities": self._capabilities,
            }
        )

    def _bootstrap_ws_url(self, request: Any) -> str:
        # 构造前端可用的 ws/wss URL。Host 优先取请求头，缺失时回退到配置的 host:port；
        # 协议根据 X-Forwarded-Proto（反向代理场景）或是否配置 SSL 证书决定 ws/wss。
        headers = getattr(request, "headers", {}) or {}
        host = _safe_host_header(_case_insensitive_header(headers, "Host"))
        if not host:
            host = _host_for_url(self.config.host, self.config.port)
        proto = _case_insensitive_header(headers, "X-Forwarded-Proto")
        # X-Forwarded-Proto 可能是逗号分隔的多值，只取第一个（最靠近客户端的那一跳）。
        proto = proto.split(",", 1)[0].strip().lower()
        secure = proto in {"https", "wss"} or bool(self.config.ssl_certfile.strip())
        scheme = "wss" if secure else "ws"
        expected_path = _normalize_config_path(self.config.path)
        return f"{scheme}://{host}{expected_path}"

    # -- Session routes -----------------------------------------------------

    async def _dispatch_session_routes(self, request: WsRequest, got: str) -> Response | None:
        # 会话级 REST 路由：以正则匹配 ``/api/sessions/<key>/<action>`` 形式，
        # <key> 部分在后续处理器中经 _decode_api_key 解码与校验，防止注入。
        m = re.match(r"^/api/sessions/([^/]+)/messages$", got)
        if m:
            return self._handle_session_messages(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/webui-thread$", got)
        if m:
            return self._handle_webui_thread_get(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-preview$", got)
        if m:
            return self._handle_file_preview(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/automations$", got)
        if m:
            return self._handle_session_automations(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/delete$", got)
        if m:
            return self._handle_session_delete(request, m.group(1))

        return None

    async def _handle_sessions_list(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        # 会话列表读取涉及磁盘 IO，放到线程池执行避免阻塞事件循环。
        payload = await asyncio.to_thread(self._sessions_list_payload)
        return _http_json_response(payload)

    def _sessions_list_payload(self) -> dict[str, Any]:
        assert self.session_manager is not None
        sessions = list_webui_sessions(self.session_manager)
        from nanobot.session.webui_turns import websocket_turn_wall_started_at

        cleaned = []
        for s in sessions:
            key = s.get("key")
            # 只返回 WebSocket 渠道的会话：WebUI 只展示通过 ws 通道建立的会话，
            # 其他渠道（Telegram/Discord 等）的会话不在 WebUI 列表中暴露。
            if not (isinstance(key, str) and key.startswith("websocket:")):
                continue
            # 去掉 path 字段（含磁盘路径，不应泄露给前端），并补充运行状态与工作区作用域。
            row = {k: v for k, v in s.items() if k != "path"}
            chat_id = key.split(":", 1)[1]
            started_at = websocket_turn_wall_started_at(chat_id)
            if started_at is not None:
                row["run_started_at"] = started_at
            scope = self.workspaces.scope_for_session_key(key)
            row["workspace_scope"] = scope.payload()
            cleaned.append(row)
        return {"sessions": cleaned}

    def _handle_session_messages(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        # 仅允许访问 WebSocket 渠道会话，避免通过 WebUI 读取其他渠道的私有历史。
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        data = self.session_manager.read_session_file(decoded_key)
        if data is None:
            return _http_error(404, "session not found")
        messages = data.get("messages")
        if isinstance(messages, list):
            # 清洗子代理内部消息，避免把子代理的中间步骤泄露给渠道展示层。
            scrub_subagent_messages_for_channel(messages)
        # 把媒体引用改写为可通过 /api/media 访问的签名 URL，前端才能直接加载。
        self.media.augment_media_urls(data)
        return _http_json_response(data)

    def _handle_webui_thread_get(self, request: WsRequest, key: str) -> Response:
        # 读取 WebUI 专用线程视图：相比 /messages 更完整，支持分页（limit/before/direction），
        # 并对用户与助手消息中的媒体引用做改写，使前端可加载工作区内的本地图片。
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        scope = self.workspaces.scope_for_session_key(decoded_key)
        session_messages: list[dict[str, Any]] | None = None
        if self.session_manager is not None:
            session_data = self.session_manager.read_session_file(decoded_key)
            raw_messages = session_data.get("messages") if isinstance(session_data, dict) else None
            if isinstance(raw_messages, list):
                session_messages = [m for m in raw_messages if isinstance(m, dict)]
        query = _parse_query(request.path)
        raw_limit = _query_first(query, "limit")
        limit: int | None = None
        if raw_limit is not None and raw_limit.strip():
            try:
                limit = int(raw_limit)
            except ValueError:
                return _http_error(400, "invalid limit")
        # direction 目前只支持 "latest"（从最新向前翻页），其他值视为非法。
        direction = _query_first(query, "direction")
        if direction is not None and direction not in {"latest"}:
            return _http_error(400, "invalid direction")
        before = _query_first(query, "before")
        data = build_webui_thread_response(
            decoded_key,
            augment_user_media=self.media.augment_transcript_media,
            augment_assistant_media=self.media.augment_transcript_media,
            # 助手文本中的本地 markdown 图片需用工作区路径改写为可访问的签名 URL。
            augment_assistant_text=lambda text: self.media.rewrite_local_markdown_images(
                text,
                workspace_path=scope.project_path,
            ),
            session_messages=session_messages,
            limit=limit,
            direction=direction,
            before=before,
        )
        if data is None:
            return _http_error(404, "webui thread not found")
        data["workspace_scope"] = scope.payload()
        return _http_json_response(data)

    def _handle_file_preview(self, request: WsRequest, key: str) -> Response:
        # 文件预览接口：在会话所属工作区作用域内解析 path，生成可预览的内容载荷。
        # 路径校验与作用域隔离由 file_preview_payload 负责，越界访问抛 WebUIFilePreviewError。
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        path = _query_first(_parse_query(request.path), "path")
        try:
            payload = file_preview_payload(
                path,
                scope=self.workspaces.scope_for_session_key(decoded_key),
            )
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    def _handle_session_automations(self, request: WsRequest, key: str) -> Response:
        # 返回该会话绑定的自动化（cron）任务列表，附带待执行的任务 ID 供前端高亮。
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        pending_job_ids: set[str] = set()
        if self.cron_pending_job_ids is not None:
            pending_job_ids = self.cron_pending_job_ids(decoded_key)
        return _http_json_response(
            session_automations_payload(
                self.cron_service,
                decoded_key,
                pending_job_ids=pending_job_ids,
            )
        )

    def _handle_session_delete(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        # 删除保护：若会话仍绑定了自动化任务且未显式确认删除（delete_automations=1），
        # 则返回 blocked_by_automations 让前端二次确认，避免误删仍需运行的定时任务。
        delete_automations = (_query_first(query, "delete_automations") or "").lower()
        automation_jobs = session_automation_jobs(self.cron_service, decoded_key)
        if automation_jobs and delete_automations not in {"1", "true", "yes"}:
            return _http_json_response(
                {
                    "deleted": False,
                    "blocked_by_automations": True,
                    "automations": serialize_automation_jobs(automation_jobs),
                }
            )
        # 确认删除后，先移除关联的 cron 任务，再删除会话与 WebUI 线程记录。
        if automation_jobs and self.cron_service is not None:
            for job in automation_jobs:
                self.cron_service.remove_job(job.id)
        deleted = self.session_manager.delete_session(decoded_key)
        delete_webui_thread(decoded_key)
        return _http_json_response({"deleted": bool(deleted)})

    # -- Automation routes --------------------------------------------------

    async def _dispatch_automation_routes(
        self,
        request: WsRequest,
        got: str,
    ) -> Response | None:
        # 自动化管理路由：列表用精确匹配，动作用正则捕获 action 类型后分发。
        if got == "/api/webui/automations":
            return self._handle_webui_automations(request)
        m = re.match(r"^/api/webui/automations/(enable|disable|delete|run|update)$", got)
        if m:
            return await self._handle_webui_automation_action(request, m.group(1))
        return None

    def _pending_cron_job_ids_for_all(self) -> set[str]:
        # 汇总所有 cron 任务对应会话的"待执行任务 ID"集合。
        # 部分旧任务可能没有 session_key，此时用 origin_channel:origin_chat_id 重建键。
        if self.cron_service is None or self.cron_pending_job_ids is None:
            return set()
        pending: set[str] = set()
        for job in self.cron_service.list_jobs(include_disabled=True):
            session_key = job.payload.session_key
            if not session_key and job.payload.origin_channel and job.payload.origin_chat_id:
                session_key = f"{job.payload.origin_channel}:{job.payload.origin_chat_id}"
            if session_key:
                pending.update(self.cron_pending_job_ids(session_key))
        return pending

    def _handle_webui_automations(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            all_automations_payload(
                self.cron_service,
                session_manager=self.session_manager,
                pending_job_ids=self._pending_cron_job_ids_for_all(),
            )
        )

    async def _handle_webui_automation_action(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        # 自动化动作分发：enable/disable/delete/run/update 五种操作共享同一鉴权与任务定位逻辑。
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.cron_service is None:
            return _http_error(503, "cron service unavailable")

        query = _parse_query(request.path)
        # 兼容 id 与 job_id 两种查询参数名，取非空者作为任务 ID。
        job_id = (_query_first(query, "id") or _query_first(query, "job_id") or "").strip()
        if not job_id:
            return _http_error(400, "missing automation id")
        job = self.cron_service.get_job(job_id)
        if job is None:
            return _http_error(404, "automation not found")
        # 系统事件类自动化受保护，禁止通过 WebUI 修改或删除。
        if job.payload.kind == "system_event":
            return _http_error(403, "system automation is protected")
        # enable/run 要求任务已绑定到某个聊天会话，否则没有执行上下文。
        if action in {"enable", "run"} and not is_bound_cron_job(job):
            return _http_error(409, "automation has no linked chat")

        if action == "enable":
            if self.cron_service.enable_job(job_id, enabled=True) is None:
                return _http_error(404, "automation not found")
        elif action == "disable":
            if self.cron_service.enable_job(job_id, enabled=False) is None:
                return _http_error(404, "automation not found")
        elif action == "delete":
            result = self.cron_service.remove_job(job_id)
            if result == "not_found":
                return _http_error(404, "automation not found")
            if result == "protected":
                return _http_error(403, "system automation is protected")
        elif action == "run":
            # 立即触发运行：用 create_task 后台执行，不阻塞 HTTP 响应；
            # force=False 表示仍遵守正常的运行间隔/条件约束。
            if not job.enabled:
                return _http_error(409, "automation is disabled")
            task = asyncio.create_task(self.cron_service.run_job(job_id, force=False))
            task.add_done_callback(self._log_automation_run_result)
        elif action == "update":
            values = _automation_values_from_request(request)
            if values is None:
                return _http_error(400, "invalid automation update payload")
            parsed = _parse_automation_update(values, current_job=job)
            if isinstance(parsed, str):
                return _http_error(400, parsed)
            try:
                result = self.cron_service.update_job(job_id, **parsed)
            except ValueError as exc:
                return _http_error(400, str(exc))
            if result == "not_found":
                return _http_error(404, "automation not found")
            if result == "protected":
                return _http_error(403, "system automation is protected")
        else:
            return _http_error(404, "unknown automation action")

        # 动作执行成功后返回最新的完整列表，便于前端一次性刷新状态。
        return self._handle_webui_automations(request)

    @staticmethod
    def _log_automation_run_result(task: asyncio.Task[bool]) -> None:
        # 后台运行任务的回调：仅记录异常与"未实际执行"的情况，正常完成不打日志。
        try:
            ran = task.result()
        except Exception:
            logger.exception("WebUI automation run-now task failed")
            return
        if not ran:
            logger.warning("WebUI automation run-now task did not execute")

    # -- Media routes -------------------------------------------------------

    def _dispatch_media_routes(self, request: WsRequest, got: str) -> Response | None:
        # 媒体路由：``/api/media/<sig>/<payload>``，sig 与 payload 共同构成签名凭证，
        # 由媒体网关验证签名后返回对应的图片/音频等二进制内容。
        m = re.match(r"^/api/media/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$", got)
        if m:
            return self._handle_media_fetch(m.group(1), m.group(2), request)
        return None

    def _handle_media_fetch(
        self, sig: str, payload: str, request: WsRequest | None = None
    ) -> Response:
        return self.media.serve_signed_media(
            sig,
            payload,
            request=request,
        )

    # -- Misc routes --------------------------------------------------------

    async def _dispatch_misc_routes(
        self, connection: Any, request: WsRequest, got: str
    ) -> Response | None:
        # 杂项路由：会话列表、命令面板、工作区、技能、侧栏状态等单资源端点。
        if got == "/api/sessions":
            return await self._handle_sessions_list(request)
        if got == "/api/commands":
            return self._handle_commands(request)
        if got == "/api/workspaces":
            return self._handle_workspaces(connection, request)
        if got == "/api/webui/skills":
            return self._handle_webui_skills(request)
        m = re.match(r"^/api/webui/skills/([^/]+)$", got)
        if m:
            return self._handle_webui_skill_detail(request, m.group(1))
        if got == "/api/webui/sidebar-state":
            return self._handle_webui_sidebar_state(request)
        if got == "/api/webui/sidebar-state/update":
            return self._handle_webui_sidebar_state_update(request)
        return None

    def _handle_commands(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response({"commands": builtin_command_palette()})

    def _handle_workspaces(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            self.workspaces.payload(
                controls_available=self.workspace_controls_available(connection)
            )
        )

    def _handle_webui_skills(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            webui_skills_payload(
                self.skills_workspace_path,
                disabled_skills=self.disabled_skills,
            )
        )

    def _handle_webui_skill_detail(self, request: WsRequest, raw_name: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from urllib.parse import unquote

        name = unquote(raw_name)
        # 拒绝包含路径分隔符的名字，防止通过技能名穿越到工作区外的文件。
        if not name or "/" in name or "\\" in name:
            return _http_error(400, "invalid skill name")
        payload = webui_skill_detail_payload(
            self.skills_workspace_path,
            name,
            disabled_skills=self.disabled_skills,
        )
        if payload is None:
            return _http_error(404, "skill not found")
        return _http_json_response(payload)

    def _handle_webui_sidebar_state(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(read_webui_sidebar_state())

    def _handle_webui_sidebar_state_update(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        raw_state = _query_first(query, "state")
        if raw_state is None:
            return _http_error(400, "missing state")
        try:
            decoded = json.loads(raw_state)
        except json.JSONDecodeError:
            return _http_error(400, "state must be JSON")
        if not isinstance(decoded, dict):
            return _http_error(400, "state must be an object")
        try:
            state = write_webui_sidebar_state(decoded)
        except ValueError as e:
            return _http_error(400, str(e))
        except OSError:
            self._log.exception("failed to write webui sidebar state")
            return _http_error(500, "failed to write sidebar state")
        return _http_json_response(state)

    # -- Static file serving ------------------------------------------------

    def _serve_static(self, request_path: str) -> Response | None:
        # 静态资源/SPA 服务：把请求路径映射到 static_dist_path 下的文件。
        # 安全要点：多重路径穿越防护（``..`` 段、绝对路径、resolve 后越界检查），
        # 未命中文件时回退到 index.html 以支持前端客户端路由。
        assert self.static_dist_path is not None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        # 第一道防线：路径段中不允许出现 ``..``，也不允许以 / 开头（绝对路径）。
        if ".." in rel.split("/") or rel.startswith("/"):
            return _http_error(403, "Forbidden")
        candidate = (self.static_dist_path / rel).resolve()
        # 第二道防线：resolve 后必须仍位于 static_dist_path 之内，防止符号链接等绕过。
        try:
            candidate.relative_to(self.static_dist_path)
        except ValueError:
            return _http_error(403, "Forbidden")
        if not candidate.is_file():
            # 非文件路径回退到 index.html，交由前端路由处理（SPA 常见做法）。
            index = self.static_dist_path / "index.html"
            if index.is_file():
                candidate = index
            else:
                return None
        try:
            body = candidate.read_bytes()
        except OSError as e:
            self._log.warning("static: failed to read {}: {}", candidate, e)
            return _http_error(500, "Internal Server Error")
        ctype, _ = mimetypes.guess_type(candidate.name)
        if ctype is None:
            ctype = "application/octet-stream"
        # 文本类资源追加 charset=utf-8，避免浏览器按错误编码解析。
        if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
            ctype = f"{ctype}; charset=utf-8"
        # index.html 禁止缓存以保证前端总是拿到最新版本；其余静态资源带长缓存
        # 与 immutable（文件名通常含 hash，内容变更即换名）。
        if candidate.name == "index.html":
            cache = "no-cache"
        else:
            cache = "public, max-age=31536000, immutable"
        return _http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", cache)],
        )


def _automation_values_from_request(request: WsRequest) -> dict[str, Any] | None:
    # 从自定义请求头 X-Nanobot-Automation-Values 读取自动化更新字段。
    # 头部可能直接是 JSON，也可能被 URL 编码过，因此先尝试原样解析，失败再 URL 解码后解析。
    # 返回 None 表示格式非法（调用方返回 400），返回 {} 表示未携带该头（不更新任何字段）。
    raw = _case_insensitive_header(request.headers, _AUTOMATION_VALUES_HEADER)
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except Exception:
        try:
            values = json.loads(unquote(raw))
        except Exception:
            return None
    return values if isinstance(values, dict) else None


def _parse_automation_update(
    values: dict[str, Any],
    *,
    current_job: CronJob | None = None,
) -> dict[str, Any] | str:
    """解析自动化更新载荷，返回待更新字段字典或错误字符串。

    仅采集 values 中出现的字段（部分更新语义）。返回 ``str`` 表示校验失败，
    返回 ``dict`` 表示可应用的更新。schedule 字段有特殊处理：若与当前任务完全一致则
    跳过后续校验直接返回（避免无变更时仍触发未来时间/cron 表达式校验）。"""
    update: dict[str, Any] = {}
    if "name" in values:
        raw_name = values.get("name")
        if not isinstance(raw_name, str):
            return "name must be a string"
        name = raw_name.strip()
        if not name:
            return "name cannot be empty"
        update["name"] = name
    if "message" in values:
        raw_message = values.get("message")
        if not isinstance(raw_message, str):
            return "message must be a string"
        message = raw_message.strip()
        if not message:
            return "message cannot be empty"
        update["message"] = message
    if "schedule" in values:
        raw_schedule = values.get("schedule")
        if not isinstance(raw_schedule, dict):
            return "schedule must be an object"
        parsed_schedule = _parse_automation_schedule(raw_schedule)
        if isinstance(parsed_schedule, str):
            return parsed_schedule
        # 调度未发生变化时短路返回：既免去校验开销，也避免对历史 cron 任务
        # 重新做"必须在未来"等校验而误报错误。
        if current_job is not None and _schedule_matches_job(parsed_schedule, current_job):
            return update
        schedule_error = _validate_automation_schedule(parsed_schedule)
        if schedule_error:
            return schedule_error
        update["schedule"] = parsed_schedule
        # 一次性（at）任务运行后应自动删除，故随调度更新一并设置 delete_after_run。
        update["delete_after_run"] = parsed_schedule.kind == "at"
    return update


def _parse_automation_schedule(values: dict[str, Any]) -> CronSchedule | str:
    """把前端提交的调度对象解析为 ``CronSchedule``，失败返回错误字符串。

    支持三种调度类型：``every``（固定间隔毫秒）、``cron``（cron 表达式 + 可选时区）、
    ``at``（一次性，毫秒时间戳）。"""
    raw_kind = values.get("kind")
    if not isinstance(raw_kind, str):
        return "schedule kind must be a string"
    kind = raw_kind.strip()
    if kind == "every":
        every_ms = _positive_int(values.get("every_ms"))
        if every_ms is None:
            return "every schedule requires positive every_ms"
        return CronSchedule(kind="every", every_ms=every_ms)
    if kind == "cron":
        raw_expr = values.get("expr")
        if not isinstance(raw_expr, str):
            return "cron schedule requires expr"
        expr = raw_expr.strip()
        if not expr:
            return "cron schedule requires expr"
        raw_tz = values.get("tz")
        if raw_tz is not None and not isinstance(raw_tz, str):
            return "cron schedule timezone must be a string"
        tz = raw_tz.strip() if isinstance(raw_tz, str) else ""
        return CronSchedule(kind="cron", expr=expr, tz=tz or None)
    if kind == "at":
        at_ms = _positive_int(values.get("at_ms"))
        if at_ms is None:
            return "one-time schedule requires positive at_ms"
        return CronSchedule(kind="at", at_ms=at_ms)
    return "unknown schedule kind"


def _schedule_matches_job(schedule: CronSchedule, job: CronJob) -> bool:
    # 判断新调度是否与任务现有调度完全一致（按类型逐字段比较）。
    # cron 类型的 tz 用 ``or None`` 归一化空串，使空字符串与 None 视为相等。
    current = job.schedule
    if schedule.kind != current.kind:
        return False
    if schedule.kind == "at":
        return schedule.at_ms == current.at_ms
    if schedule.kind == "every":
        return schedule.every_ms == current.every_ms
    if schedule.kind == "cron":
        return (schedule.expr or "") == (current.expr or "") and (
            schedule.tz or None
        ) == (current.tz or None)
    return False


def _validate_automation_schedule(schedule: CronSchedule) -> str | None:
    # 校验调度可行性：一次性任务的时间戳必须在未来；cron 任务用 croniter 验证表达式合法性。
    if schedule.kind == "at":
        if not schedule.at_ms or schedule.at_ms <= int(time.time() * 1000):
            return "one-time schedule must be in the future"
        return None
    if schedule.kind != "cron":
        return None

    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from croniter import croniter

        # 指定时区则用该时区，否则用系统本地时区，保证 get_next 计算的基准时间正确。
        tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
        base = datetime.now(tz=tz)
        croniter(schedule.expr, base).get_next(datetime)
    except Exception:
        return "cron schedule is invalid"
    return None


def _positive_int(value: Any) -> int | None:
    # 显式排除 bool：Python 中 bool 是 int 子类，True/False 会被 isinstance(int) 误判，
    # 此处要求数值型正整数，布尔值视为非法。
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _is_websocket_channel_session_key(key: str) -> bool:
    # 会话键前缀约定：WebUI 相关接口只接受 ``websocket:`` 前缀的会话，
    # 以隔离其他渠道（IM 平台等）的会话历史。
    return key.startswith("websocket:")
