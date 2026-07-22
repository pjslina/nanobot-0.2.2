# WebUI

WebUI 是 nanobot 的浏览器工作台。当基础的 CLI 回复已经可用，而你又希望拥有一个持久的聊天工作区、可见的 agent 活动、工作区控制、Apps、Skills、设置和 Automations 集于一处时，就可以使用它。

已发布的 `nanobot-ai` wheel 已包含 WebUI 打包文件。只有在你要修改前端本身时，才需要 `webui/` 源码目录。

## 打开 WebUI

首先确认你的 provider 和模型可以回答：

```bash
nanobot agent -m "Hello!"
```

然后将 WebSocket channel 合并到你现有的 `~/.nanobot/config.json` 中。把 `tokenIssueSecret` 设置为你将在 WebUI 登录表单中输入的密码：

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "tokenIssueSecret": "your-webui-password",
      "websocketRequiresToken": true
    }
  }
}
```

如果你不熟悉 JSON 片段，参见
[`start-without-technical-background.md#how-to-merge-json-snippets`](./start-without-technical-background.md#how-to-merge-json-snippets)。

启动 gateway：

```bash
nanobot gateway
```

让 gateway 保持运行，然后打开
[`http://127.0.0.1:8765`](http://127.0.0.1:8765)。WebUI 默认由 WebSocket channel 在端口 `8765` 上提供。gateway 健康检查端点默认为 `18790`，并不是浏览器 UI。
当 WebUI 要求输入密码时，输入 `tokenIssueSecret`。

## 它的用途

| 区域 | 用途 |
|---|---|
| Chat | 启动、切换、搜索、分叉和删除浏览器会话 |
| Agent activity | 在上下文中查看思考、工具调用、文件活动、命令输出和生成的产物 |
| Workspace | 在请求文件或 shell 工作之前选择项目工作区 |
| Access | 为你的 gateway 配置所允许的本地能力选择访问模式 |
| Composer | 发送文本、图片、语音输入、斜杠命令，以及用于 Apps 或 MCP 预设的 `@` 提及 |
| Apps | 安装、测试、更新和使用本地 CLI App 适配器及 MCP 预设 |
| Skills | 在依赖某个技能之前，查看可用的内置技能和工作区技能 |
| Automations | 审查、搜索、运行、暂停、编辑和删除已调度的 agent 轮次 |
| Settings | 调整模型、provider、图片生成、语音、web 工具、运行时和安全选项 |

## Chat Workspace

侧边栏是会话切换器。每个会话保留自己的历史、标题、工作区元数据和关联的 automations。当你想要一个独立的上下文时使用新会话；当你想从某个已有节点继续而不改动原始线程时使用 fork。

消息时间线会同时显示用户可见的回复和 agent 活动。较长的工具或推理区段可以在你需要细节时展开。

## Workspace and Access

在开始针对项目的工作之前使用工作区选择器。这会为文件路径、shell 命令和会话元数据给 agent 提供正确的项目上下文。

composer 中的访问控制用于调节本次聊天的本地能力级别。它不会绕过你的 gateway、provider、shell 沙箱或操作系统配置；它只是在当前 WebUI 会话已可用的能力之中进行选择。

## Composer

composer 支持纯文本消息、图片附件、配置了转写时的语音输入、斜杠命令，以及用于已安装 Apps 或 MCP 预设的 `@` 提及。模型徽标显示当前模型或预设，并在设置不完整时链接回模型设置。

对于图片生成，请先配置一个图片 provider，然后在 composer 中使用 WebUI 图片模式。关于 provider 设置和输出行为，参见 [`image-generation.md`](./image-generation.md)。

## Apps

从侧边栏或设置导航打开 Apps，以管理 nanobot 可在聊天中调用的集成。CLI Apps 会安装 nanobot 在你机器上运行的本地适配器；它们不会修改原生应用本身。MCP 预设会添加预定义的 MCP server 配置。

某些 MCP 预设会连接到托管的免密钥端点。例如，Firecrawl 预设使用 Firecrawl 的托管 MCP 端点来提供搜索、抓取、爬取和抽取工具，无需 API key。这并不替代 nanobot 内置的 web 搜索 provider；当某个轮次需要 Firecrawl 更丰富的 web 数据工具时，用 `@` 提及 Firecrawl MCP 预设即可。

当某个 App 或 MCP 预设可用后，在 composer 中用 `@` 提及它，即可把该能力附加到下一条消息。

## Skills

Skills 视图显示 agent 可用的技能说明，包括内置技能和工作区提供的技能。当你想知道 nanobot 是否已为某项任务提供聚焦的工作流，再让它执行该任务时，请查看此视图。

## Automations

Automations 是已调度的 agent 轮次。应当在期望它们运行的 chat、channel 或 session 中创建，以便 nanobot 保留正确的目标上下文。

使用 Automations 视图可以：

- 按全部、活跃、已暂停、需关注或系统任务过滤。
- 按任务名、消息、关联聊天、调度或状态搜索。
- 按下次运行、上次运行、更新时间或名称排序。
- 立即运行、暂停或恢复、编辑或删除用户创建的 automations。
- 检查受保护的系统 automations 而不修改它们。

搜索接受纯文本以及字段过滤器，例如 `name:backup`、
`chat:WeChat`、`schedule:09:30`、`cron:"0 23 * * *"` 和 `status:paused`。

没有关联聊天的 automation 无法从 WebUI 启用或运行，因为 nanobot 不知道该把调度的轮次投递到哪里。请从目标 chat 或 channel 重新创建它，以便该 automation 拥有完整上下文。

## Settings

Settings 是浏览器会话以及由 gateway 支撑的运行时配置的控制面板。可用它审查或调整模型预设、provider 可见性、图片生成、语音转写、web 工具、Apps、Automations、Skills、运行时身份以及高级安全控制。

某些设置会立即生效。影响 gateway 或 agent 进程的运行时设置可能需要重启；WebUI 会在相应控件旁边显示该要求。

## LAN Access

要从同一网络中的另一台设备打开 WebUI，请把 WebSocket channel 绑定到所有接口，并设置 token 或 token 签发密钥：

```json
{
  "channels": {
    "websocket": {
      "enabled": true,
      "host": "0.0.0.0",
      "port": 8765,
      "tokenIssueSecret": "your-secret-here"
    }
  }
}
```

当 `host` 设置为 `"0.0.0.0"` 时，若未配置 `token` 或 `tokenIssueSecret`，gateway 会拒绝启动。gateway 启动后，从另一台设备打开
`http://<your-ip>:8765` 并在登录表单中输入密钥。

## Troubleshooting

如果页面打不开，请按顺序检查以下各项：

1. 在同一 Python 环境中 `nanobot agent -m "Hello!"` 可正常运行。
2. `~/.nanobot/config.json` 中已启用 WebSocket channel。
3. `nanobot gateway` 仍在运行。
4. 你打开的是端口 `8765`，而不是 gateway 健康检查端口。
5. LAN 访问使用了 `host: "0.0.0.0"` 并配置了 token 或 token 签发密钥。

如需详细诊断，参见
[`troubleshooting.md#webui-problems`](./troubleshooting.md#webui-problems)。
关于前端开发，参见 [`../webui/README.md`](../webui/README.md)。
