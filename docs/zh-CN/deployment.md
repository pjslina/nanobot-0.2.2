# 部署

在 `nanobot agent -m "Hello!"` 本地可用之后使用本页。部署用于让长期运行的对外服务保持在线：WebUI、聊天应用、heartbeat、Dream、cron 作业以及频道连接。

## 部署之前

在使用 Docker、systemd 或 LaunchAgent 之前检查以下各项：

| 检查项 | 重要性 |
|---|---|
| `nanobot status` 显示预期的配置和工作区 | 确认进程将读取你打算运行的实例 |
| `nanobot agent -m "Hello!"` 可正常运行 | 在加入服务层之前证明安装、配置、provider、模型和工作区写入均正常 |
| 密钥位于环境变量或受保护的配置文件中 | API 密钥、bot token、OAuth 状态和聊天凭证不应被全局可读 |
| `~/.nanobot/` 或你自定义的配置/工作区路径是持久化的 | 会话、记忆、频道登录状态、生成的产物和 cron 作业都存放在那里 |
| 频道访问控制是有意为之的 | 在暴露 bot 之前使用 `allowFrom`、配对、WebSocket `token`/`tokenIssueSecret` 或私有测试频道 |
| 端口已规划 | Gateway 健康检查默认为 `18790`；WebUI/WebSocket 默认为 `8765`；`nanobot serve` 默认为 `8900` |
| 日志易于访问 | 在诊断启动问题时使用 `docker compose logs`、`journalctl`、LaunchAgent 日志文件或 `nanobot gateway --verbose` |

编辑 `config.json` 后重启已部署的进程。长期运行的进程在启动时读取配置。

## 选择运行时

| 运行时 | 适用场景 | 状态位置 | 有用的首条命令 |
|---|---|---|---|
| Docker Compose | 在 Linux 服务器或工作站上可重复的容器运行 | 将 `~/.nanobot` 绑定挂载到 `/home/nanobot/.nanobot` | `docker compose run --rm nanobot-cli agent -m "Hello!"` |
| Docker CLI | 手动容器测试或小型一次性主机 | 将 `~/.nanobot` 绑定挂载到 `/home/nanobot/.nanobot` | `docker run -v ~/.nanobot:/home/nanobot/.nanobot --rm nanobot status` |
| systemd user service | 自动重启的 Linux 用户级 gateway | 宿主用户的 `~/.nanobot`，除非你传入显式路径 | `systemctl --user status nanobot-gateway` |
| macOS LaunchAgent | 登录后启动的 macOS gateway | 宿主用户的 `~/.nanobot`，除非 plist 传入显式路径 | `launchctl list | grep ai.nanobot.gateway` |

## Docker

> [!TIP]
> `-v ~/.nanobot:/home/nanobot/.nanobot` 标志将你的本地配置目录挂载到容器中，这样你的配置和工作区在容器重启后仍然保留。
> 容器以非 root 用户 `nanobot`（UID 1000）运行，并从 `/home/nanobot/.nanobot` 读取配置。始终将宿主配置目录挂载到 `/home/nanobot/.nanobot`，而不是 `/root/.nanobot`。
> 如果遇到 **Permission denied**，请先在宿主上修复所有权：`sudo chown -R 1000:1000 ~/.nanobot`，或传入 `--user $(id -u):$(id -g)` 以匹配你的宿主 UID。Podman 用户可以改用 `--userns=keep-id`。
>
> [!IMPORTANT]
> 官方 Docker 用法目前指使用本仓库附带的 `Dockerfile` 进行构建。第三方命名空间下的 Docker Hub 镜像不由 HKUDS/nanobot 维护或验证；除非你信任发布者，否则不要将 API 密钥或 bot token 挂载到其中。

> [!IMPORTANT]
> 在 `config.json` 中（设置位于 `nanobot/config/schema.py`），gateway 和 WebSocket 频道默认使用 `host: "127.0.0.1"`。Docker 的 `-p` 端口转发无法访问容器的环回接口，因此要让宿主或局域网访问暴露的端口，必须在启动容器之前将两个绑定都设置为 `0.0.0.0`（在 `~/.nanobot/config.json` 中）。要从 Docker 提供内置 WebUI，请启用 WebSocket 频道并用密钥保护引导过程：
>
> ```json
> {
>   "gateway": { "host": "0.0.0.0" },
>   "channels": {
>     "websocket": {
>       "enabled": true,
>       "host": "0.0.0.0",
>       "port": 8765,
>       "tokenIssueSecret": "your-secret-here"
>     }
>   }
> }
> ```
>
> 当 WebSocket 的 `host` 为 `0.0.0.0` 时，除非同时配置了 `token` 或 `tokenIssueSecret`，否则频道拒绝启动。详见 [`webui.md#lan-access`](./webui.md#lan-access)。

### Docker Compose

```bash
docker compose run --rm nanobot-cli onboard   # 首次设置
vim ~/.nanobot/config.json                     # 添加 API 密钥
docker compose up -d nanobot-gateway           # 启动 gateway
```

```bash
docker compose run --rm nanobot-cli agent -m "Hello!"   # 运行 CLI
docker compose logs -f nanobot-gateway                   # 查看日志
docker compose down                                      # 停止
```

### Docker

```bash
# 构建镜像
docker build -t nanobot .

# 初始化配置（仅首次）
docker run -v ~/.nanobot:/home/nanobot/.nanobot --rm nanobot onboard

# 在宿主上编辑配置以添加 API 密钥
vim ~/.nanobot/config.json

# 运行 gateway（连接到已启用的频道，例如 Telegram/Discord/Mochat）。
# 对应 docker-compose.yml 中声明的安全能力和端口映射：
#   - 当 `tools.exec.sandbox: "bwrap"` 启用时，需要 `--cap-drop ALL --cap-add SYS_ADMIN`
#     + unconfined apparmor/seccomp（bwrap 需要 CAP_SYS_ADMIN 以使用用户命名空间）。
#     没有它们，`bwrap` 会以 `clone3: Operation not permitted` 退出。
#   - `-p 8765:8765` 在 18790 的 gateway 健康检查端点之外，还暴露了 WebSocket 频道 / WebUI。
docker run \
  --cap-drop ALL --cap-add SYS_ADMIN \
  --security-opt apparmor=unconfined \
  --security-opt seccomp=unconfined \
  -v ~/.nanobot:/home/nanobot/.nanobot \
  -p 18790:18790 -p 8765:8765 \
  nanobot gateway

# 或者运行单个命令
docker run -v ~/.nanobot:/home/nanobot/.nanobot --rm nanobot agent -m "Hello!"
docker run -v ~/.nanobot:/home/nanobot/.nanobot --rm nanobot status
```

## Linux 服务

将 gateway 作为 systemd user service 运行，这样它会自动启动并在失败时重启。

先预览生成的 unit：

```bash
nanobot gateway install-service --manager systemd --dry-run
```

安装、启用并启动它：

```bash
nanobot gateway install-service --manager systemd
```

对于自定义实例，传入你用于运行 gateway 的相同配置/工作区选择器：

```bash
nanobot gateway install-service \
  --manager systemd \
  --name nanobot-telegram \
  --config ~/.nanobot-telegram/config.json \
  --workspace ~/.nanobot-telegram/workspace
```

常用操作：

```bash
systemctl --user status nanobot-gateway        # 检查状态
systemctl --user restart nanobot-gateway       # 配置变更后重启
journalctl --user -u nanobot-gateway -f        # 跟踪日志
nanobot gateway uninstall-service --manager systemd
```

安装器会写入 `~/.config/systemd/user/nanobot-gateway.service`，运行
`systemctl --user daemon-reload`，启用该 unit 并重启它。它使用当前
Python 可执行文件配合 `python -m nanobot gateway --foreground`，因此服务运行在你
用于安装 nanobot 的相同环境中。

> **注意：** 用户服务仅在你登录时运行。要让 gateway 在注销后继续运行，请启用 lingering：
>
> ```bash
> loginctl enable-linger $USER
> ```

## macOS LaunchAgent

当你希望 `nanobot gateway` 在登录后保持在线而又不必保持终端打开时，使用 LaunchAgent。

先预览生成的 plist：

```bash
nanobot gateway install-service --manager launchd --dry-run
```

安装、加载、启用并启动它：

```bash
nanobot gateway install-service --manager launchd
```

对于自定义实例：

```bash
nanobot gateway install-service \
  --manager launchd \
  --name nanobot-telegram \
  --config ~/.nanobot-telegram/config.json \
  --workspace ~/.nanobot-telegram/workspace
```

常用操作：

```bash
launchctl list | grep ai.nanobot.gateway
launchctl kickstart -k gui/$(id -u)/ai.nanobot.gateway
nanobot gateway uninstall-service --manager launchd
```

安装器会写入 `~/Library/LaunchAgents/ai.nanobot.gateway.plist`，使用当前
Python 可执行文件配合 `python -m nanobot gateway --foreground`，并将
LaunchAgent 日志写入 `~/.nanobot/logs/`。

> **注意：** 如果启动失败并提示 "address already in use"，请先停止手动启动的 `nanobot gateway` 进程。
