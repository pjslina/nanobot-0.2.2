"""Tool registry for dynamic tool management.

工具注册表：管理所有已加载工具的定义与按名查找，并负责把工具调用解析、
类型转换、参数校验后交给对应工具执行。AgentRunner 通过本类驱动 LLM 的工具调用。
"""

import json
from typing import Any

from nanobot.agent.tools.base import Tool


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.

    工具注册表：维护工具名 -> Tool 实例的映射。注册表同时缓存"暴露给 LLM 的
    工具定义列表"（get_definitions），在 register/unregister 后失效重算，
    以保证多次构建 prompt 时顺序稳定、减少重复序列化开销。
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        # 暴露给 LLM 的工具 schema 列表缓存；注册/注销后置 None 触发重算
        self._cached_definitions: list[dict[str, Any]] | None = None

    def register(self, tool: Tool) -> None:
        """Register a tool. 注册单个工具并使缓存失效。"""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name. 按名注销并使缓存失效。"""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name. 按名精确查找工具实例。"""
        return self._tools.get(name)

    @staticmethod
    def _lookup_key(name: str) -> str:
        """Normalize names for suggestions only; never for execution.

        将名称归一化（去非字母数字、转小写），仅用于"你是不是想用 X"的提示，
        绝不用于实际执行——执行必须精确匹配工具名。
        """
        return "".join(ch.lower() for ch in name if ch.isalnum())

    def _suggest_name(self, name: str) -> str | None:
        # 当归一化后仅匹配到一个已注册工具时，返回该工具名作为拼写建议
        key = self._lookup_key(str(name or ""))
        if not key:
            return None
        matches = [
            registered
            for registered in self._tools
            if self._lookup_key(registered) == key
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """Extract a normalized tool name from either OpenAI or flat schemas.

        兼容两种 schema 结构抽取工具名：OpenAI 风格 {"function":{"name":...}}
        与扁平风格 {"name":...}，用于后续排序。
        """
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions with stable ordering for cache-friendly prompts.

        Built-in tools are sorted first as a stable prefix, then MCP tools are
        sorted and appended.  The result is cached until the next
        register/unregister call.

        生成暴露给 LLM 的工具定义列表并缓存。为保证 prompt 稳定（便于缓存与
        token 复用），先放按名排序的内置工具，再追加按名排序的 MCP 工具（以
        "mcp_" 前缀区分）。仅在注册/注销后重算。
        """
        if self._cached_definitions is not None:
            return self._cached_definitions

        definitions = [tool.to_schema() for tool in self._tools.values()]
        builtins: list[dict[str, Any]] = []
        mcp_tools: list[dict[str, Any]] = []
        for schema in definitions:
            name = self._schema_name(schema)
            # MCP 工具统一加 mcp_ 前缀，据此与内置工具分组排序
            if name.startswith("mcp_"):
                mcp_tools.append(schema)
            else:
                builtins.append(schema)

        builtins.sort(key=self._schema_name)
        mcp_tools.sort(key=self._schema_name)
        self._cached_definitions = builtins + mcp_tools
        return self._cached_definitions

    def prepare_call(
        self,
        name: str,
        params: Any,
    ) -> tuple[Tool | None, Any, str | None]:
        """Resolve, cast, and validate one tool call.

        工具调用预处理三步：1) 按名解析工具（找不到则给出拼写建议）；
        2) 对参数做类型强转与解包（LLM 常把 JSON 字符串/arguments 包装传错）；
        3) 按 schema 校验必填与类型。返回 (tool, 处理后参数, 错误信息)，
        错误信息非空时表示调用不应继续执行。
        """
        tool = self._tools.get(name)
        if not tool:
            suggestion = self._suggest_name(str(name))
            hint = f" Did you mean '{suggestion}'? Tool names must match exactly." if suggestion else ""
            return None, params, (
                f"Error: Tool '{name}' not found.{hint} Available: {', '.join(self.tool_names)}"
            )

        params = self._coerce_params(tool, params)
        if not isinstance(params, dict):
            return tool, params, (
                f"Error: Tool '{name}' parameters must be a JSON object, got "
                f"{type(params).__name__}. Use named parameters like "
                'tool_name(param1="value1", param2="value2") matching the tool schema.'
            )

        cast_params = tool.cast_params(params)
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            )
        return tool, cast_params, None

    @classmethod
    def _coerce_argument_value(cls, value: Any) -> Any:
        # 把字符串形式的 JSON 对象/数组解析回结构，空串/None 视作空对象，
        # 以兼容部分 provider 把对象参数当字符串传递的情况
        if value is None:
            return {}
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return {}

        if not stripped.startswith(("{", "[")):
            return value

        try:
            parsed = json.loads(stripped)
        except Exception:
            return value

        return parsed

    @classmethod
    def _coerce_params(cls, tool: Tool, params: Any) -> Any:
        params = cls._coerce_argument_value(params)
        return cls._unwrap_arguments_payload(tool, params)

    @classmethod
    def _unwrap_arguments_payload(cls, tool: Tool, params: Any) -> Any:
        # 处理 {"arguments": {...}} 这种把所有参数裹进 arguments 字段的误用：
        # 仅当唯一键是 arguments 且该工具 schema 本身没有 arguments 属性时才解包
        if not isinstance(params, dict) or set(params) != {"arguments"}:
            return params
        properties = (tool.parameters or {}).get("properties", {})
        if isinstance(properties, dict) and "arguments" in properties:
            return params
        return cls._coerce_argument_value(params.get("arguments"))

    async def execute(self, name: str, params: Any) -> Any:
        """Execute a tool by name with given parameters.

        执行工具的完整入口：prepare_call 解析+校验，通过则调用 tool.execute。
        任何错误或以 "Error" 开头的返回都会附加提示，引导 LLM 分析错误并换思路。
        """
        hint = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return error + hint

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + hint
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}" + hint

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
