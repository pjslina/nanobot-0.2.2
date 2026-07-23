"""Settings REST helpers for the WebUI HTTP surface.

The WebSocket channel owns transport/authentication. This module owns the
settings payload shape and the allowlisted config mutations exposed to WebUI.
"""

# 本模块是 WebUI 设置页面的后端 HTTP 辅助层。职责有二：
# 1) 读取 `~/.nanobot/config.json` 并组装成 WebUI 可渲染的设置 payload（`settings_payload`）；
# 2) 接收 WebUI 提交的查询参数，在白名单范围内校验并修改 config，再原子写回磁盘。
# WebSocket 通道负责传输与鉴权，本模块只关心"设置数据的形状"与"允许的配置变更"，
# 不直接处理 HTTP 路由，而是被 gateway/api 层调用。

from __future__ import annotations

import os
import re
import time
from contextlib import suppress
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from nanobot import __version__
from nanobot.audio.transcription import resolve_transcription_config
from nanobot.audio.transcription_registry import (
    resolve_transcription_provider,
    transcription_provider_names,
)
from nanobot.config.loader import get_config_path, load_config, save_config
from nanobot.config.schema import ModelPresetConfig, ProviderConfig
from nanobot.providers.image_generation import (
    get_image_gen_provider,
    image_gen_provider_names,
)
from nanobot.providers.registry import PROVIDERS, create_dynamic_spec, find_by_name
from nanobot.security.workspace_access import workspace_sandbox_status
from nanobot.webui.token_usage import token_usage_payload
from nanobot.webui.workspaces import (
    read_webui_default_access_mode,
    write_webui_default_access_mode,
)

QueryParams = dict[str, list[str]]
RuntimeSurface = Literal["browser", "native"]


def _version_payload() -> dict[str, Any]:
    """Return version info for the settings payload."""
    return {
        "current": __version__,
    }

# 浏览器端运行时能力开关：WebUI 在浏览器中无法重启引擎、选择目录等，全部为 False。
_RUNTIME_CAPABILITIES = {
    "can_restart_engine": False,
    "can_pick_folder": False,
    "can_open_logs": False,
    "can_export_diagnostics": False,
}

# 原生桌面端运行时能力：继承浏览器端默认值，并放开引擎重启、目录选择等本地能力。
_NATIVE_RUNTIME_CAPABILITIES = {
    **_RUNTIME_CAPABILITIES,
    "can_restart_engine": True,
    "can_pick_folder": True,
    "can_open_logs": True,
    "can_export_diagnostics": True,
}

# 浏览器端各设置分区应用后所需的"重启行为"：none=无需重启，engineRestart=需重启引擎，
# appRestart=需重启整个应用。WebUI 据此提示用户。
_BROWSER_RESTART_BEHAVIOR_BY_SECTION = {
    "appearance": "none",
    "models": "none",
    "providers": "none",
    "runtime": "engineRestart",
    "browser": "engineRestart",
    "image": "engineRestart",
    "apps": "engineRestart",
    "advanced": "appRestart",
}

# 原生端重启行为：与浏览器端基本一致，因为原生端同样需要重启引擎使配置生效。
_NATIVE_RESTART_BEHAVIOR_BY_SECTION = {
    **_BROWSER_RESTART_BEHAVIOR_BY_SECTION,
    "runtime": "engineRestart",
    "browser": "engineRestart",
    "image": "engineRestart",
    "apps": "engineRestart",
}

# Web 搜索 provider 选项表：credential 字段标识该 provider 需要的凭据类型
# （none=无需凭据、api_key=必填密钥、base_url=自建服务地址、optional_api_key=可选密钥）。
# 切换 provider 时据此决定保留/清空 api_key 与 base_url。
_WEB_SEARCH_PROVIDER_OPTIONS: tuple[dict[str, str], ...] = (
    {"name": "duckduckgo", "label": "DuckDuckGo", "credential": "none"},
    {"name": "brave", "label": "Brave Search", "credential": "api_key"},
    {"name": "tavily", "label": "Tavily", "credential": "api_key"},
    {"name": "searxng", "label": "SearXNG", "credential": "base_url"},
    {"name": "jina", "label": "Jina", "credential": "api_key"},
    {"name": "kagi", "label": "Kagi", "credential": "api_key"},
    {"name": "exa", "label": "Exa", "credential": "api_key"},
    {"name": "olostep", "label": "Olostep", "credential": "api_key"},
    {"name": "bocha", "label": "Bocha", "credential": "api_key"},
    {"name": "volcengine", "label": "Volcengine Search", "credential": "api_key"},
    {"name": "keenable", "label": "Keenable", "credential": "optional_api_key"},
)
_WEB_SEARCH_PROVIDER_BY_NAME = {
    provider["name"]: provider for provider in _WEB_SEARCH_PROVIDER_OPTIONS
}

_IMAGE_GENERATION_ASPECT_RATIOS = {
    "1:1",
    "3:4",
    "9:16",
    "4:3",
    "16:9",
    "3:2",
    "2:3",
    "21:9",
}
_CONTEXT_WINDOW_TOKEN_OPTIONS = {65_536, 200_000, 262_144}
_MODEL_CONFIGURATION_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_MODEL_LIST_UNSUPPORTED_BACKENDS = {
    "anthropic",
    "azure_openai",
    "bedrock",
    "github_copilot",
    "openai_codex",
}

# 下列 provider 提供"目录式"模型列表：通过聚合网关暴露多家厂商的模型。
_MODEL_LIST_CATALOG_PROVIDERS = {
    "aihubmix",
    "byteplus",
    "byteplus_coding_plan",
    "huggingface",
    "novita",
    "openrouter",
    "siliconflow",
    "volcengine",
    "volcengine_coding_plan",
}

# 下列 provider 提供"官方"模型列表：直接对接厂商官方 /models 接口。
_MODEL_LIST_OFFICIAL_PROVIDERS = {
    "ant_ling",
    "dashscope",
    "deepseek",
    "gemini",
    "groq",
    "longcat",
    "minimax",
    "minimax_anthropic",
    "mistral",
    "moonshot",
    "nvidia",
    "openai",
    "qianfan",
    "skywork",
    "stepfun",
    "xiaomi_mimo",
    "zhipu",
}


class WebUISettingsError(ValueError):
    """User-facing settings validation failure.

    面向用户的设置校验失败异常。`status` 字段携带建议的 HTTP 状态码（默认 400），
    供上层 HTTP 处理器直接映射为响应码，区分"参数错误"与"服务端缺失依赖"等场景。"""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _normalize_surface(surface: str | None) -> RuntimeSurface:
    # 将多种调用方传入的"运行表面"名称归一化为 browser/native 两种，便于统一判定能力。
    return "native" if surface in {"native", "desktop"} else "browser"


def runtime_capabilities(
    surface: str | None = "browser",
    overrides: dict[str, Any] | None = None,
) -> dict[str, bool]:
    """Return the capability flags exposed to the WebUI runtime.

    返回暴露给 WebUI 的运行时能力开关。先按 surface 选浏览器端/原生端基线，
    再用 overrides 覆盖已知键（忽略未知键，避免前端误传字段污染结果）。"""
    base = (
        _NATIVE_RUNTIME_CAPABILITIES
        if _normalize_surface(surface) == "native"
        else _RUNTIME_CAPABILITIES
    )
    result = dict(base)
    for key, value in (overrides or {}).items():
        # 仅允许覆盖已存在的键，防止前端注入未知能力字段。
        if key in result:
            result[key] = bool(value)
    return result


def restart_behavior_by_section(surface: str | None = "browser") -> dict[str, str]:
    return dict(
        _NATIVE_RESTART_BEHAVIOR_BY_SECTION
        if _normalize_surface(surface) == "native"
        else _BROWSER_RESTART_BEHAVIOR_BY_SECTION
    )


def decorate_settings_payload(
    payload: dict[str, Any],
    *,
    surface: str | None = "browser",
    runtime_capability_overrides: dict[str, Any] | None = None,
    restart_required_sections: list[str] | None = None,
    apply_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach runtime-surface metadata without changing the core settings shape.

    在不改动核心设置数据结构的前提下，为 payload 追加运行时表面元数据：
    surface、能力开关、各分区重启行为、待重启分区列表以及 apply_state（应用状态机）。
    这些字段供前端决定是否展示"需要重启"提示与重启按钮。"""
    surface_value = _normalize_surface(surface)
    sections = restart_required_sections
    if sections is None:
        # 调用方未显式传入时，回退到 payload 中已有的待重启分区（可能由上层累积）。
        raw_sections = payload.get("restart_required_sections") or []
        sections = [str(section) for section in raw_sections if isinstance(section, str)]
    # 去重并排序，保证前端拿到稳定的分区列表（dict.fromkeys 保序去重）。
    sections = sorted(dict.fromkeys(sections))
    result = dict(payload)
    result["surface"] = surface_value
    result["runtime_surface"] = surface_value
    result["runtime_capabilities"] = runtime_capabilities(
        surface_value,
        runtime_capability_overrides,
    )
    result["restart_behavior_by_section"] = restart_behavior_by_section(surface_value)
    result["restart_required_sections"] = sections
    if sections:
        # 存在待重启分区则必定需要重启；否则沿用 payload 原有的 requires_restart 标志。
        result["requires_restart"] = True
    else:
        result["requires_restart"] = bool(result.get("requires_restart", False))
    result["apply_state"] = apply_state or {
        # 应用状态机：有待重启分区时进入 pending（等待用户确认重启），否则 idle。
        "status": "pending" if result["requires_restart"] else "idle",
        "sections": sections,
    }
    return result


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _query_first_alias(query: QueryParams, snake: str, camel: str) -> str | None:
    value = _query_first(query, snake)
    return _query_first(query, camel) if value is None else value


def _mask_secret_hint(secret: str | None) -> str | None:
    # 对敏感凭据做脱敏处理，仅返回前4+后4字符的提示形式，供前端展示"已配置"状态。
    # 过短的密钥（≤8 字符）只返回固定掩码，避免泄露全部内容。空值返回 None。
    if not secret:
        return None
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}••••{secret[-4:]}"


def _resolve_env_placeholders(value: str | None) -> str | None:
    # 解析字符串中的 ${ENV_VAR} 引用为实际环境变量值，用于在探测 provider 模型列表时
    # 把配置里引用的环境变量展开成真实凭据。若引用的变量不存在则标记 missing。
    if not value:
        return None
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        env_value = os.environ.get(match.group(1))
        if env_value is None:
            # 引用的环境变量未设置：置 missing 标志并用空串占位。
            missing = True
            return ""
        return env_value

    resolved = _ENV_REF_RE.sub(replace, value).strip()
    # 只有当存在未解析变量且最终结果为空时才视为 None（即整体不可用）；
    # 部分解析成功则保留已解析内容。
    if missing and not resolved:
        return None
    return resolved or None


def _provider_requires_api_key(spec: Any) -> bool:
    # 判断 provider 是否必须配置 api_key：Azure 用 endpoint+deployment、OAuth 走令牌、
    # 本地/直连后端通常无需密钥，其余云端 provider 默认需要。
    if spec.name == "azure_openai":
        return False
    if spec.is_oauth:
        return False
    if spec.is_local or spec.is_direct:
        return False
    return True


def _provider_requires_api_base(spec: Any) -> bool:
    # 判断 provider 是否必须显式配置 api_base：Azure 必填；
    # openai_compat 直连后端且 spec 未提供默认地址时也必填。
    if spec.name == "azure_openai":
        return True
    return bool(spec.backend == "openai_compat" and spec.is_direct and not spec.default_api_base)


def _oauth_provider_status(spec: Any) -> dict[str, Any]:
    # 查询 OAuth provider 的登录状态：读取本地令牌存储，判断 access 令牌是否可用、
    # 是否即将/已经过期（expires_at 为毫秒时间戳）。不同 provider 令牌来源不同，
    # 故按 spec.name 分支处理；任何读取异常都降级为"未配置"。
    if not getattr(spec, "is_oauth", False):
        return {"configured": False, "account": None, "expires_at": None, "login_supported": False}

    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
            from oauth_cli_kit.storage import FileTokenStorage
        except Exception:
            # 依赖缺失：降级为不支持登录。
            return {
                "configured": False,
                "account": None,
                "expires_at": None,
                "login_supported": False,
            }
        token = None
        with suppress(Exception):
            token = FileTokenStorage(
                token_filename=OPENAI_CODEX_PROVIDER.token_filename,
            ).load()
        expires_at = getattr(token, "expires", None) if token else None
        now_ms = int(time.time() * 1000)
        return {
            # 视为已配置：存在 access 令牌，且（有 refresh 令牌可续期 或 access 尚未过期）。
            "configured": bool(
                token
                and token.access
                and (getattr(token, "refresh", None) or (expires_at and expires_at > now_ms))
            ),
            "account": getattr(token, "account_id", None) if token else None,
            "expires_at": expires_at,
            "login_supported": True,
        }

    if spec.name == "github_copilot":
        try:
            from nanobot.providers.github_copilot_provider import get_github_copilot_login_status
        except Exception:
            return {
                "configured": False,
                "account": None,
                "expires_at": None,
                "login_supported": False,
            }
        token = None
        with suppress(Exception):
            token = get_github_copilot_login_status()
        return {
            "configured": bool(token and token.access and token.expires > int(time.time() * 1000)),
            "account": getattr(token, "account_id", None) if token else None,
            "expires_at": getattr(token, "expires", None) if token else None,
            "login_supported": True,
        }

    return {"configured": False, "account": None, "expires_at": None, "login_supported": False}


def _provider_configured_for_settings(spec: Any, provider_config: Any) -> bool:
    # 判断 provider 在设置页是否显示为"已配置"：OAuth 看令牌状态；
    # 需 api_base 的看 api_base；需 api_key 的看 api_key；
    # 其余 provider 只要填了 api_key/api_base/region/profile 任一即视为已配置。
    if spec.is_oauth:
        return bool(_oauth_provider_status(spec)["configured"])
    if _provider_requires_api_base(spec):
        return bool(provider_config.api_base)
    if _provider_requires_api_key(spec):
        return bool(provider_config.api_key)
    return bool(
        provider_config.api_key
        or provider_config.api_base
        or getattr(provider_config, "region", None)
        or getattr(provider_config, "profile", None)
    )


def _dynamic_provider_items(config: Any) -> list[tuple[str, ProviderConfig]]:
    # 提取 config.providers 中通过 model_extra 存储的"动态 provider"（即非内置 spec、
    # 用户自定义的 openai_compat 后端）。Pydantic 把未声明字段放在 model_extra 里。
    return [
        (name, provider_config)
        for name, provider_config in (config.providers.model_extra or {}).items()
        if isinstance(provider_config, ProviderConfig)
    ]


def _resolve_settings_provider(
    config: Any,
    provider_name: str,
) -> tuple[Any, str, ProviderConfig] | None:
    # 按名称解析 provider：先在内置 PROVIDERS 中查找（spec.name）；
    # 找不到再遍历动态 provider，名称匹配时（兼容连字符/下划线差异）用 create_dynamic_spec
    # 生成临时 spec。返回 (spec, 实际存储键名, provider_config) 三元组，未找到返回 None。
    spec = find_by_name(provider_name)
    if spec is not None:
        provider_config = getattr(config.providers, spec.name, None)
        if isinstance(provider_config, ProviderConfig):
            return spec, spec.name, provider_config
        return None

    normalized = provider_name.replace("-", "_")
    for extra_name, provider_config in _dynamic_provider_items(config):
        if provider_name == extra_name or normalized == extra_name.replace("-", "_"):
            return create_dynamic_spec(extra_name), extra_name, provider_config
    return None


def _provider_settings_row(
    name: str,
    spec: Any,
    provider_config: ProviderConfig,
) -> dict[str, Any]:
    # 组装单个 provider 在设置页的展示行：标签、是否已配置、鉴权类型、api_key 脱敏提示、
    # api_base 等。OAuth provider 额外附带账号与过期时间。openai 额外附带 api_type。
    oauth_status = _oauth_provider_status(spec) if spec.is_oauth else None
    row = {
        "name": name,
        "label": spec.label,
        "configured": (
            bool(oauth_status["configured"])
            if oauth_status is not None
            else _provider_configured_for_settings(spec, provider_config)
        ),
        "auth_type": "oauth" if spec.is_oauth else "api_key",
        "api_key_required": _provider_requires_api_key(spec),
        "api_key_hint": _mask_secret_hint(provider_config.api_key),
        "api_base": provider_config.api_base,
        "default_api_base": spec.default_api_base or None,
        "model_selectable": not spec.is_transcription_only,
    }
    if oauth_status is not None:
        row["oauth_account"] = oauth_status["account"]
        row["oauth_expires_at"] = oauth_status["expires_at"]
        row["oauth_login_supported"] = oauth_status["login_supported"]
    if spec.name == "openai":
        row["api_type"] = provider_config.api_type
    return row


def _model_catalog_kind(spec: Any) -> str:
    # 判定 provider 的模型列表"目录类型"，用于前端区分展示：
    # catalog=聚合网关、official=官方接口、local=本地、custom=用户自定义直连。
    if spec.name in _MODEL_LIST_CATALOG_PROVIDERS:
        return "catalog"
    if spec.name in _MODEL_LIST_OFFICIAL_PROVIDERS:
        return "official"
    if spec.is_local:
        return "local"
    if spec.is_direct:
        return "custom"
    if spec.is_gateway:
        return "catalog"
    return "official"


def _model_id_from_row(row: Any) -> str | None:
    if isinstance(row, str):
        return row.strip() or None
    if not isinstance(row, dict):
        return None
    for key in ("id", "name", "model"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _model_context_window(row: Any) -> int | None:
    # 从模型行数据中提取上下文窗口大小：不同 provider 返回的 JSON 字段名不一致，
    # 故依次尝试多个候选键，取首个为正整数的值（兼容 float 转 int）。
    if not isinstance(row, dict):
        return None
    for key in (
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "max_input_tokens",
    ):
        value = row.get(key)
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value > 0:
            return int(value)
    return None


def _model_row_payload(row: Any) -> dict[str, Any] | None:
    # 将 provider 返回的单条模型记录归一化为统一 payload：id、展示标签（与 id 相同则置空）、
    # 所属厂商、上下文窗口。label 仅在 display_name 与 id 不同时才保留，避免冗余。
    model_id = _model_id_from_row(row)
    if not model_id:
        return None
    label: str | None = None
    owned_by: str | None = None
    if isinstance(row, dict):
        raw_label = row.get("display_name") or row.get("label") or row.get("name")
        if isinstance(raw_label, str) and raw_label.strip() and raw_label.strip() != model_id:
            label = raw_label.strip()
        raw_owner = row.get("owned_by") or row.get("owner") or row.get("organization")
        if isinstance(raw_owner, str) and raw_owner.strip():
            owned_by = raw_owner.strip()
    return {
        "id": model_id,
        "label": label,
        "owned_by": owned_by,
        "context_window": _model_context_window(row),
    }


def _extract_model_rows(body: Any) -> list[dict[str, Any]]:
    # 从 provider 的 /models 响应中提取去重后的模型列表。
    # OpenAI 风格响应是 {"data": [...]}，也兼容直接返回数组的情况。
    raw_rows = body.get("data") if isinstance(body, dict) else body
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_row in raw_rows:
        row = _model_row_payload(raw_row)
        if row is None or row["id"] in seen:
            # 跳过无法识别或重复的模型 id。
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows


def provider_models_payload(query: QueryParams) -> dict[str, Any]:
    """Fetch an OpenAI-compatible provider's model list for Settings.

    The result is advisory only: users can always type a custom model id. This
    helper deliberately avoids mutating config so probing model lists never
    changes runtime behavior.

    向指定 provider 发起一次 /models 探测请求，用于设置页的模型选择下拉框。
    结果仅作"建议"用途（用户始终可手输模型 id），且全程不写 config，避免探测行为
    影响运行时。流程：解析 provider -> 校验是否支持列表 -> 拼 api_base/api_key ->
    发请求 -> 归一化结果。每一步失败都返回结构化的 status+message，供前端友好提示。"""
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    config = load_config()
    resolved_provider = _resolve_settings_provider(config, provider_name)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, provider_key, provider_config = resolved_provider

    base_payload: dict[str, Any] = {
        "provider": provider_key,
        "label": spec.label,
        "catalog_kind": _model_catalog_kind(spec),
        "models": [],
        "model_count": 0,
        "message": None,
        "fetched_at": time.time(),
    }
    if (
        spec.is_transcription_only
        or (
            spec.backend in _MODEL_LIST_UNSUPPORTED_BACKENDS
            and spec.name != "minimax_anthropic"
        )
        or spec.is_oauth
    ):
        # 该后端不支持 /models 列表（Anthropic/Azure/Bedrock/Copilot/Codex/OAuth provider），
        # 提示用户手动输入模型 id。minimax_anthropic 虽在 unsupported 集合中但走兼容接口，故豁免。
        return {
            **base_payload,
            "status": "unsupported",
            "catalog_kind": "unsupported",
            "message": "Model list is not available for this provider. Type a model ID manually.",
        }

    api_base = _resolve_env_placeholders(provider_config.api_base) or spec.default_api_base
    if spec.name == "openai" and not api_base:
        # openai 未配置 api_base 时回退到官方端点。
        api_base = "https://api.openai.com/v1"
    if not api_base:
        return {
            **base_payload,
            "status": "missing_api_base",
            "message": "Configure an API base URL to load models.",
        }

    api_key = _resolve_env_placeholders(provider_config.api_key)
    if _provider_requires_api_key(spec) and not api_key:
        return {
            **base_payload,
            "status": "not_configured",
            "message": "Configure this provider before loading models.",
        }

    headers = {"Accept": "application/json"}
    if api_key:
        if spec.name == "minimax_anthropic":
            # minimax_anthropic 用 X-Api-Key 头而非 Bearer。
            headers["X-Api-Key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    models_url = f"{api_base.rstrip('/')}/models"
    if spec.name == "minimax_anthropic" and not api_base.rstrip("/").endswith("/v1"):
        # minimax_anthropic 的模型列表接口需补 /v1 前缀。
        models_url = f"{api_base.rstrip('/')}/v1/models"

    try:
        # 短超时探测：禁止跟随重定向以避免凭据泄漏到未知主机。
        response = httpx.get(
            models_url,
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        rows = _extract_model_rows(response.json())
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            # 鉴权失败：凭据无效或权限不足。
            return {
                **base_payload,
                "status": "not_configured",
                "message": "The provider rejected the configured credential.",
            }
        return {
            **base_payload,
            "status": "error",
            "message": f"Model list request failed with HTTP {status}.",
        }
    except (httpx.HTTPError, ValueError) as exc:
        # 网络错误或响应非 JSON：统一归为 error。
        return {
            **base_payload,
            "status": "error",
            "message": f"Could not load models: {exc}",
        }

    return {
        **base_payload,
        "status": "available",
        "models": rows,
        "model_count": len(rows),
    }


def _parse_bool(value: str, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"1", "0", "true", "false", "yes", "no"}:
        raise WebUISettingsError(f"{field} must be boolean")
    return normalized in {"1", "true", "yes"}


def _parse_context_window_tokens(value: str | None) -> int | None:
    # 解析 context_window_tokens 并限定为白名单三选一（65536/200000/262144），
    # 防止用户填入 provider 不支持的值。
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        raise WebUISettingsError("context_window_tokens must be an integer") from None
    if parsed not in _CONTEXT_WINDOW_TOKEN_OPTIONS:
        raise WebUISettingsError("context_window_tokens must be 65536, 200000, or 262144")
    return parsed


def _model_configuration_slug(label: str) -> str:
    # 将用户输入的配置名称转为 slug：小写化、非 [a-z0-9_-] 字符替换为连字符、去首尾连字符。
    # 禁止保留名 "default"，长度上限 48 字符。slug 作为 model_presets 的存储键名。
    normalized = _MODEL_CONFIGURATION_SLUG_RE.sub("-", label.strip().lower())
    normalized = normalized.strip("-_")
    if not normalized:
        raise WebUISettingsError("configuration name is required")
    if normalized == "default":
        raise WebUISettingsError("configuration name is reserved")
    if len(normalized) > 48:
        normalized = normalized[:48].rstrip("-_")
    return normalized


def _validate_configured_provider(config: Any, provider: str) -> None:
    # 校验切换到的 provider 合法且已配置：auto 直接放行；其余需存在 spec、支持聊天模型、
    # 且凭据已就绪，否则拒绝切换以避免运行时报错。
    if provider == "auto":
        return
    resolved_provider = _resolve_settings_provider(config, provider)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, _, provider_config = resolved_provider
    if spec.is_transcription_only:
        raise WebUISettingsError("provider does not support chat models")
    if not _provider_configured_for_settings(spec, provider_config):
        raise WebUISettingsError("provider is not configured")


def _image_generation_provider_rows(config: Any) -> list[dict[str, Any]]:
    # 构建图像生成 provider 的展示行列表。configured 判定优先用通用逻辑
    # （_provider_configured_for_settings），spec 或配置缺失时退化为仅看 api_key 是否存在。
    rows: list[dict[str, Any]] = []
    for name in image_gen_provider_names():
        spec = find_by_name(name)
        provider_config = getattr(config.providers, name, None)
        configured = (
            _provider_configured_for_settings(spec, provider_config)
            if spec is not None and provider_config is not None
            else bool(getattr(provider_config, "api_key", None))
        )
        rows.append(
            {
                "name": name,
                "label": spec.label if spec is not None else name,
                "configured": configured,
                "auth_type": "oauth" if spec is not None and spec.is_oauth else "api_key",
                "api_key_hint": _mask_secret_hint(
                    getattr(provider_config, "api_key", None)
                ),
                "api_base": getattr(provider_config, "api_base", None),
                "default_api_base": (
                    spec.default_api_base if spec and spec.default_api_base else None
                ),
            }
        )
    return rows


_DEFAULT_REASONING_EFFORT_VALUES: tuple[str, ...] = ("", "low", "medium", "high")


def _reasoning_effort_values_for(provider_name: str, model: str) -> list[str]:
    """Return user-facing reasoning_effort options for this provider+model.

    Mistral chat models accept only "high"/"none"; Magistral rejects the
    kwarg entirely (reasoning is implicit). For everyone else, return the
    full OpenAI vocab.

    返回该 provider+model 在 UI 上可选的 reasoning_effort 取值集合：
    - 隐式推理模型（如 Magistral）推理常开，只给 "Default"（空串）；
    - 带 reasoning_effort_remap 的 provider（如 Mistral）把 OpenAI 词表映射成自有词表，
      这里反向取出实际下发的非 none 值作为可选项；
    - 其余 provider 返回标准 OpenAI 词表（""/low/medium/high）。"""
    spec = find_by_name(provider_name) if provider_name else None
    if spec is None:
        return list(_DEFAULT_REASONING_EFFORT_VALUES)

    model_lower = (model or "").lower()
    implicit = getattr(spec, "implicit_reasoning_models", ())
    if implicit and any(pat in model_lower for pat in implicit):
        # Reasoning is always on; only "Default" makes sense.
        return [""]

    remap = getattr(spec, "reasoning_effort_remap", ())
    if remap:
        # Reverse the remap: surface the distinct wire-vocab outputs as the
        # user's options. Mistral collapses to "high"/"none" → UI shows
        # "Default" + "High".
        wire_values: list[str] = []
        for _user_val, wire_val in remap:
            if wire_val and wire_val != "none" and wire_val not in wire_values:
                wire_values.append(wire_val)
        return ["", *wire_values]

    return list(_DEFAULT_REASONING_EFFORT_VALUES)


def _transcription_provider_rows(config: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in transcription_provider_names():
        spec = find_by_name(name)
        provider_config = getattr(config.providers, name, None)
        rows.append({
            "name": name,
            "label": spec.label if spec is not None else name,
            "configured": bool(getattr(provider_config, "api_key", None)),
            "api_key_hint": _mask_secret_hint(getattr(provider_config, "api_key", None)),
            "api_base": getattr(provider_config, "api_base", None),
            "default_api_base": spec.default_api_base if spec and spec.default_api_base else None,
        })
    return rows


def settings_payload(
    *,
    requires_restart: bool = False,
    surface: str | None = "browser",
    runtime_capability_overrides: dict[str, Any] | None = None,
    restart_required_sections: list[str] | None = None,
    apply_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 组装完整的设置 payload（设置页主数据源）。读取 config 后逐分区构建：
    # agent（模型/provider/预设）、providers 列表、web_search、image_generation、
    # transcription、runtime、usage、advanced 等。最后用 decorate_settings_payload
    # 追加运行时元数据。所有敏感字段经 _mask_secret_hint 脱敏后才输出。
    config = load_config()
    defaults = config.agents.defaults
    active_preset_name = defaults.model_preset or "default"
    try:
        # 解析当前生效的预设：可能是 default 或某个具名预设。解析失败时回退到默认预设，
        # 并把 active 标记为 "default"，避免脏配置导致整页加载失败。
        effective_preset = config.resolve_preset()
    except Exception:
        effective_preset = config.resolve_default_preset()
        active_preset_name = "default"

    # 解析实际生效的 provider 名称与配置对象。get_provider_name 优先按模型推断，
    # 推断不到再用预设里显式声明的 provider。selected_provider 是规整后的展示名。
    provider_name = (
        config.get_provider_name(effective_preset.model, preset=effective_preset)
        or effective_preset.provider
    )
    provider = config.get_provider(effective_preset.model, preset=effective_preset)
    selected_provider = provider_name
    if effective_preset.provider != "auto":
        spec = find_by_name(effective_preset.provider)
        selected_provider = spec.name if spec else provider_name

    # 内置 provider 与动态 provider 合并成展示列表。内置 spec 无配置项的跳过。
    providers = []
    for spec in PROVIDERS:
        provider_config = getattr(config.providers, spec.name, None)
        if provider_config is None:
            continue
        providers.append(_provider_settings_row(spec.name, spec, provider_config))
    for provider_key, provider_config in _dynamic_provider_items(config):
        providers.append(
            _provider_settings_row(
                provider_key,
                create_dynamic_spec(provider_key),
                provider_config,
            )
        )

    search_config = config.tools.web.search
    image_config = config.tools.image_generation
    transcription = resolve_transcription_config(config)
    # 搜索 provider 不在白名单时回退到 duckduckgo（无需凭据）。
    search_provider = (
        search_config.provider
        if search_config.provider in _WEB_SEARCH_PROVIDER_BY_NAME
        else "duckduckgo"
    )
    image_providers = _image_generation_provider_rows(config)
    selected_image_provider = next(
        (
            provider
            for provider in image_providers
            if provider["name"] == image_config.provider
        ),
        None,
    )
    # 预设列表首个永远是 "default" 预设（由 defaults 字段构成），其后追加用户自定义预设。
    model_presets = [
        {
            "name": "default",
            "label": "Default",
            "active": active_preset_name == "default",
            "is_default": True,
            "model": defaults.model,
            "provider": defaults.provider,
            "max_tokens": defaults.max_tokens,
            "context_window_tokens": defaults.context_window_tokens,
            "temperature": defaults.temperature,
            "reasoning_effort": defaults.reasoning_effort,
            "reasoning_effort_values": _reasoning_effort_values_for(
                defaults.provider, defaults.model
            ),
        }
    ]
    for name, preset in config.model_presets.items():
        model_presets.append(
            {
                "name": name,
                "label": preset.label or name,
                "active": active_preset_name == name,
                "is_default": False,
                "model": preset.model,
                "provider": preset.provider,
                "max_tokens": preset.max_tokens,
                "context_window_tokens": preset.context_window_tokens,
                "temperature": preset.temperature,
                "reasoning_effort": preset.reasoning_effort,
                "reasoning_effort_values": _reasoning_effort_values_for(
                    preset.provider, preset.model
                ),
            }
        )

    exec_config = config.tools.exec
    sandbox_status = workspace_sandbox_status(
        restrict_to_workspace=config.tools.restrict_to_workspace,
        workspace=config.workspace_path,
    )
    payload = {
        "agent": {
            "model": effective_preset.model,
            "provider": selected_provider,
            "resolved_provider": provider_name,
            "has_api_key": bool(provider and provider.api_key),
            "model_preset": active_preset_name,
            "max_tokens": effective_preset.max_tokens,
            "context_window_tokens": effective_preset.context_window_tokens,
            "temperature": effective_preset.temperature,
            "reasoning_effort": effective_preset.reasoning_effort,
            "timezone": defaults.timezone,
            "bot_name": defaults.bot_name,
            "bot_icon": defaults.bot_icon,
            "tool_hint_max_length": defaults.tool_hint_max_length,
        },
        "model_presets": model_presets,
        "providers": providers,
        "web_search": {
            "provider": search_provider,
            "api_key_hint": _mask_secret_hint(search_config.api_key),
            "base_url": search_config.base_url or None,
            "max_results": search_config.max_results,
            "timeout": search_config.timeout,
            "providers": list(_WEB_SEARCH_PROVIDER_OPTIONS),
        },
        "web": {
            "enable": config.tools.web.enable,
            "proxy": config.tools.web.proxy,
            "user_agent": config.tools.web.user_agent,
            "search": {
                "max_results": search_config.max_results,
                "timeout": search_config.timeout,
            },
            "fetch": {
                "use_jina_reader": config.tools.web.fetch.use_jina_reader,
            },
        },
        "image_generation": {
            "enabled": image_config.enabled,
            "provider": image_config.provider,
            "provider_configured": bool(
                selected_image_provider and selected_image_provider["configured"]
            ),
            "model": image_config.model,
            "default_aspect_ratio": image_config.default_aspect_ratio,
            "default_image_size": image_config.default_image_size,
            "max_images_per_turn": image_config.max_images_per_turn,
            "save_dir": image_config.save_dir,
            "providers": image_providers,
        },
        "transcription": {
            "enabled": transcription.enabled,
            "provider": transcription.provider,
            "provider_configured": transcription.configured,
            "model": transcription.model,
            "language": transcription.language,
            "max_duration_sec": transcription.max_duration_sec,
            "max_upload_mb": transcription.max_upload_mb,
            "providers": _transcription_provider_rows(config),
        },
        "runtime": {
            "config_path": str(get_config_path().expanduser()),
            "workspace_path": str(config.workspace_path),
            "gateway_host": config.gateway.host,
            "gateway_port": config.gateway.port,
            "heartbeat": {
                "enabled": config.gateway.heartbeat.enabled,
                "interval_s": config.gateway.heartbeat.interval_s,
                "keep_recent_messages": config.gateway.heartbeat.keep_recent_messages,
            },
            "dream": {
                "schedule": defaults.dream.describe_schedule(),
            },
            "unified_session": defaults.unified_session,
        },
        "usage": token_usage_payload(timezone_name=defaults.timezone),
        "advanced": {
            "restrict_to_workspace": config.tools.restrict_to_workspace,
            "workspace_sandbox": sandbox_status.as_dict(),
            "webui_allow_local_service_access": config.tools.webui_allow_local_service_access,
            "allow_local_preview_access": config.tools.webui_allow_local_service_access,
            "webui_default_access_mode": read_webui_default_access_mode(),
            "private_service_protection_enabled": True,
            "ssrf_whitelist_count": len(config.tools.ssrf_whitelist),
            "mcp_server_count": len(config.tools.mcp_servers),
            "exec_enabled": exec_config.enable,
            "exec_sandbox": exec_config.sandbox or None,
            "exec_path_prepend_set": bool(exec_config.path_prepend),
            "exec_path_append_set": bool(exec_config.path_append),
        },
        "requires_restart": requires_restart,
        "version": _version_payload(),
    }
    return decorate_settings_payload(
        payload,
        surface=surface,
        runtime_capability_overrides=runtime_capability_overrides,
        restart_required_sections=restart_required_sections,
        apply_state=apply_state,
    )


def settings_usage_payload() -> dict[str, Any]:
    """Return the lightweight token usage slice for Overview refreshes.

    返回 Overview 页面刷新所需的轻量 token 用量切片，仅读 config 取时区，
    不构建完整 payload，降低刷新开销。"""
    config = load_config()
    return token_usage_payload(timezone_name=config.agents.defaults.timezone)


def update_agent_settings(query: QueryParams) -> dict[str, Any]:
    # 更新 agent 默认设置（预设切换、model、provider、时区、bot 名称/图标、工具提示长度等）。
    # 逐字段校验并按需置 changed；只有 changed 时才 save_config 写盘。
    # restart_required 标记哪些变更需重启引擎才生效（如时区、bot_name、bot_icon、
    # tool_hint_max_length），前端据此提示用户重启。
    config = load_config()
    defaults = config.agents.defaults
    changed = False
    restart_required = False

    if "model_preset" in query or "modelPreset" in query:
        # 预设切换：default 用 None 表示（defaults.model_preset 为 None 即用默认预设），
        # 其余需校验预设确实存在。
        preset = (_query_first_alias(query, "model_preset", "modelPreset") or "").strip()
        preset_value = None if not preset or preset == "default" else preset
        if preset_value is not None and preset_value not in config.model_presets:
            raise WebUISettingsError("unknown model preset")
        if defaults.model_preset != preset_value:
            defaults.model_preset = preset_value
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("model is required")
        if defaults.model != model:
            defaults.model = model
            changed = True

    provider = _query_first(query, "provider")
    if provider is not None:
        provider = provider.strip()
        if not provider:
            raise WebUISettingsError("provider is required")
        # 切换 provider 前先校验目标 provider 已配置，避免切到不可用的 provider。
        _validate_configured_provider(config, provider)
        if defaults.provider != provider:
            defaults.provider = provider
            changed = True

    context_window_tokens = _parse_context_window_tokens(
        _query_first_alias(query, "context_window_tokens", "contextWindowTokens")
    )
    if (
        context_window_tokens is not None
        and defaults.context_window_tokens != context_window_tokens
    ):
        defaults.context_window_tokens = context_window_tokens
        changed = True

    timezone = _query_first(query, "timezone")
    if timezone is not None:
        timezone = timezone.strip()
        if not timezone:
            raise WebUISettingsError("timezone is required")
        # 用 ZoneInfo 构造校验时区名合法性，无效则拒绝。
        try:
            ZoneInfo(timezone)
        except Exception:
            raise WebUISettingsError("invalid timezone") from None
        if defaults.timezone != timezone:
            defaults.timezone = timezone
            changed = True
            # 时区影响心跳/cron 调度，需重启引擎生效。
            restart_required = True

    bot_name = _query_first_alias(query, "bot_name", "botName")
    if bot_name is not None:
        bot_name = bot_name.strip()
        if not bot_name:
            raise WebUISettingsError("bot_name is required")
        if defaults.bot_name != bot_name:
            defaults.bot_name = bot_name
            changed = True
            # bot_name 被注入系统提示，需重启引擎重新构建上下文。
            restart_required = True

    bot_icon = _query_first_alias(query, "bot_icon", "botIcon")
    if bot_icon is not None:
        bot_icon = bot_icon.strip()
        if defaults.bot_icon != bot_icon:
            defaults.bot_icon = bot_icon
            changed = True
            restart_required = True

    tool_hint_max_length = _query_first_alias(
        query,
        "tool_hint_max_length",
        "toolHintMaxLength",
    )
    if tool_hint_max_length is not None:
        try:
            parsed = int(tool_hint_max_length)
        except ValueError:
            raise WebUISettingsError("tool_hint_max_length must be an integer") from None
        # 限制 20-500，过短会截断工具描述、过长浪费上下文。
        if parsed < 20 or parsed > 500:
            raise WebUISettingsError("tool_hint_max_length must be between 20 and 500")
        if defaults.tool_hint_max_length != parsed:
            defaults.tool_hint_max_length = parsed
            changed = True
            restart_required = True

    if changed:
        # save_config 在 loader 层做原子写（临时文件 + fsync + rename），保证写盘安全。
        save_config(config)
    return settings_payload(requires_restart=restart_required)


def create_model_configuration(query: QueryParams) -> dict[str, Any]:
    # 新建一个具名模型预设。name 经 slug 化作为存储键，禁止与已存在预设或 "default" 冲突。
    # 其余参数（max_tokens/temperature 等）从默认预设继承，用户后续可通过 update 修改。
    # 创建后立即把该预设设为当前激活预设。
    label = (_query_first_alias(query, "label", "displayName") or "").strip()
    raw_name = (_query_first(query, "name") or label).strip()
    model = (_query_first(query, "model") or "").strip()
    provider = (_query_first(query, "provider") or "").strip()

    if not label:
        label = raw_name
    if not model:
        raise WebUISettingsError("model is required")
    if not provider:
        raise WebUISettingsError("provider is required")

    name = _model_configuration_slug(raw_name or label)
    config = load_config()
    if name in config.model_presets:
        # 重名冲突返回 409，区别于普通参数错误。
        raise WebUISettingsError("configuration already exists", status=409)
    _validate_configured_provider(config, provider)

    base = config.resolve_default_preset()
    # 用默认预设的派生值填充未在创建表单中提供的参数（max_tokens/temperature 等），
    # 保证新预设即用即可用。
    config.model_presets[name] = ModelPresetConfig(
        label=label,
        model=model,
        provider=provider,
        max_tokens=base.max_tokens,
        context_window_tokens=base.context_window_tokens,
        temperature=base.temperature,
        reasoning_effort=base.reasoning_effort,
    )
    # 创建后立即激活该预设。
    config.agents.defaults.model_preset = name
    save_config(config)
    return settings_payload()


def update_model_configuration(query: QueryParams) -> dict[str, Any]:
    # 更新已存在的具名模型预设（label/model/provider/context_window_tokens）。
    # "default" 预设不可由此修改（其字段由 defaults 控制），故直接拒绝。
    # 若该预设当前未激活，更新同时把它设为激活预设。
    name = (_query_first(query, "name") or "").strip()
    if not name or name == "default":
        raise WebUISettingsError("model configuration is required")

    config = load_config()
    preset = config.model_presets.get(name)
    if preset is None:
        raise WebUISettingsError("unknown model configuration")

    changed = False
    label = _query_first_alias(query, "label", "displayName")
    if label is not None:
        label = label.strip()
        if not label:
            raise WebUISettingsError("label is required")
        if preset.label != label:
            preset.label = label
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("model is required")
        if preset.model != model:
            preset.model = model
            changed = True

    provider = _query_first(query, "provider")
    if provider is not None:
        provider = provider.strip()
        if not provider:
            raise WebUISettingsError("provider is required")
        _validate_configured_provider(config, provider)
        if preset.provider != provider:
            preset.provider = provider
            changed = True

    context_window_tokens = _parse_context_window_tokens(
        _query_first_alias(query, "context_window_tokens", "contextWindowTokens")
    )
    if (
        context_window_tokens is not None
        and preset.context_window_tokens != context_window_tokens
    ):
        preset.context_window_tokens = context_window_tokens
        changed = True

    # 编辑预设即视为选中该预设：把当前激活预设指向被编辑的 name。
    if config.agents.defaults.model_preset != name:
        config.agents.defaults.model_preset = name
        changed = True

    if changed:
        save_config(config)
    return settings_payload()


def update_provider_settings(query: QueryParams) -> dict[str, Any]:
    # 更新单个 provider 的凭据（api_key/api_base/api_type）。OAuth provider 不走此接口
    # （由 login/logout OAuth 接口管理令牌），故拒绝。凭据为空串时归一化为 None。
    # 若该 provider 同时被图像生成功能引用，则需重启引擎使新凭据生效。
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")

    config = load_config()
    resolved_provider = _resolve_settings_provider(config, provider_name)
    if resolved_provider is None:
        raise WebUISettingsError("unknown provider")
    spec, provider_key, provider_config = resolved_provider
    if spec.is_oauth:
        # OAuth provider 的凭据由令牌文件管理，不在此处用 api_key 设置。
        raise WebUISettingsError("unknown provider")

    changed = False
    if "api_key" in query or "apiKey" in query:
        api_key = _query_first_alias(query, "api_key", "apiKey")
        # 空白串归一化为 None，表示清除已配置的密钥。
        api_key = (api_key or "").strip() or None
        if provider_config.api_key != api_key:
            provider_config.api_key = api_key
            changed = True

    if "api_base" in query or "apiBase" in query:
        api_base = _query_first_alias(query, "api_base", "apiBase")
        api_base = (api_base or "").strip() or None
        if provider_config.api_base != api_base:
            provider_config.api_base = api_base
            changed = True

    if "api_type" in query:
        if spec.name == "openai":
            # api_type 仅 openai provider 支持（auto/chat_completions/responses）。
            # 用构造器实例化触发 Pydantic 校验，避免非法值落盘。
            api_type = (_query_first(query, "api_type") or "").strip()
            try:
                parsed_api_type = type(provider_config)(api_type=api_type).api_type
            except Exception:
                raise WebUISettingsError("api_type must be auto, chat_completions, or responses") from None
            if provider_config.api_type != parsed_api_type:
                provider_config.api_type = parsed_api_type
                changed = True

    if changed:
        save_config(config)
    image_config = config.tools.image_generation
    # 图像生成复用 chat provider 的凭据：若改的就是图像生成所用 provider，需重启引擎。
    restart_required = (
        changed
        and image_config.enabled
        and image_config.provider == provider_key
        and get_image_gen_provider(provider_key) is not None
    )
    return settings_payload(requires_restart=restart_required)


def login_oauth_provider(query: QueryParams) -> dict[str, Any]:
    # 触发 OAuth provider 的登录流程。先尝试读取已有令牌，无则发起交互式登录。
    # openai_codex 走 oauth_cli_kit，github_copilot 走专用 provider 模块。
    # 登录依赖缺失返回 500，登录失败返回 401。注意 login_oauth_interactive 的
    # prompt_fn 返回空串以避免在服务端阻塞等待输入。
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")
    spec = find_by_name(provider_name)
    if spec is None or not spec.is_oauth:
        raise WebUISettingsError("unknown OAuth provider")

    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit import get_token, login_oauth_interactive
        except ImportError:
            raise WebUISettingsError("oauth_cli_kit is not installed", status=500) from None

        token = None
        with suppress(Exception):
            token = get_token()
        if not (token and token.access):
            messages: list[str] = []
            # print_fn 收集流程输出供调试；prompt_fn 返回空串避免服务端阻塞读 stdin。
            token = login_oauth_interactive(
                print_fn=lambda message: messages.append(str(message)),
                prompt_fn=lambda _prompt: "",
            )
        if not (token and token.access):
            raise WebUISettingsError("OAuth login failed", status=401)
        return settings_payload()

    if spec.name == "github_copilot":
        try:
            from nanobot.providers.github_copilot_provider import (
                get_github_copilot_login_status,
                login_github_copilot,
            )
        except ImportError:
            raise WebUISettingsError("GitHub Copilot OAuth support is unavailable", status=500) from None

        token = get_github_copilot_login_status()
        if not token:
            token = login_github_copilot(print_fn=lambda _message: None)
        if not (token and token.access):
            raise WebUISettingsError("OAuth login failed", status=401)
        return settings_payload()

    raise WebUISettingsError("OAuth login is not supported for this provider")


def logout_oauth_provider(query: QueryParams) -> dict[str, Any]:
    # 注销 OAuth provider：删除本地令牌文件及其 .lock 锁文件。
    # 用 suppress(FileNotFoundError) 容忍文件不存在的情况，保证登出幂等。
    provider_name = (_query_first(query, "provider") or "").strip()
    if not provider_name:
        raise WebUISettingsError("provider is required")
    spec = find_by_name(provider_name)
    if spec is None or not spec.is_oauth:
        raise WebUISettingsError("unknown OAuth provider")

    if spec.name == "openai_codex":
        try:
            from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
            from oauth_cli_kit.storage import FileTokenStorage
        except ImportError:
            raise WebUISettingsError("oauth_cli_kit is not installed", status=500) from None
        token_path = FileTokenStorage(token_filename=OPENAI_CODEX_PROVIDER.token_filename).get_token_path()
    elif spec.name == "github_copilot":
        try:
            from nanobot.providers.github_copilot_provider import get_storage
        except ImportError:
            raise WebUISettingsError("GitHub Copilot OAuth support is unavailable", status=500) from None
        token_path = get_storage().get_token_path()
    else:
        raise WebUISettingsError("OAuth logout is not supported for this provider")

    for path in (token_path, token_path.with_suffix(".lock")):
        # 同时删除令牌文件与其 .lock 锁文件；suppress 容忍文件已不存在，保证登出幂等。
        with suppress(FileNotFoundError):
            path.unlink()
    return settings_payload()


def update_network_safety_settings(query: QueryParams) -> dict[str, Any]:
    # 更新网络与访问安全设置：webui_allow_local_service_access（是否允许 WebUI 访问本地服务）
    # 与 webui_default_access_mode（默认访问模式 default/full）。前者写入 config，
    # 后者通过独立函数持久化（workspaces 模块）。两者至少传一个，否则报错。
    raw_allow = (
        _query_first_alias(query, "webui_allow_local_service_access", "webuiAllowLocalServiceAccess")
        or _query_first_alias(query, "allow_local_preview_access", "allowLocalPreviewAccess")
    )
    raw_default_access_mode = _query_first_alias(query, "webui_default_access_mode", "webuiDefaultAccessMode")
    if raw_allow is None and raw_default_access_mode is None:
        raise WebUISettingsError("webui_allow_local_service_access or webui_default_access_mode is required")

    config = load_config()
    changed = False
    if raw_allow is not None:
        webui_allow_local_service_access = _parse_bool(raw_allow, "webui_allow_local_service_access")
        if config.tools.webui_allow_local_service_access != webui_allow_local_service_access:
            config.tools.webui_allow_local_service_access = webui_allow_local_service_access
            changed = True

    if changed:
        save_config(config)
    if raw_default_access_mode is not None:
        default_access_mode = raw_default_access_mode.strip().lower()
        # "restricted" 是前端历史取值，映射为内部 "default"。
        if default_access_mode == "restricted":
            default_access_mode = "default"
        if default_access_mode not in {"default", "full"}:
            raise WebUISettingsError("webui_default_access_mode must be default or full")
        try:
            write_webui_default_access_mode(default_access_mode)
        except ValueError as exc:
            # workspaces 层校验失败时转为面向用户的错误。
            raise WebUISettingsError(str(exc)) from exc
    return settings_payload(requires_restart=changed)


def update_web_search_settings(query: QueryParams) -> dict[str, Any]:
    # 更新 Web 搜索设置。按目标 provider 的 credential 类型决定保留/清空 api_key 与 base_url：
    # none=两者皆清空、base_url=保留/设置 base_url 并清空 api_key、api_key/optional_api_key=反之。
    # 切换 provider 时若未传新凭据且 provider 未变，则沿用已有凭据，避免前端回显导致凭据被清空。
    # use_jina_reader 切换需重启引擎生效。
    provider_name = (_query_first(query, "provider") or "").strip().lower()
    provider_option = _WEB_SEARCH_PROVIDER_BY_NAME.get(provider_name)
    if provider_option is None:
        raise WebUISettingsError("unknown web search provider")

    config = load_config()
    search_config = config.tools.web.search
    web_config = config.tools.web
    previous_provider = search_config.provider
    changed = False
    restart_required = False

    def set_search_value(attr: str, value: object) -> None:
        # 仅在值实际变化时写入并置 changed，避免无变更触发写盘。
        nonlocal changed
        if getattr(search_config, attr) != value:
            setattr(search_config, attr, value)
            changed = True

    def set_fetch_value(attr: str, value: object) -> None:
        nonlocal changed
        if getattr(web_config.fetch, attr) != value:
            setattr(web_config.fetch, attr, value)
            changed = True

    if search_config.provider != provider_name:
        search_config.provider = provider_name
        changed = True

    credential = provider_option["credential"]
    if credential == "none":
        # 无需凭据的 provider（如 DuckDuckGo）清空 api_key 与 base_url。
        set_search_value("api_key", "")
        set_search_value("base_url", "")
    elif credential == "base_url":
        base_url = _query_first_alias(query, "base_url", "baseUrl")
        base_url = base_url.strip() if base_url is not None else None
        # 未传 base_url 且 provider 未变时沿用旧值，适配前端仅刷新其它字段的场景。
        if not base_url and previous_provider == provider_name and search_config.base_url:
            base_url = search_config.base_url
        if not base_url:
            raise WebUISettingsError("base_url is required")
        set_search_value("base_url", base_url)
        set_search_value("api_key", "")
    elif credential in {"api_key", "optional_api_key"}:
        raw_api_key = _query_first_alias(query, "api_key", "apiKey")
        api_key = raw_api_key.strip() if raw_api_key is not None else None
        # 同上：未传密钥且 provider 未变时沿用旧密钥，避免回显清空。
        if api_key is None and previous_provider == provider_name and search_config.api_key:
            api_key = search_config.api_key
        if credential == "api_key" and not api_key:
            raise WebUISettingsError("api_key is required")
        set_search_value("api_key", api_key or "")
        set_search_value("base_url", "")
    else:
        raise WebUISettingsError("unknown web search credential type")

    max_results = _query_first_alias(query, "max_results", "maxResults")
    if max_results is not None:
        try:
            parsed = int(max_results)
        except ValueError:
            raise WebUISettingsError("max_results must be an integer") from None
        # 限制 1-10 条结果，平衡有用性与请求开销。
        if parsed < 1 or parsed > 10:
            raise WebUISettingsError("max_results must be between 1 and 10")
        set_search_value("max_results", parsed)

    timeout = _query_first(query, "timeout")
    if timeout is not None:
        try:
            parsed_timeout = int(timeout)
        except ValueError:
            raise WebUISettingsError("timeout must be an integer") from None
        # 超时 1-120 秒，防止过短无法返回或过长阻塞。
        if parsed_timeout < 1 or parsed_timeout > 120:
            raise WebUISettingsError("timeout must be between 1 and 120")
        set_search_value("timeout", parsed_timeout)

    use_jina_reader = _query_first_alias(query, "use_jina_reader", "useJinaReader")
    if use_jina_reader is not None:
        normalized = use_jina_reader.strip().lower()
        if normalized not in {"1", "0", "true", "false", "yes", "no"}:
            raise WebUISettingsError("use_jina_reader must be boolean")
        previous_jina_reader = web_config.fetch.use_jina_reader
        set_fetch_value("use_jina_reader", normalized in {"1", "true", "yes"})
        # jina reader 影响抓取链路，切换需重启引擎生效。
        if web_config.fetch.use_jina_reader != previous_jina_reader:
            restart_required = True

    if changed:
        save_config(config)
    return settings_payload(requires_restart=restart_required)


def update_image_generation_settings(query: QueryParams) -> dict[str, Any]:
    # 更新图像生成设置（provider/enabled/model/宽高比/尺寸/单轮最大张数）。
    # 启用图像生成时强制校验所选 provider 已配置凭据，避免运行时生成失败。
    # 任意字段变更都需重启引擎（图像生成工具在引擎启动时初始化）。
    config = load_config()
    image_config = config.tools.image_generation
    changed = False

    provider_name = _query_first(query, "provider")
    if provider_name is not None:
        provider_name = provider_name.strip().lower()
        if not provider_name:
            raise WebUISettingsError("image generation provider is required")
        if get_image_gen_provider(provider_name) is None:
            raise WebUISettingsError("unknown image generation provider")
        if image_config.provider != provider_name:
            image_config.provider = provider_name
            changed = True

    enabled = _query_first(query, "enabled")
    if enabled is not None:
        parsed_enabled = _parse_bool(enabled, "enabled")
        if image_config.enabled != parsed_enabled:
            image_config.enabled = parsed_enabled
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip()
        if not model:
            raise WebUISettingsError("image generation model is required")
        if len(model) > 200:
            raise WebUISettingsError("image generation model is too long")
        if image_config.model != model:
            image_config.model = model
            changed = True

    default_aspect_ratio = _query_first_alias(
        query,
        "default_aspect_ratio",
        "defaultAspectRatio",
    )
    if default_aspect_ratio is not None:
        default_aspect_ratio = default_aspect_ratio.strip()
        if default_aspect_ratio not in _IMAGE_GENERATION_ASPECT_RATIOS:
            raise WebUISettingsError("unsupported image generation aspect ratio")
        if image_config.default_aspect_ratio != default_aspect_ratio:
            image_config.default_aspect_ratio = default_aspect_ratio
            changed = True

    default_image_size = _query_first_alias(
        query,
        "default_image_size",
        "defaultImageSize",
    )
    if default_image_size is not None:
        default_image_size = default_image_size.strip()
        if not default_image_size:
            raise WebUISettingsError("default image size is required")
        # 尺寸字符串白名单校验：仅允许 ASCII 字母数字与 x/X/:/-/_（如 "1024x1024"、"16:9"），
        # 长度上限 32，防止注入异常字符到下游图像 API。
        if len(default_image_size) > 32 or not all(
            char.isascii() and (char.isalnum() or char in {"x", "X", ":", "-", "_"})
            for char in default_image_size
        ):
            raise WebUISettingsError("unsupported image generation size")
        if image_config.default_image_size != default_image_size:
            image_config.default_image_size = default_image_size
            changed = True

    max_images_per_turn = _query_first_alias(
        query,
        "max_images_per_turn",
        "maxImagesPerTurn",
    )
    if max_images_per_turn is not None:
        try:
            parsed_max = int(max_images_per_turn)
        except ValueError:
            raise WebUISettingsError("max_images_per_turn must be an integer") from None
        if parsed_max < 1 or parsed_max > 8:
            raise WebUISettingsError("max_images_per_turn must be between 1 and 8")
        if image_config.max_images_per_turn != parsed_max:
            image_config.max_images_per_turn = parsed_max
            changed = True

    if image_config.enabled:
        # 启用状态下必须确保所选 provider 已配置凭据，否则生成时会失败，故在此前置校验。
        selected_provider = next(
            (
                provider
                for provider in _image_generation_provider_rows(config)
                if provider["name"] == image_config.provider
            ),
            None,
        )
        if not selected_provider or not selected_provider["configured"]:
            raise WebUISettingsError("image generation provider is not configured")

    if changed:
        save_config(config)
    return settings_payload(requires_restart=changed)


def update_transcription_settings(query: QueryParams) -> dict[str, Any]:
    # 更新语音转写设置（enabled/provider/model/language/最大时长/最大上传大小）。
    # language 校验为 2-3 位小写字母（ISO 639-1/639-2 风格），其余数值字段均有上下界校验。
    config = load_config()
    transcription = config.transcription
    changed = False

    enabled = _query_first(query, "enabled")
    if enabled is not None:
        parsed_enabled = _parse_bool(enabled, "enabled")
        if transcription.enabled != parsed_enabled:
            transcription.enabled = parsed_enabled
            changed = True

    provider = _query_first(query, "provider")
    if provider is not None:
        provider = provider.strip().lower()
        provider_spec = resolve_transcription_provider(provider)
        if provider_spec is None:
            raise WebUISettingsError("unknown transcription provider")
        # 用解析后的规范名落库，避免大小写/别名差异导致后续匹配失败。
        provider = provider_spec.name
        if transcription.provider != provider:
            transcription.provider = provider
            changed = True

    model = _query_first(query, "model")
    if model is not None:
        model = model.strip() or None
        if model is not None and len(model) > 200:
            raise WebUISettingsError("transcription model is too long")
        if transcription.model != model:
            transcription.model = model
            changed = True

    language = _query_first(query, "language")
    if language is not None:
        language = language.strip().lower() or None
        # 限制为 2-3 位小写字母语言码，防止非法语言标识传给转写 API。
        if language is not None and not re.fullmatch(r"[a-z]{2,3}", language):
            raise WebUISettingsError("transcription language must be 2-3 lowercase letters")
        if transcription.language != language:
            transcription.language = language
            changed = True

    max_duration_sec = _query_first_alias(query, "max_duration_sec", "maxDurationSec")
    if max_duration_sec is not None:
        try:
            parsed_duration = int(max_duration_sec)
        except ValueError:
            raise WebUISettingsError("max_duration_sec must be an integer") from None
        # 单段音频 1-600 秒，避免过短无意义或过长拖慢转写。
        if parsed_duration < 1 or parsed_duration > 600:
            raise WebUISettingsError("max_duration_sec must be between 1 and 600")
        if transcription.max_duration_sec != parsed_duration:
            transcription.max_duration_sec = parsed_duration
            changed = True

    max_upload_mb = _query_first_alias(query, "max_upload_mb", "maxUploadMb")
    if max_upload_mb is not None:
        try:
            parsed_upload = int(max_upload_mb)
        except ValueError:
            raise WebUISettingsError("max_upload_mb must be an integer") from None
        # 上传体积 1-100MB，平衡可用性与网关内存压力。
        if parsed_upload < 1 or parsed_upload > 100:
            raise WebUISettingsError("max_upload_mb must be between 1 and 100")
        if transcription.max_upload_mb != parsed_upload:
            transcription.max_upload_mb = parsed_upload
            changed = True

    if changed:
        save_config(config)
    return settings_payload()
