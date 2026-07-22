# 多实例

同时运行多个 nanobot 实例，各自拥有独立的配置和运行时数据。以 `--config` 作为主入口。当需要为特定实例初始化或更新已保存的工作区时，可选择在 `onboard` 期间传入 `--workspace`。

## 快速开始

如果你希望每个实例从一开始就拥有各自独立的工作区，请在 onboard 期间同时传入 `--config` 和 `--workspace`。

**初始化实例：**

```bash
# Create separate instance configs and workspaces
nanobot onboard --config ~/.nanobot-telegram/config.json --workspace ~/.nanobot-telegram/workspace
nanobot onboard --config ~/.nanobot-discord/config.json --workspace ~/.nanobot-discord/workspace
nanobot onboard --config ~/.nanobot-feishu/config.json --workspace ~/.nanobot-feishu/workspace
```

**配置每个实例：**

编辑 `~/.nanobot-telegram/config.json`、`~/.nanobot-discord/config.json` 等，填入不同的渠道设置。你在 `onboard` 期间传入的工作区会作为该实例的默认工作区保存到每个配置中。

**运行实例：**

```bash
# Instance A - Telegram bot
nanobot gateway --config ~/.nanobot-telegram/config.json

# Instance B - Discord bot
nanobot gateway --config ~/.nanobot-discord/config.json

# Instance C - Feishu bot with custom port
nanobot gateway --config ~/.nanobot-feishu/config.json --port 18792
```

## 路径解析

使用 `--config` 时，nanobot 会根据配置文件所在位置派生其运行时数据目录。除非你用 `--workspace` 覆盖，否则工作区仍来自 `agents.defaults.workspace`。

要在本地针对其中某个实例开启 CLI 会话：

```bash
nanobot agent -c ~/.nanobot-telegram/config.json -m "Hello from Telegram instance"
nanobot agent -c ~/.nanobot-discord/config.json -m "Hello from Discord instance"

# Optional one-off workspace override
nanobot agent -c ~/.nanobot-telegram/config.json -w /tmp/nanobot-telegram-test
```

> `nanobot agent` 使用所选的工作区/配置启动一个本地 CLI agent。它不会附加到或代理通过一个已在运行的 `nanobot gateway` 进程。

| 组件 | 解析来源 | 示例 |
|-----------|---------------|---------|
| **配置（Config）** | `--config` 路径 | `~/.nanobot-A/config.json` |
| **工作区（Workspace）** | `--workspace` 或配置 | `~/.nanobot-A/workspace/` |
| **定时任务（Cron Jobs）** | 工作区目录 | `~/.nanobot-A/workspace/cron/` |
| **媒体 / 运行时状态** | 配置目录 | `~/.nanobot-A/media/` |

## 工作原理

- `--config` 选择要加载的配置文件
- 默认情况下，工作区来自该配置中的 `agents.defaults.workspace`
- 如果你传入 `--workspace`，它会覆盖配置文件中的工作区

## 最小化设置

1. 将你的基础配置复制到一个新的实例目录。
2. 为该实例设置不同的 `agents.defaults.workspace`。
3. 用 `--config` 启动实例。

示例配置片段：

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.nanobot-telegram/workspace"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN"
    }
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

复制的基础配置可以继续使用相同的 `modelPresets` 和 `agents.defaults.modelPreset`。如果该实例需要不同的模型，添加另一个预设并将 `agents.defaults.modelPreset` 设置为该预设名称。

启动各自独立的实例：

```bash
nanobot gateway --config ~/.nanobot-telegram/config.json
nanobot gateway --config ~/.nanobot-discord/config.json
```

每个 gateway 实例还会在 `gateway.host:gateway.port` 上暴露一个轻量级 HTTP 健康检查端点。默认情况下，gateway 绑定到 `127.0.0.1`，因此除非你显式将 `gateway.host` 设置为面向公网或局域网的地址，该端点会保持本地可见。

- `GET /health` 返回 `{"status":"ok"}`
- 其他路径返回 `404`

需要时可为一次性运行覆盖工作区：

```bash
nanobot gateway --config ~/.nanobot-telegram/config.json --workspace /tmp/nanobot-telegram-test
```

## 常见用例

- 为 Telegram、Discord、Feishu 等平台运行各自的 bot
- 保持测试实例与生产实例相互隔离
- 为不同团队使用不同的模型或 provider
- 使用独立的配置和运行时数据服务多个租户

## 注意事项

- 如果实例同时运行，每个实例必须使用不同的端口
- 如果你希望隔离记忆、会话和技能，请为每个实例使用不同的工作区
- `--workspace` 会覆盖配置文件中定义的工作区
- 定时任务存储在活动工作区中；运行时媒体/状态派生自配置目录
