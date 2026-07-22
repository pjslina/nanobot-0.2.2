# Python SDK

将 nanobot 作为 Python 库使用。SDK 提供与 CLI 相同的 agent 运行时，但通过代码调用：模型路由、工具、工作区访问、对话历史、记忆、流式事件以及运行时辅助功能。

如果你之前用过 OpenAI SDK，最重要的区别在于：

- OpenAI SDK 调用一个模型。
- nanobot SDK 在模型周围运行一个 agent。

这意味着一次 SDK 调用可以读取文件、调用工具、保留会话历史、使用记忆、流式输出进度，并返回结构化的运行时信息。

```text
your Python code
  -> Nanobot SDK
    -> agent runtime
      -> configured model provider
      -> tools
      -> workspace
      -> session history
      -> memory
```

## 开始之前

请先安装并配置 nanobot。如果尚未完成，请遵循[快速开始](quick-start.md)并完成设置向导。对于仅使用 SDK 的 Python 环境，使用以下命令安装该包：

```bash
python -m pip install nanobot-ai
```

`Nanobot.from_config()` 会复用你常规的 `~/.nanobot/config.json` 和
`~/.nanobot/workspace/`。除非你显式覆盖，否则 Provider、模型、工具、记忆和会话行为都与 CLI 一致。关于 config 与 workspace 的区别，参见[概念：Config 与 Workspace](concepts.md#config-vs-workspace)。

在编写 SDK 代码之前，请先运行主[安装与快速开始](quick-start.md)中提到的首次运行检查：

```bash
nanobot status
```

`nanobot status` 应显示配置路径、工作区路径、当前模型或预设，以及 Provider 概要。然后发送一条真实消息：

```bash
nanobot agent -m "Hello!"
```

收到正常的助手回复意味着安装、配置、Provider/模型选择以及工作区访问均可正常使用。一旦上述步骤工作正常，SDK 应能看到相同的运行时。

## 5 分钟快速开始

### 提问一个问题

```python
import asyncio

from nanobot import Nanobot


async def main() -> None:
    async with Nanobot.from_config() as bot:
        result = await bot.run("What time is it in Tokyo?")
    print(result.content)


asyncio.run(main())
```

尽可能使用 `async with`，以便在事件循环退出前关闭工具连接和后台清理。如果你手动管理实例，请在 `finally` 块中调用 `await bot.aclose()`。

SDK 是异步优先（async-first）的，因为 agent 运行可能流式输出 token、执行工具并等待外部服务。在普通 Python 脚本中，按上面所示用 `asyncio.run(...)` 包装你的异步函数。在 notebook 或其他异步应用中，可直接从现有事件循环调用 `await bot.run(...)`。

### 查看发生了什么

`bot.run(...)` 返回一个 `RunResult`，而不仅仅是字符串：

```python
result = await bot.run("Review this repository")

print(result.content)     # final answer
print(result.tools_used)  # tools the agent used
print(result.usage)       # token usage when available
print(result.stop_reason) # why the run stopped
```

### 继续一段对话

当你希望历史跨轮次保留时，使用 `session_key`。不同的会话键之间彼此隔离：

```python
await bot.run("My name is Alice.", session_key="user:alice")
result = await bot.run("What is my name?", session_key="user:alice")

print(result.content)
```

这相当于在 SDK 中为每个用户、任务、评测用例或工作流分配各自的对话线程。

### 流式输出长回答

要获得实时输出，使用 `bot.stream(...)`：

```python
from nanobot import STREAM_EVENT_TEXT_DELTA

async for event in bot.stream("Write a migration plan"):
    if event.type == STREAM_EVENT_TEXT_DELTA:
        print(event.delta, end="", flush=True)
```

流式输出返回结构化事件，因此你还可以观察工具调用、推理片段、完成和失败。

## 完整入门脚本

在 `nanobot agent -m "Hello!"` 可正常工作后，将以下内容保存为 `sdk_demo.py`：

```python
import asyncio
import sys

from nanobot import (
    STREAM_EVENT_RUN_COMPLETED,
    STREAM_EVENT_RUN_FAILED,
    STREAM_EVENT_TEXT_DELTA,
    STREAM_EVENT_TOOL_STARTED,
    Nanobot,
)


async def main() -> None:
    prompt = " ".join(sys.argv[1:]) or "Explain what nanobot is in one paragraph."
    session_key = "sdk:demo"

    async with Nanobot.from_config() as bot:
        print(f"model: {bot.runtime.model}")
        print(f"workspace: {bot.runtime.workspace}")
        print()

        final_result = None
        async for event in bot.stream(prompt, session_key=session_key):
            if event.type == STREAM_EVENT_TEXT_DELTA:
                print(event.delta, end="", flush=True)
            elif event.type == STREAM_EVENT_TOOL_STARTED:
                print(f"\n[tool] {event.name}", flush=True)
            elif event.type == STREAM_EVENT_RUN_COMPLETED:
                final_result = event.result
            elif event.type == STREAM_EVENT_RUN_FAILED:
                raise RuntimeError(event.error or "nanobot run failed")

        print()
        if final_result is not None:
            print(f"\nstop_reason: {final_result.stop_reason}")
            print(f"tools_used: {final_result.tools_used}")
            print(f"usage: {final_result.usage}")


if __name__ == "__main__":
    asyncio.run(main())
```

运行它：

```bash
python sdk_demo.py "List the top-level files in the current workspace."
```

你应该能看到已配置的模型、工作区路径、流式输出的助手文本以及最终的运行元数据。具体回答取决于你的配置和工作区，但文件列表类提示的输出可能如下所示：

```text
model: openai/gpt-4.1-mini
workspace: /Users/alice/.nanobot/workspace

[tool] list_dir
Here are the top-level files I found...

stop_reason: completed
tools_used: ['list_dir']
usage: {'prompt_tokens': ..., 'completion_tokens': ..., 'total_tokens': ...}
```

此脚本展示了常见的生产形态：创建一个 `Nanobot`，选择稳定的 `session_key`，流式输出事件，保留最终的 `RunResult`，并由 `async with` 关闭运行时资源。

## 核心概念

| 概念 | 含义 |
|---------|---------|
| `Nanobot` | 拥有一个已配置 agent 运行时的 SDK 对象。 |
| Run（运行） | 对 `bot.run(...)`、`bot.run_streamed(...)` 或 `bot.stream(...)` 的一次调用。 |
| `session_key` | 对话历史键。复用它以继续一个线程；更改它以隔离一个线程。 |
| Workspace（工作区） | 文件工具和 shell 工具操作的本地目录。 |
| Tools（工具） | agent 可调用的能力，例如文件访问、shell、web，或来自你配置的自定义工具。 |
| Memory（记忆） | 由 nanobot 管理的长期记忆文件。 |
| Stream event（流式事件） | 一个带类型的事件，例如 `text.delta`、`tool.started` 或 `run.completed`。 |
| Model override（模型覆盖） | 用于单个 SDK 实例或单次运行的临时模型或模型预设。 |

对大多数用户而言，心智模型是：

1. 从配置创建一个 `Nanobot`。
2. 选择一个 `session_key`。
3. 调用 `run` 或 `stream`。
4. 读取 `RunResult` 或流式事件。
5. 仅在需要更多控制时使用会话/记忆/运行时辅助功能。

## 选择 SDK 还是 OpenAI 兼容 API？

nanobot 有两种编程接口：

| 用途 | 选择 | 原因 |
|-----|--------|-----|
| 在与 nanobot 同一进程中运行的 Python 代码 | Python SDK | 可直接访问 `RunResult`、会话、记忆、运行时辅助功能、钩子和流式事件。 |
| 现有的 OpenAI 兼容客户端、其他语言或独立进程 | [OpenAI 兼容 API](openai-api.md) | 通过熟悉的客户端库实现 HTTP `/v1/chat/completions` 兼容。 |

当你编写评测、notebook、基准测试运行器、产品后端、本地脚本，或需要直接控制 nanobot 的集成时，Python SDK 是最佳选择。

当你已拥有 HTTP 客户端、需要进程隔离，或需要从非 Python 服务调用 nanobot 时，OpenAI 兼容 API 是最佳选择。

## 常见模式

### 使用特定配置或工作区

当你的 agent 应在某个特定项目内工作时，设置工作区：

```python
from nanobot import Nanobot

async with Nanobot.from_config(workspace="/my/project") as bot:
    result = await bot.run("Explain the project structure")
```

当你运行多个 nanobot 实例或测试一套隔离配置时，使用自定义配置：

```python
async with Nanobot.from_config(
    config_path="./bot-a/config.json",
    workspace="./bot-a/workspace",
) as bot:
    result = await bot.run("Hello from bot A")
```

配置控制 nanobot 可以使用什么。工作区是 nanobot 为该实例存放状态的位置。多实例 CLI 与网关示例参见 [multiple-instances.md](multiple-instances.md)。

### 选择默认或按运行指定模型

创建 bot 时设置 SDK 实例的默认模型：

```python
bot = Nanobot.from_config(model="openai/gpt-4.1")
```

在不改变实例默认值的情况下为单次运行覆盖模型：

```python
result = await bot.run("Summarize this file", model="openai/gpt-4.1-mini")
```

`config.json` 中的模型预设工作方式相同：

```python
bot = Nanobot.from_config(model_preset="fast")

result = await bot.run("Think deeply about this bug", model_preset="reasoning")
```

`model` 与 `model_preset` 互斥。

首次配置时，推荐在 `config.json` 中使用命名预设。将一个 Provider 的 API 密钥与另一个 Provider 的模型 ID 混用是最常见的首次运行失败原因。关于 `provider`、`model`、`apiKey` 和 `apiBase` 的确切区别，参见 [Providers：Provider、Model、API Key 与 Base URL](providers.md#provider-model-api-key-and-base-url)。
如果某次运行在 SDK 做任何有意义的事情之前就失败了，请先用 `nanobot agent -m "Hello!"` 确认同一 Provider 和模型可正常工作。

### 使用 `session_key` 隔离对话

不同的会话键保持独立的对话历史：

```python
await bot.run("hi", session_key="user-alice")
await bot.run("hi", session_key="task-42")
```

在产品代码中使用稳定的键：

```python
session_key = f"user:{user_id}"
result = await bot.run(user_message, session_key=session_key)
```

避免为多个用户或不相关的工作流使用默认的 `"sdk:default"`。它对本地实验很方便，但稳定的产品代码应选择显式键，例如 `user:<id>`、`project:<id>` 或 `eval:<case-id>`。

### 处理失败

对于普通非流式运行，在 `bot.run(...)` 周围捕获异常，并在运行时返回结构化失败时检查 `RunResult.error`：

```python
try:
    result = await bot.run("Review this repo", session_key="project:demo")
except Exception as exc:
    print(f"SDK call failed before a result was returned: {exc}")
else:
    if result.error:
        print(f"Agent run failed: {result.error}")
    else:
        print(result.content)
```

对于流式运行，要么将流消费完毕，要么关闭它：

```python
run = await bot.run_streamed("Write a long answer", session_key="task:123")
try:
    async for event in run.stream_events():
        ...
finally:
    if not run.done:
        await run.aclose()
```

当用户按下停止按钮或在流结束前离开页面时，使用 `await run.cancel()`。

### 流式输出长时间运行的输出

当你希望获得 Cursor/OpenAI 风格的实时事件，而不是等待最终 `RunResult` 时，使用 `bot.stream()`：

```python
from nanobot import (
    STREAM_EVENT_RUN_COMPLETED,
    STREAM_EVENT_TEXT_DELTA,
    STREAM_EVENT_TOOL_STARTED,
)

async for event in bot.stream("Review this repository"):
    if event.type == STREAM_EVENT_TEXT_DELTA:
        print(event.delta, end="", flush=True)
    elif event.type == STREAM_EVENT_TOOL_STARTED:
        print(f"\nusing {event.name}")
    elif event.type == STREAM_EVENT_RUN_COMPLETED:
        print("\nfinal:", event.result.content)
```

当你还希望获得一个可以等待的句柄时，使用 `run_streamed()`：

```python
from nanobot import STREAM_EVENT_TEXT_DELTA

run = await bot.run_streamed("Write a detailed migration plan")

async for event in run.stream_events():
    if event.type == STREAM_EVENT_TEXT_DELTA:
        print(event.delta, end="", flush=True)

result = await run.wait()
```

始终要么消费流，要么调用 `await run.wait()` / `await run.text()`，要么用 `await run.cancel()` / `await run.aclose()` 关闭它。提前退出 `stream_events()` 或 `bot.stream()` 会取消底层运行，这样半消费的流不会在背压之后留下卡住的后台任务。

### 导入已有对话记录

这对评测、基准测试运行器、迁移和测试很有用。

当你已有一份对话记录并希望它成为 nanobot 会话历史时，使用 `bot.sessions.ingest()`。导入对话记录不会调用模型、执行工具、更新记忆或自动压缩。

```python
await bot.sessions.ingest(
    "eval:case-1",
    [
        {
            "role": "user",
            "content": "I graduated with a degree in Business Administration.",
            "timestamp": "2023/05/30 (Tue) 17:27",
            "source_session_id": "answer_280352e9",
        },
        {
            "role": "assistant",
            "content": "Congratulations on your degree.",
            "timestamp": "2023/05/30 (Tue) 17:27",
        },
    ],
    source="longmemeval",
)

await bot.runtime.compact_session("eval:case-1")

result = await bot.run(
    "Current Date: 2023/05/30 (Tue) 23:40\n"
    "Question: What degree did I graduate with?",
    session_key="eval:case-1",
)
print(result.content)
```

### 附加钩子以实现可观测性

钩子是一种高级逃生舱口。当你希望在不修改 nanobot 内部实现的情况下进行自定义日志、指标、追踪或输出后处理时使用它们：

```python
from nanobot.agent import AgentHook, AgentHookContext


class AuditHook(AgentHook):
    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tc in context.tool_calls:
            print(f"[tool] {tc.name}")


result = await bot.run("Review this change", hooks=[AuditHook()])
```

## 接下来去哪里

SDK 页面是编程入口。更完整的概念与配置文档仍是其周围运行时的权威参考：

| 需求 | 阅读 |
|------|------|
| 首次完成安装与配置 | [安装与快速开始](quick-start.md) |
| 配置、工作区、会话、工具和记忆的心智模型 | [概念](concepts.md) |
| Provider/模型/API 密钥/基础 URL 的匹配 | [Providers 与模型](providers.md) |
| 可直接粘贴的 Provider 配方 | [Provider 食谱](provider-cookbook.md) |
| 完整配置参考 | [配置](configuration.md) |
| 长期记忆设计 | [记忆](memory.md) |
| 使用 HTTP API 而非 Python SDK | [OpenAI 兼容 API](openai-api.md) |
| 调试安装、配置、Provider 或运行时失败 | [故障排查](troubleshooting.md) |

## API 参考

### `Nanobot.from_config(config_path=None, *, workspace=None, model=None, model_preset=None)`

从配置文件创建一个 `Nanobot` 实例。

| 参数 | 类型 | 默认值 | 描述 |
|-------|------|---------|-------------|
| `config_path` | `str \| Path \| None` | `None` | 指向 `config.json` 的路径。默认为 `~/.nanobot/config.json`。 |
| `workspace` | `str \| Path \| None` | `None` | 覆盖配置中的工作区目录。 |
| `model` | `str \| None` | `None` | 覆盖实例的默认模型。 |
| `model_preset` | `str \| None` | `None` | 覆盖实例来自 `config.json` 的默认模型预设。 |

当显式配置路径不存在时抛出 `FileNotFoundError`。
当同时提供 `model` 和 `model_preset` 时抛出 `ValueError`。

### `await bot.run(...)`

运行一次 agent 并返回一个 `RunResult`。

| 参数 | 类型 | 默认值 | 描述 |
|-------|------|---------|-------------|
| `message` | `str` | *(必填)* | 要处理的用户消息。 |
| `session_key` | `str` | `"sdk:default"` | 用于隔离对话的会话标识符。不同的键获得独立的历史。 |
| `channel` | `str` | `"cli"` | 运行时上下文中使用的逻辑通道标签。 |
| `chat_id` | `str` | `"direct"` | 运行时上下文中使用的逻辑聊天标识符。 |
| `sender_id` | `str` | `"user"` | 运行时上下文中使用的逻辑发送者标识符。 |
| `media` | `list[str] \| None` | `None` | 附加到消息的可选本地媒体路径。 |
| `ephemeral` | `bool` | `False` | 运行时不持久化该轮次，也不压缩会话历史。 |
| `hooks` | `list[AgentHook] \| None` | `None` | 仅用于本次运行的生命周期钩子。 |
| `model` | `str \| None` | `None` | 仅覆盖本次运行的模型。 |
| `model_preset` | `str \| None` | `None` | 仅覆盖本次运行的模型预设。 |

`model` 和 `model_preset` 是按运行的覆盖，不会在运行完成后改变 `bot.runtime.model`。二者互斥。

### `await bot.run_streamed(...)`

启动一个流式 agent 轮次并返回一个 `RunStream`。它接受与 `bot.run(...)` 相同的参数。

```python
run = await bot.run_streamed("Generate a long answer")

async for event in run.stream_events():
    ...

result = await run.wait()
```

### `bot.stream(...)`

围绕 `run_streamed()` 的便捷封装，用于直接迭代事件。它接受与 `bot.run(...)` 相同的参数。

```python
async for event in bot.stream("Generate a long answer"):
    ...
```

### `RunStream`

| 方法 | 描述 |
|--------|-------------|
| `stream_events()` | `StreamEvent` 对象的单消费者异步迭代器。 |
| `await wait()` | 等待运行完成并返回 `RunResult`。 |
| `await text()` | 等待运行完成并返回 `RunResult.content`。 |
| `await cancel()` | 取消运行并释放流资源。 |
| `await aclose()` | 关闭流；是 `async with` / 手动生命周期代码中等价的清理原语。 |

使用不同会话键的普通 SDK 运行可以重叠。使用按运行 `model` 或 `model_preset` 覆盖的运行在覆盖生效期间是独占的，因为当前 `AgentLoop` 的 Provider/模型状态是可变的。

### `StreamEvent`

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `type` | `StreamEventType` | 事件类型，例如 `text.delta` 或 `run.completed`。 |
| `delta` | `str` | 增量的文本或推理片段。 |
| `content` | `str` | 完成的文本片段或最终内容。 |
| `result` | `RunResult \| None` | 在 `run.completed` 时存在。 |
| `name` | `str \| None` | 工具事件对应的工具名称。 |
| `tool_call_id` | `str \| None` | 可用时由 Provider 提供的工具调用 id。 |
| `arguments` | `dict \| None` | 可用时工具的参数。 |
| `iteration` | `int \| None` | 可用时 agent 循环的迭代次数。 |
| `resuming` | `bool \| None` | 某段文本是否在更多工具工作之前结束。 |
| `usage` | `dict[str, int]` | 完成事件上的 token 用量。 |
| `error` | `str \| None` | 失败事件上的错误文本。 |
| `metadata` | `dict` | 额外的事件元数据。 |

尽可能使用导出的常量而不是硬编码字符串：

| 常量 | 值 |
|----------|-------|
| `STREAM_EVENT_RUN_STARTED` | `run.started` |
| `STREAM_EVENT_TEXT_DELTA` | `text.delta` |
| `STREAM_EVENT_TEXT_COMPLETED` | `text.completed` |
| `STREAM_EVENT_REASONING_DELTA` | `reasoning.delta` |
| `STREAM_EVENT_REASONING_COMPLETED` | `reasoning.completed` |
| `STREAM_EVENT_TOOL_STARTED` | `tool.started` |
| `STREAM_EVENT_TOOL_COMPLETED` | `tool.completed` |
| `STREAM_EVENT_TOOL_FAILED` | `tool.failed` |
| `STREAM_EVENT_RUN_COMPLETED` | `run.completed` |
| `STREAM_EVENT_RUN_FAILED` | `run.failed` |

`STREAM_EVENT_TYPES` 包含所有稳定的 v1 事件值。

### `await bot.aclose()`

释放 SDK 实例持有的资源，包括工具连接。异步上下文管理器会自动调用它：

```python
async with Nanobot.from_config() as bot:
    result = await bot.run("Summarize this repo")
```

### `RunResult`

| 字段 | 类型 | 描述 |
|-------|------|-------------|
| `content` | `str` | agent 的最终文本响应。 |
| `tools_used` | `list[str]` | 运行期间使用的工具名称。 |
| `messages` | `list[dict]` | 运行产生的最终消息列表。 |
| `usage` | `dict[str, int]` | 运行时报告或估算的 token 用量。 |
| `stop_reason` | `str \| None` | 运行停止的原因，例如 `"completed"` 或 `"max_iterations"`。 |
| `error` | `str \| None` | 运行在 agent 运行时内部失败时的错误文本。 |
| `metadata` | `dict` | 出站元数据，例如延迟。 |

## 会话、记忆与运行时辅助功能

### `bot.sessions`

| 方法 | 描述 |
|--------|-------------|
| `await ingest(session_key, messages, metadata=None, source=None, save=True)` | 导入已有对话记录消息而不运行模型。 |
| `get(session_key)` | 返回一个 `SessionSnapshot`，若不存在则返回 `None`。 |
| `list()` | 返回紧凑的 `SessionInfo` 行。 |
| `export(session_key)` | 返回适合 JSON 序列化的完整 `SessionSnapshot`。 |
| `clear(session_key)` | 清空并持久化一个会话。 |
| `delete(session_key)` | 从磁盘和缓存中删除一个会话。 |
| `flush()` | 将缓存的会话刷新到持久存储。 |

导入的消息必须包含 `role` 和 `content`。角色可以是 `user`、`assistant`、`tool` 或 `system`。其他字段，例如 `timestamp`、`source_session_id` 或 `source_date`，将作为消息元数据持久化。

### `bot.memory`

| 方法 | 描述 |
|--------|-------------|
| `read()` | 读取 `memory/MEMORY.md`。 |
| `write(text)` | 覆盖 `memory/MEMORY.md`。 |
| `append_history(text, session_key=None)` | 追加一条 `memory/history.jsonl` 条目并返回其游标。 |
| `read_history(session_key=None)` | 读取记忆历史条目，可按会话键过滤。 |

### `bot.runtime`

| 方法 / 属性 | 描述 |
|-------------------|-------------|
| `model` | 当前运行时模型名称。 |
| `workspace` | 当前运行时工作区路径。 |
| `await compact_session(session_key)` | 对一个会话执行 token/回放窗口合并。 |
| `await compact_idle_session(session_key, max_suffix=8)` | 执行空闲会话压缩并返回其摘要。 |

## 钩子

钩子让你可以观察或自定义 agent 循环。继承 `AgentHook` 并覆盖你需要的方法。

### 钩子生命周期

| 方法 | 时机 |
|--------|------|
| `wants_streaming()` | 当你希望获得逐 token 的 `on_stream()` 回调时返回 `True` |
| `before_iteration(context)` | 每次调用 LLM 之前 |
| `on_stream(context, delta)` | 启用流式时，在每个流式 token 上 |
| `on_stream_end(context, *, resuming)` | 流式结束时 |
| `before_execute_tools(context)` | 执行工具之前 |
| `after_iteration(context)` | 每次迭代之后 |
| `finalize_content(context, content)` | 转换最终输出文本 |

`AgentHookContext` 上的有用字段包括：

- `iteration`
- `messages`
- `response`
- `usage`
- `tool_calls`
- `tool_results`
- `tool_events`
- `final_content`
- `stop_reason`
- `error`

### 示例：审计工具调用

```python
from nanobot.agent import AgentHook, AgentHookContext


class AuditHook(AgentHook):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        for tc in context.tool_calls:
            self.calls.append(tc.name)
            print(f"[audit] {tc.name}({tc.arguments})")
```

```python
hook = AuditHook()
result = await bot.run("List files in /tmp", hooks=[hook])
print(result.content)
print(f"Tools observed: {hook.calls}")
```

### 示例：接收流式 token

```python
from nanobot.agent import AgentHook, AgentHookContext


class StreamingHook(AgentHook):
    def wants_streaming(self) -> bool:
        return True

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        print(delta, end="", flush=True)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        print()
```

### 组合多个钩子

当你希望组合多种行为时传入多个钩子：

```python
result = await bot.run("hi", hooks=[AuditHook(), MetricsHook()])
```

异步钩子方法是带错误隔离的扇出。`finalize_content` 是一个管道：每个钩子接收前一个钩子的输出。

### 示例：后处理最终内容

```python
from nanobot.agent import AgentHook


class Censor(AgentHook):
    def finalize_content(self, context, content):
        return content.replace("secret", "***") if content else content
```

## 完整示例

```python
import asyncio
import time

from nanobot import Nanobot
from nanobot.agent import AgentHook, AgentHookContext


class TimingHook(AgentHook):
    def __init__(self) -> None:
        super().__init__()
        self._started_at = 0.0

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._started_at = time.perf_counter()

    async def after_iteration(self, context: AgentHookContext) -> None:
        elapsed_ms = (time.perf_counter() - self._started_at) * 1000
        print(f"[timing] iteration {context.iteration} took {elapsed_ms:.1f}ms")


async def main() -> None:
    async with Nanobot.from_config(workspace="/my/project") as bot:
        result = await bot.run(
            "Explain the main function",
            session_key="sdk:demo",
            hooks=[TimingHook()],
        )
    print(result.content)


asyncio.run(main())
```
