"""Tool discovery and registration via package scanning.

工具的"自动发现与注册"。nanobot 不硬编码工具列表，而是：
1. 用 pkgutil 扫描 nanobot.agent.tools 包下所有模块，找出 Tool 的非抽象子类并实例化注册；
2. 通过 entry_points（group="nanobot.tools"）发现外部插件包提供的工具。
这样内置工具与第三方插件都能被自动加载，无需修改核心代码。
"""
from __future__ import annotations

import importlib
import pkgutil
from importlib.metadata import entry_points
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry

# 扫描时跳过的模块：这些是基础设施/基类模块，本身不提供具体工具。
_SKIP_MODULES = frozenset({
    "base", "schema", "registry", "context", "loader", "config",
    "file_state", "sandbox", "mcp", "__init__", "runtime_state",
})


class ToolLoader:
    def __init__(self, package: Any = None, *, test_classes: list[type[Tool]] | None = None):
        if package is None:
            import nanobot.agent.tools as _pkg
            package = _pkg
        self._package = package
        self._test_classes = test_classes  # 测试时可注入固定的工具类列表
        self._discovered: list[type[Tool]] | None = None
        self._plugins: dict[str, type[Tool]] | None = None

    def discover(self) -> list[type[Tool]]:
        # 扫描包内模块，发现所有可实例化的 Tool 子类。结果缓存，按类名排序保证稳定。
        if self._test_classes is not None:
            return list(self._test_classes)
        if self._discovered is not None:
            return self._discovered
        seen: set[int] = set()
        results: list[type[Tool]] = []
        for _importer, module_name, _ispkg in pkgutil.iter_modules(self._package.__path__):
            if module_name.startswith("_") or module_name in _SKIP_MODULES:
                continue
            try:
                module = importlib.import_module(f".{module_name}", self._package.__name__)
            except Exception:
                logger.exception("Failed to import tool module: %s", module_name)
                continue
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                # 收集条件：是 Tool 的非抽象子类、非下划线开头、未被 _plugin_discoverable 禁用、未重复。
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Tool)
                    and attr is not Tool
                    and not attr_name.startswith("_")
                    and not getattr(attr, "__abstractmethods__", None)
                    and getattr(attr, "_plugin_discoverable", True)
                    and id(attr) not in seen
                ):
                    seen.add(id(attr))
                    results.append(attr)
        results.sort(key=lambda cls: cls.__name__)
        self._discovered = results
        return results

    def _discover_plugins(self) -> dict[str, type[Tool]]:
        """Discover external tool plugins registered via entry_points.

        通过 entry_points（group="nanobot.tools"）发现外部插件包注册的工具。
        """
        if self._plugins is not None:
            return self._plugins
        plugins: dict[str, type[Tool]] = {}
        try:
            eps = entry_points(group="nanobot.tools")
        except Exception:
            return plugins
        for ep in eps:
            try:
                cls = ep.load()
                if (
                    isinstance(cls, type)
                    and issubclass(cls, Tool)
                    and not getattr(cls, "__abstractmethods__", None)
                    and getattr(cls, "_plugin_discoverable", True)
                ):
                    plugins[ep.name] = cls
            except Exception:
                logger.exception("Failed to load tool plugin: %s", ep.name)
        self._plugins = plugins
        return plugins

    def load(self, ctx: Any, registry: ToolRegistry, *, scope: str = "core") -> list[str]:
        # 加载并注册所有工具：先内置（discover），后插件（entry_points）。
        # 插件与内置工具同名时，插件被跳过（内置优先）。返回已注册的工具名列表。
        registered: list[str] = []
        builtin_names: set[str] = set()
        sources = [(self.discover(), False), (self._discover_plugins().values(), True)]
        for source, is_plugin_source in sources:
            for tool_cls in source:
                cls_label = tool_cls.__name__
                try:
                    if scope not in getattr(tool_cls, "_scopes", {"core"}):
                        continue  # 工具声明的作用域不含当前 scope，跳过
                    if not tool_cls.enabled(ctx):
                        continue  # 工具在当前配置下未启用，跳过
                    tool = tool_cls.create(ctx)
                    if registry.has(tool.name):
                        if is_plugin_source and tool.name in builtin_names:
                            # 插件与内置工具同名：跳过插件，内置优先。
                            logger.warning(
                                "Plugin %s skipped: conflicts with built-in tool %s",
                                cls_label, tool.name,
                            )
                            continue
                        logger.warning(
                            "Tool name collision: %s from %s overwrites existing",
                            tool.name, cls_label,
                        )
                    registry.register(tool)
                    registered.append(tool.name)
                    if not is_plugin_source:
                        builtin_names.add(tool.name)
                except Exception:
                    logger.exception("Failed to register tool: %s", cls_label)
        return registered
