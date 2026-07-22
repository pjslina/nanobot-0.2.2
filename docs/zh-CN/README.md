# nanobot 文档

如需查看已发布的发行版文档，请访问 [nanobot.wiki](https://nanobot.wiki/docs/latest/getting-started/nanobot-overview)。本目录中的页面跟踪当前仓库，可能会描述尚未发布到正式站点的功能。

如果你从未使用过终端或编辑过配置文件，请从 [`start-without-technical-background.md`](./start-without-technical-background.md) 开始。否则，请从 [`quick-start.md`](./quick-start.md) 开始，在连接聊天应用、WebUI、Docker 或自定义工具之前，先让一条本地的 `nanobot agent -m "Hello!"` 回复跑通。

本文档中的大多数 JSON 示例都是需要合并到 `~/.nanobot/config.json` 的片段，而不是完整替换文件。

提供商示例是具体的操作指南，不是排名或推荐。请使用你实际掌握其密钥、端点和模型 ID 的提供商。

如果你发现文档错误、过时命令或令人困惑的步骤，请提交一个 issue：<https://github.com/HKUDS/nanobot/issues>。

## 选择路线

| 你的情况 | 从这里开始 | 然后使用 |
|---|---|---|
| 不熟悉终端和配置文件 | [`start-without-technical-background.md`](./start-without-technical-background.md) | 如果首次回复失败，参阅 [`troubleshooting.md`](./troubleshooting.md) |
| 能熟练粘贴命令和 JSON | [`quick-start.md`](./quick-start.md) | [`provider-cookbook.md`](./provider-cookbook.md) 提供可粘贴的提供商设置 |
| 在运行长期运行的机器人 | [`concepts.md`](./concepts.md) | [`chat-apps.md`](./chat-apps.md)、[`webui.md`](./webui.md) 和 [`deployment.md`](./deployment.md) |
| 在集成或扩展 nanobot | [`architecture.md`](./architecture.md) | [`configuration.md`](./configuration.md)、[`openai-api.md`](./openai-api.md)、[`python-sdk.md`](./python-sdk.md)、[`development.md`](./development.md) 和 [`channel-plugin-guide.md`](./channel-plugin-guide.md) |

## 从这里开始

| 目标 | 阅读 | 结果 |
|---|---|---|
| 从零技术背景开始 | [`start-without-technical-background.md`](./start-without-technical-background.md) | 一键安装、终端基础、配置、API 密钥和首次回复 |
| 安装并获得首次回复 | [`quick-start.md`](./quick-start.md) | 一个可用的 CLI 智能体和一条已知可用的配置路径 |
| 理解各部分如何组合 | [`concepts.md`](./concepts.md) | 关于配置、工作区、网关、通道、工具、记忆和会话的心智模型 |
| 选择或更换模型提供商 | [`providers.md`](./providers.md) | 无需阅读完整配置参考即可正确配对提供商/模型 |
| 复制提供商设置方案 | [`provider-cookbook.md`](./provider-cookbook.md) | 可粘贴的 OpenRouter、OpenAI、Anthropic、本地模型、回退和 Langfuse 设置 |
| 修复首次运行或运行时问题 | [`troubleshooting.md`](./troubleshooting.md) | 一套诊断顺序和针对常见故障的检查方法 |

## 首次回复跑通之后

不要一次性配置所有内容。选择下一个要接入的界面：

如果本地 `nanobot agent` 会话已经可以正常回答，你也可以让 nanobot 帮助配置自身：让它阅读相关文档、检查你当前的配置、做一项具体的下一步改动，并告诉你何时运行 `/restart`。

| 下一个目标 | 阅读 | 首先检查 |
|---|---|---|
| 在浏览器中使用 nanobot | [`webui.md`](./webui.md) | 启用 WebSocket，运行 `nanobot gateway`，打开 `http://127.0.0.1:8765` |
| 通过聊天应用对话 | [`chat-apps.md`](./chat-apps.md) | 合并一个通道片段，运行 `nanobot channels status`，保持 `nanobot gateway` 运行 |
| 更换提供商或添加回退 | [`provider-cookbook.md`](./provider-cookbook.md) | 保持 `modelPresets` 命名并设置 `agents.defaults.modelPreset` |
| 从 Python 调用 nanobot | [`python-sdk.md`](./python-sdk.md) | 在代码中复用同一配置/工作区，然后运行或流式执行一个智能体回合 |
| 在长期运行前先理解 | [`concepts.md`](./concepts.md) | 了解配置、工作区、网关、会话、记忆和工具的含义 |
| 诊断新的故障 | [`troubleshooting.md`](./troubleshooting.md) | 从 `nanobot status` 开始，然后是 `nanobot agent -m "Hello!"` |

## 使用 nanobot

| 目标 | 阅读 | 结果 |
|---|---|---|
| 打开内置的浏览器 UI | [`webui.md`](./webui.md) | 端口 `8765` 上的 WebUI、聊天工作区、Apps、Skills、Automations 和设置 |
| 连接 Telegram、Discord、微信、Slack 和其他应用 | [`chat-apps.md`](./chat-apps.md) | 一个由网关支撑、带访问控制的聊天通道 |
| 使用斜杠命令和周期性任务 | [`chat-commands.md`](./chat-commands.md) | 配对、模型预设、心跳任务和聊天侧控制 |
| 生成图像 | [`image-generation.md`](./image-generation.md) | 图像提供商配置、WebUI 图像模式和制品行为 |
| 运行多个相互隔离的机器人 | [`multiple-instances.md`](./multiple-instances.md) | 相互独立的配置、工作区、端口和会话 |
| 在终端之外部署 | [`deployment.md`](./deployment.md) | Docker、systemd 用户服务和 macOS LaunchAgent 设置 |
| 加入智能体社区 | [`agent-social-network.md`](./agent-social-network.md) | 外部智能体社区设置 |

## 参考

| 领域 | 阅读 | 最适用于 |
|---|---|---|
| 完整配置模式 | [`configuration.md`](./configuration.md) | 确切字段、默认值、提供商表、网页工具、MCP、安全和运行时选项 |
| CLI 命令 | [`cli-reference.md`](./cli-reference.md) | 命令名称、常用标志和入口点 |
| 架构 | [`architecture.md`](./architecture.md) | 面向核心流程、提供商、通道、工具、WebUI、记忆、安全和扩展点的源码级运行时图 |
| 开发 | [`development.md`](./development.md) | 关于添加提供商和转录适配器的贡献者说明 |
| 记忆 | [`memory.md`](./memory.md) | 会话历史、Dream 整合、记忆文件和版本管理 |
| 可观测性 | [`configuration.md#langfuse-observability`](./configuration.md#langfuse-observability) | Langfuse 追踪设置和所需环境变量 |
| WebSocket 协议 | [`websocket.md`](./websocket.md) | 自定义客户端、令牌签发、多路复用聊天、媒体和协议事件 |
| OpenAI 兼容 API | [`openai-api.md`](./openai-api.md) | `/v1/chat/completions`、`/v1/models`、文件上传和 SDK 兼容用法 |
| Python SDK | [`python-sdk.md`](./python-sdk.md) | SDK 入门、会话、流式、模型覆盖、运行时辅助工具和钩子 |
| 运行时自检 | [`my-tool.md`](./my-tool.md) | 检查和调整当前智能体运行 |

## 快速查找

| 需求 | 跳转到 |
|---|---|
| 提供商/模型解析顺序 | [`providers.md#provider-resolution`](./providers.md#provider-resolution) |
| 模型预设和回退链 | [`providers.md#model-presets`](./providers.md#model-presets) 和 [`providers.md#fallback-models`](./providers.md#fallback-models) |
| Langfuse 环境变量 | [`configuration.md#langfuse-observability`](./configuration.md#langfuse-observability) |
| WebSocket/WebUI 协议细节 | [`websocket.md`](./websocket.md) |
| OpenAI 兼容 API 用法 | [`openai-api.md`](./openai-api.md) |
| Python SDK 用法 | [`python-sdk.md`](./python-sdk.md) |
| 多个配置、工作区和端口 | [`multiple-instances.md`](./multiple-instances.md) |
| 安全、沙箱和 SSRF 控制 | [`configuration.md#security`](./configuration.md#security) |
| 通道插件开发 | [`channel-plugin-guide.md`](./channel-plugin-guide.md) |

## 扩展 nanobot

| 目标 | 阅读 | 结果 |
|---|---|---|
| 添加提供商或转录适配器 | [`development.md`](./development.md) | 一条与注册表/模式对齐的实现路径 |
| 添加聊天通道插件 | [`channel-plugin-guide.md`](./channel-plugin-guide.md) | 一个通过入口点发现的打包通道 |
| 添加自定义 MCP 服务器 | [`configuration.md#mcp-model-context-protocol`](./configuration.md#mcp-model-context-protocol) | 通过 MCP 暴露给智能体的外部工具 |
| 调整工具安全性 | [`configuration.md#security`](./configuration.md#security) | Shell 沙箱、工作区限制和 SSRF 策略 |

## 阅读策略

当你不确定该去哪里时，按以下顺序使用文档：

1. 如果终端命令或配置文件对你来说很陌生，[`start-without-technical-background.md`](./start-without-technical-background.md) 会解释安装相关的术语，并使用一个具体的提供商示例，使每次只需做一个决定。
2. [`quick-start.md`](./quick-start.md) 验证安装、配置加载和提供商访问。
3. [`concepts.md`](./concepts.md) 解释运行时模型，使后续页面更易于浏览。
4. [`provider-cookbook.md`](./provider-cookbook.md) 提供可粘贴的提供商、回退、本地模型和 Langfuse 方案。
5. 一份任务指南，例如 [`chat-apps.md`](./chat-apps.md)、[`image-generation.md`](./image-generation.md) 或 [`deployment.md`](./deployment.md)，可让一个工作流跑通。
6. 当你需要某个具体字段、默认值或高级选项时，[`configuration.md`](./configuration.md) 是事实来源。
7. [`troubleshooting.md`](./troubleshooting.md) 帮助你判断故障是与安装、配置、提供商、网关、通道还是工具相关。
