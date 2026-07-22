"""Base LLM provider interface.

LLM 提供方抽象基类模块：定义所有 provider（Anthropic / OpenAI 兼容 / Azure /
Bedrock 等）的统一接口。屏蔽各厂商 API 在消息格式、工具调用格式、流式协议上的
差异，对上层 AgentRunner 暴露一致的 chat / chat_stream / 工具调用 / 用量统计契约。
本模块还内建可重试错误识别、指数退避、Retry-After 解析与流式断流恢复等通用策略。
"""

import asyncio
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import json_repair
from loguru import logger

# 流式响应空闲超时相关配置：通过环境变量 NANOBOT_STREAM_IDLE_TIMEOUT_S 可调。
# 当流式响应两个 chunk 之间长时间无数据时，视为连接卡死并触发重试。
STREAM_IDLE_TIMEOUT_ENV = "NANOBOT_STREAM_IDLE_TIMEOUT_S"
DEFAULT_STREAM_IDLE_TIMEOUT_S = 90.0  # 默认空闲超时 90 秒
MAX_STREAM_IDLE_TIMEOUT_S = 3600.0  # 上限 1 小时，防止用户误配过大值


def resolve_stream_idle_timeout_s(
    *,
    env_value: str | None = None,
    default: float = DEFAULT_STREAM_IDLE_TIMEOUT_S,
    maximum: float = MAX_STREAM_IDLE_TIMEOUT_S,
) -> float:
    """Return a safe streaming idle timeout from env/config text.

    将环境变量/配置中的文本解析为安全的流式空闲超时秒数。
    对非法值、非正值、超上限值做兜底处理，保证最终返回一个可用数值。
    """
    raw = os.environ.get(STREAM_IDLE_TIMEOUT_ENV) if env_value is None else env_value
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid {}={!r}; using {}", STREAM_IDLE_TIMEOUT_ENV, raw, default)
        return default
    if value <= 0:
        logger.warning("Ignoring non-positive {}={!r}; using {}", STREAM_IDLE_TIMEOUT_ENV, raw, default)
        return default
    if value > maximum:
        logger.warning("Clamping {}={!r} to {}", STREAM_IDLE_TIMEOUT_ENV, raw, maximum)
        return maximum
    return value


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM.

    LLM 返回的一次工具调用请求的统一表示。各 provider 将自家格式的工具调用
    （OpenAI 的 tool_calls、Anthropic 的 tool_use block 等）归一化为该结构。
    extra_content / provider_specific_fields 保留厂商特有字段，便于回放历史。
    """
    id: str
    name: str
    arguments: Any
    extra_content: dict[str, Any] | None = None
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None

    def to_openai_tool_call(self) -> dict[str, Any]:
        """Serialize to an OpenAI-style tool_call payload.

        序列化为 OpenAI 风格的 tool_call 字典，供历史回放或跨 provider 传递使用。
        非字符串参数会先 JSON 序列化，保证 arguments 始终为字符串。
        """
        arguments = (
            self.arguments
            if isinstance(self.arguments, str)
            else json.dumps(self.arguments, ensure_ascii=False)
        )
        tool_call = {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": arguments,
            },
        }
        if self.extra_content:
            tool_call["extra_content"] = self.extra_content
        if self.provider_specific_fields:
            tool_call["provider_specific_fields"] = self.provider_specific_fields
        if self.function_provider_specific_fields:
            tool_call["function"]["provider_specific_fields"] = self.function_provider_specific_fields
        return tool_call


def parse_tool_arguments(arguments: Any) -> Any:
    """Parse provider tool arguments without guessing executable parameters.

    解析 provider 返回的工具参数，用于"即将执行"的工具调用。
    刻意不做容错修复：合法 JSON 对象字符串转 dict，空字符串视为无参调用，
    畸形 JSON / JSON 数组 / 标量原样保留，交由 ToolRegistry 在执行前拒绝。
    这样避免把模型生成的错误参数"猜"成可执行参数而引发意外行为。
    """
    if arguments is None:
        return {}
    if not isinstance(arguments, str):
        return arguments

    stripped = arguments.strip()
    if not stripped:
        return {}

    try:
        parsed = json.loads(stripped)
    except Exception:
        return arguments
    return arguments if parsed is None else parsed


def tool_arguments_object_for_replay(arguments: Any) -> dict[str, Any]:
    """Return object-shaped arguments for provider history replay only.

    仅用于"历史回放"（把对话历史发回 provider）时的参数整形。
    与 parse_tool_arguments 不同，这里可以用 json_repair 容错修复畸形 JSON，
    因为只是把已有历史重新塞进请求体，不会被执行。不要用于新生成的工具调用。
    """
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return {}

    stripped = arguments.strip()
    if not stripped:
        return {}

    try:
        parsed = json.loads(stripped)
    except Exception:
        try:
            parsed = json_repair.loads(stripped)
        except Exception:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def tool_arguments_json_for_replay(arguments: Any) -> str:
    """Return JSON object string arguments for provider history replay only."""
    return json.dumps(tool_arguments_object_for_replay(arguments), ensure_ascii=False)


@dataclass
class LLMResponse:
    """Response from an LLM provider.

    一次 LLM 调用的统一响应结构。content 为文本回复，tool_calls 为工具调用列表。
    除正常内容外，还携带错误元数据（error_status_code / error_kind / error_code 等），
    供重试策略与 FallbackProvider 判断是否为可重试/可回退错误。reasoning_content
    与 thinking_blocks 分别兼容 DeepSeek-Kimi 系与 Anthropic 扩展思考协议。
    """
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    retry_after: float | None = None  # Provider supplied retry wait in seconds.
    reasoning_content: str | None = None  # Kimi, DeepSeek-R1, MiMo etc.
    thinking_blocks: list[dict] | None = None  # Anthropic extended thinking
    # Structured error metadata used by retry policy when finish_reason == "error".
    # 当 finish_reason == "error" 时，以下字段供重试策略判断错误性质：
    error_status_code: int | None = None
    error_kind: str | None = None  # e.g. "timeout", "connection"
    error_type: str | None = None  # Provider/type semantic, e.g. insufficient_quota.
    error_code: str | None = None  # Provider/code semantic, e.g. rate_limit_exceeded.
    error_retry_after_s: float | None = None
    error_should_retry: bool | None = None

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0

    @property
    def should_execute_tools(self) -> bool:
        """Tools execute only when has_tool_calls AND finish_reason is a tool-capable stop.
        Blocks gateway-injected calls under ``refusal`` / ``content_filter`` / ``error`` (#3220).

        仅当存在工具调用且 finish_reason 属于可执行工具的停止原因时才执行工具。
        对 refusal / content_filter / error 等原因显式拦截，避免网关注入的调用被误执行。
        """
        if not self.has_tool_calls:
            return False
        return self.finish_reason in ("tool_calls", "function_call", "stop")


@dataclass(frozen=True)
class GenerationSettings:
    """Default generation settings.

    生成参数默认值（温度/最大 token/推理力度）。由 preset 配置注入到 provider，
    供 chat_with_retry / chat_stream_with_retry 在调用方未显式传参时回退使用。
    """

    temperature: float = 0.7
    max_tokens: int = 4096
    reasoning_effort: str | None = None


_SYNTHETIC_USER_CONTENT = "(conversation continued)"


class LLMProvider(ABC):
    """Base class for LLM providers.

    所有 LLM 提供方的抽象基类。子类需实现 chat / chat_stream / get_default_model。
    本基类内建跨厂商通用能力：消息清洗、角色交替规整、工具调用归一化、
    瞬时错误识别、指数退避重试、Retry-After 解析、流式断流恢复、图片降级等。
    """

    supports_progress_deltas = False

    # 重试策略相关常量
    _CHAT_RETRY_DELAYS = (1, 2, 4)  # 标准模式三次重试的退避秒数
    _PERSISTENT_MAX_DELAY = 60  # persistent 模式单次最长等待
    _PERSISTENT_IDENTICAL_ERROR_LIMIT = 10  # 连续相同错误达此次数则停止 persistent 重试
    _RETRY_HEARTBEAT_CHUNK = 30  # 重试等待时每 30 秒发一次心跳回调，便于 UI 提示
    # 瞬时错误文本标记（含中英文），命中其一即视为可重试
    _TRANSIENT_ERROR_MARKERS = (
        "429",
        "rate limit",
        "500",
        "502",
        "503",
        "504",
        "overloaded",
        "timeout",
        "timed out",
        "connection",
        "server error",
        "temporarily unavailable",
        "速率限制",
        "访问量过大",
    )
    _RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})
    _TRANSIENT_ERROR_KINDS = frozenset({"timeout", "connection"})
    # 不可重试的 429 语义 token：配额耗尽/欠费/余额不足等，重试也不会成功
    _NON_RETRYABLE_429_ERROR_TOKENS = frozenset({
        "insufficient_quota",
        "quota_exceeded",
        "quota_exhausted",
        "billing_hard_limit_reached",
        "insufficient_balance",
        "credit_balance_too_low",
        "billing_not_active",
        "payment_required",
    })
    _RETRYABLE_429_ERROR_TOKENS = frozenset({
        "rate_limit_exceeded",
        "rate_limit_error",
        "too_many_requests",
        "request_limit_exceeded",
        "requests_limit_exceeded",
        "overloaded_error",
    })
    # 不可重试的 429 文本标记（配额/计费类），在错误正文里匹配
    _NON_RETRYABLE_429_TEXT_MARKERS = (
        "insufficient_quota",
        "insufficient quota",
        "quota exceeded",
        "quota exhausted",
        "billing hard limit",
        "billing_hard_limit_reached",
        "billing not active",
        "insufficient balance",
        "insufficient_balance",
        "credit balance too low",
        "payment required",
        "out of credits",
        "out of quota",
        "exceeded your current quota",
    )
    # 可重试的 429 文本标记（限流/过载类），命中则重试
    _RETRYABLE_429_TEXT_MARKERS = (
        "rate limit",
        "rate_limit",
        "too many requests",
        "retry after",
        "try again in",
        "temporarily unavailable",
        "overloaded",
        "concurrency limit",
        "速率限制",
    )

    _SENTINEL = object()

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base
        self.generation: GenerationSettings = GenerationSettings()

    @staticmethod
    def _sanitize_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sanitize message content: fix empty blocks, strip internal _meta fields.

        清洗消息内容：修复空内容块、移除内部 _meta 字段，避免 provider 因空内容报错。
        - 空字符串 content：assistant 带 tool_calls 时改 None，否则用 "(empty)" 占位。
        - 列表内容中空的 text/input_text/output_text 块会被丢弃。
        - dict 形 content 会被包成单元素列表以统一形态。
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")

            if isinstance(content, str) and not content:
                clean = dict(msg)
                clean["content"] = None if (msg.get("role") == "assistant" and msg.get("tool_calls")) else "(empty)"
                result.append(clean)
                continue

            if isinstance(content, list):
                new_items: list[Any] = []
                changed = False
                for item in content:
                    if (
                        isinstance(item, dict)
                        and item.get("type") in ("text", "input_text", "output_text")
                        and not item.get("text")
                    ):
                        changed = True
                        continue
                    if isinstance(item, dict) and "_meta" in item:
                        new_items.append({k: v for k, v in item.items() if k != "_meta"})
                        changed = True
                    else:
                        new_items.append(item)
                if changed:
                    clean = dict(msg)
                    if new_items:
                        clean["content"] = new_items
                    elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                        clean["content"] = None
                    else:
                        clean["content"] = "(empty)"
                    result.append(clean)
                    continue

            if isinstance(content, dict):
                clean = dict(msg)
                clean["content"] = [content]
                result.append(clean)
                continue

            result.append(msg)
        return result

    @staticmethod
    def _tool_name(tool: dict[str, Any]) -> str:
        """Extract tool name from either OpenAI or Anthropic-style tool schemas."""
        name = tool.get("name")
        if isinstance(name, str):
            return name
        fn = tool.get("function")
        if isinstance(fn, dict):
            fname = fn.get("name")
            if isinstance(fname, str):
                return fname
        return ""

    @classmethod
    def _tool_cache_marker_indices(cls, tools: list[dict[str, Any]]) -> list[int]:
        """Return cache marker indices: builtin/MCP boundary and tail index.

        返回工具列表中用于"提示词缓存断点"的索引：内置工具与 MCP 工具的边界、
        以及列表末尾。Anthropic 等支持 prompt caching 的 provider 据此插入缓存断点，
        使工具定义部分能在多轮对话中复用缓存。
        """
        if not tools:
            return []

        tail_idx = len(tools) - 1
        last_builtin_idx: int | None = None
        for i in range(tail_idx, -1, -1):
            if not cls._tool_name(tools[i]).startswith("mcp_"):
                last_builtin_idx = i
                break

        ordered_unique: list[int] = []
        for idx in (last_builtin_idx, tail_idx):
            if idx is not None and idx not in ordered_unique:
                ordered_unique.append(idx)
        return ordered_unique

    @staticmethod
    def _sanitize_request_messages(
        messages: list[dict[str, Any]],
        allowed_keys: frozenset[str],
    ) -> list[dict[str, Any]]:
        """Keep only provider-safe message keys and normalize assistant content.

        按 provider 允许的 key 集合过滤消息，移除厂商不识别的字段；
        同时给缺少 content 的 assistant 消息补上 content=None，满足协议要求。
        """
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in allowed_keys}
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        发送一次（非流式）对话补全请求。这是 provider 最核心的抽象方法，
        各子类把统一参数翻译为自家厂商的 HTTP/SDK 调用，再把响应归一化为 LLMResponse。
        tools 为工具定义列表，tool_choice 控制工具选择策略（auto/required/指定工具）。

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier (provider-specific).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
            tool_choice: Tool selection strategy ("auto", "required", or specific tool dict).

        Returns:
            LLMResponse with content and/or tool calls.
        """
        pass

    @classmethod
    def _is_transient_error(cls, content: str | None) -> bool:
        err = (content or "").lower()
        return any(marker in err for marker in cls._TRANSIENT_ERROR_MARKERS)

    @classmethod
    def _is_transient_response(cls, response: LLMResponse) -> bool:
        """Prefer structured error metadata, fallback to text markers for legacy providers.

        判断响应是否为"瞬时错误"（可重试）。优先用结构化错误元数据判断：
        1. error_should_retry 显式给出则直接采用；
        2. HTTP 状态码判断（429 单独细分，408/409/5xx 可重试）；
        3. error_kind 属 timeout/connection 视为瞬时；
        4. 最后回退到正文文本标记匹配（兼容旧 provider）。
        """
        if response.error_should_retry is not None:
            return bool(response.error_should_retry)

        if response.error_status_code is not None:
            status = int(response.error_status_code)
            if status == 429:
                return cls._is_retryable_429_response(response)
            if status in cls._RETRYABLE_STATUS_CODES or status >= 500:
                return True

        kind = (response.error_kind or "").strip().lower()
        if kind in cls._TRANSIENT_ERROR_KINDS:
            return True

        return cls._is_transient_error(response.content)

    @classmethod
    def is_arrearage_response(cls, response: LLMResponse) -> bool:
        """Detect API-key arrearage / quota / billing errors that won't clear on retry.

        检测欠费/配额/计费类错误（HTTP 402 或 insufficient_quota/payment_required 等语义）。
        这类错误重试无意义，调用方可据此切换 provider 或提示用户充值。
        复用 429 重试策略中"不可重试"的 token 与文本标记集合。
        """
        if response.error_status_code is not None and int(response.error_status_code) == 402:
            return True

        type_token = cls._normalize_error_token(response.error_type)
        code_token = cls._normalize_error_token(response.error_code)
        if any(
            token in cls._NON_RETRYABLE_429_ERROR_TOKENS
            for token in (type_token, code_token)
            if token is not None
        ):
            return True

        content = (response.content or "").lower()
        return any(marker in content for marker in cls._NON_RETRYABLE_429_TEXT_MARKERS)

    @staticmethod
    def _normalize_error_token(value: Any) -> str | None:
        if value is None:
            return None
        token = str(value).strip().lower()
        return token or None

    @classmethod
    def _extract_error_type_code(cls, payload: Any) -> tuple[str | None, str | None]:
        data: dict[str, Any] | None = None
        if isinstance(payload, dict):
            data = payload
        elif isinstance(payload, str):
            text = payload.strip()
            if text:
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    data = parsed
        if not isinstance(data, dict):
            return None, None

        error_obj = data.get("error")
        type_value = data.get("type")
        code_value = data.get("code")
        if isinstance(error_obj, dict):
            type_value = error_obj.get("type") or type_value
            code_value = error_obj.get("code") or code_value

        return cls._normalize_error_token(type_value), cls._normalize_error_token(code_value)

    @classmethod
    def _is_retryable_429_response(cls, response: LLMResponse) -> bool:
        # 429 细分：先排除配额/计费类（不可重试），再匹配限流/过载类（可重试）。
        # 未知语义的 429 默认可重试（WATT+retry），宁可多试一次也不漏掉临时限流。
        type_token = cls._normalize_error_token(response.error_type)
        code_token = cls._normalize_error_token(response.error_code)
        semantic_tokens = {
            token for token in (type_token, code_token)
            if token is not None
        }
        if any(token in cls._NON_RETRYABLE_429_ERROR_TOKENS for token in semantic_tokens):
            return False

        content = (response.content or "").lower()
        if any(marker in content for marker in cls._NON_RETRYABLE_429_TEXT_MARKERS):
            return False

        if any(token in cls._RETRYABLE_429_ERROR_TOKENS for token in semantic_tokens):
            return True
        if any(marker in content for marker in cls._RETRYABLE_429_TEXT_MARKERS):
            return True
        # Unknown 429 defaults to WAIT+retry.
        return True

    @staticmethod
    def _enforce_role_alternation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge consecutive same-role messages and drop trailing assistant messages.

        规整消息序列以满足部分 provider 的协议约束：
        - OpenAI 兼容/Azure/vLLM/Ollama 等不接受最后一条为 assistant（不支持 prefill），
          也不接受连续两条同角色（user/assistant）消息。
        - 处理：合并连续同角色 user/assistant 的文本；带 tool_calls 的 assistant
          特殊处理避免覆盖；丢弃尾部多余 assistant；若丢弃后只剩 system 消息则把
          最后一条 assistant 转成 user 以保证请求合法。
        - 兜底：若首条非 system 消息是裸 assistant（上游截断所致），插入一条合成
          user 消息，避免 GLM 等 provider 报错（如 1214）。
        """
        if not messages:
            return messages

        merged: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if (
                merged
                and role != "system"
                and role not in ("tool",)
                and merged[-1].get("role") == role
                and role in ("user", "assistant")
            ):
                prev = merged[-1]
                if role == "assistant":
                    prev_has_tools = bool(prev.get("tool_calls"))
                    curr_has_tools = bool(msg.get("tool_calls"))
                    if curr_has_tools:
                        merged[-1] = dict(msg)
                        continue
                    if prev_has_tools:
                        continue
                prev_content = prev.get("content") or ""
                curr_content = msg.get("content") or ""
                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    prev["content"] = (prev_content + "\n\n" + curr_content).strip()
                else:
                    merged[-1] = dict(msg)
            else:
                merged.append(dict(msg))

        last_popped = None
        while merged and merged[-1].get("role") == "assistant":
            last_popped = merged.pop()

        # If removing trailing assistant messages left only system messages,
        # the request would be invalid for most providers (e.g. Zhipu/GLM
        # error 1214).  Recover by converting the last popped assistant
        # message to a user message so the LLM can still see the content.
        if (
            merged
            and last_popped is not None
            and not any(m.get("role") in ("user", "tool") for m in merged)
        ):
            recovered = dict(last_popped)
            recovered["role"] = "user"
            merged.append(recovered)

        # Safety net: ensure the first non-system message is not a bare
        # ``assistant`` message.  Providers like GLM reject system→assistant
        # with error 1214.  This can happen when upstream truncation (e.g.
        # _snip_history) drops the only user message.  Insert a synthetic
        # user message to keep the sequence valid.
        for i, msg in enumerate(merged):
            if msg.get("role") != "system":
                if msg.get("role") == "assistant" and not msg.get("tool_calls"):
                    merged.insert(i, {"role": "user", "content": _SYNTHETIC_USER_CONTENT})
                break

        return merged

    @staticmethod
    def _strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Replace image_url blocks with text placeholder. Returns None if no images found.

        把消息中的 image_url 块替换为文本占位符，返回新列表；无图片则返回 None。
        用于非瞬时错误时降级重试：某些错误由图片触发，去掉图片后重试可能成功。
        """
        found = False
        result = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_content = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "image_url":
                        placeholder = (
                            "[Image not delivered to model — "
                            "do not describe or reference it]"
                        )
                        new_content.append({"type": "text", "text": placeholder})
                        found = True
                    else:
                        new_content.append(b)
                result.append({**msg, "content": new_content})
            else:
                result.append(msg)
        return result if found else None

    @staticmethod
    def _strip_image_content_inplace(messages: list[dict[str, Any]]) -> bool:
        """Replace image_url blocks with text placeholder *in-place*.

        Mutates the content lists of the original message dicts so that
        callers holding references to those dicts also see the stripped
        version.
        """
        found = False
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for i, b in enumerate(content):
                    if isinstance(b, dict) and b.get("type") == "image_url":
                        placeholder = (
                            "[Image not delivered to model — "
                            "do not describe or reference it]"
                        )
                        content[i] = {"type": "text", "text": placeholder}
                        found = True
        return found

    async def _safe_chat(self, **kwargs: Any) -> LLMResponse:
        """Call chat() and convert unexpected exceptions to error responses."""
        try:
            return await self.chat(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return LLMResponse(content=f"Error calling LLM: {exc}", finish_reason="error")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Stream a chat completion, calling *on_content_delta* for each text chunk.

        *on_thinking_delta* is reserved for providers that expose incremental
        thinking/reasoning on the wire; the default fallback invokes neither
        callback for native deltas (only the optional single *on_content_delta*
        after :meth:`chat`).

        Returns the same ``LLMResponse`` as :meth:`chat`.  The default
        implementation falls back to a non-streaming call and delivers the
        full content as a single delta.  Providers that support native
        streaming should override this method.
        """
        _ = on_thinking_delta, on_tool_call_delta
        response = await self.chat(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
        )
        if on_content_delta and response.content:
            await on_content_delta(response.content)
        return response

    async def _safe_chat_stream(self, **kwargs: Any) -> LLMResponse:
        """Call chat_stream() and convert unexpected exceptions to error responses."""
        try:
            return await self.chat_stream(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return LLMResponse(content=f"Error calling LLM: {exc}", finish_reason="error")

    async def chat_stream_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: object = _SENTINEL,
        temperature: object = _SENTINEL,
        reasoning_effort: object = _SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_thinking_delta: Callable[[str], Awaitable[None]] | None = None,
        on_tool_call_delta: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_stream_recover: Callable[[], Awaitable[None]] | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Call chat_stream() with retry on transient provider failures."""
        if max_tokens is self._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        has_streamed_content = False

        async def _tracking_delta(text: str) -> None:
            nonlocal has_streamed_content
            if text:
                has_streamed_content = True
            if on_content_delta:
                await on_content_delta(text)

        async def _recover_stream() -> None:
            nonlocal has_streamed_content
            if on_stream_recover:
                await on_stream_recover()
            has_streamed_content = False

        kw: dict[str, Any] = dict(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
            on_content_delta=_tracking_delta if on_content_delta is not None else None,
            on_thinking_delta=on_thinking_delta,
            on_tool_call_delta=on_tool_call_delta,
        )
        if on_stream_recover and getattr(self, "supports_stream_recover_callback", False):
            kw["on_stream_recover"] = _recover_stream
        return await self._run_with_retry(
            self._safe_chat_stream,
            kw,
            messages,
            retry_mode=retry_mode,
            on_retry_wait=on_retry_wait,
            should_retry_guard=lambda: not has_streamed_content,
            on_stream_recover=_recover_stream if on_stream_recover else None,
        )

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: object = _SENTINEL,
        temperature: object = _SENTINEL,
        reasoning_effort: object = _SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Call chat() with retry on transient provider failures.

        Parameters default to ``self.generation`` when not explicitly passed,
        so callers no longer need to thread temperature / max_tokens /
        reasoning_effort through every layer. Explicit ``None`` is also
        normalized to the provider's generation defaults so that downstream
        ``_build_kwargs`` never sees ``None`` for ``max_tokens`` / ``temperature``
        (which would crash ``max(1, max_tokens)``).
        """
        if max_tokens is self._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        kw: dict[str, Any] = dict(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
        )
        return await self._run_with_retry(
            self._safe_chat,
            kw,
            messages,
            retry_mode=retry_mode,
            on_retry_wait=on_retry_wait,
        )

    @classmethod
    def _extract_retry_after(cls, content: str | None) -> float | None:
        text = (content or "").lower()
        patterns = (
            r"retry after\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)?",
            r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)",
            r"wait\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)\s*before retry",
            r"retry[_-]?after[\"'\s:=]+(\d+(?:\.\d+)?)",
        )
        for idx, pattern in enumerate(patterns):
            match = re.search(pattern, text)
            if not match:
                continue
            value = float(match.group(1))
            unit = match.group(2) if idx < 3 else "s"
            return cls._to_retry_seconds(value, unit)
        return None

    @classmethod
    def _to_retry_seconds(cls, value: float, unit: str | None = None) -> float:
        normalized_unit = (unit or "s").lower()
        if normalized_unit in {"ms", "milliseconds"}:
            return max(0.1, value / 1000.0)
        if normalized_unit in {"m", "min", "minutes"}:
            return max(0.1, value * 60.0)
        return max(0.1, value)

    @classmethod
    def _extract_retry_after_from_headers(cls, headers: Any) -> float | None:
        if not headers:
            return None

        def _header_value(name: str) -> Any:
            if hasattr(headers, "get"):
                value = headers.get(name) or headers.get(name.title())
                if value is not None:
                    return value
            if isinstance(headers, dict):
                for key, value in headers.items():
                    if isinstance(key, str) and key.lower() == name.lower():
                        return value
            return None

        with suppress(TypeError, ValueError):
            retry_ms = _header_value("retry-after-ms")
            if retry_ms is not None:
                value = float(retry_ms) / 1000.0
                if value > 0:
                    return value

        retry_after = _header_value("retry-after")
        if retry_after is None:
            return None
        retry_after_text = str(retry_after).strip()
        if not retry_after_text:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", retry_after_text):
            return cls._to_retry_seconds(float(retry_after_text), "s")
        try:
            retry_at = parsedate_to_datetime(retry_after_text)
        except Exception:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        remaining = (retry_at - datetime.now(retry_at.tzinfo)).total_seconds()
        return max(0.1, remaining)

    @classmethod
    def _extract_retry_after_from_response(cls, response: LLMResponse) -> float | None:
        if response.error_retry_after_s is not None and response.error_retry_after_s > 0:
            return response.error_retry_after_s
        if response.retry_after is not None and response.retry_after > 0:
            return response.retry_after
        return cls._extract_retry_after(response.content)

    async def _sleep_with_heartbeat(
        self,
        delay: float,
        *,
        attempt: int,
        persistent: bool,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        remaining = max(0.0, delay)
        while remaining > 0:
            if on_retry_wait:
                kind = "persistent retry" if persistent else "retry"
                await on_retry_wait(
                    f"Model request failed, {kind} in {max(1, int(round(remaining)))}s "
                    f"(attempt {attempt})."
                )
            chunk = min(remaining, self._RETRY_HEARTBEAT_CHUNK)
            await asyncio.sleep(chunk)
            remaining -= chunk

    async def _run_with_retry(
        self,
        call: Callable[..., Awaitable[LLMResponse]],
        kw: dict[str, Any],
        original_messages: list[dict[str, Any]],
        *,
        retry_mode: str,
        on_retry_wait: Callable[[str], Awaitable[None]] | None,
        should_retry_guard: Callable[[], bool] | None = None,
        on_stream_recover: Callable[[], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        attempt = 0
        delays = list(self._CHAT_RETRY_DELAYS)
        persistent = retry_mode == "persistent"
        last_response: LLMResponse | None = None
        last_error_key: str | None = None
        identical_error_count = 0
        while True:
            attempt += 1
            response = await call(**kw)
            if response.finish_reason != "error":
                return response
            last_response = response
            if should_retry_guard is not None and not should_retry_guard():
                is_timeout = (response.error_kind or "").lower() == "timeout"
                if is_timeout:
                    if on_stream_recover:
                        logger.warning(
                            "LLM stream stalled after content was emitted; "
                            "starting a new stream segment and retrying"
                        )
                        await on_stream_recover()
                    else:
                        logger.warning(
                            "LLM stream stalled after content was emitted; "
                            "suppressing delta callbacks and retrying"
                        )
                        kw.setdefault("on_content_delta", None)
                        kw["on_content_delta"] = None
                        kw["on_thinking_delta"] = None
                        kw["on_tool_call_delta"] = None
                        should_retry_guard = None
                else:
                    logger.warning(
                        "LLM stream failed after content was emitted; skipping retry"
                    )
                    return response
            error_key = ((response.content or "").strip().lower() or None)
            if error_key and error_key == last_error_key:
                identical_error_count += 1
            else:
                last_error_key = error_key
                identical_error_count = 1 if error_key else 0

            if not self._is_transient_response(response):
                stripped = self._strip_image_content(original_messages)
                if stripped is not None and stripped != kw["messages"]:
                    logger.warning(
                        "Non-transient LLM error with image content, retrying without images"
                    )
                    retry_kw = dict(kw)
                    retry_kw["messages"] = stripped
                    result = await call(**retry_kw)
                    # Permanently strip images from the original messages so
                    # subsequent iterations do not repeat the error-retry cycle.
                    if result.finish_reason != "error":
                        self._strip_image_content_inplace(original_messages)
                    return result
                return response

            if persistent and identical_error_count >= self._PERSISTENT_IDENTICAL_ERROR_LIMIT:
                logger.warning(
                    "Stopping persistent retry after {} identical transient errors: {}",
                    identical_error_count,
                    (response.content or "")[:120].lower(),
                )
                if on_retry_wait:
                    await on_retry_wait(
                        f"Persistent retry stopped after {identical_error_count} identical errors."
                    )
                return response

            if not persistent and attempt > len(delays):
                logger.warning(
                    "LLM request failed after {} retries, giving up: {}",
                    attempt,
                    (response.content or "")[:120].lower(),
                )
                if on_retry_wait:
                    await on_retry_wait(
                        f"Model request failed after {attempt} retries, giving up."
                    )
                break

            base_delay = delays[min(attempt - 1, len(delays) - 1)]
            delay = self._extract_retry_after_from_response(response) or base_delay
            if persistent:
                delay = min(delay, self._PERSISTENT_MAX_DELAY)

            logger.warning(
                "LLM transient error (attempt {}{}), retrying in {}s: {}",
                attempt,
                "+" if persistent and attempt > len(delays) else f"/{len(delays)}",
                int(round(delay)),
                (response.content or "")[:120].lower(),
            )
            await self._sleep_with_heartbeat(
                delay,
                attempt=attempt,
                persistent=persistent,
                on_retry_wait=on_retry_wait,
            )

        return last_response if last_response is not None else await call(**kw)

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass
