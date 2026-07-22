---
name: memory
description: 由 Dream 管理的知识文件组成的双层记忆系统。
always: true
---

# 记忆

## 结构

- `SOUL.md` - 机器人个性与沟通风格。**由 Dream 管理。**请勿编辑。
- `USER.md` - 用户资料与偏好。**由 Dream 管理。**请勿编辑。
- `memory/MEMORY.md` - 长期事实（项目上下文、重要事件）。**由 Dream 管理。**请勿编辑。
- `memory/history.jsonl` - 仅追加的 JSONL，不加载到上下文中。优先使用内置的 `grep` 工具来搜索它。

## 搜索过往事件

`memory/history.jsonl` 是 JSONL 格式 - 每一行是一个包含 `cursor`、`timestamp`、`content` 的 JSON 对象。

- 对于广泛搜索，先从 `grep(..., path="memory", glob="*.jsonl", output_mode="count")` 或默认的 `files_with_matches` 模式开始，然后再扩展到完整内容
- 当你需要精确的匹配行时，使用 `output_mode="content"` 加上 `context_before` / `context_after`
- 对字面时间戳或 JSON 片段使用 `fixed_strings=true`
- 使用 `head_limit` / `offset` 翻阅漫长的历史记录
- 仅当内置搜索无法表达你的需求时，才将 `exec` 作为最后的兜底手段

示例（替换 `keyword`）：
- `grep(pattern="keyword", path="memory/history.jsonl", case_insensitive=true)`
- `grep(pattern="2026-04-02 10:00", path="memory/history.jsonl", fixed_strings=true)`
- `grep(pattern="keyword", path="memory", glob="*.jsonl", output_mode="count", case_insensitive=true)`
- `grep(pattern="oauth|token", path="memory", glob="*.jsonl", output_mode="content", case_insensitive=true)`

## 重要事项

- **请勿编辑 SOUL.md、USER.md 或 MEMORY.md。**它们由 Dream 自动管理。
- 如果你注意到过时的信息，它会在 Dream 下次运行时被更正。
- 用户可以使用 `/dream-log` 命令查看 Dream 的活动。
