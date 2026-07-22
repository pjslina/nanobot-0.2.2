---
name: clawhub
description: 从 ClawHub（公共 skill 注册表）搜索并安装 agent 技能。
homepage: https://clawhub.ai
metadata: {"nanobot":{"emoji":"🦞"}}
---

# ClawHub

AI agent 的公共 skill 注册表。通过自然语言搜索（向量搜索）。

## 何时使用

当用户提出以下任何请求时，使用此技能：
- "找一个……的 skill"
- "搜索 skill"
- "安装 skill"
- "有哪些可用的 skill？"
- "更新我的 skill"

## 搜索

```bash
npx --yes clawhub@latest search "web scraping" --limit 5
```

## 安装

```bash
npx --yes clawhub@latest install <slug> --workdir ~/.nanobot/workspace
```

将 `<slug>` 替换为搜索结果中的 skill 名称。这会将 skill 放入 `~/.nanobot/workspace/skills/`，nanobot 从该位置加载 workspace skill。始终包含 `--workdir`。

## 更新

```bash
npx --yes clawhub@latest update --all --workdir ~/.nanobot/workspace
```

## 列出已安装

```bash
npx --yes clawhub@latest list --workdir ~/.nanobot/workspace
```

## 注意事项

- 需要 Node.js（`npx` 随附在内）。
- 搜索和安装无需 API 密钥。
- 登录（`npx --yes clawhub@latest login`）仅在发布时需要。
- `--workdir ~/.nanobot/workspace` 至关重要 - 没有它，skill 会安装到当前目录而不是 nanobot workspace。
- 安装后，提醒用户启动新会话以加载 skill。
