"""Workspace-scoped source preview payloads for the WebUI."""

# 为 WebUI 提供工作区范围内的源文件预览能力。负责解析前端传入的路径、做工作区边界
# 安全校验、读取并截断文件内容、识别二进制文件，最终返回供前端语法高亮展示的负载。
# 所有路径解析都依赖 `resolve_allowed_path` 强制限定在当前会话工作区内，防止目录穿越。

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from nanobot.security.workspace_access import WorkspaceScope
from nanobot.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path

# 单个文件预览的最大字节数。384KB 兼顾常见源码文件体积与内存占用，超过则截断并在
# 返回负载中标记 truncated=True，前端据此提示内容已截断。
MAX_FILE_PREVIEW_BYTES = 384 * 1024


class WebUIFilePreviewError(ValueError):
    """Raised when a file cannot be previewed through the WebUI."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def file_preview_payload(
    raw_path: str | None,
    *,
    scope: WorkspaceScope,
    max_bytes: int = MAX_FILE_PREVIEW_BYTES,
) -> dict[str, Any]:
    """Return a text preview for a file inside the session workspace."""

    # 文件预览的主入口：输入为前端原始路径字符串，输出供 WebUI 展示的预览负载。
    # 关键行为：清洗路径 -> 工作区边界校验 -> 读取并截断 -> 二进制检测 -> 解码为文本。
    # 每个失败分支都映射到具体的 HTTP 状态码（400/403/404/415/500），便于前端区分处理。
    path = _clean_preview_path(raw_path)
    if not path:
        raise WebUIFilePreviewError(400, "missing path")
    if len(path) > 4096:
        raise WebUIFilePreviewError(400, "path is too long")

    try:
        # strict=True 要求解析后的真实路径必须位于工作区根目录之下，否则抛出
        # WorkspaceBoundaryError，是防止目录穿越攻击的核心防线。
        resolved = resolve_allowed_path(
            path,
            workspace=scope.project_path,
            allowed_root=scope.project_path,
            strict=True,
        )
    except FileNotFoundError as e:
        raise WebUIFilePreviewError(404, "file not found") from e
    except WorkspaceBoundaryError as e:
        raise WebUIFilePreviewError(403, "file is outside the current workspace") from e
    except OSError as e:
        raise WebUIFilePreviewError(400, "invalid path") from e

    if not resolved.is_file():
        raise WebUIFilePreviewError(404, "file not found")

    try:
        with open(resolved, "rb") as f:
            # 多读 1 字节用于判断是否需要截断：若返回长度超过 max_bytes 说明文件更大。
            raw = f.read(max_bytes + 1)
    except OSError as e:
        raise WebUIFilePreviewError(500, "failed to read file") from e

    # 仅检查前 4KB 是否含 NUL 字节作为二进制文件启发式判断：NUL 在文本中极少出现，
    # 但只看头部既够快又能避免对大文件做全量扫描。
    if b"\0" in raw[:4096]:
        raise WebUIFilePreviewError(415, "binary files cannot be previewed")

    truncated = len(raw) > max_bytes
    preview_bytes = raw[:max_bytes]
    try:
        content = preview_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # 非 UTF-8 文本用替换字符兜底，避免因编码问题直接拒绝预览。
        content = preview_bytes.decode("utf-8", errors="replace")

    display_path = _display_path(resolved, scope.project_path)
    return {
        "path": str(resolved),
        "display_path": display_path,
        "project_path": str(scope.project_path),
        "language": _language_for_path(resolved),
        "content": content,
        "size": resolved.stat().st_size,
        "truncated": truncated,
    }


def _clean_preview_path(raw_path: str | None) -> str:
    # 归一化前端传入的路径：处理 file:// URI、URL 解码、剥离查询串/锚点、并针对
    # 形如 "host:port" 或 "path:line:col" 的尾部后缀做清理，得到可交给文件系统使用的纯路径。
    if raw_path is None:
        return ""
    value = raw_path.strip()
    if not value:
        return ""
    if value.startswith("file://"):
        parsed = urlparse(value)
        value = unquote(parsed.path)
        # file:// URI 在 Windows 上常写作 file:///C:/...，解析后 path 以 "/C:/" 开头，
        # 这里去掉多余的 leading slash 还原为 "C:/" 形式，否则后续校验会失败。
        if re.match(r"^/[A-Za-z]:[\\/]", value):
            value = value[1:]
    else:
        value = unquote(value)
    # 去掉查询串和锚点，避免它们被当作路径的一部分。
    value = value.split("?", 1)[0].split("#", 1)[0].strip()
    if not re.match(r"^[A-Za-z]:[\\/]", value):
        # 非绝对 Windows 路径时，剥离形如 ":1234" 或 ":1234:5678" 的尾部端口/行列号后缀，
        # 这类后缀常见于编辑器跳转链接（如 foo.py:10:20），但与文件路径无关。
        value = re.sub(r":\d+(?::\d+)?$", "", value)
    return value


def _display_path(path: Path, root: Path) -> str:
    # 优先返回相对工作区根目录的 POSIX 风格路径用于前端展示；若不在根下则退回绝对路径。
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _language_for_path(path: Path) -> str:
    # 根据文件名/扩展名推断代码语言，供前端选择对应的语法高亮规则。
    # Dockerfile 无扩展名，需按文件名特判；其余按扩展名查表，未知扩展名回退为 "text"。
    name = path.name.lower()
    ext = path.suffix.lower().lstrip(".")
    if name == "dockerfile":
        return "dockerfile"
    return {
        "cjs": "javascript",
        "css": "css",
        "cts": "typescript",
        "html": "html",
        "js": "javascript",
        "json": "json",
        "jsonl": "json",
        "jsx": "jsx",
        "md": "markdown",
        "mdx": "markdown",
        "mjs": "javascript",
        "mts": "typescript",
        "py": "python",
        "pyi": "python",
        "scss": "scss",
        "sh": "bash",
        "toml": "toml",
        "ts": "typescript",
        "tsx": "tsx",
        "yaml": "yaml",
        "yml": "yaml",
    }.get(ext, ext or "text")
