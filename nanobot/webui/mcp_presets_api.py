"""MCP preset helpers for the WebUI settings and message surfaces.

本模块为 WebUI 设置页与消息面提供 MCP（Model Context Protocol）服务器预设的全部
CRUD 逻辑：维护一组内置预设（`MCP_PRESETS`），将 WebUI 提交的查询参数物化为
`MCPServerConfig` 并持久化到 `~/.nanobot/config.json`，同时产出供前端渲染的状态与
manifest。此外还支持自定义服务器、从外部 JSON（如 Cursor 配置）导入、连接测试以及
配置变更后的热重载通知。所有动作返回带 `last_action` 的统一 payload，便于前端展示结果。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import urllib.parse
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from nanobot.agent.tools.registry import ToolRegistry
from nanobot.apps.protocol import app_manifest, compact_dict
from nanobot.config.loader import load_config, resolve_config_env_vars, save_config
from nanobot.config.paths import get_runtime_subdir
from nanobot.config.schema import MCPServerConfig
from nanobot.utils.helpers import ensure_dir

QueryParams = dict[str, list[str]]

# 预设名合法字符：小写字母/数字开头，仅含字母数字、下划线、短横线，最长 64 字符
_MCP_PRESET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)
# 匹配 URL 查询串中形如 ?api_key=xxx / &token=xxx 的敏感参数，捕获组 1 保留键名用于脱敏
_SECRET_QUERY_RE = re.compile(
    r"([?&](?:[^=&]*(?:api[_-]?key|token|secret|password|bearer)[^=&]*)=)[^&#\s]+",
    re.IGNORECASE,
)
# 匹配命令行/文本中形如 api_key=xxx / token:xxx 的敏感赋值，捕获组 1 保留前缀用于脱敏
_SECRET_ASSIGNMENT_RE = re.compile(
    r"((?:api[_-]?key|token|secret|password|bearer)(?:[=:]|\s+))[^,\s'\"&]+",
    re.IGNORECASE,
)
# 消息附件中允许携带的预设元数据键名（首个 "name" 单独处理，作为主键）
_MCP_ATTACHMENT_KEYS = (
    "name",
    "display_name",
    "category",
    "transport",
    "logo_url",
    "brand_color",
    "status",
    "configured",
)
_MAX_TEST_TOOLS = 16
_DEFAULT_TEST_TIMEOUT = 20
_DEFAULT_CUSTOM_TIMEOUT = 30
# 走自定义服务器处理路径的 action 集合（与内置预设的 enable/remove/test 区分）
_CUSTOM_ACTIONS = {"custom", "import", "import-cursor", "tools"}

McpReload = Callable[[], Awaitable[dict[str, Any]]]


class McpPresetError(Exception):
    """WebUI-facing MCP preset error.

    面向 WebUI 的 MCP 预设异常，携带 HTTP 状态码。调用方据此返回对应的 HTTP 响应，
    而非抛出通用 500。`status` 默认 400（请求参数错误）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class McpPresetField:
    name: str
    label: str
    # target 为 (写入位置, 目标键) 二元组：值会被注入到 env、URL 查询参数、命令行参数或 HTTP 头
    target: tuple[Literal["env", "url_param", "arg", "header"], str]
    secret: bool = True
    required: bool = True
    # 对应的进程环境变量名：当配置中缺失时，可回退读取该环境变量判断是否已"配置"
    env_var: str | None = None
    placeholder: str = ""


@dataclass(frozen=True)
class McpPreset:
    """单个内置 MCP 预设的静态定义。

    描述一个可一键启用的 MCP 服务器模板：传输方式、品牌信息、所需凭据字段以及默认
    `MCPServerConfig`。`fields` 定义用户需填写的凭据，`_materialize_server` 会据此把
    用户输入注入到克隆出的 `server` 配置中。"""

    name: str
    display_name: str
    category: str
    description: str
    docs_url: str
    transport: Literal["stdio", "streamableHttp", "sse", "oauth"]
    install_supported: bool
    brand_domain: str
    brand_color: str
    server: MCPServerConfig | None = None
    fields: tuple[McpPresetField, ...] = ()
    requires: str = ""
    note: str = ""


def _favicon_url(domain: str) -> str:
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=64"


MCP_PRESETS: tuple[McpPreset, ...] = (
    McpPreset(
        name="browserbase",
        display_name="Browserbase",
        category="browser",
        description="Cloud browser automation through Browserbase's hosted MCP server.",
        docs_url="https://docs.browserbase.com/integrations/mcp/setup",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="browserbase.com",
        brand_color="#111827",
        requires="Browserbase API key",
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://mcp.browserbase.com/mcp",
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="browserbase_api_key",
                label="Browserbase API key",
                target=("url_param", "browserbaseApiKey"),
                env_var="BROWSERBASE_API_KEY",
                placeholder="bb_live_...",
            ),
        ),
    ),
    McpPreset(
        name="playwright",
        display_name="Playwright",
        category="browser",
        description="Local browser inspection and automation with Playwright's MCP server.",
        docs_url="https://playwright.dev/docs/getting-started-mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="playwright.dev",
        brand_color="#2EAD33",
        requires="Node.js and npx",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@playwright/mcp@latest"],
            tool_timeout=60,
        ),
    ),
    McpPreset(
        name="context7",
        display_name="Context7",
        category="docs",
        description="Fetch current library docs and code examples while the agent works.",
        docs_url="https://context7.com/docs/resources/all-clients",
        transport="stdio",
        install_supported=True,
        brand_domain="context7.com",
        brand_color="#111827",
        requires="Node.js and npx; API key optional",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@upstash/context7-mcp@latest"],
            tool_timeout=45,
        ),
        fields=(
            McpPresetField(
                name="context7_api_key",
                label="Context7 API key",
                target=("arg", "--api-key"),
                env_var="CONTEXT7_API_KEY",
                placeholder="ctx7_...",
                required=False,
            ),
        ),
        note="Works without a key for basic public docs; add a key for higher limits or private docs.",
    ),
    McpPreset(
        name="firecrawl",
        display_name="Firecrawl",
        category="web",
        description="Scrape, crawl, search, and extract web pages through Firecrawl's MCP server.",
        docs_url="https://docs.firecrawl.dev/use-cases/developers-mcp",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="firecrawl.dev",
        brand_color="#EB5E28",
        requires="Network access",
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://mcp.firecrawl.dev/v2/mcp",
            tool_timeout=60,
        ),
        note=(
            "Uses Firecrawl Keyless through the hosted MCP endpoint. No API key is required for "
            "the built-in preset; use a custom MCP server URL if you want account-specific limits."
        ),
    ),
    McpPreset(
        name="exa",
        display_name="Exa",
        category="web",
        description="Search the web and fetch clean page content through Exa's hosted MCP server.",
        docs_url="https://exa.ai/mcp",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="exa.ai",
        brand_color="#101010",
        requires="Network access",
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://mcp.exa.ai/mcp",
            tool_timeout=45,
        ),
        note="Hosted Exa MCP endpoint currently does not require an API key.",
    ),
    McpPreset(
        name="microsoft-learn",
        display_name="Microsoft Learn",
        category="docs",
        description="Search and fetch Microsoft Learn documentation through Microsoft's hosted MCP server.",
        docs_url="https://learn.microsoft.com/en-us/training/support/mcp",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="learn.microsoft.com",
        brand_color="#0078D4",
        requires="Network access",
        server=MCPServerConfig(
            type="streamableHttp",
            url="https://learn.microsoft.com/api/mcp",
            tool_timeout=45,
        ),
        note="Public documentation only; no authentication required.",
    ),
    McpPreset(
        name="aws-docs",
        display_name="AWS Documentation",
        category="docs",
        description="Search AWS documentation and service guidance through AWS Labs' documentation MCP server.",
        docs_url="https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server/",
        transport="stdio",
        install_supported=True,
        brand_domain="aws.amazon.com",
        brand_color="#FF9900",
        requires="uvx",
        server=MCPServerConfig(
            type="stdio",
            command="uvx",
            args=["awslabs.aws-documentation-mcp-server@latest"],
            env={"FASTMCP_LOG_LEVEL": "ERROR", "AWS_DOCUMENTATION_PARTITION": "aws"},
            tool_timeout=60,
        ),
    ),
    McpPreset(
        name="brave-search",
        display_name="Brave Search",
        category="web",
        description="Run web, news, image, video, and local search through Brave Search.",
        docs_url="https://www.npmjs.com/package/@brave/brave-search-mcp-server",
        transport="stdio",
        install_supported=True,
        brand_domain="brave.com",
        brand_color="#FB542B",
        requires="Node.js, npx, and Brave Search API key",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@brave/brave-search-mcp-server@latest", "--transport", "stdio"],
            tool_timeout=45,
        ),
        fields=(
            McpPresetField(
                name="brave_api_key",
                label="Brave Search API key",
                target=("env", "BRAVE_API_KEY"),
                env_var="BRAVE_API_KEY",
                placeholder="BSA...",
            ),
        ),
    ),
    McpPreset(
        name="postman",
        display_name="Postman",
        category="api",
        description="Inspect and manage Postman APIs, collections, and workspaces through the local MCP server.",
        docs_url="https://learning.postman.com/docs/developer/postman-api/postman-mcp-server/postman-mcp-local-server",
        transport="stdio",
        install_supported=True,
        brand_domain="postman.com",
        brand_color="#FF6C37",
        requires="Node.js, npx, and Postman API key",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@postman/postman-mcp-server@latest", "--full"],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="postman_api_key",
                label="Postman API key",
                target=("env", "POSTMAN_API_KEY"),
                env_var="POSTMAN_API_KEY",
                placeholder="PMAK-...",
            ),
        ),
    ),
    McpPreset(
        name="figma",
        display_name="Figma",
        category="design",
        description="Read design context from Figma using the local Dev Mode MCP server.",
        docs_url="https://help.figma.com/hc/en-us/articles/32132100833559-Guide-to-the-Figma-MCP-server",
        transport="streamableHttp",
        install_supported=True,
        brand_domain="figma.com",
        brand_color="#F24E1E",
        requires="Figma desktop app with MCP enabled",
        server=MCPServerConfig(
            type="streamableHttp",
            url="http://127.0.0.1:3845/mcp",
            tool_timeout=45,
        ),
        note="Requires Figma Desktop Dev Mode MCP to be running locally.",
    ),
    McpPreset(
        name="github",
        display_name="GitHub",
        category="code",
        description="Repository, issue, and pull request workflows via GitHub's MCP server.",
        docs_url="https://github.com/github/github-mcp-server",
        transport="stdio",
        install_supported=True,
        brand_domain="github.com",
        brand_color="#24292F",
        requires="Docker and GitHub token",
        server=MCPServerConfig(
            type="stdio",
            command="docker",
            args=[
                "run",
                "-i",
                "--rm",
                "-e",
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "ghcr.io/github/github-mcp-server",
            ],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="github_token",
                label="GitHub token",
                target=("env", "GITHUB_PERSONAL_ACCESS_TOKEN"),
                env_var="GITHUB_PERSONAL_ACCESS_TOKEN",
                placeholder="ghp_...",
            ),
        ),
    ),
    McpPreset(
        name="supabase",
        display_name="Supabase",
        category="database",
        description="Inspect and manage Supabase projects through the Supabase MCP server.",
        docs_url="https://supabase.com/docs/guides/ai-tools/mcp",
        transport="stdio",
        install_supported=True,
        brand_domain="supabase.com",
        brand_color="#3ECF8E",
        requires="Node.js, npx, and Supabase access token",
        server=MCPServerConfig(
            type="stdio",
            command="npx",
            args=["-y", "@supabase/mcp-server-supabase@latest", "--read-only"],
            tool_timeout=60,
        ),
        fields=(
            McpPresetField(
                name="supabase_access_token",
                label="Supabase access token",
                target=("env", "SUPABASE_ACCESS_TOKEN"),
                env_var="SUPABASE_ACCESS_TOKEN",
                placeholder="sbp_...",
            ),
        ),
        note="MVP config starts read-only by default.",
    ),
)


def _query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _query_value(query: QueryParams, key: str) -> str | None:
    raw = _query_first(query, key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _preset_by_name(name: str) -> McpPreset:
    if not name or _MCP_PRESET_NAME_RE.match(name) is None:
        raise McpPresetError("invalid MCP preset name")
    for preset in MCP_PRESETS:
        if preset.name == name:
            return preset
    raise McpPresetError("unknown MCP preset", status=404)


def _preset_by_name_optional(name: str) -> McpPreset | None:
    try:
        return _preset_by_name(name)
    except McpPresetError:
        return None


def _known_preset_names() -> set[str]:
    return {preset.name for preset in MCP_PRESETS}


def _known_mcp_names() -> set[str]:
    """返回内置预设名与已配置服务器名的并集，用于校验 WebUI 提及的预设是否合法。"""
    names = _known_preset_names()
    # 读取配置失败时静默忽略，仅返回内置预设名，避免拖垮整条消息处理流程
    with suppress(Exception):
        names.update(load_config().tools.mcp_servers)
    return names


def _clip_ws_string(value: Any, limit: int = 240) -> str | None:
    """裁剪字符串首尾空白并截断到 `limit`，空值返回 None。用于规整 WebUI 传入的可疑文本。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


def normalize_mcp_preset_mentions(raw: Any) -> list[dict[str, Any]]:
    """Sanitize structured MCP preset mentions sent by the WebUI.

    清洗 WebUI 在消息附件中提交的 MCP 预设提及：仅保留已知名、去重、限制条数与字段，
    防止前端注入任意键或过长文本。最多保留 8 条，每个字段按类型截断。"""
    if not isinstance(raw, list):
        return []
    known = _known_mcp_names()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        name = _clip_ws_string(item.get("name"), 64)
        if not name or _MCP_PRESET_NAME_RE.match(name) is None:
            continue
        key = name.lower()
        # 跳过重复名与未知名：提及必须对得上某个内置预设或已配置服务器
        if key in seen or key not in known:
            continue
        seen.add(key)
        row: dict[str, Any] = {"name": key}
        # 跳过首个 "name"，逐个提取并截断其余元数据字段
        for field_name in _MCP_ATTACHMENT_KEYS[1:]:
            value = item.get(field_name)
            if isinstance(value, bool):
                row[field_name] = value
                continue
            # logo_url 允许更长，其余字段限制 160 字符
            limit = 512 if field_name == "logo_url" else 160
            text = _clip_ws_string(value, limit)
            if text:
                row[field_name] = text
        out.append(row)
    return out


def _clone_server(server: MCPServerConfig) -> MCPServerConfig:
    """深拷贝一个 `MCPServerConfig`：经 JSON 序列化再校验，确保后续修改不影响原预设。"""
    return MCPServerConfig.model_validate(server.model_dump(mode="json"))


def _with_managed_stdio_cwd(name: str, cfg: MCPServerConfig) -> MCPServerConfig:
    """为 stdio 型 MCP 服务器指定受管工作目录（`runtime/mcp/<name>`）。

    每个预设的本地进程在独立的运行时子目录下运行，便于卸载时统一清理。仅当未显式设置
    `cwd` 时才填充。"""
    if cfg.command and (cfg.type in (None, "stdio")) and not cfg.cwd:
        cfg.cwd = str(ensure_dir(get_runtime_subdir("mcp") / name))
    return cfg


def _remove_managed_stdio_cwd(name: str, cfg: MCPServerConfig | None) -> bool:
    """删除预设的受管工作目录，仅当该目录确实由 `_with_managed_stdio_cwd` 创建时生效。

    通过将实际 `cwd` 解析后与 `runtime/mcp/<name>` 比对，避免误删用户自定义目录。"""
    if cfg is None or not cfg.cwd:
        return False
    cwd = Path(cfg.cwd).expanduser().resolve(strict=False)
    managed = (get_runtime_subdir("mcp") / name).resolve(strict=False)
    # 不是受管目录或已不存在则跳过，保证只清理我们创建的内容
    if cwd != managed or not cwd.exists():
        return False
    if cwd.is_symlink() or cwd.is_file():
        cwd.unlink()
    else:
        shutil.rmtree(cwd)
    return True


def _url_with_param(url: str, key: str, value: str) -> str:
    """将 URL 查询参数 `key` 替换/追加为 `value`：先剔除已有同名参数再追加，避免重复键。"""
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k != key]
    query.append((key, value))
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def _arg_value(args: list[str], flag: str) -> str | None:
    """从命令行参数列表中读取 `flag` 的值，支持 `--flag value` 与 `--flag=value` 两种形式。"""
    prefix = f"{flag}="
    for index, item in enumerate(args):
        if item == flag and index + 1 < len(args):
            return args[index + 1]
        if item.startswith(prefix):
            return item[len(prefix):]
    return None


def _with_arg_value(args: list[str], flag: str, value: str) -> list[str]:
    """返回新的参数列表：先剔除 `flag` 的所有既有出现（含空格分隔与等号形式），再追加
    `flag value`。用 `skip_next` 跳过空格分隔形式中紧随其后的值。"""
    out: list[str] = []
    skip_next = False
    prefix = f"{flag}="
    for item in args:
        if skip_next:
            skip_next = False
            continue
        if item == flag:
            skip_next = True
            continue
        if item.startswith(prefix):
            continue
        out.append(item)
    out.extend([flag, value])
    return out


def _field_value_from_config(field: McpPresetField, cfg: MCPServerConfig | None) -> str | None:
    """根据字段的 `target` 类型，从已有配置中读取该凭据的当前值（env/header/arg/url_param）。"""
    if cfg is None:
        return None
    target_kind, target_name = field.target
    if target_kind == "env":
        value = cfg.env.get(target_name)
        return value if value else None
    if target_kind == "header":
        value = cfg.headers.get(target_name)
        return value if value else None
    if target_kind == "arg":
        return _arg_value(list(cfg.args), target_name)
    if target_kind == "url_param" and cfg.url:
        parsed = urllib.parse.urlsplit(cfg.url)
        values = urllib.parse.parse_qs(parsed.query).get(target_name)
        if values:
            return values[0]
    return None


def _field_configured(field: McpPresetField, cfg: MCPServerConfig | None) -> bool:
    """判断字段是否"已配置"：配置中有值即可；否则回退检查同名进程环境变量。"""
    value = _field_value_from_config(field, cfg)
    if value:
        return True
    return bool(field.env_var and os.environ.get(field.env_var))


def _field_payload(field: McpPresetField, cfg: MCPServerConfig | None) -> dict[str, Any]:
    return {
        "name": field.name,
        "label": field.label,
        "secret": field.secret,
        "required": field.required,
        "configured": _field_configured(field, cfg),
        "placeholder": field.placeholder,
        "env_var": field.env_var,
    }


def _resolve_field_value(
    field: McpPresetField,
    query: QueryParams,
    existing: MCPServerConfig | None,
) -> str | None:
    """按优先级解析字段值：WebUI 提交值 > 已有配置值 > 环境变量占位符。

    当仅环境变量存在时返回 `${VAR}` 占位形式，由配置加载器 `resolve_config_env_vars`
    在运行时展开，避免把真实凭据写入配置文件。"""
    provided = _query_value(query, field.name)
    if provided:
        return provided
    current = _field_value_from_config(field, existing)
    if current:
        return current
    if field.env_var and os.environ.get(field.env_var):
        return f"${{{field.env_var}}}"
    return None


def _materialize_server(
    preset: McpPreset,
    query: QueryParams,
    existing: MCPServerConfig | None,
) -> MCPServerConfig:
    """根据预设模板与 WebUI 输入，构造可持久化的 `MCPServerConfig`。

    核心流程：克隆预设默认配置 -> 逐字段解析值并按 `target` 注入到 env/header/args/url ->
    为 stdio 服务器设置受管工作目录。必填字段缺失时抛出 400 错误。"""
    if preset.server is None or not preset.install_supported:
        raise McpPresetError(f"{preset.display_name} is not supported yet", status=409)

    cfg = _clone_server(preset.server)
    for field_spec in preset.fields:
        value = _resolve_field_value(field_spec, query, existing)
        if field_spec.required and not value:
            raise McpPresetError(f"missing {field_spec.label}")
        if not value:
            continue
        target_kind, target_name = field_spec.target
        if target_kind == "env":
            cfg.env[target_name] = value
        elif target_kind == "header":
            cfg.headers[target_name] = value
        elif target_kind == "arg":
            cfg.args = _with_arg_value(list(cfg.args), target_name, value)
        elif target_kind == "url_param":
            cfg.url = _url_with_param(cfg.url, target_name, value)
    return _with_managed_stdio_cwd(preset.name, cfg)


def _command_available(command: str) -> bool:
    """检查命令是否可用：先在 PATH 中查找，再尝试作为文件路径是否存在。"""
    if not command:
        return False
    if shutil.which(command):
        return True
    path = Path(command).expanduser()
    return path.exists() and path.is_file()


def _config_available(cfg: MCPServerConfig | None) -> bool:
    """判断服务器配置"可用"：stdio 型看命令是否存在，远程型只要有 URL 即视为可用。"""
    if cfg is None:
        return False
    if cfg.command:
        return _command_available(cfg.command)
    if cfg.url:
        return True
    return False


def _status_for(preset: McpPreset, cfg: MCPServerConfig | None) -> str:
    """计算预设在前端展示的状态字符串（驱动 UI 徽标）。

    优先级：未安装 -> 缺凭据 -> 缺依赖 -> 已配置。缺凭据判定会回退检查环境变量。"""
    if cfg is None:
        return "not_installed" if preset.install_supported else "coming_soon"
    if any(field.required and not _field_configured(field, cfg) for field in preset.fields):
        return "missing_credentials"
    if cfg.command and not _command_available(cfg.command):
        return "missing_dependency"
    return "configured"


def _connection_summary(cfg: MCPServerConfig | None) -> str:
    """生成人类可读的连接摘要：stdio 取命令+前两个参数，远程型取去查询串的 URL。"""
    if cfg is None:
        return ""
    if cfg.command:
        return " ".join([cfg.command, *cfg.args[:2]]).strip()
    if cfg.url:
        parsed = urllib.parse.urlsplit(cfg.url)
        # 仅保留 scheme/netloc/path，丢弃查询串以避免泄露凭据
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return ""


def _tool_allowlist(cfg: MCPServerConfig | None) -> list[str]:
    """返回启用的工具白名单：未配置时默认 `["*"]`（全部启用）。"""
    if cfg is None:
        return ["*"]
    return list(cfg.enabled_tools)


def _managed_mcp_path(name: str, cfg: MCPServerConfig | None) -> list[str]:
    """返回该预设受管路径标识列表：仅 stdio 型有运行时目录，远程型返回空。"""
    if cfg is None or not cfg.command:
        return []
    return [f"runtime:mcp/{name}"]


def _preset_manifest(preset: McpPreset, *, logo_url: str) -> dict[str, Any]:
    """构建内置预设的 app manifest（统一的应用描述结构），供前端展示安装/卸载能力与信任级别。"""
    server = preset.server
    managed_paths = _managed_mcp_path(preset.name, server)
    field_specs = [
        compact_dict({
            "name": field.name,
            "target": field.target[0],
            "required": field.required,
            "secret": field.secret,
            "env_var": field.env_var,
        })
        for field in preset.fields
    ]
    capabilities = [
        compact_dict({
            "type": "mcp",
            "transport": preset.transport,
            "command": server.command if server and server.command else None,
            "args": list(server.args) if server and server.command else None,
            "url": _connection_summary(server) if server and server.url else None,
            "fields": field_specs,
        })
    ]
    return app_manifest(
        app_id=preset.name,
        display_name=preset.display_name,
        description=preset.description,
        category=preset.category,
        source="mcp-preset",
        docs_url=preset.docs_url,
        logo_url=logo_url,
        brand_color=preset.brand_color,
        capabilities=capabilities,
        install=compact_dict({
            "supported": preset.install_supported,
            "strategy": "config",
            "managed_paths": managed_paths,
            "verification": ["config_present", "dependency_available"],
        }),
        remove=compact_dict({
            "supported": True,
            "strategy": "config",
            "managed_paths": managed_paths,
            "verification": ["config_absent", "managed_paths_absent"] if managed_paths else ["config_absent"],
        }),
        trust={
            "registry": "mcp-presets",
            "level": "builtin",
            "review_status": "builtin_preset",
        },
    )


def _custom_manifest(name: str, cfg: MCPServerConfig) -> dict[str, Any]:
    """构建用户自定义 MCP 服务器的 app manifest。无受管路径（不清理运行时目录），
    信任级别为 `user`，与内置预设的 `builtin` 区分。"""
    transport = cfg.type or ("stdio" if cfg.command else "streamableHttp")
    managed_paths: list[str] = []
    return app_manifest(
        app_id=name,
        display_name=name,
        description="Custom MCP server from nanobot config.",
        category="custom",
        source="mcp-custom",
        brand_color="#64748B",
        capabilities=[
            compact_dict({
                "type": "mcp",
                "transport": transport,
                "command": cfg.command or None,
                "url": _connection_summary(cfg) if cfg.url else None,
            })
        ],
        install=compact_dict({
            "supported": True,
            "strategy": "config",
            "managed_paths": managed_paths,
            "verification": ["config_present", "dependency_available"],
        }),
        remove=compact_dict({
            "supported": True,
            "strategy": "config",
            "managed_paths": managed_paths,
            "verification": ["config_absent", "managed_paths_absent"] if managed_paths else ["config_absent"],
        }),
        trust={
            "registry": "user-config",
            "level": "user",
            "review_status": "user_managed",
        },
    )


def _preset_payload(preset: McpPreset, configured_servers: dict[str, MCPServerConfig]) -> dict[str, Any]:
    """构建单个内置预设的完整前端 payload：状态、凭据字段、连接摘要、工具白名单与 manifest。"""
    cfg = configured_servers.get(preset.name)
    status = _status_for(preset, cfg)
    # "已配置"判定：配置存在且非缺凭据状态（缺依赖也算已配置，仅凭据未填不算）
    configured = cfg is not None and status not in {"missing_credentials"}
    logo_url = _favicon_url(preset.brand_domain)
    return {
        "name": preset.name,
        "display_name": preset.display_name,
        "category": preset.category,
        "description": preset.description,
        "docs_url": preset.docs_url,
        "transport": preset.transport,
        "requires": preset.requires,
        "note": preset.note,
        "install_supported": preset.install_supported,
        "installed": cfg is not None,
        "configured": configured,
        "available": configured and _config_available(cfg),
        "status": status,
        "logo_url": logo_url,
        "brand_color": preset.brand_color,
        "required_fields": [_field_payload(field, cfg) for field in preset.fields],
        "connection_summary": _connection_summary(cfg),
        "enabled_tools": _tool_allowlist(cfg),
        "source": "preset",
        "manifest": _preset_manifest(preset, logo_url=logo_url),
    }


def _custom_payload(
    name: str,
    cfg: MCPServerConfig,
    *,
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    """构建用户自定义服务器的 payload。传输方式缺省时按 URL 后缀推断（`/sse` -> sse，
    否则 streamableHttp），状态仅区分缺依赖与已配置。"""
    transport = cfg.type
    if not transport:
        transport = "stdio" if cfg.command else ("sse" if cfg.url.rstrip("/").endswith("/sse") else "streamableHttp")
    status = "missing_dependency" if cfg.command and not _command_available(cfg.command) else "configured"
    return {
        "name": name,
        "display_name": name,
        "category": "custom",
        "description": "Custom MCP server from nanobot config.",
        "docs_url": "",
        "transport": transport,
        "requires": "",
        "note": "",
        "install_supported": True,
        "installed": True,
        "configured": True,
        "available": _config_available(cfg),
        "status": status,
        "logo_url": None,
        "brand_color": "#64748B",
        "required_fields": [],
        "connection_summary": _connection_summary(cfg),
        "enabled_tools": _tool_allowlist(cfg),
        "tool_names": tool_names or [],
        "source": "custom",
        "manifest": _custom_manifest(name, cfg),
    }


def mcp_presets_payload(
    *,
    last_action: dict[str, Any] | None = None,
    tool_preview: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    """构建 MCP 设置页的主 payload：合并内置预设行与自定义服务器行，附安装计数与上次动作结果。

    自定义行通过 `name not in known` 过滤掉与内置预设同名的条目，避免重复展示。
    `tool_preview` 提供最近一次测试得到的工具名预览，按名注入对应行。"""
    config = load_config()
    known = _known_preset_names()
    preset_rows = [
        _preset_payload(preset, config.tools.mcp_servers)
        | ({"tool_names": tool_preview.get(preset.name, [])} if tool_preview and preset.name in tool_preview else {})
        for preset in MCP_PRESETS
    ]
    custom_rows = [
        _custom_payload(name, cfg, tool_names=(tool_preview or {}).get(name))
        for name, cfg in sorted(config.tools.mcp_servers.items())
        if name not in known
    ]
    payload: dict[str, Any] = {
        "presets": [*preset_rows, *custom_rows],
        "installed_count": len(config.tools.mcp_servers),
    }
    if last_action is not None:
        payload["last_action"] = last_action
    return payload


def _display_name_for(name: str, preset: McpPreset | None = None) -> str:
    return preset.display_name if preset is not None else name


def _action_message(action: str, preset: McpPreset, *, ok: bool = True) -> dict[str, Any]:
    """构建内置预设动作的 `last_action` 消息，含 `verification` 数组告知前端应校验的状态
    （enable -> config_present，remove -> config_absent）。"""
    verb = {
        "enable": "Enabled",
        "remove": "Removed",
        "test": "Checked",
    }.get(action, "Updated")
    payload: dict[str, Any] = {
        "ok": ok,
        "message": f"{verb} MCP preset for {preset.display_name}.",
    }
    if action == "enable":
        payload["installed"] = True
        payload["verification"] = ["config_present"]
    elif action == "remove":
        payload["removed"] = True
        payload["verification"] = ["config_absent"]
    return payload


def _server_action_message(action: str, name: str, *, ok: bool = True) -> dict[str, Any]:
    """构建自定义服务器动作的 `last_action` 消息，语义同 `_action_message` 但面向自定义服务器。"""
    verb = {
        "custom": "Saved",
        "import": "Imported",
        "import-cursor": "Imported",
        "tools": "Updated tools for",
        "remove": "Removed",
    }.get(action, "Updated")
    payload: dict[str, Any] = {
        "ok": ok,
        "message": f"{verb} MCP server {name}.",
    }
    if action in {"custom", "import", "import-cursor"}:
        payload["installed"] = True
        payload["verification"] = ["config_present"]
    elif action == "remove":
        payload["removed"] = True
        payload["verification"] = ["config_absent"]
    return payload


def _scrub_test_error(text: str) -> str:
    """从测试错误信息中脱敏：用预定义正则把 API key/token 等敏感值替换为 `<redacted>`，
    再截断到 400 字符，避免凭据经错误信息泄露到前端。"""
    scrubbed = _SECRET_QUERY_RE.sub(r"\1<redacted>", text.strip())
    scrubbed = _SECRET_ASSIGNMENT_RE.sub(r"\1<redacted>", scrubbed)
    return scrubbed[:400] if scrubbed else "Connection failed."


def _checked_at() -> str:
    """返回 UTC 时间戳的 ISO 8601 字符串（用 `Z` 后缀代替 `+00:00`，前端友好）。"""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _test_timeout(cfg: MCPServerConfig) -> int:
    """计算测试连接的超时：取配置值或默认 20s，并钳制在 [5, 20] 区间内。"""
    raw = cfg.tool_timeout or _DEFAULT_TEST_TIMEOUT
    return max(5, min(int(raw), _DEFAULT_TEST_TIMEOUT))


async def _close_mcp_stacks(stacks: Mapping[str, Any]) -> None:
    """逐一关闭 MCP 连接栈，忽略单个关闭异常以保证全部清理。"""
    for stack in stacks.values():
        with suppress(Exception):
            await stack.aclose()


async def mcp_presets_test_action(query: QueryParams) -> dict[str, Any]:
    """Connect to an enabled MCP preset and report its tool surface.

    执行连接测试：解析环境变量占位 -> 预检凭据/依赖 -> 在超时内尝试 MCP 握手 ->
    采集注册表中以 `mcp_<name>_` 为前缀的工具名。任何失败都返回带 `last_action` 的
    payload（而非抛异常），便于前端在设置页内联展示结果。"""
    from nanobot.agent.tools.mcp import connect_mcp_servers

    name = (_query_first(query, "name") or "").strip()
    if not name:
        raise McpPresetError("missing MCP preset name")
    if _MCP_PRESET_NAME_RE.match(name) is None:
        raise McpPresetError("invalid MCP server name")
    preset = _preset_by_name_optional(name)
    display_name = _display_name_for(name, preset)

    try:
        # 展开配置中的 `${VAR}` 占位符为真实环境变量值，供测试连接直接使用
        config = resolve_config_env_vars(load_config())
    except ValueError as exc:
        return mcp_presets_payload(last_action={
            "ok": False,
            "message": _scrub_test_error(str(exc)),
            "error": _scrub_test_error(str(exc)),
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        })

    cfg = config.tools.mcp_servers.get(name)
    if cfg is None:
        raise McpPresetError(f"{display_name} is not enabled", status=404)

    # 自定义服务器无预设定义，单独判定缺依赖状态
    status = _status_for(preset, cfg) if preset is not None else (
        "missing_dependency" if cfg.command and not _command_available(cfg.command) else "configured"
    )
    if status == "missing_credentials":
        last_action = {
            "ok": False,
            "message": f"{display_name} is missing required credentials.",
            "error": "missing credentials",
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        }
        return mcp_presets_payload(last_action=last_action)

    if cfg.command and not _command_available(cfg.command):
        last_action = {
            "ok": False,
            "message": f"{display_name} requires '{cfg.command}' on PATH.",
            "error": "missing dependency",
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        }
        return mcp_presets_payload(last_action=last_action)

    registry = ToolRegistry()
    stacks: dict[str, Any] = {}
    try:
        stacks = await asyncio.wait_for(
            connect_mcp_servers({name: cfg}, registry),
            timeout=_test_timeout(cfg),
        )
        # MCP 工具注册时统一加 `mcp_<name>_` 前缀，据此筛出本服务器暴露的工具
        tool_prefix = f"mcp_{name}_"
        tool_names = sorted(name for name in registry.tool_names if name.startswith(tool_prefix))
        ok = name in stacks
        if ok:
            last_action = {
                "ok": True,
                "message": (
                    f"{display_name} connected with {len(tool_names)} tools."
                    if tool_names
                    else f"{display_name} connected, but reported no tools."
                ),
                "tool_count": len(tool_names),
                "tool_names": tool_names[:_MAX_TEST_TOOLS],
                "checked_at": _checked_at(),
            }
        else:
            last_action = {
                "ok": False,
                "message": f"{display_name} did not complete an MCP handshake.",
                "error": "MCP handshake failed",
                "tool_count": 0,
                "tool_names": [],
                "checked_at": _checked_at(),
            }
    except asyncio.TimeoutError:
        last_action = {
            "ok": False,
            "message": f"{display_name} test timed out.",
            "error": "timeout",
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        }
    except Exception as exc:
        error = _scrub_test_error(str(exc))
        last_action = {
            "ok": False,
            "message": f"{display_name} could not connect.",
            "error": error,
            "tool_count": 0,
            "tool_names": [],
            "checked_at": _checked_at(),
        }
    finally:
        await _close_mcp_stacks(stacks)

    preview = {name: last_action.get("tool_names", [])} if last_action.get("tool_names") else None
    return mcp_presets_payload(last_action=last_action, tool_preview=preview)


def _parse_json_value(raw: str | None, *, fallback: Any) -> Any:
    """解析 JSON 字符串；空输入返回 `fallback`，解析失败抛 McpPresetError。"""
    if raw is None or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpPresetError(f"invalid JSON: {exc.msg}") from exc


def _parse_string_list(raw: str | None) -> list[str]:
    """解析字符串列表：JSON 数组优先；若是纯字符串则用 `shlex.split` 按 shell 规则拆分
    （兼容 `--foo bar --baz=qux` 形式的命令行参数）。"""
    if raw is None or not raw.strip():
        return []
    parsed = _parse_json_value(raw, fallback=None)
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return [item for item in parsed if item.strip()]
    if isinstance(parsed, str):
        return shlex.split(parsed)
    raise McpPresetError("expected a JSON string array")


def _parse_string_map(raw: str | None) -> dict[str, str]:
    """解析 JSON 对象为 string->string 映射，键值非字符串或非对象均报错，空键被丢弃。"""
    parsed = _parse_json_value(raw, fallback={})
    if not isinstance(parsed, dict):
        raise McpPresetError("expected a JSON object")
    out: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise McpPresetError("JSON object values must be strings")
        if key.strip():
            out[key.strip()] = value
    return out


def _parse_enabled_tools(raw: str | None) -> list[str]:
    """解析工具白名单：含 `*` 即视为全部启用（归一化为 `["*"]`），否则返回具体工具名列表。"""
    if raw is None or not raw.strip():
        return ["*"]
    values = _parse_string_list(raw)
    if "*" in values:
        return ["*"]
    return values


def _normalize_transport(value: str | None, *, command: str = "", url: str = "") -> Literal["stdio", "sse", "streamableHttp"]:
    """归一化传输类型为三种标准值之一。

    显式值经别名表映射；缺省时按线索推断：有 command 推断为 stdio，URL 以 `/sse` 结尾
    推断为 sse，否则为 streamableHttp。支持 `streamable-http`/`http` 等常见写法别名。"""
    raw = (value or "").strip()
    if not raw:
        if command:
            return "stdio"
        if url.rstrip("/").endswith("/sse"):
            return "sse"
        return "streamableHttp"
    aliases = {
        "stdio": "stdio",
        "sse": "sse",
        "streamableHttp": "streamableHttp",
        "streamable-http": "streamableHttp",
        "streamable_http": "streamableHttp",
        "http": "streamableHttp",
    }
    normalized = aliases.get(raw)
    if normalized is None:
        raise McpPresetError("unsupported MCP transport")
    return normalized  # type: ignore[return-value]


def _validated_server_name(name: str) -> str:
    """校验服务器名合法性并归一化为小写。"""
    if not name or _MCP_PRESET_NAME_RE.match(name) is None:
        raise McpPresetError("invalid MCP server name")
    return name.strip().lower()


def _custom_server_from_query(query: QueryParams) -> tuple[str, MCPServerConfig]:
    """从 WebUI 查询参数构建自定义 `MCPServerConfig`。

    校验传输方式与必填项（stdio 需 command，远程需 url），超时钳制到 [5, 600] 秒，
    并按传输类型决定哪些字段保留（远程型清空 command/cwd，stdio 型清空 url）。"""
    name = _validated_server_name((_query_first(query, "name") or "").strip())
    command = (_query_first(query, "command") or "").strip()
    url = (_query_first(query, "url") or "").strip()
    transport = _normalize_transport(_query_first(query, "transport"), command=command, url=url)
    if transport == "stdio" and not command:
        raise McpPresetError("stdio MCP servers require a command")
    if transport in {"sse", "streamableHttp"} and not url:
        raise McpPresetError("remote MCP servers require a URL")
    raw_timeout = (_query_first(query, "tool_timeout") or "").strip()
    tool_timeout = _DEFAULT_CUSTOM_TIMEOUT
    if raw_timeout:
        try:
            tool_timeout = max(5, min(int(raw_timeout), 600))
        except ValueError as exc:
            raise McpPresetError("tool_timeout must be an integer") from exc
    cfg = MCPServerConfig(
        type=transport,
        command=command if transport == "stdio" else "",
        args=_parse_string_list(_query_first(query, "args")),
        env=_parse_string_map(_query_first(query, "env")),
        cwd=(_query_first(query, "cwd") or "").strip() if transport == "stdio" else "",
        url=url if transport in {"sse", "streamableHttp"} else "",
        headers=_parse_string_map(_query_first(query, "headers")),
        tool_timeout=tool_timeout,
        enabled_tools=_parse_enabled_tools(_query_first(query, "enabled_tools")),
    )
    return name, cfg


def _mcp_server_config(name: str, raw: Any) -> tuple[str, MCPServerConfig]:
    """从导入 JSON 中的单个服务器对象构建 `MCPServerConfig`。

    兼容 camelCase 与 snake_case 键名（`enabledTools`/`enabled_tools`、`toolTimeout`/
    `tool_timeout`），并对 args/env/headers 做严格类型校验，超时非法时回退默认值。"""
    server_name = _validated_server_name(name)
    if not isinstance(raw, Mapping):
        raise McpPresetError(f"MCP server '{server_name}' must be an object")
    command = str(raw.get("command") or "").strip()
    url = str(raw.get("url") or "").strip()
    # 同时接受 `type` 与 `transport` 两种键名
    transport_value = str(raw.get("type", raw.get("transport", "")) or "")
    transport = _normalize_transport(transport_value, command=command, url=url)
    if transport == "stdio" and not command:
        raise McpPresetError(f"MCP server '{server_name}' stdio transport requires a command")
    if transport in {"sse", "streamableHttp"} and not url:
        raise McpPresetError(f"MCP server '{server_name}' remote transport requires a URL")
    args = raw.get("args") or []
    env = raw.get("env") or {}
    headers = raw.get("headers") or {}
    cwd = str(raw.get("cwd") or "").strip()
    # 兼容 camelCase 与 snake_case 两类键名
    enabled_tools = raw.get("enabledTools", raw.get("enabled_tools", ["*"]))
    tool_timeout = raw.get("toolTimeout", raw.get("tool_timeout", _DEFAULT_CUSTOM_TIMEOUT))
    try:
        timeout_int = max(5, min(int(tool_timeout), 600))
    except (TypeError, ValueError):
        timeout_int = _DEFAULT_CUSTOM_TIMEOUT
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise McpPresetError(f"MCP server '{server_name}' args must be a string array")
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise McpPresetError(f"MCP server '{server_name}' env must be a string object")
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise McpPresetError(f"MCP server '{server_name}' headers must be a string object")
    # enabled_tools 类型异常时宽容回退为全部启用，避免导入因小错中断
    if not isinstance(enabled_tools, list) or not all(isinstance(item, str) for item in enabled_tools):
        enabled_tools = ["*"]
    return server_name, MCPServerConfig(
        type=transport,
        command=command if transport == "stdio" else "",
        args=args,
        env=dict(env),
        cwd=cwd if transport == "stdio" else "",
        url=url if transport in {"sse", "streamableHttp"} else "",
        headers=dict(headers),
        tool_timeout=timeout_int,
        enabled_tools=list(enabled_tools),
    )


def _import_mcp_servers(raw_json: str | None) -> dict[str, MCPServerConfig]:
    """解析导入的 JSON 配置（兼容 Claude Desktop / Cursor 等格式）。

    接受顶层为 `{"mcpServers": {...}}` 或直接为服务器映射的结构，逐项经
    `_mcp_server_config` 校验后返回。空配置视为错误。"""
    parsed = _parse_json_value(raw_json, fallback=None)
    if not isinstance(parsed, Mapping):
        raise McpPresetError("MCP config must be a JSON object")
    servers = parsed.get("mcpServers", parsed)
    if not isinstance(servers, Mapping):
        raise McpPresetError("MCP config must contain mcpServers")
    out: dict[str, MCPServerConfig] = {}
    for name, raw_server in servers.items():
        if not isinstance(name, str):
            raise McpPresetError("MCP server names must be strings")
        server_name, cfg = _mcp_server_config(name, raw_server)
        out[server_name] = cfg
    if not out:
        raise McpPresetError("MCP config contains no servers")
    return out


def custom_mcp_action(action: str, query: QueryParams) -> dict[str, Any]:
    """处理自定义 MCP 服务器的动作（保存/导入/更新工具白名单）。

    所有写操作都会 `save_config` 并标记 `requires_restart=True`，提示前端需重启或热重载
    才能让 agent 感知新配置。"""
    config = load_config()
    if action == "custom":
        name, cfg = _custom_server_from_query(query)
        config.tools.mcp_servers[name] = cfg
        save_config(config)
        payload = mcp_presets_payload(last_action=_server_action_message(action, name))
        payload["requires_restart"] = True
        return payload

    if action in {"import", "import-cursor"}:
        servers = _import_mcp_servers(_query_first(query, "config"))
        # update 合并而非替换，保留已有服务器
        config.tools.mcp_servers.update(servers)
        save_config(config)
        payload = mcp_presets_payload(last_action={
            "ok": True,
            "message": f"Imported {len(servers)} MCP server(s).",
        })
        payload["requires_restart"] = True
        return payload

    if action == "tools":
        name = _validated_server_name((_query_first(query, "name") or "").strip())
        cfg = config.tools.mcp_servers.get(name)
        if cfg is None:
            raise McpPresetError("unknown MCP server", status=404)
        cfg.enabled_tools = _parse_enabled_tools(_query_first(query, "enabled_tools"))
        config.tools.mcp_servers[name] = cfg
        save_config(config)
        payload = mcp_presets_payload(last_action=_server_action_message(action, name))
        payload["requires_restart"] = True
        return payload

    raise McpPresetError(f"unknown MCP action '{action}'", status=404)


def mcp_presets_action(action: str, query: QueryParams) -> dict[str, Any]:
    """处理内置预设的 enable/remove 动作（test 由异步入口单独处理）。

    enable: 物化预设配置写入 config；remove: 删除配置并清理受管运行时目录，
    清理失败会标记 `verification_failed` 但不阻断删除。"""
    name = (_query_first(query, "name") or "").strip()
    if not name:
        raise McpPresetError("missing MCP preset name")
    preset = _preset_by_name_optional(name)

    config = load_config()
    existing = config.tools.mcp_servers.get(name)

    if action == "enable":
        if preset is None:
            raise McpPresetError("unknown MCP preset", status=404)
        config.tools.mcp_servers[preset.name] = _materialize_server(preset, query, existing)
        save_config(config)
        payload = mcp_presets_payload(last_action=_action_message(action, preset))
        payload["requires_restart"] = True
        return payload

    if action == "remove":
        if preset is None and name not in config.tools.mcp_servers:
            raise McpPresetError("unknown MCP server", status=404)
        removed_runtime_files = False
        cleanup_error = ""
        if name in config.tools.mcp_servers:
            existing_cfg = config.tools.mcp_servers[name]
            try:
                # 先清理受管运行时目录再删配置：清理失败不阻断删除，仅记录错误
                removed_runtime_files = _remove_managed_stdio_cwd(name, existing_cfg)
            except OSError as exc:
                cleanup_error = str(exc)
            del config.tools.mcp_servers[name]
            save_config(config)
        last_action = (
            _action_message(action, preset)
            if preset is not None
            else _server_action_message(action, name)
        )
        if removed_runtime_files:
            last_action["message"] = f"{last_action['message']} Removed managed runtime files."
            last_action["managed_paths_removed"] = [f"runtime:mcp/{name}"]
            last_action["verification"] = ["config_absent", "managed_paths_absent"]
        if cleanup_error:
            last_action["ok"] = False
            last_action["message"] = (
                f"{last_action['message']} Could not remove managed runtime files: {cleanup_error}"
            )
            last_action["verification_failed"] = ["managed_paths_absent"]
        payload = mcp_presets_payload(last_action=last_action)
        payload["requires_restart"] = True
        return payload

    if action == "test":
        raise McpPresetError("MCP preset test must run through the async test action", status=500)

    raise McpPresetError(f"unknown MCP preset action '{action}'", status=404)


def attach_mcp_hot_reload_result(
    payload: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Merge an agent MCP reload acknowledgement into a WebUI settings payload.

    将 agent 侧 MCP 热重载的回执合并进设置 payload：用回执覆盖 `requires_restart`，
    拼接回执消息到 `last_action.message`，并在缺失 `ok` 时以回执结果填充。"""
    payload = dict(payload)
    payload["hot_reload"] = result
    payload["requires_restart"] = bool(result.get("requires_restart"))
    last_action = dict(payload.get("last_action") or {})
    base_message = str(last_action.get("message") or "").strip()
    reload_message = str(result.get("message") or "").strip()
    if reload_message:
        last_action["message"] = (
            f"{base_message} {reload_message}" if base_message else reload_message
        )
    if "ok" not in last_action:
        last_action["ok"] = bool(result.get("ok", False))
    payload["last_action"] = last_action
    return payload


async def mcp_presets_settings_action(
    action: str | None,
    query: QueryParams,
    *,
    reload_mcp: McpReload | None = None,
) -> dict[str, Any]:
    """Run a WebUI MCP preset action and hot-reload the agent when config changes.

    设置页统一异步入口：分发 test/自定义/预设动作。同步写配置动作用 `asyncio.to_thread`
    包裹避免阻塞事件循环；若提供 `reload_mcp` 回调则在配置变更后触发 agent 热重载并合并回执。"""
    if action is None:
        return mcp_presets_payload()
    if action == "test":
        return await mcp_presets_test_action(query)
    if action in _CUSTOM_ACTIONS:
        payload = await asyncio.to_thread(custom_mcp_action, action, query)
    else:
        payload = await asyncio.to_thread(mcp_presets_action, action, query)
    if reload_mcp is not None:
        payload = attach_mcp_hot_reload_result(payload, await reload_mcp())
    return payload
