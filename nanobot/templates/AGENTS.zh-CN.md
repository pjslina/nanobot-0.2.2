# Agent 指令

## 工作区指引

将项目特定的偏好、常用的工作流程约定，以及希望 agent 为该工作区记住的指令放在本文件中。用户的持久事实信息放在 `USER.md`，个性/风格指引放在 `SOUL.md`，长期记忆放在 `memory/MEMORY.md`。

## 计划提醒

- 在安排提醒之前，先检查可用技能并遵循技能指引。
- 使用内置的 `cron` 工具来创建/列出/删除任务（不要通过 `exec` 调用 `nanobot cron`）。
- 从当前会话中获取 USER_ID 和 CHANNEL（例如，从 `telegram:8281248569` 中获取 `8281248569` 和 `telegram`）。

**不要仅把提醒写到 MEMORY.md** - 那样不会触发实际通知。

## 心跳任务

当 `gateway.heartbeat.enabled` 为 true 时，`nanobot gateway` 会注册一个受保护的心跳 cron 任务，它会周期性地检查 `HEARTBEAT.md`。除非用户已禁用内置任务并明确希望使用自定义调度，否则不要创建重复的心跳任务。

- 使用 `apply_patch` 进行常规任务列表更新，尤其是在添加、删除或修改多行时。
- 仅在从当前 `HEARTBEAT.md` 复制的小范围精确替换时使用 `edit_file`。
- 首次创建或有意的整文件重写时使用 `write_file`。

当用户要求执行循环/周期性心跳任务时，更新 `HEARTBEAT.md`，而不是创建一次性提醒。对于不应属于心跳任务列表的单独提醒或自定义调度，请使用内置的 `cron` 工具。
