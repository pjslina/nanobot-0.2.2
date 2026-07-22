# 聊天内命令

这些命令可在聊天频道和交互式 agent 会话中使用：

| 命令 | 说明 |
|---------|-------------|
| `/new` | 停止当前任务并开始新对话 |
| `/stop` | 停止当前任务 |
| `/restart` | 重启机器人 |
| `/status` | 显示机器人状态 |
| `/model` | 显示当前模型和可用的模型预设 |
| `/model <preset>` | 为后续轮次切换运行时模型预设 |
| `/dream` | 立即运行 Dream 记忆整合 |
| `/dream-log` | 显示最新的 Dream 记忆变更 |
| `/dream-log <sha>` | 显示特定的 Dream 记忆变更 |
| `/dream-restore` | 列出最近的 Dream 记忆版本 |
| `/dream-restore <sha>` | 将记忆恢复到某次特定变更之前的状态 |
| `/skill` | 列出已启用的技能及其说明 |
| `/pairing` | 列出待处理的配对请求 |
| `/pairing approve <code>` | 批准一个配对码 |
| `/pairing deny <code>` | 拒绝一个待处理的配对请求 |
| `/pairing revoke <user_id>` | 在当前频道撤销一个已批准的用户 |
| `/pairing revoke <channel> <user_id>` | 在特定频道撤销一个已批准的用户 |
| `/help` | 显示可用的聊天内命令 |

## 配对

当有人向机器人发送私信且不在允许列表中时——无论是新用户还是现有用户在新频道上——nanobot 会自动回复一个**配对码**（如 `ABCD-EFGH`），该配对码在 10 分钟后过期。要授予其访问权限：

```text
/pairing approve ABCD-EFGH
```

要查看谁在等待，请使用 `/pairing`。稍后要移除某人，请使用 `/pairing revoke <user_id>`——你可以在 `/pairing list` 的输出中找到用户 ID。

完整的设置指南请参阅[配置：配对](./configuration.md#pairing)。

## 模型预设

使用 `/model` 查看当前运行时模型：

```text
/model
```

响应会显示当前模型、当前预设以及可用的预设名称。命名预设来自顶层 `modelPresets` 配置，是配置模型选择的推荐方式。`default` 始终可用，表示来自直接 `agents.defaults.*` 字段的模型设置。

为后续轮次切换预设：

```text
/model fast
/model deep
/model default
```

预设名称来自顶层 `modelPresets` 配置。切换仅在运行时生效：它不会重写 `config.json`，进行中的轮次会继续使用它开始时使用的模型。设置详情请参阅[配置：模型预设](./configuration.md#model-presets)。

## 定期任务

定期任务由工作区中的 `HEARTBEAT.md`（`~/.nanobot/workspace/HEARTBEAT.md`）驱动。当 `nanobot gateway` 启动时，默认会注册一个受保护的心跳 cron 作业。每 30 分钟，该作业会检查此文件；如果在 `## Active Tasks` 下找到任务，agent 会执行这些任务并将结果投递到你最近活跃的聊天频道。如果没有活动任务，心跳会被静默跳过。

**设置：**编辑 `~/.nanobot/workspace/HEARTBEAT.md`（由 `nanobot onboard` 自动创建）：

```markdown
## Active Tasks

- 查看天气预报并发送摘要
- 扫描收件箱中的紧急邮件
```

agent 也可以自行管理此文件——让它"添加一个定期任务"，它会为你更新 `HEARTBEAT.md`。已完成的任务应从文件中删除，而不是移动到其他章节。

你可以在 `~/.nanobot/config.json` 中更改间隔或禁用内置心跳：

```json
{
  "gateway": {
    "heartbeat": {
      "enabled": true,
      "intervalS": 1800
    }
  }
}
```

心跳作业在 `cron(action="list")` 中以 `heartbeat` 显示，但它是系统管理的，无法用 `cron` 工具移除。要停止它，请将 `gateway.heartbeat.enabled` 设为 `false` 并重启网关。

> **注意：**网关必须正在运行（`nanobot gateway`），并且你必须至少与机器人聊过一次，以便它知道投递到哪个频道。
