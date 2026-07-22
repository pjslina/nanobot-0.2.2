# nanobot 技能

本目录包含扩展 nanobot 能力的内置技能。

## 技能格式

每个技能是一个目录，其中包含一个 `SKILL.md` 文件，内容包括：
- YAML frontmatter（name、description、metadata）
- 给 Agent 的 Markdown 指令

当技能引用大量本地文档或日志时，在加载完整文件之前，优先使用 nanobot 内置的
`grep` 工具缩小搜索范围。
先使用 `grep(output_mode="count")` / `files_with_matches` 进行广泛搜索，
使用 `head_limit` / `offset` 分页遍历大型结果集，
并使用 `grep(glob="*.md")` 按文件名模式过滤。

## 版权归属

这些技能改编自 [OpenClaw](https://github.com/openclaw/openclaw) 的技能系统。
技能格式和元数据结构遵循 OpenClaw 的约定，以保持兼容性。

## 可用技能

| 技能 | 描述 |
|-------|-------------|
| `github` | 使用 `gh` CLI 与 GitHub 交互 |
| `weather` | 使用 wttr.in 和 Open-Meteo 获取天气信息 |
| `summarize` | 总结 URL、文件和 YouTube 视频 |
| `tmux` | 远程控制 tmux 会话 |
| `clawhub` | 从 ClawHub 注册表搜索并安装技能 |
| `skill-creator` | 创建新技能 |
| `long-goal` | 持续目标：`long_task`、`complete_goal`、幂等目标、模块化项目工作、前期调研 |
