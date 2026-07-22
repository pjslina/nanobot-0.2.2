---
name: summarize
description: 从 URL、播客和本地文件中总结或提取文本/字幕（"转写这个 YouTube/视频"的优秀后备方案）。
homepage: https://summarize.sh
metadata: {"nanobot":{"emoji":"🧾","requires":{"bins":["summarize"]},"install":[{"id":"brew","kind":"brew","formula":"steipete/tap/summarize","bins":["summarize"],"label":"Install summarize (brew)"}]}}
---

# Summarize

用于总结 URL、本地文件和 YouTube 链接的快速 CLI。

## 何时使用（触发短语）

当用户提出以下任何请求时，立即使用此技能：
- "use summarize.sh"
- "这个链接/视频讲的是什么？"
- "总结这个 URL/文章"
- "转写这个 YouTube/视频"（尽力而为的字幕提取；无需 `yt-dlp`）

## 快速开始

```bash
summarize "https://example.com" --model google/gemini-3-flash-preview
summarize "/path/to/file.pdf" --model google/gemini-3-flash-preview
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto
```

## YouTube：总结 vs 字幕

尽力而为的字幕（仅限 URL）：

```bash
summarize "https://youtu.be/dQw4w9WgXcQ" --youtube auto --extract-only
```

如果用户要求字幕但内容巨大，先返回紧凑的总结，然后询问要展开哪个部分/时间范围。

## 模型 + 密钥

为所选 provider 设置 API 密钥：
- OpenAI：`OPENAI_API_KEY`
- Anthropic：`ANTHROPIC_API_KEY`
- xAI：`XAI_API_KEY`
- Google：`GEMINI_API_KEY`（别名：`GOOGLE_GENERATIVE_AI_API_KEY`、`GOOGLE_API_KEY`）

如果未设置，默认模型为 `google/gemini-3-flash-preview`。

## 有用标志

- `--length short|medium|long|xl|xxl|<chars>`
- `--max-output-tokens <count>`
- `--extract-only`（仅限 URL）
- `--json`（机器可读）
- `--firecrawl auto|off|always`（后备提取）
- `--youtube auto`（如果设置了 `APIFY_API_TOKEN` 则使用 Apify 后备）

## 配置

可选的配置文件：`~/.summarize/config.json`

```json
{ "model": "openai/gpt-5.2" }
```

可选服务：
- `FIRECRAWL_API_KEY` 用于被屏蔽的网站
- `APIFY_API_TOKEN` 用于 YouTube 后备
