# My Tool

让 agent 感知并调整自身的运行时状态——就像问同事一句"你忙吗？能换个更大的显示器吗？"

## 为什么需要它

普通工具让 agent 操作外部世界（读写文件、搜索代码）。但 agent 对自己一无所知——它不知道自己运行在哪个模型上，不知道还剩多少次迭代，也不知道已经消耗了多少 token。

My tool 填补了这一空白。借助它，agent 可以：

- **知道自己是做什么的**：我在用哪个模型？我的工作区在哪？还剩多少次迭代？
- **即时调整**：任务复杂？扩展上下文窗口。简单聊天？切换到更快的模型。
- **跨轮次记忆**：把笔记存进你的 scratchpad（草稿本），保留到下一轮对话。

## Configuration

默认启用（只读模式）。agent 可以查看自身状态但不能修改。

```yaml
tools:
  my:
    enable: true       # default: true
    allow_set: false   # default: false (read-only)
```

若要允许 agent 修改自身配置（例如切换模型、调整参数），请设置 `tools.my.allow_set: true`。

旧的 `tools.myEnabled` / `tools.mySet` 键会在加载时自动迁移，并在下一次 `nanobot onboard` 刷新配置时原地改写。

所有修改仅保存在内存中——重启后恢复默认值。

---

## check - 查看"my"的当前状态

不带参数时，返回关键配置概览：

```text
my(action="check")
# -> max_iterations: 40
#   context_window_tokens: 200000
#   model: 'anthropic/claude-sonnet-4-20250514'
#   workspace: PosixPath('/tmp/workspace')
#   provider_retry_mode: 'standard'
#   max_tool_result_chars: 16000
#   _current_iteration: 3
#   _last_usage: {'prompt_tokens': 45000, 'completion_tokens': 8000}
#   Note: prompt_tokens is cumulative across all turns, not current context window occupancy.
```

带 key 参数时，可深入查看某项具体配置：

```text
my(action="check", key="_last_usage.prompt_tokens")
# -> How many prompt tokens I've used so far

my(action="check", key="model")
# -> What model I'm currently running on

my(action="check", key="web_config.enable")
# -> Whether web search is enabled
```

### 你可以用它做什么

| 场景 | 方式 |
|----------|-----|
| "你在用哪个模型？" | `check("model")` |
| "当前激活的是哪个模型预设？" | `check("model_preset")` |
| "你还能再调用多少次工具？" | `check("max_iterations")` 减去 `check("_current_iteration")` |
| "这次对话用了多少 token？" | `check("_last_usage")`——跨所有轮次累计 |
| "你的工作目录在哪？" | `check("workspace")` |
| "给我看看你的完整配置" | `check()` |
| "有没有子 agent 在运行？" | `check("subagents")`——显示阶段、迭代、已用时、工具事件 |

---

## set - 运行时调优

修改立即生效，无需重启。

```text
my(action="set", key="max_iterations", value=80)
# -> Bump iteration limit from 40 to 80

my(action="set", key="model_preset", value="fast")
# -> Switch to a configured model preset

my(action="set", key="model", value="fast-model")
# -> Switch to a raw model and clear the active preset

my(action="set", key="context_window_tokens", value=262144)
# -> Expand context window for long documents
```

你也可以在 scratchpad 中存储自定义状态：

```text
my(action="set", key="current_project", value="nanobot")
my(action="set", key="user_style_preference", value="concise")
my(action="set", key="task_complexity", value="high")
# -> These values persist into the next conversation turn
```

### 受保护参数

这些参数有类型和范围校验——无效值会被拒绝：

| 参数 | 类型 | 范围 | 用途 |
|-----------|------|-------|---------|
| `max_iterations` | int | 1–100 | 每个对话轮次的最大工具调用次数 |
| `context_window_tokens` | int | 4,096–1,000,000 | 上下文窗口大小 |
| `model` | str | 非空 | 要使用的 LLM 模型 |
| `model_preset` | str | 已配置的预设名 | 要使用的命名预设 |

其他参数（例如 `workspace`、`provider_retry_mode`、`max_tool_result_chars`）可以自由设置，只要值是 JSON 安全的即可。

---

## 实际场景

### "这个任务很复杂，我需要更多空间"

```text
Agent: This codebase is large, let me expand my context window to handle it.
-> my(action="set", key="context_window_tokens", value=262144)
```

### "简单问题，别浪费算力"

```text
Agent: This is a straightforward question, let me switch to the fast preset.
-> my(action="set", key="model_preset", value="fast")
```

### "跨轮次记住用户偏好"

```text
Turn 1: my(action="set", key="user_prefers_concise", value=True)
Turn 2: my(action="check", key="user_prefers_concise")
# -> True (still remembers the user likes concise replies)
```

### "自诊"

```text
User: "Why aren't you searching the web?"
Agent: Let me check my web config.
-> my(action="check", key="web_config.enable")
# -> False
Agent: Web search is disabled - please set web.enable: true in your config.
```

### "Token 预算管理"

```text
Agent: Let me check how much budget I have left.
-> my(action="check", key="_last_usage")
# -> {"prompt_tokens": 45000, "completion_tokens": 8000}
Agent: I've used ~53k tokens total so far. I'll keep my remaining replies concise.
```

### "子 agent 监控"

```text
Agent: Let me check on the background tasks.
-> my(action="check", key="subagents")
# -> 2 subagent(s):
#   [task-1] 'Code review'
#     phase: running, iteration: 5, elapsed: 12.3s
#     tools: read(✓), grep(✓)
#     usage: {'prompt_tokens': 8000, 'completion_tokens': 1200}
#   [task-2] 'Write tests'
#     phase: pending, iteration: 0, elapsed: 0.2s
#     tools: none
Agent: The code review is progressing well. The test task hasn't started yet.
```

---

## 安全机制

核心设计原则：**所有修改仅存在于内存中。重启即恢复默认值。** agent 无法造成持久性损害。

### 禁止访问（BLOCKED）

不可查看或修改——完全隐藏：

| 类别 | 属性 | 原因 |
|----------|-----------|--------|
| 核心基础设施 | `bus`、`provider`、`_running` | 修改会导致系统崩溃 |
| 工具注册表 | `tools` | 不得移除自身工具 |
| 子系统 | `runner`、`sessions`、`consolidator` 等 | 会影响其他用户/会话 |
| 敏感数据 | `_mcp_servers`、`_pending_queues` 等 | 包含凭据和消息路由 |
| 安全边界 | `restrict_to_workspace`、`channels_config` | 绕过将违反隔离 |
| Python 内部 | `__class__`、`__dict__` 等 | 防止沙箱逃逸 |

### 只读（仅可 check）

可查看但不可设置：

| 类别 | 属性 | 原因 |
|----------|-----------|--------|
| 子 agent 管理器 | `subagents` | 可观察，但替换会破坏系统 |
| 执行配置 | `exec_config` | 可查看沙箱/启用状态，不能修改 |
| Web 配置 | `web_config` | 可查看启用状态，不能修改 |
| 迭代计数器 | `_current_iteration` | 仅由 runner 更新 |

### 敏感字段保护

匹配敏感名称（`api_key`、`password`、`secret`、`token` 等）的子字段在 check 和 set 中均被阻止，无论其父路径为何。这可防止通过点路径遍历泄露凭据（例如 `web_config.search.api_key`）。
