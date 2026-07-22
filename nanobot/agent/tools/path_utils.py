"""Shared path helpers for workspace-scoped tools.

工作区作用域工具共享的路径助手。
nanobot 的文件/shell 工具受"工作区边界"（restrict_to_workspace）约束：
agent 只能访问工作区目录（及显式允许的额外目录/文件，如媒体目录）内的路径，
防止越权访问工作区外的文件。本模块把路径解析与边界检查封装成统一接口。
"""

from pathlib import Path

from nanobot.config.paths import get_media_dir
from nanobot.security.workspace_policy import (
    is_path_within,
    resolve_allowed_path,
)


def is_under(path: Path, directory: Path) -> bool:
    """Return True when path resolves under directory. 路径解析后是否位于 directory 之下。"""
    return is_path_within(path, directory)


def resolve_workspace_path(
    path: str,
    workspace: Path | None = None,
    allowed_dir: Path | None = None,
    extra_allowed_dirs: list[Path] | None = None,
    extra_allowed_files: list[Path] | None = None,
    include_media_dir: bool = True,
) -> Path:
    """Resolve path against workspace and enforce allowed directory containment.

    相对工作区解析路径，并强制限制在允许的目录范围内。
    媒体目录默认纳入允许范围（工具可能需要读写上传的媒体文件）。
    若路径越出所有允许范围，resolve_allowed_path 会抛错。
    """
    media_roots = [get_media_dir()] if include_media_dir else []
    extra_roots = [*media_roots, *(extra_allowed_dirs or [])] if allowed_dir else None
    return resolve_allowed_path(
        path,
        workspace=workspace,
        allowed_root=allowed_dir,
        extra_allowed_roots=extra_roots,
        extra_allowed_files=extra_allowed_files,
    )
