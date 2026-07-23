"""HTTP route adapter for WebUI Settings APIs.

Keep WebUI Settings route handlers here, not in ``channels/websocket.py``.
The websocket channel owns transport concerns; this module owns WebUI Settings
request mapping and response shaping.

WebUI 设置相关 HTTP 路由的适配层。``WebUISettingsRouter`` 把网关收到的设置类
HTTP 请求映射到 ``settings_api`` 等模块的处理函数，并统一负责鉴权、响应封装
以及"需要重启"状态的累计。WebSocket 通道只管传输，本模块只管设置请求的语义，
职责分离便于测试与替换。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.agent.tools.mcp import request_mcp_reload
from nanobot.bus.queue import MessageBus
from nanobot.webui.cli_apps_api import cli_apps_action, cli_apps_payload
from nanobot.webui.http_utils import query_first as _query_first
from nanobot.webui.mcp_presets_api import mcp_presets_settings_action
from nanobot.webui.settings_api import (
    WebUISettingsError,
    create_model_configuration,
    decorate_settings_payload,
    login_oauth_provider,
    logout_oauth_provider,
    provider_models_payload,
    settings_payload,
    settings_usage_payload,
    update_agent_settings,
    update_image_generation_settings,
    update_model_configuration,
    update_network_safety_settings,
    update_provider_settings,
    update_transcription_settings,
    update_web_search_settings,
)
from nanobot.webui.version_check import check_for_update

QueryParams = dict[str, list[str]]

_MCP_VALUES_HEADER = "X-Nanobot-MCP-Values"
_MCP_VALUES_HEADER_MAX_BYTES = 64 * 1024

_MCP_PRESET_ACTIONS_BY_PATH = {
    "/api/settings/mcp-presets/enable": "enable",
    "/api/settings/mcp-presets/remove": "remove",
    "/api/settings/mcp-presets/test": "test",
    "/api/settings/mcp-presets/custom": "custom",
    "/api/settings/mcp-presets/import": "import",
    "/api/settings/mcp-presets/import-cursor": "import-cursor",
    "/api/settings/mcp-presets/tools": "tools",
}


class WebUISettingsRouter:
    """Route WebUI Settings HTTP requests behind a transport-neutral boundary.

    以路径前缀 ``/api/settings`` 为入口分发请求。构造时注入鉴权回调、查询串
    解析器与响应构造器，使路由逻辑不绑定具体传输实现。实例在网关生命周期内
    复用，因此 ``_restart_sections`` 会跨请求累积"已改动需重启"的分区。
    """

    def __init__(
        self,
        *,
        bus: MessageBus,
        logger: Any,
        check_api_token: Callable[[WsRequest], bool],
        parse_query: Callable[[str], QueryParams],
        json_response: Callable[[dict[str, Any]], Response],
        error_response: Callable[[int, str | None], Response],
        runtime_surface: str,
        runtime_capabilities: dict[str, Any],
    ) -> None:
        self.bus = bus
        self.logger = logger
        self._check_api_token = check_api_token
        self._parse_query = parse_query
        self._json_response = json_response
        self._error_response = error_response
        self._runtime_surface = runtime_surface
        self._runtime_capabilities = runtime_capabilities
        self._restart_sections: set[str] = set()

    async def dispatch(self, request: WsRequest, path: str) -> Response | None:
        # 按路径精确匹配分发；未命中返回 None 表示该路由不处理此路径。
        if path == "/api/settings":
            return self._handle_settings(request)
        if path == "/api/settings/usage":
            return self._handle_settings_usage(request)
        if path == "/api/settings/update":
            return self._handle_settings_update(request)
        if path == "/api/settings/model-configurations/create":
            return self._handle_settings_model_configuration_create(request)
        if path == "/api/settings/model-configurations/update":
            return self._handle_settings_model_configuration_update(request)
        if path == "/api/settings/provider/update":
            return self._handle_settings_provider_update(request)
        if path == "/api/settings/provider-models":
            return await self._handle_settings_provider_models(request)
        if path == "/api/settings/provider/oauth-login":
            return await self._handle_settings_provider_oauth(request, "login")
        if path == "/api/settings/provider/oauth-logout":
            return await self._handle_settings_provider_oauth(request, "logout")
        if path == "/api/settings/web-search/update":
            return self._handle_settings_web_search_update(request)
        if path == "/api/settings/image-generation/update":
            return self._handle_settings_image_generation_update(request)
        if path == "/api/settings/transcription/update":
            return self._handle_settings_transcription_update(request)
        if path == "/api/settings/network-safety/update":
            return self._handle_settings_network_safety_update(request)
        if path == "/api/settings/cli-apps":
            return await self._handle_settings_cli_apps(request)
        if path == "/api/settings/cli-apps/install":
            return await self._handle_settings_cli_apps_action(request, "install")
        if path == "/api/settings/cli-apps/update":
            return await self._handle_settings_cli_apps_action(request, "update")
        if path == "/api/settings/cli-apps/uninstall":
            return await self._handle_settings_cli_apps_action(request, "uninstall")
        if path == "/api/settings/cli-apps/test":
            return await self._handle_settings_cli_apps_action(request, "test")
        if path == "/api/settings/mcp-presets":
            return await self._handle_settings_mcp_presets(request)
        if path == "/api/settings/version-check":
            return await self._handle_settings_version_check(request)
        # MCP 预设动作通过路径表反查 action，避免为每个动作单独写分支。
        mcp_action = _MCP_PRESET_ACTIONS_BY_PATH.get(path)
        if mcp_action is not None:
            return await self._handle_settings_mcp_presets(request, mcp_action)
        return None

    def _query(self, request: WsRequest) -> QueryParams:
        return self._parse_query(request.path)

    def _authorized(self, request: WsRequest) -> bool:
        return self._check_api_token(request)

    def _unauthorized(self) -> Response:
        return self._error_response(401, "Unauthorized")

    def _with_restart_state(
        self,
        payload: dict[str, Any],
        *,
        section: str | None = None,
    ) -> dict[str, Any]:
        """Keep restart-required state alive for this gateway process.

        累计"需要重启"的分区：一旦某次更新返回 ``requires_restart``，对应分区
        会被记入 ``_restart_sections`` 并在后续所有设置响应中持续上报，直到
        网关重启。最后通过 ``decorate_settings_payload`` 注入运行时能力与重启
        分区信息。
        """
        if section and payload.get("requires_restart"):
            self._restart_sections.add(section)
        sections = sorted(self._restart_sections)
        payload = dict(payload)
        if sections:
            payload["requires_restart"] = True
        return decorate_settings_payload(
            payload,
            surface=self._runtime_surface,
            runtime_capability_overrides=self._runtime_capabilities,
            restart_required_sections=sections,
        )

    def _parse_mcp_settings_query(self, request: WsRequest) -> QueryParams:
        # MCP 设置可能通过自定义请求头 X-Nanobot-MCP-Values 携带较大 JSON 负载，
        # 这里将其与 URL 查询串合并为统一的 QueryParams 结构供下游处理。
        query = self._query(request)
        raw = request.headers.get(_MCP_VALUES_HEADER)
        if not raw:
            return query
        # 限制头部体积，防止异常大负载拖垮请求处理。
        if len(raw.encode("utf-8")) > _MCP_VALUES_HEADER_MAX_BYTES:
            raise WebUISettingsError("MCP settings payload is too large")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WebUISettingsError("invalid MCP settings payload") from exc
        if not isinstance(payload, dict):
            raise WebUISettingsError("MCP settings payload must be a JSON object")
        merged = {key: list(values) for key, values in query.items()}
        for key, value in payload.items():
            if not isinstance(key, str) or not key:
                raise WebUISettingsError("MCP settings payload contains an invalid key")
            if value is None:
                continue
            # 非字符串值紧凑序列化为单元素列表，与查询串的多值格式对齐。
            if isinstance(value, str):
                text = value.strip()
            else:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if text:
                merged[key] = [text]
        return merged

    def _handle_settings(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(
            self._with_restart_state(
                settings_payload(
                    surface=self._runtime_surface,
                    runtime_capability_overrides=self._runtime_capabilities,
                )
            )
        )

    def _handle_settings_usage(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(settings_usage_payload())

    def _handle_settings_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_agent_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    def _handle_settings_model_configuration_create(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = create_model_configuration(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_model_configuration_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_model_configuration(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_provider_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_provider_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="image"))

    async def _handle_settings_provider_models(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            # provider 拉取模型列表是阻塞的网络调用，放到线程池避免阻塞事件循环。
            payload = await asyncio.to_thread(provider_models_payload, self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("failed to load provider model list")
            return self._error_response(500, "failed to load provider model list")
        return self._json_response(payload)

    async def _handle_settings_provider_oauth(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        try:
            if action == "login":
                payload = await asyncio.to_thread(login_oauth_provider, query)
            else:
                payload = await asyncio.to_thread(logout_oauth_provider, query)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_web_search_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_web_search_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="browser"))

    def _handle_settings_image_generation_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_image_generation_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="image"))

    def _handle_settings_transcription_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_transcription_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_network_safety_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_network_safety_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    async def _handle_settings_cli_apps(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        # installed_only 查询参数接受多种真值表示，统一归一为布尔。
        installed_only = (_query_first(self._query(request), "installed_only") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        try:
            payload = await cli_apps_payload(installed_only=installed_only)
        except Exception:
            self.logger.exception("failed to load CLI Apps payload")
            return self._error_response(500, "failed to load CLI Apps")
        return self._json_response(payload)

    async def _handle_settings_cli_apps_action(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(cli_apps_action, action, self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception as e:
            # 自定义异常携带 status/message；其他异常按 500 处理并仅在服务端错误时记堆栈。
            status = getattr(e, "status", 500)
            message = getattr(e, "message", str(e))
            if status >= 500:
                self.logger.exception("CLI Apps action '{}' failed", action)
            return self._error_response(status, message)
        return self._json_response(payload)

    async def _handle_settings_mcp_presets(
        self,
        request: WsRequest,
        action: str | None = None,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await mcp_presets_settings_action(
                action,
                self._parse_mcp_settings_query(request),
                # 以 lambda 延迟触发 MCP 重载，交由具体动作决定是否在改动后刷新。
                reload_mcp=lambda: request_mcp_reload(self.bus),
            )
        except Exception as e:
            status = getattr(e, "status", 500)
            message = getattr(e, "message", str(e))
            if status >= 500:
                self.logger.exception("MCP preset action '{}' failed", action or "list")
            return self._error_response(status, message)
        if action is None:
            return self._json_response(payload)
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    async def _handle_settings_version_check(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            update_info = await asyncio.to_thread(check_for_update)
        except Exception:
            self.logger.exception("version check failed")
            return self._error_response(500, "version check failed")
        return self._json_response({
            "updateAvailable": update_info,
        })
