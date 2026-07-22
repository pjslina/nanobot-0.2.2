"""Runtime path helpers derived from the active config context.

运行时路径助手模块：基于当前配置上下文派生各类运行时目录路径。
所有路径都以"配置文件所在目录"（即 nanobot 实例的数据目录）为根，
从而支持多实例共存（每个实例有自己的配置文件与数据目录）。
"""

from __future__ import annotations

from pathlib import Path

from nanobot.utils.helpers import ensure_dir


def get_config_path() -> Path:
    """Get the configuration file path (lazy import to break circular dependency).

    获取配置文件路径（采用惰性导入以打破循环依赖）。

    Delegates to ``nanobot.config.loader.get_config_path`` at call time so
    that importing this module never triggers a circular import during startup.
    在调用时才委托给 ``nanobot.config.loader.get_config_path``，确保本模块的
    导入不会在启动阶段触发循环导入。
    """
    from nanobot.config.loader import get_config_path as _loader_get_config_path
    return _loader_get_config_path()


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory.

    返回当前实例级别的运行时数据目录（即配置文件的父目录，并确保已创建）。
    """
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir.

    返回实例数据目录下指定名称的子目录（不存在则创建）。
    """
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel.

    返回媒体文件目录；若指定 channel 则在该目录下按渠道再分一层，
    避免不同渠道的媒体文件相互覆盖。
    """
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron storage directory.

    返回定时任务（cron）的存储目录。
    """
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory.

    返回日志目录。
    """
    return get_runtime_subdir("logs")


def get_webui_dir() -> Path:
    """Return the directory for WebUI-only persisted display threads (JSON).

    返回 WebUI 专用的持久化展示会话目录（JSON 文件）。
    这些会话仅用于 WebUI 展示，不参与 agent 核心逻辑。
    """
    return get_runtime_subdir("webui")


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure the agent workspace path.

    解析并确保 agent 工作区目录存在；未指定时默认使用 ~/.nanobot/workspace。
    """
    path = Path(workspace).expanduser() if workspace else Path.home() / ".nanobot" / "workspace"
    return ensure_dir(path)


def is_default_workspace(workspace: str | Path | None) -> bool:
    """Return whether a workspace resolves to nanobot's default workspace path.

    判断给定工作区是否解析为 nanobot 的默认工作区路径。
    用于判断是否需要提示用户工作区为默认值。
    """
    current = Path(workspace).expanduser() if workspace is not None else Path.home() / ".nanobot" / "workspace"
    default = Path.home() / ".nanobot" / "workspace"
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    """Return the shared CLI history file path.

    返回共享的 CLI 历史记录文件路径。
    """
    return Path.home() / ".nanobot" / "history" / "cli_history"


def get_bridge_install_dir() -> Path:
    """Return the shared WhatsApp bridge installation directory.

    返回共享的 WhatsApp 桥接服务安装目录。
    """
    return Path.home() / ".nanobot" / "bridge"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global session directory used for migration fallback.

    返回旧版全局会话目录，用于迁移时的回退兜底。
    早期版本会话存储在全局目录，新版改为按实例隔离，此函数用于兼容旧数据。
    """
    return Path.home() / ".nanobot" / "sessions"
