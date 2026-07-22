# 故障排查

使用本页面来定位故障所在。从能证明最多问题的最小范围开始：先本地 CLI，再网关，最后 WebUI 或聊天应用。

## 快速诊断顺序

按顺序运行以下命令：

```bash
nanobot --version
nanobot status
nanobot agent -m "Hello!"
```

然后，仅当 CLI 可正常工作时：

```bash
nanobot gateway
```

这会将故障划分到不同层级：

| 层级 | 所证明的内容 |
|---|---|
| `nanobot --version` | 安装与 shell 命令发现 |
| `nanobot status` | 配置路径、工作区路径、活动模型以及 provider 摘要 |
| `nanobot agent -m "Hello!"` | 配置加载、provider/模型访问、工作区写入以及 agent 循环 |
| `nanobot gateway` | 通道启动、cron 系统任务、心跳、WebUI/WebSocket 以及健康检查端点 |

如果 `nanobot agent -m "Hello!"` 失败，请先修复它，再去调试 WebUI、Telegram、Discord、Docker、systemd 或任何聊天应用。

## 如何阅读 `nanobot status`

`nanobot status` 不会调用模型。它只检查 nanobot 是否能找到默认配置、默认工作区、活动模型或预设，以及 provider 设置摘要。

其输出大致如下：

```text
nanobot Status

Config: /path/to/config.json ✓
Workspace: /path/to/workspace ✓
Model: provider/model-name (preset: primary)
Provider A: not set
Provider B: ✓
Local Provider: ✓ http://localhost:11434/v1
OAuth Provider: ✓ (OAuth)
```

按如下方式阅读：

| 行 | 良好迹象 | 看起来不对时该怎么做 |
|---|---|---|
| `Config` | 指向你打算使用的配置文件并显示 `✓`。 | 运行 `nanobot onboard`，或在测试非默认实例时向 `nanobot agent`、`gateway` 或 `serve` 传入 `--config`。 |
| `Workspace` | 指向你打算使用的工作区并显示 `✓`。 | 运行 `nanobot onboard`、创建该文件夹、修复权限，或在支持 `--workspace` 的命令上传入该参数。 |
| `Model` | 显示你期望的活动模型或预设名称。 | 将 `agents.defaults.modelPreset` 设置为预期的预设，或在会话期间通过 `/model` 检查你已更改的模型。 |
| Provider 各行 | 活动预设所使用的 provider 显示 `✓`、OAuth 标记或本地 URL。 | 先只配置当前活动的 provider。未使用的 provider 显示 `not set` 是正常的。 |

如果 `nanobot status` 看起来正常，但 `nanobot agent -m "Hello!"` 失败，那么安装和配置路径很可能没有问题。请继续查看 [Provider 与模型问题](#provider-and-model-problems)。

## 安装问题

在安装检查和模块回退中使用相同的 Python 命令。在 macOS/Linux 上可能是 `python3`；在 Windows 上可能是 `python` 或 `py`。

| 症状 | 检查项 |
|---|---|
| `python: command not found` | 在 macOS/Linux 上尝试 `python3 --version`，或在 Windows 上尝试 `py --version`。然后将文档命令中的 `python` 替换为可用的命令。 |
| `curl: command not found` | macOS/Linux 的一键安装器无法下载脚本。请安装 curl，或使用手动隔离安装，例如 `uv tool install nanobot-ai` 或 `pipx install nanobot-ai`。 |
| `irm` 未被识别 | PowerShell 无法运行下载辅助程序。请使用手动安装：`uv tool install nanobot-ai`、`pipx install nanobot-ai`，或在你自己控制的环境内运行 `py -m pip install nanobot-ai`。 |
| 无法下载 `raw.githubusercontent.com` | 你的网络、代理或防火墙阻止了安装器脚本下载。请从 PyPI 手动安装，或配置代理后重新运行命令。 |
| `nanobot: command not found` | 使用模块形式，例如 `python -m nanobot ...`、`python3 -m nanobot ...` 或 `py -m nanobot ...`。用相同的 Python 命令重新安装，或将该 Python 的 scripts 目录加入 `PATH`。 |
| `No module named nanobot` | 你运行的 Python 与安装时使用的不是同一个。请运行 `python -m pip show nanobot-ai`、`python3 -m pip show nanobot-ai` 或 `py -m pip show nanobot-ai`，要与安装 nanobot 时使用的命令一致。 |
| `pip is not available` | 当安装器使用虚拟环境时，会尝试 `python -m ensurepip --upgrade`。如果失败，请为该 Python 安装 pip，或使用自带 pip 的 Python 安装器/发行版。 |
| `externally-managed-environment` | 你的系统 Python 阻止了全局 pip 安装。请使用一键安装器、`uv tool install nanobot-ai`、`pipx install nanobot-ai`，或创建一个虚拟环境；不要为 nanobot 添加 `--break-system-packages`。 |
| 安装器选错了 Python | 在运行安装器之前设置 `PYTHON`，例如 `curl -fsSL https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.sh | PYTHON=python3 sh`，或在运行 PowerShell 命令之前设置 `$env:PYTHON="py"`。 |
| 可编辑源码安装未更新 | 从仓库根目录，使用开发所用的 Python 命令再次运行 `python -m pip install -e .`，然后检查 `python -m nanobot --version` 或 `nanobot --version`。 |
| 缺少 WebUI 构建工具 | 它们仅在 WebUI 开发时需要。打包安装已包含 WebUI 产物。 |

## 配置问题

默认配置路径：

```text
~/.nanobot/config.json
```

默认工作区路径：

```text
~/.nanobot/workspace/
```

`nanobot status` 读取默认配置。在调试多个实例时，请在支持路径参数的命令上显式指定路径：

```bash
nanobot agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
nanobot gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
```

常见配置错误：

| 症状 | 检查项 |
|---|---|
| JSON 解析错误 | 检查逗号、花括号和引号。多数文档示例是需要合并的部分片段。 |
| 未知或缺失的 provider | 使用 provider 注册表名称，例如 `openrouter`、`anthropic`、`openai`、`ollama`、`vllm`、`lm_studio`，或在 `providers` 下定义一个自定义 OpenAI 兼容 provider 键，并在活动预设中引用该确切的键。 |
| snake_case 与 camelCase 混淆 | 两者都被接受，但文档使用 camelCase，因为 nanobot 写入配置时使用 `apiKey`、`modelPresets`、`intervalS` 等别名。 |
| 环境变量错误 | `${VAR_NAME}` 引用在启动时解析。请在运行 nanobot 之前设置该变量。 |
| 修改了配置但行为未变 | 重启 `nanobot gateway`；长期运行的进程在启动时读取配置。 |

要刷新缺失的默认值而不覆盖已有设置，请运行：

```bash
nanobot onboard
```

当提示是否覆盖配置时，请选择保留当前值并合并缺失默认值的选项。

## Provider 与模型问题

首先在 CLI 中验证该 provider：

```bash
nanobot agent -m "Hello!"
```

然后将你的配置与 [`providers.md`](./providers.md) 进行对比。

如果你需要一份已知可用的代码片段而非诊断，请使用 [`provider-cookbook.md`](./provider-cookbook.md)。

| 症状 | 可能原因 |
|---|---|
| 401、未授权、API 密钥无效 | 密钥缺失、过期、粘贴时带有空白字符，或放在了错误的 provider 键下。 |
| 模型未找到 | 该模型 ID 属于另一个 provider 或网关。 |
| 无法推断 provider | 在活动预设中固定 `modelPresets.<name>.provider`，而不是使用 `"auto"`。对于旧式直接配置，请固定 `agents.defaults.provider`。 |
| 本地模型连接被拒绝 | Ollama、vLLM、LM Studio 或其他本地服务器未运行，或 `apiBase` 指向了错误的端口。 |
| Bedrock 校验错误 | 检查 AWS 区域、凭证、模型访问权限、模型 ID，以及该模型是否支持 Converse。 |
| OAuth provider 失败 | 运行 `nanobot provider login openai-codex` 或 `nanobot provider login github-copilot`，然后显式选择该 provider。 |

## Langfuse 问题

Langfuse 追踪是可选的，并由环境变量控制。

| 症状 | 检查项 |
|---|---|
| `LANGFUSE_SECRET_KEY is set but langfuse is not installed` | 在运行 nanobot 的同一个 Python 环境中安装 `langfuse`，然后重启该进程。 |
| 没有追踪出现 | 在启动 nanobot 之前设置 `LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY` 和 `LANGFUSE_BASE_URL`。 |
| Langfuse 项目或区域错误 | 检查密钥对与 `LANGFUSE_BASE_URL` 是否来自同一个 Langfuse 项目/区域。 |
| 仅部分 provider 有追踪 | Langfuse 追踪适用于 OpenAI 兼容的 provider 调用；原生 provider 可能不使用该客户端路径。 |

设置命令请参见 [`configuration.md#langfuse-observability`](./configuration.md#langfuse-observability)。

## 网关问题

WebUI、聊天应用、心跳、Dream 以及长期运行的通道连接都需要 `nanobot gateway`。

默认端口：

| 对外服务 | 默认值 |
|---|---|
| 网关健康检查端点 | `http://127.0.0.1:18790/health` |
| WebUI/WebSocket 通道 | `http://127.0.0.1:8765` |
| OpenAI 兼容 API（`nanobot serve`） | `http://127.0.0.1:8900` |

常用网关检查：

```bash
nanobot gateway --verbose
```

| 症状 | 检查项 |
|---|---|
| 端口已被占用 | 更改相关命令的 `gateway.port`、`channels.websocket.port` 或 `--port` CLI 参数。 |
| WebUI 在 `18790` 上打开但无有用内容 | 请打开 `8765`；`18790` 是健康检查端点。 |
| 配置更改被忽略 | 重启网关。 |
| 心跳从未运行 | 保持网关运行，在 `<workspace>/HEARTBEAT.md` -> `## Active Tasks` 下添加任务，并确保 `gateway.heartbeat.enabled` 为 true。 |
| 切换工作区后 cron 任务消失 | cron 任务以工作区为作用域，存放在 `<workspace>/cron/jobs.json`；请检查你使用的是否是预期的工作区。 |

## WebUI 问题

打包的 WebUI 由 WebSocket 通道提供。

最小配置：

```json
{
  "channels": {
    "websocket": {
      "enabled": true
    }
  }
}
```

然后运行：

```bash
nanobot gateway
```

打开：

```text
http://127.0.0.1:8765
```

如果要从其他设备访问，请将 WebSocket 通道绑定到 `0.0.0.0`，并设置 `token` 或 `tokenIssueSecret`。WebSocket 通道在没有 token 或 token 签发密钥时会拒绝公开绑定。

局域网设置请参见 [`webui.md#lan-access`](./webui.md#lan-access)，前端开发请参见 [`../webui/README.md`](../webui/README.md)。

## 聊天应用问题

在调试聊天应用之前：

```bash
nanobot agent -m "Hello!"
nanobot channels status
nanobot gateway
```

然后检查：

| 症状 | 检查项 |
|---|---|
| Bot 从不回复 | 网关未运行、通道未启用，或 bot/应用 token 错误。 |
| 未知发送者被忽略 | 配置 `allowFrom`、配对（pairing）或通道特定的允许列表。 |
| Telegram 失败 | 确认 BotFather token 和 `allowFrom` 用户 ID。 |
| Discord 缺少回复 | 启用 Message Content intent，并以所需权限邀请 bot。 |
| WhatsApp 或 WeChat 登录过期 | 重新运行 `nanobot channels login whatsapp` 或 `nanobot channels login weixin`。 |
| 聊天应用可用但 WebUI 不可用 | provider 和网关很可能没问题；单独调试 WebSocket 通道。 |

通道特定设置请参见 [`chat-apps.md`](./chat-apps.md)。

## 工具与工作区问题

| 症状 | 检查项 |
|---|---|
| 文件访问被拒绝 | 检查 `tools.restrictToWorkspace` 以及目标路径是否位于活动工作区内。 |
| Shell 命令在 Docker 中失败 | 沙箱设置可能需要 Linux 能力；请参见 [`deployment.md`](./deployment.md)。 |
| Web 抓取被阻止 | SSRF 保护会阻止不安全的目标；仅对可信的私有网络使用 `tools.ssrfWhitelist`。 |
| MCP 工具缺失 | 检查 `tools.mcpServers`、服务器启动命令、环境变量以及工具允许列表。 |
| 生成的制品缺失 | 检查活动工作区和通道媒体目录。 |

## 记忆与会话问题

| 症状 | 检查项 |
|---|---|
| 会话上下文看起来不对 | 确认活动工作区与会话。WebUI 会话和聊天应用线程可能使用不同的会话。 |
| 记忆未立即更新 | Dream 整合是周期性的；最近的轮次仍保留在会话历史中。 |
| 移动配置后出现旧会话 | 会话文件存放在 `<workspace>/sessions/` 下；请核实工作区路径。 |
| 你希望跨设备共享一个会话 | 有意识地设置 `agents.defaults.unifiedSession`；否则请保持会话相互独立。 |

## 收集有用的证据

在提交 issue 或寻求帮助时，请包含：

- 安装方式以及 `nanobot --version`；
- 操作系统与 Python 版本；
- 你运行的命令；
- 相关的 `nanobot status` 输出；
- 脱敏后的配置片段，尤其是 provider、模型、通道和工具设置；
- 来自 `nanobot gateway --verbose` 的网关日志；
- `nanobot agent -m "Hello!"` 是否可用。

请勿将真实的 API 密钥、bot token、OAuth token 或私人聊天 ID 粘贴到公开的 issue 中。

如果你发现文档错误、过时命令或令人困惑的步骤，请提交 issue：<https://github.com/HKUDS/nanobot/issues>。
