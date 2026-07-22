# nanobot 的记忆

nanobot 的记忆建立在一个简单的信念之上：记忆应当显得鲜活，但不应当显得混乱。

好的记忆不是一堆杂乱的笔记。它是一种安静的注意力系统。它留意值得保留的内容，放下不再需要关注的事物，将经历过的体验转化为平静、持久且有用的东西。

这就是 nanobot 记忆的形态。

## 设计

nanobot 不会把记忆当作一个巨大的文件来处理。

它将记忆分层，因为不同种类的记忆需要不同的工具：

- `session.messages` 保存活跃的短期对话。
- `memory/history.jsonl` 是压缩后的历史轮次的滚动归档。
- `SOUL.md`、`USER.md` 和 `memory/MEMORY.md` 是持久的知识文件。
- `GitStore` 记录这些持久文件如何随时间变化。

这使得系统在当下保持轻量，又能随着时间进行反思。

## 流程

记忆在 nanobot 中分两个阶段流动。

### 阶段 1：Consolidator

当对话增长到足以对上下文窗口造成压力时，nanobot 不会试图永久保留每一条旧消息。

相反，`Consolidator` 会总结对话中最早的安全切片，并将该摘要追加到 `memory/history.jsonl`。

这个文件是：

- 只追加的（append-only）
- 基于游标（cursor）的
- 优先为机器消费而优化，其次为人工检查

每一行都是一个 JSON 对象：

```json
{"cursor": 42, "timestamp": "2026-04-03 00:02", "content": "- User prefers dark mode\n- Decided to use PostgreSQL"}
```

它不是最终的记忆。它是塑造最终记忆的原材料。

### 阶段 2：Dream

`Dream` 是更慢、更深思熟虑的层。它默认按 cron 计划运行，也可以手动触发。

Dream 读取：

- `memory/history.jsonl` 中的新条目
- 当前的 `SOUL.md`
- 当前的 `USER.md`
- 当前的 `memory/MEMORY.md`

然后它在一次处理中精准地编辑长期文件——不是重写所有内容，而是做出保持记忆连贯的最小、最诚实的改动。

这就是为什么 nanobot 的记忆不仅仅是归档，它是解释性的。

## 文件

```text
workspace/
├── SOUL.md              # The bot's long-term voice and communication style
├── USER.md              # Stable knowledge about the user
└── memory/
    ├── MEMORY.md        # Project facts, decisions, and durable context
    ├── history.jsonl    # Append-only history summaries
    ├── .cursor          # Consolidator write cursor
    ├── .dream_cursor    # Dream consumption cursor
    └── .git/            # Version history for long-term memory files
```

这些文件扮演不同的角色：

- `SOUL.md` 记住 nanobot 应该如何发声。
- `USER.md` 记住用户是谁以及他们偏好什么。
- `MEMORY.md` 记住关于工作本身仍然成立的事实。
- `history.jsonl` 记住一路走来发生了什么。

## 为什么用 `history.jsonl`

旧的 `HISTORY.md` 格式便于随手阅读，但作为运行基础它过于脆弱。

`history.jsonl` 给 nanobot 带来了：

- 稳定的增量游标
- 更安全的机器解析
- 更容易的批处理
- 更清晰的迁移与压缩
- 原始历史与精心整理的知识之间更好的边界

你仍然可以用熟悉的工具搜索它：

```bash
# grep
grep -i "keyword" memory/history.jsonl

# jq
cat memory/history.jsonl | jq -r 'select(.content | test("keyword"; "i")) | .content' | tail -20

# Python
python -c "import json; [print(json.loads(l).get('content','')) for l in open('memory/history.jsonl','r',encoding='utf-8') if l.strip() and 'keyword' in l.lower()][-20:]"
```

这既是技术上的差异，也是理念上的差异：

- `history.jsonl` 用于结构
- `SOUL.md`、`USER.md` 和 `MEMORY.md` 用于意义

## 命令

记忆并不隐藏在幕布之后。用户可以检查和引导它。

| 命令 | 作用 |
|---------|--------------|
| `/dream` | 立即运行 Dream |
| `/dream-log` | 显示最新的 Dream 记忆变更 |
| `/dream-log <sha>` | 显示某次特定的 Dream 变更 |
| `/dream-restore` | 列出最近的 Dream 记忆版本 |
| `/dream-restore <sha>` | 将记忆恢复到某次特定变更之前的状态 |

这些命令的存在是有原因的：自动记忆功能强大，但用户应始终保留检查、理解和恢复它的权利。

## 版本化记忆

在 Dream 修改长期记忆文件后，nanobot 可以用 `GitStore` 记录该变更。

这赋予记忆自身的版本历史：

- 你可以检查发生了什么变更
- 你可以比较版本
- 你可以恢复之前的状态

这将记忆从一种静默的改动变成了一个可审计的过程。

## 配置

Dream 在 `agents.defaults.dream` 下配置：

```json
{
  "agents": {
    "defaults": {
      "dream": {
        "intervalH": 2,
        "modelOverride": null,
        "maxBatchSize": 20,
        "maxIterations": 10
      }
    }
  }
}
```

| 字段 | 含义 |
|-------|---------|
| `intervalH` | Dream 运行的频率，以小时为单位 |
| `cron` | cron 表达式覆盖（优先于 `intervalH`） |
| `modelOverride` | 可选的 Dream 专用模型覆盖*（待实现）* |
| `maxBatchSize` | *（已弃用 - 不再使用）* |
| `maxIterations` | *（已弃用 - 不再使用）* |

实际而言：

- `intervalH` 是配置 Dream 频率的常规方式。在内部它以 `every` 计划运行。
- `cron` 在设置时覆盖 `intervalH`，允许使用精确的 cron 表达式（例如 `0 */4 * * *`）。
- `modelOverride` 保留给未来版本使用。当前 Dream 使用与主 agent 相同的模型。
- `maxBatchSize` 和 `maxIterations` 为保持配置兼容性而保留，但不再影响行为。

## 实际使用

在日常使用中，这意味着：

- 对话可以保持快速，而无需承载无限上下文
- 持久事实会随着时间变得更清晰，而不是更嘈杂
- 用户可以在需要时检查和恢复记忆

记忆不应感觉像一个垃圾堆。它应当感觉像一种延续。

这正是本设计试图守护的东西。
