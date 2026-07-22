# 零技术背景上手

如果你从未使用过终端、编辑过 JSON 文件，或配置过 AI 模型，本页就是为你准备的。

目标很小：在浏览器中拿到一条本地 nanobot 回复。先不要连接 Telegram、Discord、Docker、本地模型或进行部署。等第一条回复跑通之后，这些都会更容易。

## 你要搭建的东西

在"快速开始"中你只需要了解这些词：

| 词语 | 通俗含义 |
|---|---|
| Terminal（终端） | 一个文本窗口，你在里面粘贴命令并按回车。 |
| Command（命令） | 你在终端中运行的一行文本。 |
| API key（API 密钥） | 来自 AI 提供方的类密码令牌。不要公开分享。 |
| Config file（配置文件） | nanobot 启动时读取的设置文件。 |
| Wizard（向导） | 一个交互式终端菜单，替你编辑配置文件。 |
| Browser UI（浏览器界面） | 你与 nanobot 聊天的本地网页。 |

## 1. 打开终端

你会把命令粘贴到终端中。只复制每个代码块内的命令文本；不要复制 ``` 标记。

| 系统 | 如何打开 |
|---|---|
| Windows | 按 `Win`，输入 `PowerShell`，然后打开 **Windows PowerShell**。 |
| macOS | 按 `Command` + `Space`，输入 `Terminal`，然后按 `Enter`。 |
| Linux | 打开应用启动器，搜索 `Terminal`，然后打开它。 |

终端打开后，在窗口内点击，粘贴命令，然后按 `Enter`。如果命令打印了一些文本并回到提示符，通常都是正常的。

## 2. 安装 Python

从 [python.org](https://www.python.org/downloads/) 安装 Python 3.11 或更新版本。

在 Windows 上，如果安装程序显示该选项，请在安装过程中启用 **Add python.exe to PATH**。

在该终端中检查 Python：

```bash
python --version
```

如果 Windows 提示找不到 `python`，请关闭并重新打开 PowerShell。如果仍然不行，尝试：

```bash
py --version
```

如果 `py` 可用而 `python` 不可用，请在下面的命令中把 `python` 替换为 `py`。

如果 macOS 或 Linux 提示找不到 `python`，尝试：

```bash
python3 --version
```

如果 `python3` 可用而 `python` 不可用，请在下面的手动命令中把 `python` 替换为 `python3`。一键安装程序已经会同时检查 `python3` 和 `python`。

## 3. 获取提供方 API 密钥

nanobot 不会替你创建 AI 账户或 API 密钥。请使用你已经控制的 AI 提供方账户、公司端点、订阅端点或本地模型服务器。如果提供方文档中有 OpenAI 兼容的 base URL，也把它放在手边。

按以下步骤操作：

1. 打开你的提供方 API 密钥页面。
2. 创建或复制一个 API 密钥。
3. 保密该密钥。
4. 如果提供方文档给出了 base URL，把它放在手边。

## 4. 安装 nanobot

最简单的路径是使用一键安装程序。它会安装或升级 nanobot，然后启动设置向导。在 macOS 和 Linux 上，它会通过使用已激活的虚拟环境、`uv`、`pipx`，或位于 `~/.nanobot/venv` 下的受管 venv，避免系统范围的 pip 安装。

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.sh | sh
```

**Windows PowerShell**

```powershell
irm https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.ps1 | iex
```

这些命令会安装稳定的 PyPI 包。要在不改变环境的情况下预览安装程序会做什么，请传入 `--dry-run`：

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.sh | sh -s -- --dry-run
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.ps1))) --dry-run
```

仅当维护者要求你测试当前 `main` 分支时，才使用开发安装程序：

```bash
curl -fsSL https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.sh | sh -s -- --dev
```

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/HKUDS/nanobot/main/scripts/install.ps1))) --dev
```

如果命令提示找不到 `curl` 或 `irm`，或者无法从 GitHub 下载，请使用下面的某条手动安装命令。

如果已安装 `uv`，使用：

```bash
uv tool install nanobot-ai
```

如果你偏好 pip，请仅在由你控制的环境中使用：

```bash
python -m pip install nanobot-ai
```

如果 pip 在 macOS 或 Linux 上报告 `externally-managed-environment`，请回到一键安装程序，使用 `uv tool install nanobot-ai`，使用 `pipx install nanobot-ai`，或先创建虚拟环境。

然后检查 nanobot 是否已安装：

```bash
nanobot --version
```

如果终端找不到 `nanobot`，使用模块形式：

```bash
python -m nanobot --version
```

如果第 2 步中可用的 Python 命令是 `python3 -m nanobot --version` 或 `py -m nanobot --version`，请相应使用。

## 5. 运行设置向导

一键安装程序会在安装后自动为你启动向导。如果你是手动安装的，请运行：

```bash
nanobot onboard --wizard
```

如果找不到 `nanobot`，运行：

```bash
python -m nanobot onboard --wizard
```

如果第 2 步中可用的 Python 命令是 `python3 -m nanobot onboard --wizard` 或 `py -m nanobot onboard --wizard`，请相应使用。

向导是一个终端菜单。它不是图形应用，但能让你通过选择选项来完成配置，而不必手工编辑每个 JSON 字段。

你会看到这样一个菜单：

```text
> What would you like to do?
  [Q] Quick Start
  [A] Advanced Settings
  [X] Exit
```

按以下方式在向导中操作：

| 当你看到 | 这样做 |
|---|---|
| 一个菜单 | 用方向键高亮某个选项，然后按 `Enter`。 |
| 提供方菜单 | 选择你想使用的公司或服务。 |
| 端点菜单 | 选择与你的密钥匹配的标准 API 或订阅套餐端点。 |
| API 密钥字段 | 粘贴密钥，然后按 `Enter`。 |
| 提供方 base URL 字段 | 粘贴来自其文档的提供方 base URL，然后按 `Enter`。 |
| 模型 ID 字段 | 粘贴来自你提供方的模型名称，然后按 `Enter`。 |
| Advanced Settings 中的返回选项 | 选择它以返回上一个菜单。 |

首次设置请选择 `[Q] Quick Start`。它会为你配置推荐的本地浏览器界面和默认 AI 设置。仅在你后续需要聊天应用、工具设置或提供方专属字段时，再使用 `Advanced Settings`。

1. 选择 `[Q] Quick Start`。
2. 选择你想使用的提供方。
3. 如果向导询问端点，请选择，例如 Standard API、Coding Plan、Token Plan 或 Step Plan。
4. 如果向导要求，粘贴你的 API 密钥。
5. 如果向导要求，粘贴提供方 base URL。
6. 粘贴该提供方可运行的模型 ID。
7. 确认 Quick Start 应为本地 WebUI 启用 WebSocket 通道。
8. 出现提示时设置 WebUI 密码。
9. 检查 Quick Start 摘要。Quick Start 完成时向导会保存并退出。

推荐路径会为本地 WebUI 启用 `channels.websocket`，要求设置 WebUI 密码，并写入默认 AI 设置。首次运行时你无需单独选择聊天应用。

如果你已经明确需要自定义请求头、提供方专属请求字段、聊天应用或工具，请改选 `Advanced Settings`。[`provider-cookbook.md`](./provider-cookbook.md) 提供了若干常见提供方设置的可复制示例。你修改高级设置后，主菜单中会出现保存选项。选择 `[S] Save and Exit`。

向导会创建或更新：

| 路径 | 含义 |
|---|---|
| `~/.nanobot/config.json` | 设置文件。 |
| `~/.nanobot/workspace/` | 用于记忆、会话和生成文件的工作目录。 |

如果 Quick Start 成功完成，请直接跳到 [打开 WebUI](#7-打开-webui)。接下来两节仅供手动设置使用。

## 手动设置：如何合并 JSON 片段

大多数文档示例都是片段，不是完整文件。你的 `config.json` 有一个外层的 `{ ... }`。把 `providers`、`modelPresets`、`agents` 或 `channels` 等新的顶级章节添加到同一个外层对象内。

不要把两个独立的 JSON 对象粘贴到一个文件里：

```text
{
  "providers": { "...": "..." }
}
{
  "channels": { "...": "..." }
}
```

把它们合并成一个对象：

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "channels": {
    "websocket": {
      "enabled": true,
      "tokenIssueSecret": "your-webui-password",
      "websocketRequiresToken": true
    }
  }
}
```

注意 `providers` 块后面的逗号。JSON 要求同级章节之间有逗号，但最后一个章节后不要加逗号。如果觉得困难，请尽量使用 `nanobot onboard --wizard`。

## 6. 手动设置：配置回退

仅在向导不可用，或你更愿意自己打开文件时使用此方式。

如果 `~/.nanobot/config.json` 尚不存在，请先运行 `nanobot onboard`。

使用以下命令之一：

**Windows PowerShell**

```powershell
notepad "$env:USERPROFILE\.nanobot\config.json"
```

**macOS**

```bash
open -e ~/.nanobot/config.json
```

**Linux**

```bash
xdg-open ~/.nanobot/config.json
```

如果是全新安装且你尚未配置任何其他内容，请用以下最小配置替换该文件：

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "label": "Primary",
      "provider": "custom",
      "model": "model-id-from-your-provider",
      "maxTokens": 4096,
      "contextWindowTokens": 65536,
      "temperature": 0.1
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  },
  "channels": {
    "websocket": {
      "enabled": true,
      "tokenIssueSecret": "your-webui-password",
      "websocketRequiresToken": true
    }
  }
}
```

将 `your-api-key`、`https://api.example.com/v1`、`model-id-from-your-provider` 和 `your-webui-password` 替换为你自己的值。

如需可复制的提供方专属示例，请使用 [`provider-cookbook.md`](./provider-cookbook.md)。

保存文件。

## 7. 打开 WebUI

首先检查 nanobot 能读取已保存的设置：

```bash
nanobot status
```

这应显示配置文件路径、工作区路径，以及当前活动的模型或预设。如果找不到 `nanobot`，使用 `python -m nanobot status`、`python3 -m nanobot status` 或 `py -m nanobot status`，与第 2 步中可用的 Python 命令保持一致。

大多数提供方显示 `not set` 是正常的。只有你为活动预设所选的提供方需要看起来已配置。

启动本地浏览器界面：

```bash
nanobot gateway
```

保持该终端打开，然后在浏览器中打开 `http://127.0.0.1:8765`。输入你在向导中设置的 WebUI 密码，或手动配置中的 `tokenIssueSecret` 值。

在浏览器中发送第一条消息：

```text
Hello!
```

如果成功，说明 nanobot 已安装并能调用模型。你应在浏览器中看到一条正常的助手回复。具体文字会不同，但应该形如：

```text
Hello! How can I help you today?
```

如果找不到 `nanobot`，运行：

```bash
python -m nanobot gateway
```

如果第 2 步中可用的 Python 命令是 `python3 -m nanobot gateway` 或 `py -m nanobot gateway`，请相应使用。

一旦跑通，nanobot 就能帮你完成下一步设置。在浏览器界面中，让它阅读这些文档并为某个具体目标更新你当前的配置，等 nanobot 告诉你配置就绪后运行 `/restart`。例如，让它添加一个提供方预设或配置一个聊天应用。

## 8. 如果某步失败

不要一次改动太多。检查确切的错误：

| 错误或症状 | 通常含义 |
|---|---|
| `JSON parse error` | 配置文件缺少逗号、多余逗号或大括号不匹配。重新复制示例。 |
| `401`、`unauthorized` 或 `invalid API key` | API 密钥错误、过期、含多余空格，或被粘贴到了错误的提供方下。 |
| `model not found` | 你的账户无法使用默认模型。回到 `nanobot onboard --wizard`，选择 `Advanced Settings`，然后编辑 `Model Presets`。 |
| `nanobot: command not found` | 安装在 Python 中成功了，但你的 shell 找不到该脚本。使用 `python -m nanobot ...`、`python3 -m nanobot ...` 或 `py -m nanobot ...`，与之前可用的 Python 命令保持一致。 |
| 编辑配置后无响应 | 重启命令。长时间运行的进程在启动时读取配置。 |

更完整的诊断路径，见 [`troubleshooting.md`](./troubleshooting.md)。

## 暂时不要配置的内容

在第一条本地消息跑通之前，跳过这些：

- `apiBase`：托管的内置提供方通常已有默认端点。仅本地模型、代理、自定义 OpenAI 兼容提供方或特殊区域/订阅端点才需要 `apiBase`。
- 聊天应用：先证明本地浏览器界面能回答。
- 备用模型：以后有用，但第一条回复不需要。
- Langfuse：对可观测性有用，但首次设置不需要。

## 下一步

第一条回复跑通后，只选择一个下一步目标。每当你使用 WebUI 或聊天应用时，都要保持运行 `nanobot gateway` 的终端打开。

### 再次打开浏览器界面

运行：

```bash
nanobot gateway
```

保持该终端打开，然后在浏览器中打开 `http://127.0.0.1:8765`。

稍后要停止 WebUI，回到 gateway 终端并按 `Ctrl+C`。

如果找不到 `nanobot`，运行 `python -m nanobot gateway`、`python3 -m nanobot gateway` 或 `py -m nanobot gateway`，与之前可用的 Python 命令保持一致。更多细节见 [`webui.md`](./webui.md)。

### 连接聊天应用

1. 阅读 [`chat-apps.md`](./chat-apps.md) 中某个应用的章节。
2. 仅添加该应用的配置片段。把它合并进现有文件，而不是替换整个文件。
3. 运行：

```bash
nanobot channels status
nanobot gateway
```

4. 保持 gateway 终端打开，然后从允许的账户发送一条消息。

先从私聊或测试服务器开始。除非你有意让任何能触达该通道的人都能与机器人对话，否则不要把 `allowFrom` 设为 `["*"]`。

### 更换模型或添加备用

当某对提供方/模型失败时使用 [`providers.md`](./providers.md)，需要可复制片段时使用 [`provider-cookbook.md`](./provider-cookbook.md)。把模型选择保留在 `modelPresets` 中，然后用 `agents.defaults.modelPreset` 选择活动预设。

### 寻求帮助

寻求帮助时，请包含：

- 你的操作系统；
- 你运行的命令；
- `nanobot --version`；
- `nanobot status`；
- 浏览器界面是否能回答 `Hello!`；
- 确切的错误文本；
- 去除 API 密钥和令牌后的配置片段。

绝不要把真实的 API 密钥、机器人令牌、OAuth 令牌或私聊 ID 粘贴到公开的 issue 或聊天中。

如果你发现文档错误、过时命令或令人困惑的步骤，请提一个 issue：<https://github.com/HKUDS/nanobot/issues>。
