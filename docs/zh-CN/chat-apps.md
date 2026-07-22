# 聊天应用

将 nanobot 连接到你最喜欢的聊天平台。想要构建自己的平台？请参阅[频道插件指南](./channel-plugin-guide.md)。

在配置聊天应用之前，请确保本地 CLI 路径正常工作：

```bash
nanobot agent -m "Hello!"
```

如果该命令失败，请先使用 [`quick-start.md`](./quick-start.md)、[`providers.md`](./providers.md) 和 [`troubleshooting.md`](./troubleshooting.md) 修复安装、配置、provider 或模型设置。聊天应用要求在频道配置完成后 `nanobot gateway` 保持运行。

下面大多数示例都是需要合并到 `~/.nanobot/config.json` 中的代码片段。

## 通用设置模式

每个聊天应用都使用相同的结构：

1. 在聊天平台中创建或准备好机器人/账号。
2. 复制平台提供的 token、密钥、QR 登录状态、webhook URL 或账号 ID。
3. 将该平台的 JSON 片段合并到 `~/.nanobot/config.json`。
4. 首先通过 `allowFrom` 或平台特定的允许列表保持访问控制范围狭窄。
5. 检查 nanobot 能否看到已配置的频道：

```bash
nanobot channels status
```

6. 启动网关并保持该终端运行：

```bash
nanobot gateway
```

7. 从允许的账号发送消息。在群聊中，请遵循该频道的 `groupPolicy` 行为：许多频道默认为仅提及模式，而 Matrix 和 WhatsApp 默认为开放的群组回复。

如果 `nanobot channels status` 未将频道显示为已启用，说明配置片段位置错误、频道名称拼写错误，或你编辑的配置文件不是 nanobot 正在读取的文件。如果频道已启用但消息未到达，请运行 `nanobot gateway --verbose` 并对比平台端的凭据、事件权限和允许列表。

> `["*"]` 允许任何可以访问该频道的人与机器人对话。仅在你有意为之，或在私有沙盒中临时测试时使用。

| 频道 | 你需要的内容 |
|---------|---------------|
| **Telegram** | 来自 @BotFather 的 Bot token |
| **Discord** | Bot token + Message Content intent |
| **WhatsApp** | 扫描二维码（`nanobot channels login whatsapp`） |
| **WeChat (Weixin)** | 扫描二维码（`nanobot channels login weixin`） |
| **Feishu** | 扫描二维码（`nanobot channels login feishu`）或 App ID + App Secret |
| **DingTalk** | App Key + App Secret |
| **Slack** | Bot token + App-Level token |
| **Matrix** | Homeserver URL + Access token |
| **Email** | IMAP/SMTP 凭据 |
| **QQ** | App ID + App Secret |
| **Napcat (QQ)** | Napcat Forward WebSocket URL + access token |
| **Wecom** | Bot ID + Bot Secret |
| **Microsoft Teams** | App ID + App Password + 公共 HTTPS 端点 |
| **Mochat** | Claw token（提供自动设置） |
| **Signal** | signal-cli daemon + 电话号码 |

<details>
<summary><b>Telegram</b></summary>

**1. 创建机器人**
- 打开 Telegram，搜索 `@BotFather`
- 发送 `/newbot`，按照提示操作
- 复制 token

**2. 配置**

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

> 你可以在 Telegram 设置中找到你的**用户 ID**。它显示为 `@yourUserId`。复制此值时**不要包含 `@` 符号**，并将其粘贴到配置文件中。


**3. 运行**

```bash
nanobot gateway
```

**Webhook 模式（可选）**

Telegram 默认使用长轮询。要通过 webhook 接收更新，请暴露一个公共 HTTPS URL 转发到 nanobot 的本地监听器，并将 `mode` 设置为 `webhook`：

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "mode": "webhook",
      "webhookUrl": "https://example.com/telegram",
      "webhookListenHost": "127.0.0.1",
      "webhookListenPort": 8081,
      "webhookPath": "/telegram",
      "webhookSecretToken": "CHANGE_ME_RANDOM_SECRET",
      "webhookMaxConnections": 4,
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

> webhook 模式下必须设置 `webhookSecretToken`。不要在没有反向代理或隧道的情况下将本地 webhook 监听器直接暴露到公共互联网。TLS/Host 策略由你的代理处理；nanobot 仅监听 `webhookListenHost:webhookListenPort` 并验证 Telegram 的 webhook 密钥 token。`webhookMaxConnections` 默认为 `4`；nanobot 在将 Telegram 更新转发给 agent 之前仍会按会话串行化处理。
>
> `webhookUrl` 是向 Telegram 注册的公共 HTTPS URL。`webhookPath` 是 nanobot 监听的本地路径。它们通常使用相同的路径，但当反向代理或隧道重写请求路径时可能不同。

</details>

<details>
<summary><b>Mochat (Claw IM)</b></summary>

默认使用 **Socket.IO WebSocket**，带有 HTTP 轮询回退。

**1. 让 nanobot 为你设置 Mochat**

只需向 nanobot 发送以下消息（将 `xxx@xxx` 替换为你的真实邮箱）：

```
Read https://raw.githubusercontent.com/HKUDS/MoChat/refs/heads/main/skills/nanobot/skill.md and register on MoChat. My Email account is xxx@xxx Bind me as your owner and DM me on MoChat.
```

nanobot 将自动注册、配置 `~/.nanobot/config.json` 并连接到 Mochat。

**2. 重启网关**

```bash
nanobot gateway
```

就是这样 - nanobot 会处理其余的事！

<br>

<details>
<summary>手动配置（高级）</summary>

如果你更喜欢手动配置，请将以下内容添加到 `~/.nanobot/config.json`：

> 保持 `claw_token` 私密。它只应通过 `X-Claw-Token` 请求头发送到你的 Mochat API 端点。

```json
{
  "channels": {
    "mochat": {
      "enabled": true,
      "base_url": "https://mochat.io",
      "socket_url": "https://mochat.io",
      "socket_path": "/socket.io",
      "claw_token": "claw_xxx",
      "agent_user_id": "6982abcdef",
      "sessions": ["*"],
      "panels": ["*"],
      "reply_delay_mode": "non-mention",
      "reply_delay_ms": 120000
    }
  }
}
```



</details>

</details>

<details>
<summary><b>Discord</b></summary>

**1. 创建机器人**
- 前往 https://discord.com/developers/applications
- 创建应用 -> Bot -> Add Bot
- 复制机器人 token

**2. 启用 intents**
- 在 Bot 设置中，启用 **MESSAGE CONTENT INTENT**
- （可选）如果你计划使用基于成员数据的允许列表，请启用 **SERVER MEMBERS INTENT**

**3. 获取你的用户 ID**
- Discord 设置 -> 高级 -> 启用 **开发者模式**
- 右键点击你的头像 -> **复制用户 ID**

**4. 配置**

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"],
      "allowChannels": [],
      "groupPolicy": "mention",
      "streaming": true
    }
  }
}
```

> `groupPolicy` 控制机器人在群组频道中的响应方式：
> - `"mention"`（默认）- 仅在被 @提及时响应
> - `"open"` - 响应所有消息
> 当发送者在 `allowFrom` 中时，DM 始终响应。
> - 如果你将群组策略设置为 open，请将新线程创建为私有线程，然后将机器人 @提及到其中。否则线程本身以及你生成它的频道都会生成一个机器人会话。
> `allowChannels` 将机器人限制为特定的 Discord 频道 ID。为空（默认）表示在机器人能看到的每个频道中响应。示例：`["1234567890", "0987654321"]`。该过滤器在 `allowFrom` 之后应用，因此两者都必须通过。允许的父频道下的 Discord 线程也被允许；对于论坛频道，允许父论坛频道即表示允许该论坛中的所有线程/帖子。
> `streaming` 默认为 `true`。仅在你明确需要非流式回复时禁用它。

**5. 邀请机器人**
- OAuth2 -> URL Generator
- 作用域：`bot`
- 机器人权限：`Send Messages`、`Read Message History`
- 打开生成的邀请 URL 并将机器人添加到你的服务器

**6. 运行**

```bash
nanobot gateway
```

</details>

<details>
<summary><b>Matrix (Element)</b></summary>

首先安装 Matrix 依赖：

```bash
python -m pip install "nanobot-ai[matrix]"
```

> [!NOTE]
> Matrix 在 Windows 上不受支持。`matrix-nio[e2e]` 依赖 `python-olm`，而后者没有预构建的 Windows wheel，在 `sys_platform == 'win32'` 上会被 `matrix` extra 跳过。上述命令在 Windows 上仍会成功，但不会安装 `matrix-nio`，因此启用 Matrix 频道会在启动时失败。请使用 macOS、Linux 或 WSL2。

**1. 创建/选择 Matrix 账号**

- 在你的 homeserver 上创建或重用 Matrix 账号（例如 `matrix.org`）。
- 确认你可以使用 Element 登录。

**2. 获取凭据**

- 你需要：
  - `userId`（示例：`@nanobot:matrix.org`）
  - `password`

（注意：出于遗留原因，仍支持 `accessToken` 和 `deviceId`，但为了可靠的加密，建议使用密码登录。如果提供了 `password`，`accessToken` 和 `deviceId` 将被忽略。）

**3. 配置**

```json
{
  "channels": {
    "matrix": {
      "enabled": true,
      "homeserver": "https://matrix.org",
      "userId": "@nanobot:matrix.org",
      "password": "mypasswordhere",
      "e2eeEnabled": true,
      "sasVerification": true,
      "allowFrom": ["@your_user:matrix.org"],
      "groupPolicy": "open",
      "groupAllowFrom": [],
      "allowRoomMentions": false,
      "maxMediaBytes": 20971520
    }
  }
}
```

> 保持持久的 `matrix-store` - 如果这些在重启之间发生变化，加密会话状态将会丢失。

| 选项 | 描述 |
|--------|-------------|
| `allowFrom` | 允许交互的用户 ID。为空表示拒绝所有；使用 `["*"]` 允许所有人。 |
| `groupPolicy` | `open`（默认）、`mention` 或 `allowlist`。 |
| `groupAllowFrom` | 房间允许列表（当策略为 `allowlist` 时使用）。 |
| `allowRoomMentions` | 在提及模式下接受 `@room` 提及。 |
| `e2eeEnabled` | E2EE 支持（默认 `true`）。设置为 `false` 表示仅使用明文。 |
| `sasVerification` | 自动完成来自允许用户的 SAS 设备验证请求（默认 `false`）。适用于 Element X，因为它不为第三方设备暴露手动信任功能。 |
| `maxMediaBytes` | 最大附件大小（默认 `20MB`）。设置为 `0` 以阻止所有媒体。 |




**4. 运行**

```bash
nanobot gateway
```

</details>

<details>
<summary><b>WhatsApp</b></summary>

需要 **Node.js ≥18**。

**1. 关联设备**

```bash
nanobot channels login whatsapp
# 用 WhatsApp 扫描二维码 -> 设置 -> 已关联设备
```

**2. 配置**

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["+1234567890"]
    }
  }
}
```

**3. 运行**（两个终端）

```bash
# 终端 1
nanobot channels login whatsapp

# 终端 2
nanobot gateway
```

> WhatsApp 桥接更新不会自动应用于现有安装。升级 nanobot 后，请使用以下命令重建本地桥接：
> `rm -rf ~/.nanobot/bridge && nanobot channels login whatsapp`

**可选：静态 LID 映射**

现代 WhatsApp 可能会发送发送者的 LID 而不是其电话号码。nanobot 会在运行时学习 LID->电话号码的映射（并重用桥接在磁盘上持久化的映射），但你也可以预先植入映射，以便从第一条消息开始就能解析电话号码：

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["+1234567890"],
      "lidMappings": { "123456789012345": "1234567890" }
    }
  }
}
```

</details>

<details>
<summary><b>Feishu</b></summary>

使用 **WebSocket** 长连接 - 无需公共 IP。

**快速设置：扫码登录**

```bash
nanobot channels login feishu
# 使用 --force 创建/登录新机器人
```

打开打印的 URL 或使用手机上的飞书/Lark 扫描二维码。如果安装了可选的 `qrcode` 包，nanobot 会显示终端二维码；否则会打印登录 URL。nanobot 会在活动配置文件的 `channels.feishu` 下写入 `appId`、`appSecret`、`domain` 和 `enabled`。使用 `--config <path>` 更新非默认配置。

如果你的账号无法使用扫码登录，请使用下面的手动设置。

**手动设置**

**1. 创建飞书机器人**
- 访问[飞书开放平台](https://open.feishu.cn/app)
- 创建新应用 -> 启用**机器人**能力
- **权限**：
  - `im:message`（发送消息）和 `im:message.p2p_msg:readonly`（接收消息）
  - **流式回复**（nanobot 中的默认值）：添加 **`cardkit:card:write`**（在飞书开发者控制台中通常标记为**创建并更新卡片**）。CardKit 实体和流式助手文本需要此权限。旧应用可能尚未具备此权限 - 打开**权限管理**，启用该作用域，然后在控制台要求时**发布**新应用版本。
  - 如果你**无法**添加 `cardkit:card:write`，请在 `channels.feishu` 下设置 `"streaming": false`（见下文）。机器人仍可工作；回复使用普通的交互式卡片，没有逐 token 流式传输。
- **事件**：添加 `im.message.receive_v1`（接收消息）
  - 选择**长连接**模式（需要先运行 nanobot 以建立连接）
- 从"凭证与基础信息"中获取 **App ID** 和 **App Secret**
- 发布应用

**2. 配置**

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "cli_xxx",
      "appSecret": "xxx",
      "encryptKey": "",
      "verificationToken": "",
      "allowFrom": ["ou_YOUR_OPEN_ID"],
      "groupPolicy": "mention",
      "reactEmoji": "OnIt",
      "doneEmoji": "DONE",
      "toolHintPrefix": "🔧",
      "streaming": true,
      "domain": "feishu"
    }
  }
}
```

> `streaming` 默认为 `true`。如果你的应用没有 **`cardkit:card:write`**，请使用 `false`（参见上面的权限）。
> `encryptKey` 和 `verificationToken` 对于长连接模式是可选的。
> `allowFrom`：添加你的 open_id（在你向机器人发送消息时可在 nanobot 日志中找到）。使用 `["*"]` 允许所有用户。
> `groupPolicy`：`"mention"`（默认 - 仅在被 @提及时响应）、`"open"`（响应所有群组消息）。私聊始终响应。
> `reactEmoji`：表示"处理中"状态的表情符号（默认：`OnIt`）。参见[可用表情符号](https://open.larkoffice.com/document/server-docs/im-v1/message-reaction/emojis-introduce)。
> `doneEmoji`：表示"已完成"状态的可选表情符号（例如 `DONE`、`OK`、`HEART`）。设置后，机器人会在移除 `reactEmoji` 后添加此回应。
> `toolHintPrefix`：流式卡片中内联工具提示的前缀（默认：`🔧`）。
> `domain`：`"feishu"`（默认）用于中国（open.feishu.cn），`"lark"` 用于国际版 Lark（open.larksuite.com）。

**3. 运行**

```bash
nanobot gateway
```

> [!TIP]
> 飞书使用 WebSocket 接收消息 - 无需 webhook 或公共 IP！

</details>

<details>
<summary><b>QQ (QQ单聊)</b></summary>

使用 **botpy SDK** 和 WebSocket - 无需公共 IP。目前仅支持**私聊消息**。

**1. 注册并创建机器人**
- 访问 [QQ 开放平台](https://q.qq.com) -> 注册为开发者（个人或企业）
- 创建新的机器人应用
- 前往**开发设置 (Developer Settings)** -> 复制 **AppID** 和 **AppSecret**

**2. 设置沙箱以进行测试**
- 在机器人管理控制台中，找到**沙箱配置 (Sandbox Config)**
- 在**在消息列表配置**下，点击**添加成员**并添加你自己的 QQ 号码
- 添加后，用手机 QQ 扫描机器人的二维码 -> 打开机器人资料 -> 点击"发消息"开始聊天

**3. 配置**

> - `allowFrom`：添加你的 openid（在你向机器人发送消息时可在 nanobot 日志中找到）。使用 `["*"]` 进行公开访问。
> - `msgFormat`：可选。使用 `"plain"`（默认）以获得与旧版 QQ 客户端的最大兼容性，或使用 `"markdown"` 在较新客户端上获得更丰富的格式。
> - 对于生产环境：在机器人控制台中提交审核并发布。有关完整的发布流程，请参阅 [QQ Bot 文档](https://bot.q.qq.com/wiki/)。

```json
{
  "channels": {
    "qq": {
      "enabled": true,
      "appId": "YOUR_APP_ID",
      "secret": "YOUR_APP_SECRET",
      "allowFrom": ["YOUR_OPENID"],
      "msgFormat": "plain"
    }
  }
}
```

**4. 运行**

```bash
nanobot gateway
```

现在从 QQ 向机器人发送消息 - 它应该会响应！

</details>

<details>
<summary><b>Napcat (QQ via OneBot v11 支持群聊等功能)</b></summary>

通过其 **forward WebSocket**（OneBot v11）连接到 [Napcat](https://github.com/NapNeko/NapCatQQ) 实例。当你通过 Napcat 运行自己的 QQ 账户并希望获得完整的私聊 + 群聊支持时，请使用此选项。

**1. 设置 Napcat**

- 安装并登录 Napcat，然后启用 **Forward WebSocket** 服务器。参见[官方 Napcat Docker 教程](https://github.com/NapNeko/NapCat-Docker)。
- 在 webui 中，按照"网络配置" -> "新建" -> "Websocket 服务器"创建一个 forward websocket 服务器。默认情况下，URL 为 `ws://127.0.0.1:3001`
- 复制 forward websocket 服务器的 token
- （可选）在 webui 中，按照"系统配置" -> "登陆配置" -> "快速登录QQ"在重启后自动登录

**2. 配置**

```json
{
  "channels": {
    "napcat": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3001",
      "accessToken": "YOUR_WEBSOCKET_TOKEN",
      "allowFrom": ["*"],
      "groupPolicy": "mention",
      "groupPolicyOverrides": {
        "123456789": "open",
        "987654321": 0.2
      },
      "welcomeNewMembers": true
    }
  }
}
```

| 选项 | 作用 |
|--------|--------------|
| `wsUrl` | Napcat forward-WebSocket 端点。通过 `accessToken` 进行 Bearer 认证，在 `Authorization` 请求头中发送。 |
| `allowFrom` | 允许与机器人对话的 QQ 号码。`["*"]` = 任何人。要让 `welcomeNewMembers` 触发，必须为 `["*"]`（或包含加入的用户）。 |
| `groupPolicy` | `"mention"`（默认）- 仅在被 @提及或回复机器人自己的消息时回复。`"open"` - 回复每条群组消息。`[0.0, 1.0]` 范围内的浮点数 `p` - @提及和回复机器人始终回复；其他所有群组消息以概率 `p` 回复（因此 `0.0` ≡ `"mention"`，`1.0` ≡ `"open"`）。私聊始终回复。 |
| `groupPolicyOverrides` | 可选的按群组覆盖 `groupPolicy`，以群组 ID（作为字符串）为键。每个值采用与 `groupPolicy` 相同的形式（`"mention"`、`"open"` 或浮点数）。未列出的群组回退到 `groupPolicy`。 |
| `welcomeNewMembers` | 为 true 时，`notice.group_increase` 事件会作为合成消息推送到总线，以便 agent 可以问候新加入者。 |
| `maxImageBytes` | 入站图片下载的硬上限（以字节为单位）。默认为 20 MB。较大的图片会被丢弃并发出警告。 |

</details>

<details>
<summary><b>DingTalk (钉钉)</b></summary>

使用 **Stream Mode** - 无需公共 IP。

**1. 创建钉钉机器人**
- 访问[钉钉开放平台](https://open-dev.dingtalk.com/)
- 创建新应用 -> 添加**机器人**能力
- **配置**：
  - 开启 **Stream Mode**
- **权限**：添加发送消息所需的权限
- 从"凭证"中获取 **AppKey**（Client ID）和 **AppSecret**（Client Secret）
- 发布应用

**2. 配置**

```json
{
  "channels": {
    "dingtalk": {
      "enabled": true,
      "clientId": "YOUR_APP_KEY",
      "clientSecret": "YOUR_APP_SECRET",
      "allowFrom": ["YOUR_STAFF_ID"],
      "groupUserIsolation": false
    }
  }
}
```

> `allowFrom`：添加你的员工 ID。使用 `["*"]` 允许所有用户。
>
> `groupUserIsolation`：可选。默认为 `false`，即每个群聊保持一个共享会话。设置为 `true` 可为钉钉群聊中的每个发送者提供单独的会话，同时回复仍发送回同一个群。

**3. 运行**

```bash
nanobot gateway
```

</details>

<details>
<summary><b>Slack</b></summary>

使用 **Socket Mode** - 无需公共 URL。

**1. 创建 Slack 应用**
- 前往 [Slack API](https://api.slack.com/apps) -> **Create New App** -> "From scratch"
- 选择名称并选择你的工作区

**2. 配置应用**
- **Socket Mode**：开启 -> 生成一个具有 `connections:write` 作用域的 **App-Level Token** -> 复制它（`xapp-...`）
- **OAuth & Permissions**：添加机器人作用域：`chat:write`、`reactions:write`、`app_mentions:read`、`files:read`、`files:write`、`channels:history`、`groups:history`、`im:history`、`mpim:history`
- **Event Subscriptions**：开启 -> 订阅机器人事件：`message.im`、`message.channels`、`app_mention` -> Save Changes
- **App Home**：滚动到 **Show Tabs** -> 启用 **Messages Tab** -> 勾选**"Allow users to send Slash commands and messages from the messages tab"**
- **Install App**：点击 **Install to Workspace** -> 授权 -> 复制 **Bot Token**（`xoxb-...`）

> 需要 `files:read` 来读取用户发送给 nanobot 的文件。需要 `files:write` 让 nanobot 发送图片、视频和其他文件上传。如果你稍后添加任一作用域，请将 Slack 应用重新安装到工作区并重启 nanobot，以便它使用更新后的机器人 token。

**3. 配置 nanobot**

```json
{
  "channels": {
    "slack": {
      "enabled": true,
      "botToken": "xoxb-...",
      "appToken": "xapp-...",
      "allowFrom": ["YOUR_SLACK_USER_ID"],
      "groupPolicy": "mention"
    }
  }
}
```

**4. 运行**

```bash
nanobot gateway
```

直接向机器人发送 DM 或在频道中 @提及它 - 它应该会响应！

> [!TIP]
> - `groupPolicy`：`"mention"`（默认 - 仅在被 @提及时响应）、`"open"`（响应所有频道消息）或 `"allowlist"`（通过 `groupAllowFrom` 限制为特定频道）。
> - `groupAllowFrom`：当 `groupPolicy` 为 `"allowlist"` 时，机器人可以响应的频道 ID。
> - `groupRequireMention`：当为 `true` 且 `groupPolicy` 为 `"allowlist"` 时，机器人仅回复 `groupAllowFrom` 中的频道**且**仅在被 @提及时（而不是每条消息）。对 `"mention"`/`"open"` 无影响。使用此选项将机器人限定到已批准的频道，同时保持仅提及行为。
> - DM 策略默认为开放。设置 `"dm": {"enabled": false}` 以禁用 DM。

</details>

<details>
<summary><b>Email</b></summary>

为 nanobot 提供其自己的电子邮件账户。它通过轮询 **IMAP** 接收邮件，并通过 **SMTP** 回复 - 就像个人邮件助手一样。

**1. 获取凭据（以 Gmail 为例）**
- 为你的机器人创建一个专用 Gmail 账户（例如 `my-nanobot@gmail.com`）
- 启用两步验证 -> 创建[应用密码](https://myaccount.google.com/apppasswords)
- 将此应用密码同时用于 IMAP 和 SMTP

**2. 配置**

> - `consentGranted` 必须为 `true` 才能允许邮箱访问。这是一个安全门 - 设置为 `false` 可完全禁用。
> - `allowFrom`：添加你的电子邮件地址。使用 `["*"]` 接受任何人的电子邮件。
> - `smtpUseTls` 和 `smtpUseSsl` 分别默认为 `true` / `false`，这对于 Gmail（端口 587 + STARTTLS）是正确的。无需显式设置。
> - 如果你只想读取/分析电子邮件而不发送自动回复，请设置 `"autoReplyEnabled": false`。
> - `postAction`：已处理电子邮件的可选后处理：`"delete"` 或 `"move"`（默认 `null`）。
>   这仅在已接受的电子邮件成功传递到 AI 管道后运行。
> - `postActionMoveMailbox`：当 `postAction` 为 `"move"` 时使用的目标邮箱（例如 `"Processed"` 或 `"[Gmail]/Trash"`）。
> - `postActionIgnoreSkipped`：如果为 `true`（默认），被跳过的电子邮件在后处理操作中被忽略，不会被移动/删除。
> - `postActionExpunge`：当为 `true` 时，如果 UID 作用域的 expunge 不可用或失败，频道允许全邮箱 `EXPUNGE` 回退（默认 `false`）。仅在缺乏现代 UIDPLUS 支持的非常旧的 IMAP 服务器上启用。请注意，此回退将 expunge 邮箱中所有标记为已删除的消息，包括 agent 未处理的消息。对于所有现代 IMAP 服务器，保持关闭是安全的。
> - `allowedAttachmentTypes`：保存匹配这些 MIME 类型的入站附件 - `["*"]` 表示全部，例如 `["application/pdf", "image/*"]`（默认 `[]` = 禁用）。
> - `maxAttachmentSize`：每个附件的最大大小（以字节为单位，默认 `2000000` / 2MB）。
> - `maxAttachmentsPerEmail`：每封电子邮件最多保存的附件数（默认 `5`）。

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "consentGranted": true,
      "imapHost": "imap.gmail.com",
      "imapPort": 993,
      "imapUsername": "my-nanobot@gmail.com",
      "imapPassword": "your-app-password",
      "smtpHost": "smtp.gmail.com",
      "smtpPort": 587,
      "smtpUsername": "my-nanobot@gmail.com",
      "smtpPassword": "your-app-password",
      "fromAddress": "my-nanobot@gmail.com",
      "allowFrom": ["your-real-email@gmail.com"],
      "postAction": "move",
      "postActionMoveMailbox": "[Gmail]/Trash",
      "postActionIgnoreSkipped": true,
      "postActionExpunge": false,
      "allowedAttachmentTypes": ["application/pdf", "image/*"]
    }
  }
}
```


**3. 运行**

```bash
nanobot gateway
```

</details>

<details>
<summary><b>WeChat (微信 / Weixin)</b></summary>

使用 **HTTP 长轮询**，通过 ilinkai 个人微信 API 进行二维码登录。无需本地微信桌面客户端。

**1. 安装 WeChat 支持**

```bash
python -m pip install "nanobot-ai[weixin]"
```

**2. 配置**

```json
{
  "channels": {
    "weixin": {
      "enabled": true,
      "allowFrom": ["YOUR_WECHAT_USER_ID"]
    }
  }
}
```

> - `allowFrom`：添加你在 nanobot 日志中看到的微信账户发送者 ID。使用 `["*"]` 允许所有用户。
> - `token`：可选。如果省略，将进行交互式登录，nanobot 会为你保存 token。
> - `routeTag`：可选。当你的上游 Weixin 部署需要请求路由时，nanobot 会将其作为 `SKrouteTag` 请求头发送。
> - `stateDir`：可选。默认为 nanobot 的 Weixin 状态运行时目录。
> - `pollTimeout`：可选的长轮询超时时间（以秒为单位）。

**3. 登录**

```bash
nanobot channels login weixin
```

使用 `--force` 重新认证并忽略任何已保存的 token：

```bash
nanobot channels login weixin --force
```

**4. 运行**

```bash
nanobot gateway
```

</details>

<details>
<summary><b>Wecom (企业微信)</b></summary>

> 这里我们使用 [wecom-aibot-sdk-python](https://github.com/chengyongru/wecom_aibot_sdk)（官方 [@wecom/aibot-node-sdk](https://www.npmjs.com/package/@wecom/aibot-node-sdk) 的社区 Python 版本）。
>
> 使用 **WebSocket** 长连接 - 无需公共 IP。

**1. 安装可选依赖**

```bash
python -m pip install "nanobot-ai[wecom]"
```

**2. 创建 WeCom AI Bot**

前往 WeCom 管理控制台 -> 智能机器人 -> 创建机器人 -> 选择 **API 模式**和**长连接**。复制 Bot ID 和 Secret。

**3. 配置**

```json
{
  "channels": {
    "wecom": {
      "enabled": true,
      "botId": "your_bot_id",
      "secret": "your_bot_secret",
      "allowFrom": ["your_id"]
    }
  }
}
```

**4. 运行**

```bash
nanobot gateway
```

</details>

<details>
<summary><b>Microsoft Teams</b> (MVP - 仅 DM)</summary>

> 直接消息文本输入/输出、租户感知 OAuth、对话引用持久化。
> 使用公共 HTTPS webhook - 无 WebSocket；你需要隧道或反向代理。

**1. 安装可选依赖**

```bash
python -m pip install "nanobot-ai[msteams]"
```

**2. 创建 Teams / Azure 机器人应用注册**

创建或重用 Microsoft Teams / Azure 机器人应用注册。将机器人消息端点设置为以 `/api/messages` 结尾的公共 HTTPS URL。

**3. 配置**

```json
{
  "channels": {
    "msteams": {
      "enabled": true,
      "appId": "YOUR_APP_ID",
      "appPassword": "YOUR_APP_SECRET",
      "tenantId": "YOUR_TENANT_ID",
      "host": "0.0.0.0",
      "port": 3978,
      "path": "/api/messages",
      "allowFrom": ["*"],
      "replyInThread": true,
      "mentionOnlyResponse": "Hi - what can I help with?",
      "validateInboundAuth": true,
      "refTtlDays": 30,
      "pruneWebChatRefs": true,
      "pruneNonPersonalRefs": true,
      "refTouchIntervalS": 300
    }
  }
}
```

> - `replyInThread: true` 当存储的 `activity_id` 可用时，回复触发的 Teams 活动。
> - `mentionOnlyResponse` 控制当用户仅发送机器人提及（`<at>Nanobot</at>`）时 Nanobot 接收到的内容。设置为 `""` 可忽略仅提及消息。
> - `validateInboundAuth: true` 启用入站 Bot Framework bearer-token 验证（签名、颁发者、受众、生命周期、`serviceUrl`）。这是公共部署的安全默认值。仅在本地开发或严格控制测试时设置为 `false`。
> - `refTtlDays`（默认 `30`）控制存储的对话引用在被清理之前可以存在多久。
> - `pruneWebChatRefs`（默认 `true`）丢弃具有 `webchat.botframework.com` 服务 URL 的引用。
> - `pruneNonPersonalRefs`（默认 `true`）丢弃 `conversation_type` 不为 `personal` 的引用。
> - `refTouchIntervalS`（默认 `300`）限制成功发送刷新活动引用 `updated_at` 的频率。

**4. 运行**

```bash
nanobot gateway
```

</details>

<details>
<summary><b>Signal</b></summary>

使用 **signal-cli** daemon 的 HTTP 模式 - 通过 SSE 接收消息，通过 JSON-RPC 发送消息。

**1. 安装 signal-cli**

安装 [signal-cli](https://github.com/AsamK/signal-cli) 并注册一个电话号码：

```bash
signal-cli -u +1234567890 register
signal-cli -u +1234567890 verify <CODE>
```

启动 daemon：

```bash
signal-cli -a +1234567890 daemon --http localhost:8080
```

**2. 配置**

```json
{
  "channels": {
    "signal": {
      "enabled": true,
      "phoneNumber": "+1234567890",
      "daemonHost": "localhost",
      "daemonPort": 8080,
      "dm": {
        "enabled": true,
        "policy": "open"
      },
      "group": {
        "enabled": true,
        "policy": "open",
        "requireMention": true
      }
    }
  }
}
```

> - `phoneNumber`：你注册的 Signal 电话号码。
> - `daemonHost` / `daemonPort`：signal-cli daemon 监听的位置（默认 `localhost:8080`）。
> - `dm.policy`：`"open"`（任何人都可以 DM）或 `"allowlist"`（仅列出的号码/UUID）。当为 `"allowlist"` 时，未列出的 DM 发送者会收到一个配对码。
> - `dm.allowFrom`：允许的电话号码或 UUID 列表（当策略为 `"allowlist"` 时使用）。
> - `group.policy`：`"open"`（所有群组）或 `"allowlist"`（仅列出的群组 ID）。
> - `group.requireMention`：当为 `true`（默认）时，机器人在群组中仅在被 @提及时响应。
> - `group.allowFrom`：允许的群组 ID 列表（当群组策略为 `"allowlist"` 时使用）。
> - `attachmentsDir`：覆盖 signal-cli 存储入站附件的目录。默认为 `~/.local/share/signal-cli/attachments`（Linux 默认值）。如果 signal-cli 使用自定义 `XDG_DATA_HOME` 运行或在 macOS/Windows 上运行，请设置此项。
> - `groupMessageBufferSize`：为上下文保留的最近群组消息数量（默认 `20`，必须 > 0）。

**3. 运行**

```bash
nanobot gateway
```

> [!TIP]
> 如果连接断开，频道会以指数退避方式自动重新连接到 signal-cli daemon。
> 机器人回复中的 Markdown 会自动转换为 Signal 文本样式（粗体、斜体、代码等）。

</details>
