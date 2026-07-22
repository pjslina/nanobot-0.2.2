# nanobot WebUI 源码

本目录包含 nanobot WebUI 的 React/TypeScript 源码。如果你从 PyPI 安装了
`nanobot-ai` 且只想使用内置的浏览器 UI，请阅读
[`docs/webui.md`](../docs/webui.md) 中的用户指南。除非你要修改前端，否则你不需要
Node.js、Bun、Vite 或本目录中的任何内容。

如需项目概览、安装指南和通用文档索引，请参阅根目录的 [`README.md`](../README.md) 和 [`docs/README.md`](../docs/README.md)。

## 选择路径

| 目标 | 从此处开始 | 打开地址 |
|---|---|---|
| 使用内置浏览器 UI | [`docs/webui.md`](../docs/webui.md) | `http://127.0.0.1:8765` |
| 从其他设备使用 WebUI | [`docs/webui.md#lan-access`](../docs/webui.md#lan-access) | `http://<your-ip>:8765` |
| 修改 WebUI 源码 | [开发 WebUI（Vite HMR）](#develop-the-webui-vite-hmr) | `http://127.0.0.1:5173` |
| 调试安装失败问题 | [`docs/troubleshooting.md#webui-problems`](../docs/troubleshooting.md#webui-problems) | 诊断顺序与常见修复方法 |

源码应用基于 Vite + React 18 + TypeScript + Tailwind 3 +
shadcn/ui 构建。它通过 WebSocket 多路复用协议与网关通信，并从同一端口上的嵌入式 REST 接口读取会话元数据。

## 目录结构

```text
webui/                 source tree (this directory)
nanobot/web/dist/      build output served by the gateway
```

## 开发 WebUI（Vite HMR）

### 1. 从源码安装 nanobot

在仓库根目录下：

```bash
python -m pip install -e .
```

> 可编辑安装有意**跳过** WebUI 打包步骤 —— Vite HMR 比每次改动后重新构建 `dist/` 更快。

### 2. 启用 WebSocket 通道

在 `~/.nanobot/config.json` 中合并以下内容：

```json
{ "channels": { "websocket": { "enabled": true } } }
```

### 3. 启动网关

在一个终端中：

```bash
nanobot gateway
```

### 4. 启动 WebUI 开发服务器

在另一个终端中：

```bash
cd webui
bun install            # npm install also works
bun run dev
```

然后打开 `http://127.0.0.1:5173`。

默认情况下，开发服务器会将 `/api`、`/webui`、`/auth` 以及 WebSocket 流量代理到 `http://127.0.0.1:8765`。

如果你的网关监听非默认端口，请将开发服务器指向该端口：

```bash
NANOBOT_API_URL=http://127.0.0.1:9000 bun run dev
```

## 为打包运行时构建

你通常不需要手动运行此步骤：打包 wheel 时，`python -m build` 会自动调用 WebUI 构建。

如果你想在不重新构建 wheel 的情况下本地预览生产打包产物：

```bash
cd webui
bun run build          # writes to ../nanobot/web/dist
```

网关会在下次重启时加载新的打包产物。

## 测试

```bash
cd webui
bun run test
```

## 致谢

- 感谢 [`agent-chat-ui`](https://github.com/langchain-ai/agent-chat-ui) 为聊天界面提供的 UI 与交互灵感。
