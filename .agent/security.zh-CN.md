# 安全边界

agent 拥有相当大的权限（文件系统、shell、web）。修改相关代码时，以下防护措施不得被绕过。

## 工作区限制

文件系统工具（`read_file`、`write_file`、`edit_file`、`list_dir`、`apply_patch`）通过工作区路径解析器（`agent/tools/filesystem.py` / `agent/tools/path_utils.py`）解析路径，该解析器在启用工作区限制时强制要求解析后的路径必须位于活动工作区内。在受限状态下，媒体上传目录始终是内部的额外只读根。

额外的文件系统根必须是特定于能力的。`extra_allowed_dirs` 是遗留的只读别名。只读根使用 `extra_read_allowed_dirs`，仅当有意允许具备写能力的工具修改额外目录时才使用 `extra_write_allowed_dirs`，当工具只能修改特定文件时使用精确的文件允许列表。

Shell 执行（`ExecTool`、`agent/tools/shell.py`）也遵守 `restrict_to_workspace` 作为应用层防护：如果启用且 `working_dir` 在工作区之外，命令在执行前被拒绝，命令文本也会被检查是否存在明显的工作区逃逸。这不是进程级隔离；如需进程级隔离请使用 exec sandbox 后端。

**规则**：任何新的路径处理逻辑必须通过工作区路径解析器，或执行带有显式读/写能力语义的等价包含检查。

## SSRF 防护

来自 agent 工具的所有出站 HTTP 请求都必须经过 `validate_url_target`（`security/network.py`）。默认情况下，它会阻止环回地址、RFC1918 私有地址、CGNAT 范围、链路本地范围和云元数据端点（包括 `169.254.169.254`）。

唯一的逃生通道是 `configure_ssrf_whitelist(cidrs)`，它在加载时从 `config.tools.ssrf_whitelist` 读取。

HTTP/SSE MCP 传输属于此边界的一部分：在探测或构造客户端之前验证已配置的 MCP URL，并在跟随重定向之前验证每个出站 HTTP 请求。本地/私有 HTTP MCP 端点只能通过显式的 SSRF 白名单允许。Stdio MCP 服务器不属于 HTTP SSRF 路径。

**规则**：不要在工具中添加直接的 `httpx.get` / `requests.get` 调用。应通过既有的 web fetch 工具路由，或复制 `validate_url_target` 检查。

## Shell 沙箱

`tools/sandbox.py` 提供可选的命令包装。目前唯一内置的后端是 `bwrap`（bubblewrap），面向容器化部署。在没有 `bwrap` 的 Windows 和裸机 Linux 上，命令在原生 shell 中运行，仅以工作区限制作为应用层防护。

**规则**：如果添加新的沙箱后端，请实现 `_wrap_<name>(command, workspace, cwd) -> str` 并在 `_BACKENDS` 中注册。
