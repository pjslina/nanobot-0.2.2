"""Compatibility exports for WebUI-attached MCP preset annotations.

兼容性再导出模块：将 MCP 工具层（``nanobot.agent.tools.mcp``）的
``runtime_lines`` 与 ``session_extra`` 两个函数暴露到 WebUI 命名空间下，
供 WebUI 侧引用 MCP 预设运行时注解，避免直接跨层依赖。
"""

from nanobot.agent.tools.mcp import runtime_lines, session_extra

__all__ = ["runtime_lines", "session_extra"]
