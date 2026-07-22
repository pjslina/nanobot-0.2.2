本文件为使用该仓库的 AI 编码代理提供指导。

## 项目概览

nanobot 是一个轻量级的开源 AI 代理框架，使用 Python 编写，并配备 React/TypeScript WebUI。其核心是一个小型代理循环（agent loop），该循环从聊天渠道接收消息，调用 LLM provider，执行工具，并管理会话记忆。

## 开发命令

```bash
# Python: run single test / lint
pytest tests/test_openai_api.py::test_function -v
ruff check nanobot/

# WebUI: dev server (proxies API/WS to gateway :8765), build, test
# Build outputs to ../nanobot/web/dist (bundled into the Python wheel)
cd webui && bun run dev      # or NANOBOT_API_URL=... bun run dev
cd webui && bun run build
cd webui && bun run test

# Gateway
nanobot gateway
```

## 高层架构

### 核心数据流

消息通过一个异步 `MessageBus`（`nanobot/bus/queue.py`）流动，该总线将聊天渠道与代理核心解耦：

1. **渠道（Channels）**（`nanobot/channels/`）从外部平台接收消息，并将 `InboundMessage` 事件发布到总线。
2. **`AgentLoop`**（`nanobot/agent/loop.py`）消费入站消息，构建上下文，并协调本轮对话。
3. **`AgentRunner`**（`nanobot/agent/runner.py`）处理实际的 LLM 对话循环：向 provider 发送消息，接收工具调用，执行工具，并流式返回响应。
4. 响应作为 `OutboundMessage` 事件发布回相应的渠道。

### 关键子系统

- **代理循环（Agent Loop）**（`nanobot/agent/loop.py`、`runner.py`）：核心处理引擎。`AgentLoop` 管理会话密钥、钩子（hooks）和上下文构建。`AgentRunner` 执行带工具调用的多轮 LLM 对话。
- **LLM Provider**（`nanobot/providers/`）：基于公共基类（`base.py`）构建的 provider 实现（Anthropic、OpenAI 兼容、OpenAI Responses API、Azure、Bedrock、GitHub Copilot、OpenAI Codex 等）。包含图像生成（`image_generation.py`）和音频转录（`transcription.py`）。`factory.py` 和 `registry.py` 负责实例化和模型发现。
- **渠道（Channels）**（`nanobot/channels/`）：平台集成（Telegram、Discord、Slack、Feishu、Matrix、WhatsApp、QQ、WeChat、WeCom、DingTalk、Email、MoChat、MS Teams、WebSocket）。`manager.py` 负责发现和协调它们。渠道通过 `pkgutil` 扫描 + entry-point 插件自动发现。
- **工具（Tools）**（`nanobot/agent/tools/`）：暴露给 LLM 的代理能力：文件系统（读/写/编辑/列表）、shell 执行（带沙箱后端）、网页搜索/抓取、MCP 服务器、cron、notebook 编辑、子代理（subagent）派生、长时间运行任务/持续目标（`long_task.py`）、图像生成以及自我修改。工具通过 `pkgutil` 扫描 + entry-point 插件自动发现。
- **记忆（Memory）**（`nanobot/agent/memory.py`）：会话历史持久化，带有 Dream 两阶段记忆合并。使用带 fsync 的原子写入以保证持久性。
- **会话管理（Session Management）**（`nanobot/session/`）：按会话的历史记录、上下文压缩、基于 TTL 的自动压缩（`manager.py`）以及持续目标状态跟踪（`goal_state.py`）。
- **配置（Config）**（`nanobot/config/schema.py`、`loader.py`）：基于 Pydantic 的配置，从 `~/.nanobot/config.json` 加载。支持 camelCase 别名以兼容 JSON。
- **桥接（Bridge）**（`bridge/`）：TypeScript 服务（例如 WhatsApp bridge），通过 `pyproject.toml` 的 `force-include` 打包进 wheel。
- **WebUI**（`webui/`）：基于 Vite 的 React 单页应用（SPA），通过 WebSocket 多路复用协议与网关通信。开发服务器将 `/api`、`/webui`、`/auth` 以及 WebSocket 流量代理到网关。
- **API 服务器（API Server）**（`nanobot/api/server.py`）：兼容 OpenAI 的 HTTP API（`/v1/chat/completions`、`/v1/models`），用于编程式访问。
- **命令路由器（Command Router）**（`nanobot/command/`）：斜杠命令路由和内置命令处理器。
- **心跳（Heartbeat）**（`nanobot/templates/HEARTBEAT.md`）：通过 `cron` 作业检查的周期性任务列表（旧版专用服务已移除）。
- **配对（Pairing）**（`nanobot/pairing/`）：私信发送者审批存储，每个渠道带有持久化配对码。
- **技能（Skills）**（`nanobot/skills/`）：内置技能定义（long-goal、cron、github、image-generation 等），加载到代理上下文中。
- **安全（Security）**（`nanobot/security/`）：PTH 文件防护及其他在 CLI 入口激活的安全措施。

### 入口点

- **CLI**：`nanobot/cli/commands.py`
- **Python SDK**：`nanobot/nanobot.py`

## 项目特定说明

- 架构约束：[`.agent/design.md`](.agent/design.md)
- 安全边界：[`.agent/security.md`](.agent/security.md)
- 常见陷阱：[`.agent/gotchas.md`](.agent/gotchas.md)

## 贡献流程

关于贡献流程和 PR 规范，请参见 [`CONTRIBUTING.md`](./CONTRIBUTING.md)。

## 代码风格

- Python 3.11+，全程使用 asyncio。
- 行长度：100。
- 代码检查：`ruff`，规则 E、F、I、N、W（忽略 E501）。
- pytest，`asyncio_mode = "auto"`。

## 常见文件位置

- 配置模式：`nanobot/config/schema.py`
- Provider 基类/新 provider 模板：`nanobot/providers/base.py`
- 渠道基类/新渠道模板：`nanobot/channels/base.py`
- 工具注册表：`nanobot/agent/tools/registry.py`
- WebUI 开发代理配置：`webui/vite.config.ts`
- 测试镜像 `nanobot/` 包结构。
