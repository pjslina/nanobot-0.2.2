# CLI 参考

当你明确知道要运行什么并需要命令形式时使用本页。如需引导式首次运行，请从 [`quick-start.md`](./quick-start.md) 开始。

## 选择命令

| 目标 | 命令 | 说明 |
|---|---|---|
| 检查安装 | `nanobot --version` | 如果失败，尝试 `python -m nanobot --version` |
| 创建或刷新配置 | `nanobot onboard` | 创建 `~/.nanobot/config.json` 和 `~/.nanobot/workspace/` |
| 使用引导式设置 | `nanobot onboard --wizard` | 当你偏好提示而非手动编辑 JSON 时最合适 |
| 不调用模型检查配置 | `nanobot status` | 读取默认配置并汇总当前活动的 model/provider |
| 发送一条测试消息 | `nanobot agent -m "Hello!"` | 首次验证安装、配置、provider、模型和工作区均正常 |
| 在终端中聊天 | `nanobot agent` | 交互式本地聊天；用 `exit`、`/exit`、`:q` 或 `Ctrl+D` 退出 |
| 使用 WebUI 或聊天应用 | `nanobot gateway` | 保持此终端运行，或使用 `nanobot gateway --background` |
| 提供 OpenAI 兼容的 API | `nanobot serve` | 启动 `/v1/chat/completions`、`/v1/models` 和 `/health` |
| 检查聊天频道设置 | `nanobot channels status` | 在启动 `nanobot gateway` 之前有用 |
| 登录二维码/OAuth 式频道 | `nanobot channels login <channel>` | 由 WhatsApp 和 WeChat 等频道使用 |
| 登录 OAuth 模型 provider | `nanobot provider login <provider>` | 由 OpenAI Codex 和 GitHub Copilot 等 OAuth provider 使用 |

## 全局

```bash
nanobot --help
nanobot --version
python -m nanobot --help
python -m nanobot --version
```

当包已安装但 `nanobot` 脚本不在 `PATH` 上时，`python -m nanobot ...` 很有用。

## 常用模式

大多数日常命令使用默认配置和工作区。高级或多实例运行通常会显式传入两个路径：

```bash
nanobot agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
nanobot gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
nanobot serve --config ./bot-a/config.json --workspace ./bot-a/workspace
```

当你需要启动或运行时日志时，在长期运行的进程上使用 `--verbose`：

```bash
nanobot gateway --verbose
nanobot serve --verbose
```

长期运行的命令会持续工作直到你停止它们。在该终端中按 `Ctrl+C`
可停止前台运行的 `nanobot gateway` 或 `nanobot serve`。如果你使用
`--background` 启动了 gateway，请使用 `nanobot gateway stop`。

## 设置

| 命令 | 说明 |
|---|---|
| `nanobot onboard` | 初始化或刷新默认配置和工作区 |
| `nanobot onboard --wizard` | 使用交互式设置向导 |
| `nanobot onboard --config <path> --workspace <path>` | 初始化或刷新特定实例 |

默认路径：

| 路径 | 默认值 |
|---|---|
| 配置 | `~/.nanobot/config.json` |
| 工作区 | `~/.nanobot/workspace/` |

## Agent CLI

| 命令 | 说明 |
|---|---|
| `nanobot agent -m "Hello!"` | 发送一条消息并退出 |
| `nanobot agent` | 启动交互式终端聊天 |
| `nanobot agent --session <id>` | 使用特定的 session key |
| `nanobot agent --workspace <path>` | 覆盖工作区 |
| `nanobot agent --config <path>` | 使用特定的配置文件 |
| `nanobot agent --no-markdown` | 输出纯文本而非 Rich 渲染的 Markdown |
| `nanobot agent --logs` | 聊天时显示运行时日志 |

交互模式用 `exit`、`quit`、`/exit`、`/quit`、`:q` 或 `Ctrl+D` 退出。

## Gateway

`nanobot gateway` 启动已启用的聊天频道、配置后的 WebUI/WebSocket、基于 cron 的系统作业、Dream、heartbeat 和健康检查端点。默认情况下它在前台运行，这保持了现有脚本和终端工作流不变。当你想要一个可以从 CLI 管理的本地 macOS、Linux 或 Windows 进程时，使用 `--background`。

| 命令 | 说明 |
|---|---|
| `nanobot gateway` | 使用配置默认值在前台启动 gateway |
| `nanobot gateway --verbose` | 显示详细的运行时输出 |
| `nanobot gateway --port <port>` | 覆盖健康检查端点的 `gateway.port` |
| `nanobot gateway --workspace <path>` | 覆盖工作区 |
| `nanobot gateway --config <path>` | 使用特定的配置文件 |
| `nanobot gateway --background` | 将 gateway 作为后台进程启动 |
| `nanobot gateway status` | 显示已记录的后台 gateway PID、状态文件和日志文件 |
| `nanobot gateway logs --no-follow` | 打印最近的后台 gateway 日志并退出 |
| `nanobot gateway logs` | 跟踪后台 gateway 日志 |
| `nanobot gateway restart` | 使用当前配置重启已记录的后台 gateway |
| `nanobot gateway stop` | 停止已记录的后台 gateway |
| `nanobot gateway install-service` | 安装 systemd user service 或 macOS LaunchAgent |
| `nanobot gateway install-service --dry-run` | 预览生成的服务文件和系统命令 |
| `nanobot gateway uninstall-service` | 移除已安装的系统服务 |

对于自定义实例，向管理命令传入相同的选择器标志：

```bash
nanobot gateway --background --config ./bot-a/config.json --workspace ./bot-a/workspace
nanobot gateway status --config ./bot-a/config.json --workspace ./bot-a/workspace
nanobot gateway stop --config ./bot-a/config.json --workspace ./bot-a/workspace
nanobot gateway install-service --config ./bot-a/config.json --workspace ./bot-a/workspace --name bot-a
```

`--background` 是一个轻量的分离进程。`install-service` 用于
登录/启动集成：Linux 使用 systemd user service；macOS 使用
LaunchAgent plist。系统服务在 OS 监管器下运行前台 gateway，
而不是嵌套另一个后台进程。

默认健康检查端点：

```text
http://127.0.0.1:18790/health
```

内置 WebUI 由 WebSocket 频道提供服务，通常在端口 `8765` 上，而非由 gateway 健康检查端点提供。

## OpenAI 兼容 API

| 命令 | 说明 |
|---|---|
| `nanobot serve` | 启动 `/v1/chat/completions`、`/v1/models` 和 `/health` |
| `nanobot serve --host <host>` | 覆盖 API 绑定主机 |
| `nanobot serve --port <port>` | 覆盖 API 端口 |
| `nanobot serve --timeout <seconds>` | 覆盖每请求超时 |
| `nanobot serve --verbose` | 显示运行时日志 |
| `nanobot serve --workspace <path>` | 覆盖工作区 |
| `nanobot serve --config <path>` | 使用特定的配置文件 |

默认 API 端点：

```text
http://127.0.0.1:8900
```

请求示例见 [`openai-api.md`](./openai-api.md)。

## Status

```bash
nanobot status
```

显示默认配置路径、工作区路径、当前活动的模型和 provider 汇总。此命令目前不接受 `--config`；在调试特定实例时，请在 `agent`、`gateway` 或 `serve` 上使用显式的 `--config` 和 `--workspace`。

## 频道

| 命令 | 说明 |
|---|---|
| `nanobot channels status` | 显示已配置的频道状态 |
| `nanobot channels status --config <path>` | 显示特定配置的频道状态 |
| `nanobot channels login <channel>` | 为支持的频道运行交互式登录 |
| `nanobot channels login <channel> --force` | 即使凭证已存在也重新认证 |
| `nanobot channels login <channel> --config <path>` | 使用特定的配置文件 |

示例：

```bash
nanobot channels login whatsapp
nanobot channels login weixin
nanobot channels status
```

频道特定设置见 [`chat-apps.md`](./chat-apps.md)。

## Provider OAuth

| 命令 | 说明 |
|---|---|
| `nanobot provider login openai-codex` | 认证 OpenAI Codex provider |
| `nanobot provider login github-copilot` | 认证 GitHub Copilot provider |
| `nanobot provider logout openai-codex` | 移除 OpenAI Codex OAuth 状态 |
| `nanobot provider logout github-copilot` | 移除 GitHub Copilot OAuth 状态 |

关于 OAuth provider 何时需要显式 provider/模型选择，见 [`providers.md`](./providers.md#oauth-providers)。

## 有用的首次检查

```bash
nanobot --version
nanobot status
nanobot agent -m "Hello!"
```

如果这些失败，在调试 WebUI、聊天应用、Docker、systemd 或 SDK 集成之前，请先查阅 [`troubleshooting.md`](./troubleshooting.md)。
