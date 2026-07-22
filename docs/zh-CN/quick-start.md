# 安装与快速开始

本页面用于让一次本地 nanobot 回复跑通。之后你可以添加 WebUI、聊天应用、本地模型、网页搜索、MCP、部署或自定义插件。

如果你从未使用过终端或编辑过配置文件，请先阅读 [`start-without-technical-background.md`](./start-without-technical-background.md)。本页面假设你能熟练粘贴命令并编辑 JSON 片段。

## 开始之前

你需要：

- Python 3.11 或更高版本。
- 一个你可以调用的 LLM 提供商（LLM provider）、公司端点、订阅端点或本地模型服务器。下文示例使用通用的 OpenAI 兼容 `custom` 提供商，因此最短路径不会推荐某个托管服务；只要密钥、提供商名称和模型 ID 匹配，任何受支持的提供商都可以使用。
- 仅在从源码安装时需要 Git。
- 仅在自行开发 WebUI 时需要 Node.js 或 Bun。

> [!IMPORTANT]
> 仓库文档可能会描述那些最早出现在源码中的功能。从 PyPI 或 `uv` 安装可获得稳定的日常发行版；当你想要最新的仓库行为或打算贡献代码时，请从源码安装。

## 1. 安装

选择一种安装方式。

**一键安装：**

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.sh | sh
```

在 Windows PowerShell 上：

```powershell
irm https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.ps1 | iex
```

默认命令会从 PyPI 安装或升级 `nanobot-ai`，然后启动 `nanobot onboard --wizard`。它通过使用已激活的虚拟环境、`uv`、`pipx` 或位于 `~/.nanobot/venv` 的托管 venv 来避免系统级的 pip 安装。如果快速开始已完成并且你启用了 WebSocket 通道，请直接跳到[打开 WebUI](#5-open-the-webui)。

要在不更改环境的情况下预览计划，请传入 `--dry-run`；当你想预览主分支安装时，将其与 `--dev` 组合使用。

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.sh | sh -s -- --dry-run
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.ps1))) --dry-run
```

若要改为安装当前的 `main` 分支，请传入 `--dev`：

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.sh | sh -s -- --dev
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.ps1))) --dev
```

如果 `curl` 或 `irm` 不可用，或者你的网络无法访问 GitHub raw 下载，请使用下面的某种手动安装方式。

如果你想先查看脚本，请打开 [`../scripts/install.sh`](../scripts/install.sh) 或 [`../scripts/install.ps1`](../scripts/install.ps1)。

**使用 `uv` 安装稳定发行版：**

```bash
uv tool install nanobot-ai
nanobot --version
```

**使用 pip 安装稳定发行版：**

```bash
python -m pip install nanobot-ai
nanobot --version
```

仅在你可控的环境内使用 pip。如果 pip 在 macOS 或 Linux 上报告 `externally-managed-environment`，请使用一键安装脚本、`uv tool install nanobot-ai`、`pipx install nanobot-ai`，或先创建一个虚拟环境。

**拉取最新源码：**

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
python -m pip install -e .
nanobot --version
```

如果在 pip 安装后你的 shell 找不到 `nanobot`，请运行模块形式：

```bash
python -m nanobot --version
python -m nanobot onboard
```

在 Windows 上，文档中的 `~` 表示你的用户主目录，例如 `C:\Users\you`。

文档中的命令使用 `python`。如果你的系统将 Python 3.11+ 暴露为 `python3` 或 `py`，请在相应位置使用该命令，例如 `python3 -m pip install nanobot-ai` 或 `py -m nanobot --version`。

## 2. 初始化

如果一键安装已经启动了向导并且快速开始已在其中完成，请跳过本节。

```bash
nanobot onboard
```

如果你更倾向于使用提示而非手动编辑 JSON，请使用向导：

```bash
nanobot onboard --wizard
```

初始化会创建：

| 路径 | 用途 |
|------|------------|
| `~/.nanobot/config.json` | 提供商、模型、通道、工具、网关和 API 的主设置文件 |
| `~/.nanobot/workspace/` | 用于记忆、会话、心跳任务、技能和制品的智能体工作区 |

如果你已有配置，`nanobot onboard` 可以补全缺失的默认字段而不覆盖你现有的值。

## 3. 配置提供商

如果你已在向导中配置好提供商和模型设置，请跳过本节。

打开 `~/.nanobot/config.json`。将这些块添加或合并到由 `nanobot onboard` 创建的文件中；除非你想重置配置，否则不要替换整个文件。

**API 密钥：**

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.example.com/v1"
    }
  }
}
```

**模型预设：**

```json
{
  "modelPresets": {
    "primary": {
      "label": "Primary",
      "provider": "custom",
      "model": "model-id-from-your-provider",
      "maxTokens": 8192,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

预设中的提供商和模型必须匹配。上面的片段仅为示例。对于其他提供商，请同时替换这些值：

| 替换项 | 位置 |
|---|---|
| 提供商配置键，例如 `custom` | `providers.<provider>` |
| API 密钥或环境变量 | `providers.<provider>.apiKey` |
| 预设的提供商名称 | `modelPresets.primary.provider` |
| 模型 ID | `modelPresets.primary.model` |
| 端点 URL，仅在需要时 | `providers.<provider>.apiBase` |

直接使用 `agents.defaults.provider` 和 `agents.defaults.model` 对现有配置仍然有效，但命名预设是推荐的做法，因为它们还支撑着 `/model` 切换和回退链。关于直连、网关、OAuth、云端和本地等多种设置的提供商专属示例，请参阅 [`providers.md`](./providers.md)。

**关于 `apiBase` / base URL 怎么处理？**

`apiBase` 是提供商端点的 HTTP 基础 URL，不是模型名称。nanobot 中大多数托管提供商已经知道自己的默认端点，因此你通常只需设置 `apiKey` 和一个模型预设。当你使用以下情况时才需要设置 `apiBase`：

- 用于第三方或自托管的 OpenAI 兼容 API 的 `custom`；
- 本地的 OpenAI 兼容服务器，例如 Ollama、vLLM 或 LM Studio；
- 提供商专属的备用端点、区域端点、代理或订阅端点。

示例：

```json
{
  "providers": {
    "custom": {
      "apiKey": "${CUSTOM_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  }
}
```

```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434/v1"
    }
  }
}
```

如果提供商的文档说端点是 `/v1`，请在 `apiBase` 中包含 `/v1`。模型 ID 仍然属于当前生效的 `modelPresets` 条目。

如果你不想把密钥存储在 `config.json` 中，可以引用一个环境变量，并在启动 nanobot 之前设置它：

```json
{
  "providers": {
    "custom": {
      "apiKey": "${PROVIDER_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  }
}
```

## 4. 检查安装

```bash
nanobot status
```

这应当显示配置路径、工作区路径、当前生效的模型或预设，以及提供商摘要。它不会向模型发送消息，因此可在第一次真实请求之前用作快速的配置检查。

这样阅读结果：

| 状态行 | 期望结果 |
|---|---|
| `Config` | 一个对勾。 |
| `Workspace` | 一个对勾。 |
| `Model` | 你期望的模型或预设。 |
| 提供商列表 | 大多数提供商可以显示 `not set`；当前生效预设所用的提供商应显示对勾、OAuth 状态或本地 URL。 |

## 5. 打开 WebUI

如果快速开始已启用 WebSocket 通道，请启动网关：

```bash
nanobot gateway
```

保持该终端打开，然后在浏览器中打开 `http://127.0.0.1:8765`。输入你在向导中设置的 WebUI 密码，然后在那里发送你的第一条消息。

## 6. 测试一条 CLI 消息

如果你跳过了快速开始、拒绝了 WebSocket 通道，或只想进行终端检查，请使用此路径。

运行一条一次性 CLI 消息：

```bash
nanobot agent -m "Hello!"
```

首次运行成功证明：

- `nanobot` 命令已安装；
- `~/.nanobot/config.json` 可被加载；
- 所选的提供商和模型可以回答；
- 默认工作区可被创建和使用。

回复文本本身会有所不同。任何正常的助手回答都意味着安装、配置、提供商、模型和工作区路径都可用。

如果可行，请启动交互式 CLI 聊天：

```bash
nanobot agent
```

当交互式会话可以正常回答后，nanobot 可以帮助完成它自己的下一步设置。让它阅读相关文档、检查你当前的 `~/.nanobot/config.json`，并做一项具体改动，例如启用 WebUI、添加一个提供商预设，或配置一个聊天通道。当 nanobot 说配置已更新时，请在聊天中运行 `/restart` 或手动重启 nanobot 进程，以便长时间运行的进程重新加载 `config.json`。

示例提示词：

```text
阅读本检出中的 docs/quick-start.md、docs/providers.md 和 docs/configuration.md。
然后更新 ~/.nanobot/config.json，为我的提供商添加一个名为 "primary" 的模型预设。
准确告诉我改动了什么，以及我是否需要运行 /restart。
```

使用 `exit`、`quit`、`/exit`、`/quit`、`:q` 或 `Ctrl+D` 退出交互模式。

## 7. 选择你的下一步

| 想要... | 前往 |
|---|---|
| 理解配置、工作区、网关、通道、记忆和工具 | [`concepts.md`](./concepts.md) |
| 复制其他提供商或本地模型设置 | [`provider-cookbook.md`](./provider-cookbook.md) |
| 理解提供商/模型匹配 | [`providers.md`](./providers.md) |
| 打开内置的浏览器 UI | [`webui.md`](./webui.md) |
| 连接 Telegram、Discord、微信、Slack、电子邮件或其他聊天应用 | [`chat-apps.md`](./chat-apps.md) |
| 配置网页搜索、MCP、安全、记忆、网关或运行时设置 | [`configuration.md`](./configuration.md) |
| 使用 Docker、systemd 或 LaunchAgent 运行 | [`deployment.md`](./deployment.md) |
| 调试故障 | [`troubleshooting.md`](./troubleshooting.md) |

## 更新

**pip：**

```bash
python -m pip install -U nanobot-ai
nanobot --version
```

如果 pip 报告 `externally-managed-environment`，请使用你安装 nanobot 时所用的相同隔离方式来升级，例如 `uv tool upgrade nanobot-ai`、`pipx upgrade nanobot-ai`，或由一键安装脚本创建的托管 venv。

**uv：**

```bash
uv tool upgrade nanobot-ai
nanobot --version
```

**pipx：**

```bash
pipx upgrade nanobot-ai
nanobot --version
```

**源码检出：**

```bash
git pull
python -m pip install -e .
nanobot --version
```

如果你使用 WhatsApp，请在升级后重建本地桥接：

```bash
rm -rf ~/.nanobot/bridge
nanobot channels login whatsapp
```

## 首次运行故障排查

| 症状 | 检查内容 |
|---------|---------------|
| `nanobot: command not found` | 使用 `python -m nanobot ...`，或将你的 Python 脚本目录加入 `PATH`。 |
| `ModuleNotFoundError: nanobot` | 确认你安装到了运行该命令的同一个 Python 环境。 |
| JSON 解析错误 | 检查 `~/.nanobot/config.json` 中的逗号和花括号；上面的示例是需要合并的部分片段。 |
| 认证或 401 错误 | 检查 API 密钥是否有效、是否无空格地复制、是否放在你选定的提供商之下。 |
| 提供商/模型错误 | 确保当前生效的预设使用的是持有你 API 密钥的提供商，并且该模型在那里存在。 |
| CLI 可用但聊天应用不回复 | 首先保持 `nanobot gateway` 运行，然后按 [`chat-apps.md`](./chat-apps.md) 操作。 |
| WebUI 无法打开 | 启用 WebSocket 通道并打开端口 `8765`，而不是网关健康端口 `18790`。 |

如需更完整的诊断流程，请参阅 [`troubleshooting.md`](./troubleshooting.md)。
