# 常见陷阱

## 不要使用 `ruff format`

`CONTRIBUTING.md` 提到了 `ruff format`，但**不要运行它**——它会破坏 git blame 历史。只应使用 `ruff check`。

## 配置中的 `${VAR}` 引用

`config/loader.py` 在加载时解析 `config.json` 中的 `${VAR}` 模式。这**不是**类似 shell 的默认值语法。如果环境变量缺失，`load_config` 会抛出 `ValueError`，agent 会回退到默认配置。

有效用法示例：
```json
{ "providers": { "openrouter": { "apiKey": "${OPENROUTER_KEY}" } } }
```

## Windows 兼容性

nanobot 明确支持 Windows。需注意的关键差异：
- `ExecTool` 在 Windows 上使用 `cmd /c` 而非 `sh -c`（`shell.py`）。
- `cli/commands.py` 在启动时强制 `sys.stdout`/`stderr` 为 UTF-8，以处理 emoji 和多语言输入。
- MCP stdio 服务器命令会针对 Windows 路径分隔符进行标准化（`mcp.py`）。
- 路径操作始终使用 `pathlib.Path`；不要假设使用 `/` 分隔符。

## Prompt 模板

Agent 的系统 prompt 和特定场景指令以 Jinja2 markdown 文件形式存放在 `nanobot/templates/`（`identity.md`、`platform_policy.md`、`HEARTBEAT.md`、`SOUL.md` 等）。修改这些文件会像修改 Python 代码一样直接改变 agent 行为。它们由 `utils/prompt_templates.py` 加载。

工具描述、skills 和回放的会话历史也会塑造模型行为。应将这些表面的改动视同运行时代码：保持改动范围狭窄，尽量添加聚焦的回归测试，并避免教导模型重复内部标记、本地路径或工具调用文本。

## 上下文污染会持续存在

写入 memory、会话历史或 prompt 输入的任何内容都可能被回放到未来的 LLM 调用中。时间戳、本地媒体路径、工具调用回显和原始回退转储等元数据在被作为模型模仿的示例之前，必须加以限制和清理。

## Skills 作为扩展点

内置 skills 位于 `nanobot/skills/`（markdown + YAML frontmatter 格式）。属于"知识"而非代码的 agent 能力应作为 skills 添加，而不是硬编码到 agent 循环中。外部 skills 可发布到 ClawHub 并从中安装。

## 会话的原子写入

`agent/memory.py` 以原子方式写入 `history.jsonl`（临时文件 + fsync + 重命名 + 目录 fsync）。这保证了崩溃时的持久性。不要用普通的 `open(..., "w")` 写入替换此方式。
