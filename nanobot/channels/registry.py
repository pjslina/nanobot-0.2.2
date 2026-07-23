"""Auto-discovery for built-in channel modules and external plugins.

渠道自动发现模块：通过 pkgutil 扫描内置渠道模块、通过 entry_points 加载
外部插件，并按需导入。核心目标是“按需导入”——只导入配置启用的渠道，
避免加载未启用渠道依赖的第三方 SDK，从而加快启动速度、降低内存占用。
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.channels.base import BaseChannel

_INTERNAL = frozenset({"base", "manager", "registry"})
# base/manager/registry 是基础设施模块而非具体渠道，扫描时需排除。


def discover_channel_names() -> list[str]:
    """Return all built-in channel module names by scanning the package (zero imports).

    通过 pkgutil.iter_modules 扫描 channels 包，返回所有内置渠道模块名。
    该过程零导入（不会触发任何渠道模块的第三方 SDK 加载），开销很小，因此
    可安全地用于先枚举候选名、再决定导入哪些。
    """
    import nanobot.channels as pkg

    return [
        name
        for _, name, ispkg in pkgutil.iter_modules(pkg.__path__)
        if name not in _INTERNAL and not ispkg
    ]


def load_channel_class(module_name: str) -> type[BaseChannel]:
    """Import *module_name* and return the first BaseChannel subclass found.

    导入指定渠道模块，并返回其中定义的第一个 BaseChannel 子类。约定每个
    渠道模块内有且仅有一个 BaseChannel 子类（即渠道实现类）。
    """
    from nanobot.channels.base import BaseChannel as _Base

    mod = importlib.import_module(f"nanobot.channels.{module_name}")
    for attr in dir(mod):
        obj = getattr(mod, attr)
        if isinstance(obj, type) and issubclass(obj, _Base) and obj is not _Base:
            return obj
    raise ImportError(f"No BaseChannel subclass in nanobot.channels.{module_name}")


def discover_plugins(enabled_names: set[str] | None = None) -> dict[str, type[BaseChannel]]:
    """Discover external channel plugins registered via entry_points.

    发现已通过 entry_points（组名 nanobot.channels）注册的外部渠道插件。
    传入 enabled_names 时仅保留其中的插件；加载失败的插件仅告警、不影响其它。
    """
    from importlib.metadata import entry_points

    plugins: dict[str, type[BaseChannel]] = {}
    for ep in entry_points(group="nanobot.channels"):
        if enabled_names is not None and ep.name not in enabled_names:
            continue
        try:
            cls = ep.load()
            plugins[ep.name] = cls
        except Exception as e:
            logger.warning("Failed to load channel plugin '{}': {}", ep.name, e)
    return plugins


def discover_enabled(
    enabled_names: set[str],
    *,
    _names: list[str] | None = None,
    _include_all_external: bool = False,
) -> dict[str, type[BaseChannel]]:
    """Return channels whose module names are in *enabled_names*.

    返回模块名位于 enabled_names 中的渠道类。先用 pkgutil.iter_modules 廉价
    列出名字，再仅导入匹配项，从而跳过未启用渠道依赖的重量级第三方 SDK。
    外部插件与内置渠道同名时，内置优先（插件被遮蔽并告警）。

    Uses cheap ``pkgutil.iter_modules`` to list names, then imports only
    those that match — skipping the heavy third-party SDK imports of
    unneeded channels.
    """
    names = _names if _names is not None else discover_channel_names()
    result: dict[str, type[BaseChannel]] = {}
    for modname in names:
        if modname not in enabled_names:
            continue
        try:
            result[modname] = load_channel_class(modname)
        except ImportError as e:
            logger.debug("Skipping built-in channel '{}': {}", modname, e)

    external = discover_plugins(None if _include_all_external else enabled_names)
    # 外部插件若与内置渠道同名则被遮蔽（内置优先），此处收集被遮蔽的插件名
    shadowed = set(external) & set(result)
    if shadowed:
        logger.warning("Plugin(s) shadowed by built-in channels (ignored): {}", shadowed)
    if _include_all_external:
        result.update({k: v for k, v in external.items() if k not in shadowed})
    else:
        result.update({k: v for k, v in external.items() if k not in shadowed and k in enabled_names})

    return result


def discover_all() -> dict[str, type[BaseChannel]]:
    """Return all channels: built-in (pkgutil) merged with external (entry_points).

    返回所有渠道：内置（pkgutil 扫描）与外部（entry_points）合并。内置渠道
    优先——外部插件无法遮蔽同名内置渠道。

    Built-in channels take priority — an external plugin cannot shadow a built-in name.
    """
    names = discover_channel_names()
    return discover_enabled(set(names), _names=names, _include_all_external=True)
