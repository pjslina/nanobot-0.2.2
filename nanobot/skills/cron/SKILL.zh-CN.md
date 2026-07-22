---
name: cron
description: 调度提醒和重复任务。
---

# Cron

使用 `cron` 工具调度提醒或重复任务。

## 三种模式

1. **提醒（Reminder）** - 消息直接发送给用户
2. **任务（Task）** - 消息是任务描述，agent 执行并发送结果
3. **一次性（One-time）** - 在特定时间运行一次，然后自动删除

## 示例

固定提醒：
```
cron(action="add", message="Time to take a break!", every_seconds=1200)
```

动态任务（agent 每次执行）：
```
cron(action="add", message="Check HKUDS/nanobot GitHub stars and report", every_seconds=600)
```

一次性调度任务（从当前时间计算 ISO datetime）：
```
cron(action="add", message="Remind me about the meeting", at="<ISO datetime>")
```

时区感知的 cron：
```
cron(action="add", message="Morning standup", cron_expr="0 9 * * 1-5", tz="America/Vancouver")
```

列出/删除：
```
cron(action="list")
cron(action="remove", job_id="abc123")
```

## 时间表达式

| 用户说的 | 参数 |
|-----------|------------|
| 每 20 分钟 | every_seconds: 1200 |
| 每小时 | every_seconds: 3600 |
| 每天上午 8 点 | cron_expr: "0 8 * * *" |
| 工作日下午 5 点 | cron_expr: "0 17 * * 1-5" |
| 每天温哥华时间上午 9 点 | cron_expr: "0 9 * * *", tz: "America/Vancouver" |
| 在特定时间 | at: ISO datetime 字符串（从当前时间计算） |

## 时区

使用 `tz` 配合 `cron_expr` 在特定 IANA 时区中调度。如果不使用 `tz`，则使用服务器的本地时区。
