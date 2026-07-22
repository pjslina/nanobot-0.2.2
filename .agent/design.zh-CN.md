# 设计约束

这些规则约束架构决策。在添加功能或修复缺陷时，应优先选择遵守这些边界的路径。

## 核心保持精简；在边缘扩展

新功能应通过 `channels/`、`tools/`、skills 或 MCP 服务器添加。文件 `agent/loop.py` 和 `agent/runner.py` 构成关键核心路径；对其改动应尽量少且需有正当理由。如果某个功能可以放在 channel 适配器、工具或外部 MCP 服务器中，就不应内联到 agent 循环中。

运行时状态的分发遵循同一边界。`AgentLoop` 可以从 `nanobot.bus.runtime_events` 发布通用的运行时事件，用于 turn/run/model/goal 状态变更，但 WebUI/WebSocket 的传输细节（如 `_turn_end`、`_goal_status`、标题刷新和 goal 状态同步）应归属 `nanobot.session.webui_turns.WebuiTurnCoordinator` 或相应的 channel 适配器。

## 结构更少，智能更多

优先采用简单、可读的代码，而非新的框架层和间接层。仅在结构能消除真实复杂性、保护重要边界或符合既有本地模式时才添加结构。最佳修复往往是更精简的 prompt、更严格的工具契约、channel 内部的局部改动，或一个聚焦的回归测试。

## 宁可重复，不要过早抽象

Channels 和 providers 允许重复类似逻辑（发送重试、媒体处理、消息拆分）。不要为了消除 channel 文件间的重复而引入复杂的基类或共享助手函数。每个 channel 文件应保持自包含且独立可读。provider 实现同样适用此原则。

## 用最小改动解决真实问题

修复缺陷时只改动必要的部分。不要将无关的重构或清理捆绑到功能或缺陷修复 PR 中。如果确实需要重构，应作为单独的、范围清晰的 PR。

## 保持 PR 可审查

缺陷修复应使受保护的不变式清晰明确，只改动强制该不变式的最小代码面，并仅添加最贴近的回归测试。如果某个 diff 开始改变归属边界或将行为变更与清理混在一起，应在其变得难以审查前拆分。

## 显式优于魔法

配置必须在 `config/schema.py` 的 Pydantic 模型中显式声明。错误处理应抛出明确的异常，而非静默修正错误输入。Provider 自动检测存在，但每条解析路径都必须可从 factory 追溯到具体的 provider 类。
