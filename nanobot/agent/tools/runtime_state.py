"""RuntimeState protocol: agent loop state exposed to MyTool.

RuntimeState 协议：MyTool（自我修改/运行时状态工具）所需的最小 agent loop 状态契约。
实际由 AgentLoop 实现。MyTool 还会通过 getattr/setattr 动态访问任意属性
（用于点路径检查与修改），这些路径在运行时校验，而非由本协议约束。
"""

from typing import Any, Protocol


class RuntimeState(Protocol):
    """Minimum contract that MyTool requires from its runtime state provider.

    MyTool 要求其运行时状态提供者满足的最小契约。
    实际上始终由 ``AgentLoop`` 满足。MyTool 还会动态访问任意属性
    （通过 ``getattr`` / ``setattr``）做点路径检查与修改；这些路径在运行时校验，
    而非由本协议约束。
    """

    @property
    def model(self) -> str: ...  # 当前模型名

    @property
    def max_iterations(self) -> int: ...

    @property
    def current_iteration(self) -> int: ...

    @property
    def tool_names(self) -> list[str]: ...

    @property
    def workspace(self) -> str: ...

    @property
    def provider_retry_mode(self) -> str: ...

    @property
    def max_tool_result_chars(self) -> int: ...

    @property
    def context_window_tokens(self) -> int: ...

    @property
    def web_config(self) -> Any: ...

    @property
    def exec_config(self) -> Any: ...

    @property
    def workspace_sandbox(self) -> Any: ...

    @property
    def subagents(self) -> Any: ...

    @property
    def _runtime_vars(self) -> dict[str, Any]: ...

    @property
    def _last_usage(self) -> Any: ...

    def _sync_subagent_runtime_limits(self) -> None: ...

    @property
    def model_preset(self) -> str | None: ...

    _active_preset: str | None
