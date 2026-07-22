# 概念

当你想在修改高级设置之前了解 nanobot 时，请阅读本页面。它解释了各个活动部件，无需你先阅读源码。

如果你想要源文件归属和扩展点，请在本页面之后阅读 [`architecture.md`](./architecture.md)。

## 运行时形态

nanobot 有一个小型核心循环和多种进入方式：

| 部分 | 作用 |
|---|---|
| Agent loop | 构建上下文、选择会话、调用 provider、运行工具并发布回复 |
| Providers | LLM 后端，如 OpenRouter、Anthropic、OpenAI、Bedrock、Ollama、vLLM 及其他 OpenAI 兼容 API |
| Channels | 面向用户的传输层，如 CLI、WebUI/WebSocket、Telegram、Discord、Slack、Feishu、WeChat、Email 等 |
| Tools | 模型可调用的能力，包括文件、shell、web 搜索/抓取、MCP、cron、图像生成和子代理 |
| Memory | 工作区文件和会话历史，用于在多轮之间保留有用上下文 |
| Gateway | 长时间运行的进程，连接已启用的 channel 并提供健康端点 |

最简单的路径是 `nanobot agent -m "Hello!"`：一条入站消息经过 agent loop，并在你的终端打印回复。长时间运行的路径是 `nanobot gateway`：channel 从聊天应用或 WebUI 接收消息，将其发布到同一个 agent loop，并将回复发送回原始 channel。

## 配置与工作区

默认实例位于 `~/.nanobot/` 下：

| 路径 | 含义 |
|---|---|
| `~/.nanobot/config.json` | 实例配置：providers、模型默认值、channels、tools、gateway、API 和运行时选项 |
| `~/.nanobot/workspace/` | Agent 工作区：记忆、会话、heartbeat 任务、cron 作业、技能和生成的产物 |

你可以用命令标志覆盖两者：

```bash
nanobot onboard --config ./bot-a/config.json --workspace ./bot-a/workspace
nanobot agent --config ./bot-a/config.json --workspace ./bot-a/workspace -m "Hello"
nanobot gateway --config ./bot-a/config.json --workspace ./bot-a/workspace
```

配置文件控制 nanobot 可以使用什么。工作区是 nanobot 为该实例保存状态的地方。

## 配置格式

`config.json` 同时接受 camelCase 和 snake_case 键。文档使用 camelCase，因为 nanobot 在将配置写回磁盘时使用 camelCase 别名，例如 `apiKey`、`modelPresets`、`intervalS` 和 `maxToolResultChars`。

大多数示例是部分代码片段。请将它们合并到由 `nanobot onboard` 创建的现有文件中；除非你想重置实例，否则不要替换整个文件。

## 一次 Agent 轮次

普通轮次遵循以下流程：

1. 一个 channel 接收用户消息并将其发布到消息总线。
2. agent loop 选择一个会话键，并从工作区、技能、记忆、近期消息、channel 元数据和运行时设置构建上下文。
3. provider 接收模型请求。
4. 如果模型请求工具，runner 执行工具并将结果回传给模型。
5. 最终回复被保存到会话并通过 channel 发送回去。

无论消息从 CLI、WebUI、Telegram、Discord 还是其他 channel 开始，该流程都相同。

## CLI、Gateway、API 与 WebUI

| 入口 | 命令 | 用途 |
|---|---|---|
| CLI 一次性 | `nanobot agent -m "..."` | 首次运行检查、脚本和快速本地提问 |
| CLI 交互式 | `nanobot agent` | 带持久会话历史的终端聊天 |
| Gateway | `nanobot gateway` | 聊天应用、WebUI、heartbeat、Dream 和长时间运行的服务模式 |
| OpenAI 兼容 API | `nanobot serve` | 通过 `/v1/chat/completions` 进行编程访问 |
| WebUI | `nanobot gateway` 加 WebSocket channel | 由 WebSocket channel 在端口 `8765` 上提供的浏览器工作台 |

网关健康端点位于 `gateway.port`（默认为 `18790`）。浏览器 WebUI 由 WebSocket channel（默认为 `8765`）提供，而非由健康端点提供。

## Provider 与模型选择

活动模型通常应来自由 `agents.defaults.modelPreset` 选择的命名 `modelPresets` 条目。直接的 `agents.defaults.provider` 和 `agents.defaults.model` 仍然构成旧配置或最小配置的隐式 `default` 预设。活动 provider 按以下顺序解析：

1. 如果活动预设 provider 或隐式默认 provider 不是 `"auto"`，nanobot 使用该 provider。
2. 如果 provider 为 `"auto"`，nanobot 尝试从模型名称、已配置的 API key、本地 provider base URL 或网关 provider 推断 provider。
3. OAuth provider（如 OpenAI Codex 和 GitHub Copilot）需要显式登录，并在活动预设内显式选择 provider/model。

首次设置时请在预设内固定 provider。这样更易于调试：

```json
{
  "modelPresets": {
    "primary": {
      "provider": "openrouter",
      "model": "anthropic/claude-opus-4.5"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

实用示例见 [`providers.md`](./providers.md)，完整 provider 参考见 [`configuration.md#providers`](./configuration.md#providers)。

## Channel 与会话

每个 channel 将入站消息映射到一个会话键。这使得独立的对话能够保持各自独立的历史。WebUI 还支持多个聊天以及针对项目工作区的工作区作用域元数据。

`agents.defaults.unifiedSession` 可以有意地在多个 channel 之间共享一个会话，适用于单用户多设备场景。如果你希望不同的人、群组、channel 或项目保持各自独立的上下文，请保持其关闭。

## 记忆、会话与 Dream

nanobot 使用两个相关的存储：

| 存储 | 位置 | 用途 |
|---|---|---|
| 会话 | `<workspace>/sessions/*.jsonl` | 重放到上下文中的近期对话轮次 |
| 记忆 | `<workspace>/memory/MEMORY.md` 和 `<workspace>/memory/history.jsonl` | 长期事实和整合后的历史 |

Dream 是一个周期性整合作业。它读取累积的历史并更新工作区记忆，使有用的上下文能够存活到短期会话重放之后。

详细设计见 [`memory.md`](./memory.md)。

## 工具与安全

工具从内置模块和插件入口点自动发现。常见工具组包括：

- 文件读/写/编辑和打补丁；
- 带可配置沙箱的 shell 执行；
- 带 SSRF 检查的 web 搜索和 web 抓取；
- MCP 服务器；
- cron 提醒和 heartbeat 任务；
- 图像生成；
- 子代理和运行时自检。

安全敏感的控制项位于 [`configuration.md#security`](./configuration.md#security)。对于生产环境或共享聊天应用，还需配置 channel 访问控制，如 `allowFrom`、配对或 WebSocket 令牌。

## 后台作业

当 `nanobot gateway` 启动时，它会在 `<workspace>/cron/jobs.json` 创建工作区作用域的 cron 存储，并注册系统作业：

- `dream`，当 `agents.defaults.dream.enabled` 为 true 时；
- `heartbeat`，当 `gateway.heartbeat.enabled` 为 true 时。

Heartbeat 读取 `<workspace>/HEARTBEAT.md`。如果该文件在 `## Active Tasks` 下有任务，nanobot 会执行它们并将有用的结果发送到最近活跃的聊天目标。

用户创建的提醒使用相同的 cron 服务，但与受保护的 heartbeat 系统作业并不相同。

## 接下来去哪里

| 需求 | 阅读 |
|---|---|
| 首次可用安装 | [`quick-start.md`](./quick-start.md) |
| Provider/模型设置 | [`providers.md`](./providers.md) |
| 聊天应用设置 | [`chat-apps.md`](./chat-apps.md) |
| 完整配置参考 | [`configuration.md`](./configuration.md) |
| 运行时调试 | [`troubleshooting.md`](./troubleshooting.md) |
