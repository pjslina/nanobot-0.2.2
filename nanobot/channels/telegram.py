"""Telegram channel implementation using python-telegram-bot.

基于 python-telegram-bot 库实现的 Telegram 渠道。支持长轮询（默认）与
webhook 两种收消息模式；出站消息支持流式增量编辑（send_delta 通过反复
edit_message_text 就地更新）、媒体上传/下载、Markdown -> HTML 转换、
表情回应与“正在输入”指示器，以及 Bot API 10.1 的 sendRichMessage 富文本快速路径。
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeEmoji,
    ReplyParameters,
    Update,
)
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.command.builtin import build_help_text
from nanobot.config.paths import get_media_dir
from nanobot.config.schema import Base
from nanobot.security.network import validate_url_target
from nanobot.utils.helpers import split_message

TELEGRAM_MAX_MESSAGE_LEN = 4000  # Telegram message character limit
# Telegram's actual API limit is 4096; we split raw markdown at 4000 as a
# safety margin for mid-stream edits (plain text).  For _stream_end, we split
# raw markdown into chunks whose rendered HTML fits Telegram's true 4096-char
# boundary so the final rendered message never overflows.
# Telegram 真实上限是 4096 字符：流式过程中按 4000 拆分纯文本（留出编辑余量），
# 而 _stream_end 最终消息则按渲染后 HTML 不超过 4096 来拆分，确保最终消息不会溢出。
TELEGRAM_HTML_MAX_LEN = 4096
TELEGRAM_REPLY_CONTEXT_MAX_LEN = TELEGRAM_MAX_MESSAGE_LEN  # Max length for reply context in user message


def _split_telegram_markdown(content: str, max_len: int) -> list[str]:
    """Split raw Telegram Markdown without leaving fenced code blocks unbalanced.

    将原始 Telegram Markdown 拆分为不超过 max_len 的多个片段。关键难点在于
    不能把 ``` 围栏代码块从中间切断——否则 Telegram 会把剩余部分当作代码块
    渲染到下一条消息。算法在选定切点后检查该位置是否处于围栏代码块内部，
    若是则把切点回退到围栏开始处，或为本片段补上一个闭合 ``` 并让下一片段
    以同样的 ``` 开头重新开启代码块，从而保证每条消息的围栏都是成对平衡的。
    """
    if not content:
        return []
    content = content.lstrip()
    if not content:
        return []
    if len(content) <= max_len:
        return [content]

    def fence_line(fence_pos: int) -> str:
        # 取出围栏所在整行（包含语言标识，如 ```python），用于在下一片段重建相同围栏
        line_end = content.find("\n", fence_pos)
        if line_end < 0:
            return content[fence_pos:]
        return content[fence_pos:line_end]

    def split_inside_fenced_code_block(pos: int) -> tuple[bool, int, str]:
        # 判断 pos 是否落在未闭合的 ``` 围栏代码块内部。
        # 统计 pos 之前 ``` 的数量：偶数说明不在代码块内，奇数说明在代码块内。
        if content[:pos].count("```") % 2 == 0:
            return False, -1, ""
        opening = content.rfind("```", 0, pos)
        if opening < 0:
            return True, -1, "```"
        return True, opening, fence_line(opening)

    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break

        cut = content[:max_len]
        # 优先在换行处切，其次在空格处切，最后才硬切到 max_len
        pos = cut.rfind("\n")
        if pos <= 0:
            pos = cut.rfind(" ")
        if pos <= 0:
            pos = max_len

        inside_code, opening, fence = split_inside_fenced_code_block(pos)
        if inside_code:
            if opening > 0:
                # 围栏起点在切点之前 -> 直接把切点回退到围栏起点，让整块代码留在下一条消息
                pos = opening
            else:
                # 围栏起点在文档最开头（opening < 0）-> 无法回退，必须为本片段补闭合围栏
                closing = "\n```"
                min_code_pos = len(fence)
                if content.startswith(fence + "\n"):
                    min_code_pos += 1
                # 代码内容太短、连闭合围栏都放不下 -> 直接按 max_len 硬切，下一片段重新开始
                if pos < min_code_pos and min_code_pos + len(closing) > max_len:
                    chunks.append(content[:max_len])
                    content = content[max_len:].lstrip()
                    continue
                # 调整切点，为本片段预留出闭合 ``` 的空间
                if pos + len(closing) > max_len:
                    budget = max_len - len(closing)
                    if budget > 0:
                        recut = content[:budget]
                        adjusted = recut.rfind("\n")
                        if adjusted <= 0:
                            adjusted = recut.rfind(" ")
                        pos = adjusted if adjusted > 0 else budget
                    else:
                        closing = "```"
                        pos = max_len - len(closing)
                # 本片段补上闭合围栏，下一片段以同样的围栏开头重新开启代码块
                chunks.append(content[:pos] + closing)
                remainder = content[pos:]
                if remainder.startswith("\n"):
                    remainder = remainder[1:]
                content = f"{fence}\n{remainder}"
                continue

        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


def _escape_telegram_html(text: str) -> str:
    """Escape text for Telegram HTML parse mode.

    转义 Telegram HTML 解析模式下的特殊字符（& < >）。注意 Telegram 的
    HTML 模式只支持少量标签（b/i/u/s/code/pre/a 等），不支持实体转义之外
    的任意 HTML，因此必须先转义再做标签插入。
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tool_hint_to_telegram_blockquote(text: str) -> str:
    """Render tool hints as an expandable blockquote (collapsed by default).

    将工具提示渲染为 Telegram 的“可展开引用块”（expandable blockquote），
    默认折叠，用户点击才展开，避免工具调用噪音占据大量屏幕空间。
    """
    return f"<blockquote expandable>{_escape_telegram_html(text)}</blockquote>" if text else ""


def _strip_md(s: str) -> str:
    """Strip markdown inline formatting from text.

    移除行内 Markdown 格式标记（加粗、下划线、删除线、行内代码），返回纯文本。
    用于在不支持格式的场景（如按钮 callback_data）下生成干净的文本。
    """
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'__(.+?)__', r'\1', s)
    s = re.sub(r'~~(.+?)~~', r'\1', s)
    s = re.sub(r'`([^`]+)`', r'\1', s)
    return s.strip()


def _strip_md_block(text: str) -> str:
    """Strip block-level and inline markdown for readable plain-text preview.

    Used during streaming mid-edits so users see clean text instead of raw
    markdown syntax while the response is still being generated.

    在流式过程中生成“可读纯文本预览”：流式增量编辑期间消息会反复更新，
    若直接显示原始 Markdown 语法会很丑，且完整 HTML 转换开销太大，因此
    先剥离所有块级与行内 Markdown 标记，仅展示纯文本内容。最终消息
    （_stream_end）才会做完整的 HTML 转换。
    """
    # Code blocks -> just the code
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', r'\1', text)
    # Headers -> plain text
    text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', text, flags=re.MULTILINE)
    # Blockquotes
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)
    # Bold / italic / strikethrough
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # 斜体：用前后非字母数字的断言避免误伤 some_var_name 这类标识符
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Bullet lists
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)
    # Numbered lists (normalize spacing)
    text = re.sub(r'^(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)
    return text


def _render_table_box(table_lines: list[str]) -> str:
    """Convert markdown pipe-table to compact aligned text for <pre> display.

    将 Markdown 管道表格转换为用制表符（box-drawing）对齐的纯文本，便于
    放入 <pre> 块等宽显示。原因：Telegram HTML 模式不支持 <table>，
    直接展示原始管道表格在等宽字体外会很凌乱。

    注意宽度计算使用东亚字符宽度（W/F 记 2 列），否则中日韩字符会导致
    对齐错位。
    """

    def dw(s: str) -> int:
        # 显示宽度：东亚宽字符（W/F）记 2，其余记 1
        return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)

    rows: list[list[str]] = []
    has_sep = False
    for line in table_lines:
        cells = [_strip_md(c) for c in line.strip().strip('|').split('|')]
        # 识别分隔行（如 |:---:|），跳过它
        if all(re.match(r'^:?-+:?$', c) for c in cells if c):
            has_sep = True
            continue
        rows.append(cells)
    if not rows or not has_sep:
        return '\n'.join(table_lines)

    # 补齐各行列数，计算每列最大显示宽度
    ncols = max(len(r) for r in rows)
    for r in rows:
        r.extend([''] * (ncols - len(r)))
    widths = [max(dw(r[c]) for r in rows) for c in range(ncols)]

    def dr(cells: list[str]) -> str:
        # 按显示宽度右填充空格，实现对齐
        return '  '.join(f'{c}{" " * (w - dw(c))}' for c, w in zip(cells, widths))

    out = [dr(rows[0])]
    out.append('  '.join('─' * w for w in widths))
    for row in rows[1:]:
        out.append(dr(row))
    return '\n'.join(out)


def _markdown_to_telegram_html(text: str) -> str:
    """
    Convert markdown to Telegram-safe HTML.

    将 Markdown 转换为 Telegram HTML 模式可识别的 HTML。由于 Telegram 只支持
    极少 HTML 标签且不支持嵌套任意结构，转换必须按特定顺序进行：先用占位符
    保护代码块/行内代码（避免其内容被后续步骤误处理），再依次处理表格、标题、
    引用、HTML 转义、链接、加粗、斜体、删除线、列表，最后还原占位符。
    标题先用 ⟪B⟫/⟪/B⟫ 占位（在 HTML 转义之后才替换为 <b></b>），以免被转义。
    """
    if not text:
        return ""

    # 1. Extract and protect code blocks (preserve content from other processing)
    # 用 \x00CB{n}\x00 占位符替换代码块，保护其内容不被后续正则误伤
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)

    # 1.5. Convert markdown tables to box-drawing (reuse code_block placeholders)
    # 逐行扫描连续的管道表格行，转换为对齐的 box-drawing 文本后作为代码块占位
    lines = text.split('\n')
    rebuilt: list[str] = []
    li = 0
    while li < len(lines):
        if re.match(r'^\s*\|.+\|', lines[li]):
            tbl: list[str] = []
            while li < len(lines) and re.match(r'^\s*\|.+\|', lines[li]):
                tbl.append(lines[li])
                li += 1
            box = _render_table_box(tbl)
            if box != '\n'.join(tbl):
                code_blocks.append(box)
                rebuilt.append(f"\x00CB{len(code_blocks) - 1}\x00")
            else:
                rebuilt.extend(tbl)
        else:
            rebuilt.append(lines[li])
            li += 1
    text = '\n'.join(rebuilt)

    # 2. Extract and protect inline code
    # 同样用 \x00IC{n}\x00 占位符保护行内代码
    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r'`([^`]+)`', save_inline_code, text)

    # 3. Headers # Title -> <b>Title</b> (preserve visual hierarchy)
    # 标题用占位符 ⟪B⟫/⟪/B⟫ 标记，待 HTML 转义后再替换为 <b></b>，避免被转义
    text = re.sub(r'^#{1,6}\s+(.+)$', r'⟪B⟫\1⟪/B⟫', text, flags=re.MULTILINE)

    # 4. Blockquotes > text -> just the text (before HTML escaping)
    # 引用块 > text 在转义前先去掉前缀，避免 > 被转义成 &gt;
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)

    # 5. Escape HTML special characters
    # 转义剩余文本中的 & < >，保证后续插入的标签不被破坏
    text = _escape_telegram_html(text)

    # 6. Links [text](url) - must be before bold/italic to handle nested cases
    # 链接必须在加粗/斜体之前处理，否则 **[text](url)** 会被先转成 <b>[text](url)</b>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 7. Bold **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # 8. Italic _text_ (avoid matching inside words like some_var_name)
    # 斜体用前后非字母数字断言，避免误匹配 some_var_name 中的下划线
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)

    # 9. Strikethrough ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 10. Bullet lists - item -> • item
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)

    # 10.5. Numbered lists  1. item -> 1. item (keep number, normalize indent)
    text = re.sub(r'^(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)

    # 11. Restore inline code with HTML tags
    # 还原行内代码占位符，并对代码内容做 HTML 转义
    for i, code in enumerate(inline_codes):
        # Escape HTML in code content
        escaped = _escape_telegram_html(code)
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore code blocks with HTML tags
    # 还原代码块占位符为 <pre><code>...</code></pre>
    for i, code in enumerate(code_blocks):
        # Escape HTML in code content
        escaped = _escape_telegram_html(code)
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    # 13. Restore header bold markers (inserted in step 3, after HTML escaping)
    # 此时已转义完毕，把标题占位符替换为真正的 <b></b>
    text = text.replace('⟪B⟫', '<b>').replace('⟪/B⟫', '</b>')

    return text


def _split_telegram_markdown_html(content: str, max_html_len: int) -> list[str]:
    """Split raw Telegram Markdown and return HTML chunks within Telegram's limit.

    先用 4000 字符限制拆分原始 Markdown，再逐块渲染成 HTML。由于 Markdown
    转成 HTML 后会因标签/实体而膨胀，可能超过 max_html_len（4096）。此时
    不能直接切割 HTML（会破坏标签），而是按比例反推一个更小的原始 Markdown
    预算 next_limit 重新拆分，再放回 pending 队列重新渲染校验，直到所有
    片段的 HTML 都不超过上限。极端情况下（无法再拆）退化为按 HTML 硬切。
    """
    chunks: list[str] = []
    pending = _split_telegram_markdown(content, TELEGRAM_MAX_MESSAGE_LEN)
    while pending:
        chunk = pending.pop(0)
        html = _markdown_to_telegram_html(chunk)
        if len(html) <= max_html_len:
            chunks.append(html)
            continue

        # Markdown can expand when rendered as HTML (tags/entities). Re-split
        # the raw markdown with a smaller budget instead of slicing HTML tags.
        # 按比例反推更小的原始 Markdown 预算：next_limit = chunk长度 * 目标/实际 - 8（余量）
        next_limit = max(1, int(len(chunk) * max_html_len / len(html)) - 8)
        next_limit = min(next_limit, len(chunk) - 1)
        if next_limit <= 0:
            # 预算已无法再缩小 -> 退化为按 HTML 硬切（split_message）
            chunks.extend(split_message(html, max_html_len))
            continue
        parts = _split_telegram_markdown(chunk, next_limit)
        if len(parts) == 1 and parts[0] == chunk:
            # 拆分后仍是整块（无法在更小预算下拆开）-> 退化为硬切
            chunks.extend(split_message(html, max_html_len))
            continue
        # 重新拆出的片段放回队首，下一轮重新渲染校验
        pending = parts + pending
    return chunks


_SEND_MAX_RETRIES = 3
_SEND_RETRY_BASE_DELAY = 0.5  # seconds, doubled each retry
_STREAM_EDIT_INTERVAL_DEFAULT = 0.6  # min seconds between edit_message_text calls
# 流式编辑最小间隔（秒）。Telegram 对 edit_message_text 有严格限流（约每秒 1 次
# 每个聊天），低于此间隔的编辑会被合并/跳过，避免触发 429。


@dataclass
class _StreamBuf:
    """Per-chat streaming accumulator for progressive message editing.

    每个聊天的流式累加缓冲：首次 delta 时发送一条新消息并记录其 message_id，
    后续 delta 累加到 text 中并按节流间隔 edit_message_text 就地更新该消息。
    stream_id 用于区分不同的流（避免上一轮流式残留状态污染新流）。
    """
    text: str = ""
    message_id: int | None = None
    last_edit: float = 0.0
    stream_id: str | None = None


@dataclass
class _QueuedTelegramUpdate:
    """Telegram update staged for per-session ordered processing.

    暂存的 Telegram 更新，用于按会话有序处理。Telegram 的 update 顺序与
    消息顺序可能不一致（网络抖动/重排），因此先入队再按 (message_id, update_id)
    排序后处理，保证同一会话内消息按用户发送顺序进入 AgentLoop。
    """

    kind: Literal["command", "message"]
    update: Update
    context: Any
    sort_key: tuple[int, int]


class TelegramConfig(Base):
    """Telegram channel configuration.

    Telegram 渠道配置。mode 支持 polling（长轮询，默认）与 webhook 两种
    收消息模式；webhook 模式要求公网 HTTPS URL 与 secret token。
    group_policy 控制群组触发策略：open（所有消息都响应）或 mention
    （仅 @机器人 或回复机器人的消息才响应）。
    """

    enabled: bool = False
    token: str = ""
    mode: Literal["polling", "webhook"] = "polling"
    allow_from: list[str] = Field(default_factory=list)
    proxy: str | None = None
    reply_to_message: bool = False
    react_emoji: str = "👀"
    group_policy: Literal["open", "mention"] = "mention"
    connection_pool_size: int = 32
    pool_timeout: float = 5.0
    streaming: bool = True
    # Enable inline keyboard buttons in Telegram messages.
    inline_keyboards: bool = False
    stream_edit_interval: float = Field(default=_STREAM_EDIT_INTERVAL_DEFAULT, ge=0.1)
    webhook_url: str = ""
    webhook_listen_host: str = "127.0.0.1"
    webhook_listen_port: int = Field(default=8081, ge=1, le=65535)
    webhook_path: str = "/telegram"
    webhook_secret_token: str = ""
    webhook_max_connections: int = Field(default=4, ge=1, le=100)

    @field_validator("webhook_path")
    @classmethod
    def webhook_path_must_start_with_slash(cls, value: str) -> str:
        # webhook_path 必须以 / 开头（本地 HTTP 路由），空则默认 /telegram
        value = value.strip() or "/telegram"
        if not value.startswith("/"):
            raise ValueError('webhook_path must start with "/"')
        return value

    @model_validator(mode="after")
    def validate_webhook_config(self) -> "TelegramConfig":
        # webhook 模式下校验：必须有公网 HTTPS URL、且 secret token
        # 仅含 A-Za-z0-9_-、长度 1-256（Telegram 官方要求）
        if self.mode != "webhook":
            return self

        url = self.webhook_url.strip()
        if not url:
            raise ValueError("webhook_url is required when Telegram mode is webhook")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("webhook_url must be a public HTTPS URL")
        secret = self.webhook_secret_token.strip()
        if not secret:
            raise ValueError("webhook_secret_token is required when Telegram mode is webhook")
        if len(secret) > 256 or re.match(r"^[A-Za-z0-9_-]+$", secret) is None:
            raise ValueError(
                "webhook_secret_token must be 1-256 characters using only A-Z, a-z, 0-9, _ and -"
            )
        return self


class TelegramChannel(BaseChannel):
    """
    Telegram channel using long polling or webhook mode.

    Long polling is the default. Webhook mode requires a public HTTPS URL and a
    Telegram secret token.

    Telegram 渠道实现。支持长轮询（默认）与 webhook 两种模式。负责：
    - 通过 python-telegram-bot 注册命令/消息处理器并接收 update；
    - 入站消息按会话有序处理（_enqueue_ordered_update），支持群组 @提及/回复触发、
      媒体组聚合、话题（forum thread）会话隔离、媒体下载与语音转写；
    - 出站消息支持流式增量编辑（send_delta）、富文本快速路径（sendRichMessage，
      Bot API 10.1）、Markdown->HTML 转换、媒体上传、表情回应与“正在输入”指示器。
    """

    name = "telegram"
    display_name = "Telegram"

    # Commands registered with Telegram's command menu
    # 注册到 Telegram 命令菜单的斜杠命令列表（用户在输入框点 / 时看到）
    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("new", "Start a new conversation"),
        BotCommand("stop", "Stop the current task"),
        BotCommand("restart", "Restart the bot"),
        BotCommand("status", "Show bot status"),
        BotCommand("history", "Show recent conversation messages"),
        BotCommand("goal", "Start a sustained objective (long-running task)"),
        BotCommand("pairing", "Manage DM pairing (approve/deny/list)"),
        BotCommand("model", "Switch runtime model preset"),
        BotCommand("skill", "List enabled skills"),
        BotCommand("dream", "Run Dream memory consolidation now"),
        BotCommand("dream_log", "Show the latest Dream memory change"),
        BotCommand("dream_restore", "Restore Dream memory to an earlier version"),
        BotCommand("help", "Show available commands"),
    ]

    # Regex for slash commands routed to AgentLoop via ``_forward_command``.
    # Hyphenated ``dream-*`` commands stay on a separate handler (below).
    # 这些斜杠命令通过 _forward_command 转发到消息总线，由 AgentLoop 统一处理。
    # 注意 Telegram 命令名不支持连字符，故 dream-log/dream-restore 在客户端
    # 用 dream_log/dream_restore 注册，下方单独的 handler 再做归一化映射。
    TELEGRAM_BUS_SLASH_COMMAND_RE = re.compile(
        r"^/(?:new|stop|restart|status|dream|history|goal|pairing|model|skill)(?:@\w+)?(?:\s+.*)?$"
    )

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return TelegramConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = TelegramConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: TelegramConfig = config
        self._app: Application | None = None
        self._chat_ids: dict[str, int] = {}  # Map sender_id to chat_id for replies
        self._typing_tasks: dict[str, asyncio.Task] = {}  # chat_id -> typing loop task
        # 媒体组（一次发送的多张图/文件）聚合缓冲：Telegram 会把媒体组拆成多条
        # update 投递，这里按 media_group_id 短暂缓存，攒齐后作为单轮消息转发
        self._media_group_buffers: dict[str, dict] = {}
        self._media_group_tasks: dict[str, asyncio.Task] = {}
        # (chat_id, message_id) -> message_thread_id：记住入站消息所属的话题，
        # 以便回复该消息时能回到同一话题（forum 群组的 thread）
        self._message_threads: dict[tuple[str, int], int] = {}
        self._bot_user_id: int | None = None
        self._bot_username: str | None = None
        self._stream_bufs: dict[str, _StreamBuf] = {}  # chat_id -> streaming state
        # 入站 update 有序队列：key 为会话键，值为暂存的 update 列表，
        # 配合 _inbound_workers 中的消费者协程按 (message_id, update_id) 排序处理
        self._inbound_buffers: dict[str, list[_QueuedTelegramUpdate]] = {}
        self._inbound_workers: dict[str, asyncio.Task] = {}
        self._rich_send_disabled: bool = False  # Latch off if Bot API < 10.1

    def is_allowed(self, sender_id: str) -> bool:
        """Preserve Telegram's legacy id|username allowlist matching.

        保留 Telegram 渠道历史上的 allow_from 匹配语义：sender_id 形如
        ``{user_id}|{username}``，allow_from 中既可以填纯数字 user_id，
        也可以填 @username（不带 @）。基类只做整体字符串匹配，故此处额外
        拆分 id|username 两部分分别匹配。
        """
        if super().is_allowed(sender_id):
            return True

        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list or "*" in allow_list:
            return False

        sender_str = str(sender_id)
        # 必须是 "id|username" 形式才尝试拆分匹配
        if sender_str.count("|") != 1:
            return False

        sid, username = sender_str.split("|", 1)
        if not sid.isdigit() or not username:
            return False

        return sid in allow_list or username in allow_list

    @staticmethod
    def _normalize_telegram_command(content: str) -> str:
        """Map Telegram-safe command aliases back to canonical nanobot commands.

        将 Telegram 安全的命令别名映射回 nanobot 规范命令。Telegram 命令名
        只允许字母数字和下划线，不能用连字符，而 nanobot 内部命令用的是
        dream-log / dream-restore，因此在此把 /dream_log、/dream_restore
        还原成 /dream-log、/dream-restore 再转发到总线。
        """
        if not content.startswith("/"):
            return content
        if content == "/dream_log" or content.startswith("/dream_log "):
            return content.replace("/dream_log", "/dream-log", 1)
        if content == "/dream_restore" or content.startswith("/dream_restore "):
            return content.replace("/dream_restore", "/dream-restore", 1)
        return content

    async def start(self) -> None:
        """Start the Telegram bot.

        启动 Telegram bot：构建 Application、注册命令与消息处理器、初始化并
        获取 bot 身份信息、设置命令菜单，最后按配置进入 webhook 或长轮询模式
        收消息，并循环睡眠保持运行直到 stop() 置 _running=False。
        """
        if not self.config.token:
            self.logger.error("bot token not configured")
            return

        self._running = True

        proxy = self.config.proxy or None

        # Separate pools so long-polling (getUpdates) never starves outbound sends.
        # 拆分两套连接池：api_request 用于发消息（大池），poll_request 专用于
        # getUpdates 长轮询（小池），避免长轮询占满连接池导致发送被饿死。
        api_request = HTTPXRequest(
            connection_pool_size=self.config.connection_pool_size,
            pool_timeout=self.config.pool_timeout,
            connect_timeout=30.0,
            read_timeout=30.0,
            proxy=proxy,
        )
        poll_request = HTTPXRequest(
            connection_pool_size=4,
            pool_timeout=self.config.pool_timeout,
            connect_timeout=30.0,
            read_timeout=30.0,
            proxy=proxy,
        )
        builder = (
            Application.builder()
            .token(self.config.token)
            .request(api_request)
            .get_updates_request(poll_request)
        )
        self._app = builder.build()
        self._app.add_error_handler(self._on_error)

        # Add command handlers (using Regex to support @username suffixes before bot initialization)
        # 用 Regex 而非 filters.COMMAND，是为了在 bot 初始化前就支持 /cmd@botname 这类带用户名后缀的命令
        self._app.add_handler(MessageHandler(filters.Regex(r"^/start(?:@\w+)?$"), self._on_start))
        self._app.add_handler(
            MessageHandler(
                filters.Regex(TelegramChannel.TELEGRAM_BUS_SLASH_COMMAND_RE),
                self._forward_command,
            )
        )
        # dream-log / dream_restore（含连字符/下划线变体）单独走 _forward_command
        self._app.add_handler(
            MessageHandler(
                filters.Regex(r"^/(dream-log|dream_log|dream-restore|dream_restore)(?:@\w+)?(?:\s+.*)?$"),
                self._forward_command,
            )
        )
        self._app.add_handler(MessageHandler(filters.Regex(r"^/help(?:@\w+)?$"), self._on_help))

        # Add message handler for text, photos, video, voice, documents, and locations
        self._app.add_handler(
            MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.VIDEO_NOTE
                 | filters.ANIMATION | filters.VOICE | filters.AUDIO
                 | filters.Document.ALL | filters.LOCATION)
                & ~filters.COMMAND,
                self._on_message
            )
        )

        # Conditionally register inline keyboard callback handler
        # 仅在开启 inline_keyboards 时注册回调查询处理器，并告知 Telegram
        # 我们要接收 callback_query 更新（否则只接收 message 更新）
        if self.config.inline_keyboards:
            self._app.add_handler(CallbackQueryHandler(self._on_callback_query))
            allowed_updates = ["message", "callback_query"]
            self.logger.debug("inline keyboards enabled")
        else:
            allowed_updates = ["message"]

        if self.config.mode == "webhook":
            self.logger.info("Starting bot (webhook mode)...")
        else:
            self.logger.info("Starting bot (polling mode)...")

        # Initialize and start receiving updates
        await self._app.initialize()
        await self._app.start()

        # Get bot info and register command menu
        # 获取 bot 自身的 id/username，用于后续 @提及检测与命令菜单注册
        bot_info = await self._app.bot.get_me()
        self._bot_user_id = getattr(bot_info, "id", None)
        self._bot_username = getattr(bot_info, "username", None)
        self.logger.info("bot @{} connected", bot_info.username)

        try:
            await self._app.bot.set_my_commands(self.BOT_COMMANDS)
            self.logger.debug("bot commands registered")
        except Exception as e:
            self.logger.warning("Failed to register bot commands: {}", e)

        if self.config.mode == "webhook":
            # ``url_path`` is the local HTTP route. ``webhook_url`` is the
            # public HTTPS URL Telegram calls; reverse proxies may rewrite it.
            # webhook 模式：本地监听 HTTP 路由接收 Telegram 回调，secret_token
            # 用于校验请求确实来自 Telegram（反向代理可能改写 URL）。
            await self._app.updater.start_webhook(
                listen=self.config.webhook_listen_host,
                port=self.config.webhook_listen_port,
                url_path=self.config.webhook_path.lstrip("/"),
                webhook_url=self.config.webhook_url.strip(),
                allowed_updates=allowed_updates,
                drop_pending_updates=False,
                secret_token=self.config.webhook_secret_token.strip(),
                max_connections=self.config.webhook_max_connections,
            )
        else:
            # Start polling (this runs until stopped)
            # 长轮询模式：drop_pending_updates=False 表示启动时处理积压的 update
            await self._app.updater.start_polling(
                allowed_updates=allowed_updates,
                drop_pending_updates=False,  # Process pending messages on startup
                error_callback=self._on_polling_error,
            )

        # Keep running until stopped
        # 主循环：每秒睡眠一次，直到 stop() 把 _running 置 False
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the Telegram bot.

        停止 bot 并清理所有后台任务：取消“正在输入”指示器、媒体组聚合任务、
        入站有序处理 worker，最后按 updater -> app -> shutdown 的顺序优雅
        关闭 python-telegram-bot 的 Application。
        """
        self._running = False

        # Cancel all typing indicators
        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)

        for task in self._media_group_tasks.values():
            task.cancel()
        self._media_group_tasks.clear()
        self._media_group_buffers.clear()

        for task in self._inbound_workers.values():
            task.cancel()
        self._inbound_workers.clear()
        self._inbound_buffers.clear()

        if self._app:
            self.logger.info("Stopping bot...")
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    @staticmethod
    def _get_media_type(path: str) -> str:
        """Guess media type from file extension.

        根据文件扩展名猜测媒体类型（photo/video/voice/audio/document），
        用于出站发送时选择对应的 Telegram API（send_photo 等）。
        """
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext in ("jpg", "jpeg", "png", "gif", "webp"):
            return "photo"
        if ext in ("mp4", "mov", "avi", "mkv", "webm", "3gp"):
            return "video"
        if ext == "ogg":
            return "voice"
        if ext in ("mp3", "m4a", "wav", "aac"):
            return "audio"
        return "document"

    @staticmethod
    def _is_remote_media_url(path: str) -> bool:
        return path.startswith(("http://", "https://"))

    @staticmethod
    def _is_rich_capability_error(exc: Exception) -> bool:
        """True when the error indicates sendRichMessage is unavailable.

        判断异常是否表示服务器不支持 sendRichMessage（Bot API < 10.1）。
        通过匹配 "method not found" / "unknown method" / "invalid parameter"
        来识别能力缺失，以便永久关闭富文本快速路径。
        """
        err = str(exc).lower()
        return (
            "method not found" in err
            or "unknown method" in err
            or "bad request: invalid parameter" in err
        )

    async def _try_send_rich(
        self,
        chat_id: int,
        content: str,
        reply_params=None,
        thread_kwargs: dict | None = None,
        reply_markup=None,
    ) -> bool:
        """Attempt sendRichMessage (Bot API 10.1). Returns True on success.

        尝试调用 Bot API 10.1 的 sendRichMessage 直接发送原生 Markdown，
        省去本地 Markdown->HTML 转换。成功返回 True；若服务器不支持
        （通过 _is_rich_capability_error 识别）则置位 _rich_send_disabled
        永久关闭此路径，后续直接走传统 HTML 路径。超时也返回 False 让
        调用方回退，但不锁定（可能只是临时网络问题）。
        """
        if not self._app:
            return False

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "rich_message": {
                "markdown": content,
            },
        }
        if reply_params is not None:
            # sendRichMessage uses reply_parameters (object), not reply_to_message_id.
            # sendRichMessage 用 reply_parameters 对象而非 reply_to_message_id 标量
            if hasattr(reply_params, "message_id"):
                payload["reply_parameters"] = {
                    "message_id": reply_params.message_id,
                    "allow_sending_without_reply": True,
                }
            else:
                payload["reply_parameters"] = reply_params
        if thread_kwargs:
            payload.update({k: v for k, v in thread_kwargs.items() if v is not None})
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            # do_api_request 用于调用尚未被 python-telegram-bot 封装的原生 API 方法
            await self._call_with_retry(
                self._app.bot.do_api_request,
                "sendRichMessage",
                api_kwargs=payload,
            )
            return True
        except BadRequest as exc:
            if self._is_rich_capability_error(exc):
                self.logger.debug("sendRichMessage not available, disabling")
                self._rich_send_disabled = True
            else:
                self.logger.debug("sendRichMessage rejected: {}", exc)
            return False
        except Exception as exc:
            err_str = str(exc).lower()
            is_timeout = "timed out" in err_str or isinstance(exc, TimedOut)
            if is_timeout:
                # 超时不锁定，让调用方回退到传统路径，下次仍可尝试
                self.logger.debug("sendRichMessage timeout, falling back to legacy path")
                return False
            self.logger.debug("sendRichMessage failed: {}", exc)
            return False

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Telegram.

        发送完整出站消息（非流式）。流程：先停掉“正在输入”并移除表情回应
        （仅对最终回复，进度消息不影响），随后逐个上传媒体，最后发送文本。
        文本优先尝试 sendRichMessage 富文本快速路径，失败则回退到本地
        Markdown->HTML 转换并按 4000 字符拆分发送。
        """
        if not self._app:
            self.logger.warning("bot not running")
            return

        # Only stop typing indicator and remove reaction for final responses
        # 仅最终回复才停止 typing 与移除回应；进度消息（_progress）保留它们
        if not msg.metadata.get("_progress", False):
            self._stop_typing(msg.chat_id)
            if reply_to_message_id := msg.metadata.get("message_id"):
                with suppress(ValueError):
                    await self._remove_reaction(msg.chat_id, int(reply_to_message_id))

        try:
            chat_id = int(msg.chat_id)
        except ValueError:
            self.logger.exception("Invalid chat_id: {}", msg.chat_id)
            return
        reply_to_message_id = msg.metadata.get("message_id")
        message_thread_id = msg.metadata.get("message_thread_id")
        # 若未显式给出 thread，但这是一条对历史消息的回复，则查表还原所属话题
        if message_thread_id is None and reply_to_message_id is not None:
            message_thread_id = self._message_threads.get((msg.chat_id, reply_to_message_id))
        thread_kwargs = {}
        if message_thread_id is not None:
            thread_kwargs["message_thread_id"] = message_thread_id

        reply_params = None
        if self.config.reply_to_message:
            if reply_to_message_id:
                reply_params = ReplyParameters(
                    message_id=reply_to_message_id,
                    allow_sending_without_reply=True
                )

        # Send media files
        for media_path in (msg.media or []):
            try:
                media_type = self._get_media_type(media_path)
                sender = {
                    "photo": self._app.bot.send_photo,
                    "video": self._app.bot.send_video,
                    "voice": self._app.bot.send_voice,
                    "audio": self._app.bot.send_audio,
                }.get(media_type, self._app.bot.send_document)
                param = {
                    "photo": "photo",
                    "video": "video",
                    "voice": "voice",
                    "audio": "audio",
                }.get(media_type, "document")
                extra: dict[str, Any] = {}
                if media_type == "video":
                    extra["supports_streaming"] = True

                # Telegram Bot API accepts HTTP(S) URLs directly for media params.
                if self._is_remote_media_url(media_path):
                    ok, error = validate_url_target(media_path)
                    if not ok:
                        raise ValueError(f"unsafe media URL: {error}")
                    await self._call_with_retry(
                        sender,
                        chat_id=chat_id,
                        **{param: media_path},
                        reply_parameters=reply_params,
                        **thread_kwargs,
                        **extra,
                    )
                    continue

                media_bytes = Path(media_path).read_bytes()
                filename = Path(media_path).name
                send_kwargs = {param: media_bytes, "filename": filename}
                await self._call_with_retry(
                    sender,
                    chat_id=chat_id,
                    reply_parameters=reply_params,
                    **thread_kwargs,
                    **extra,
                    **send_kwargs,
                )
            except Exception:
                filename = media_path.rsplit("/", 1)[-1]
                self.logger.exception("Failed to send media {}", media_path)
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=f"[Failed to send: {filename}]",
                    reply_parameters=reply_params,
                    **thread_kwargs,
                )

        # Send text content
        if msg.content and msg.content != "[empty message]":
            render_as_blockquote = bool(msg.metadata.get("_tool_hint"))
            buttons = getattr(msg, "buttons", None) or []
            reply_markup = self._build_keyboard(buttons) if buttons else None
            text = msg.content
            # Fallback: no native keyboard → splice labels into the message so the choices survive.
            if buttons and reply_markup is None:
                text = f"{text}\n\n{self._buttons_as_text(buttons)}"

            # Bot API 10.1 rich fast-path: send raw markdown via sendRichMessage.
            # All non-blockquote content tries rich first; _rich_send_disabled
            # latches off permanently if the server doesn't support it.
            if (
                not render_as_blockquote
                and not getattr(self, "_rich_send_disabled", False)
            ):
                rich_ok = await self._try_send_rich(
                    chat_id, text, reply_params, thread_kwargs, reply_markup,
                )
                if rich_ok:
                    return

            chunks = _split_telegram_markdown(text, TELEGRAM_MAX_MESSAGE_LEN)
            for i, chunk in enumerate(chunks):
                is_last = (i == len(chunks) - 1)
                await self._send_text(
                    chat_id, chunk, reply_params, thread_kwargs,
                    render_as_blockquote=render_as_blockquote,
                    reply_markup=reply_markup if is_last else None,
                )

    async def _call_with_retry(self, fn, *args, **kwargs):
        """Call an async Telegram API function with retry on pool/network timeout and RetryAfter."""
        from telegram.error import RetryAfter

        for attempt in range(1, _SEND_MAX_RETRIES + 1):
            try:
                return await fn(*args, **kwargs)
            except TimedOut:
                if attempt == _SEND_MAX_RETRIES:
                    raise
                delay = _SEND_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                self.logger.warning(
                    "timeout (attempt {}/{}), retrying in {:.1f}s",
                    attempt, _SEND_MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            except RetryAfter as e:
                if attempt == _SEND_MAX_RETRIES:
                    raise
                delay = float(e.retry_after)
                self.logger.warning(
                    "Flood Control (attempt {}/{}), retrying in {:.1f}s",
                    attempt, _SEND_MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)

    async def _send_text(
        self,
        chat_id: int,
        text: str,
        reply_params=None,
        thread_kwargs: dict | None = None,
        render_as_blockquote: bool = False,
        reply_markup=None,
    ) -> None:
        """Send a plain text message with HTML fallback."""
        try:
            html = _tool_hint_to_telegram_blockquote(text) if render_as_blockquote else _markdown_to_telegram_html(text)
            await self._call_with_retry(
                self._app.bot.send_message,
                chat_id=chat_id, text=html, parse_mode="HTML",
                reply_parameters=reply_params,
                reply_markup=reply_markup,
                **(thread_kwargs or {}),
            )
        except BadRequest as e:
            self.logger.warning("HTML parse failed, falling back to plain text: {}", e)
            try:
                await self._call_with_retry(
                    self._app.bot.send_message,
                    chat_id=chat_id,
                    text=text,
                    reply_parameters=reply_params,
                    reply_markup=reply_markup,
                    **(thread_kwargs or {}),
                )
            except Exception:
                self.logger.exception("Error sending message")
                raise

    @staticmethod
    def _is_not_modified_error(exc: Exception) -> bool:
        return isinstance(exc, BadRequest) and "message is not modified" in str(exc).lower()

    async def send_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None) -> None:
        """Progressive message editing: send on first delta, edit on subsequent ones."""
        if not self._app:
            return
        meta = metadata or {}
        int_chat_id = int(chat_id)
        stream_id = meta.get("_stream_id")

        if meta.get("_stream_end"):
            buf = self._stream_bufs.get(chat_id)
            if not buf or not buf.message_id or not buf.text:
                return
            if stream_id is not None and buf.stream_id is not None and buf.stream_id != stream_id:
                return
            self._stop_typing(chat_id)
            if reply_to_message_id := meta.get("message_id"):
                with suppress(ValueError):
                    await self._remove_reaction(chat_id, int(reply_to_message_id))
            thread_kwargs = {}
            if message_thread_id := meta.get("message_thread_id"):
                thread_kwargs["message_thread_id"] = message_thread_id
            raw_text = buf.text

            # Try sendRichMessage for final output (Bot API 10.1)
            if not getattr(self, "_rich_send_disabled", False):
                reply_params = None
                if reply_to_message_id := meta.get("message_id"):
                    reply_params = {"message_id": int(reply_to_message_id), "allow_sending_without_reply": True}
                rich_ok = await self._try_send_rich(
                    int_chat_id, raw_text, reply_params, thread_kwargs, None,
                )
                if rich_ok:
                    # Delete the streaming preview message
                    try:
                        await self._call_with_retry(
                            self._app.bot.delete_message,
                            chat_id=int_chat_id, message_id=buf.message_id,
                        )
                    except Exception:
                        pass  # Preview stays if delete fails
                    self._stream_bufs.pop(chat_id, None)
                    return

            # Legacy path: edit existing streaming message with HTML
            html_chunks = _split_telegram_markdown_html(raw_text, TELEGRAM_HTML_MAX_LEN)
            primary_html = html_chunks[0]
            extra_html_chunks = html_chunks[1:]
            try:
                await self._call_with_retry(
                    self._app.bot.edit_message_text,
                    chat_id=int_chat_id, message_id=buf.message_id,
                    text=primary_html, parse_mode="HTML",
                )
            except BadRequest as e:
                # Only fall back to plain text on actual HTML parse/format errors.
                # Network errors (TimedOut, NetworkError) should propagate immediately
                # to avoid doubling connection demand during pool exhaustion.
                if self._is_not_modified_error(e):
                    self.logger.debug("Final stream edit already applied for {}", chat_id)
                    self._stream_bufs.pop(chat_id, None)
                    return
                self.logger.debug("Final stream edit failed (HTML), trying plain: {}", e)
                # Fall back to raw markdown (not HTML) so users don't see raw tags.
                primary_plain = split_message(raw_text, TELEGRAM_MAX_MESSAGE_LEN)[0] if len(raw_text) > TELEGRAM_MAX_MESSAGE_LEN else raw_text
                try:
                    await self._call_with_retry(
                        self._app.bot.edit_message_text,
                        chat_id=int_chat_id, message_id=buf.message_id,
                        text=primary_plain,
                    )
                except Exception as e2:
                    if self._is_not_modified_error(e2):
                        self.logger.debug("Final stream plain edit already applied for {}", chat_id)
                    else:
                        self.logger.warning("Final stream edit failed: {}", e2)
                        raise  # Let ChannelManager handle retry
            for extra_html_chunk in extra_html_chunks:
                try:
                    await self._call_with_retry(
                        self._app.bot.send_message,
                        chat_id=int_chat_id, text=extra_html_chunk,
                        parse_mode="HTML",
                        **thread_kwargs,
                    )
                except Exception:
                    # Fall back to _send_text which handles HTML→plain gracefully.
                    await self._send_text(int_chat_id, extra_html_chunk)
            self._stream_bufs.pop(chat_id, None)
            return

        buf = self._stream_bufs.get(chat_id)
        if buf is None or (stream_id is not None and buf.stream_id is not None and buf.stream_id != stream_id):
            buf = _StreamBuf(stream_id=stream_id)
            self._stream_bufs[chat_id] = buf
        elif buf.stream_id is None:
            buf.stream_id = stream_id
        buf.text += delta

        if not buf.text.strip():
            return

        now = time.monotonic()
        thread_kwargs = {}
        if message_thread_id := meta.get("message_thread_id"):
            thread_kwargs["message_thread_id"] = message_thread_id
        if buf.message_id is None:
            preview = _strip_md_block(buf.text)
            try:
                sent = await self._call_with_retry(
                    self._app.bot.send_message,
                    chat_id=int_chat_id, text=preview,
                    **thread_kwargs,
                )
                buf.message_id = sent.message_id
                buf.last_edit = now
            except Exception as e:
                self.logger.warning("Stream initial send failed: {}", e)
                raise  # Let ChannelManager handle retry
        elif (now - buf.last_edit) >= self.config.stream_edit_interval:
            if len(buf.text) > TELEGRAM_MAX_MESSAGE_LEN:
                await self._flush_stream_overflow(int_chat_id, buf, thread_kwargs)
                buf.last_edit = now
                return
            preview = _strip_md_block(buf.text)
            try:
                await self._call_with_retry(
                    self._app.bot.edit_message_text,
                    chat_id=int_chat_id, message_id=buf.message_id,
                    text=preview,
                )
                buf.last_edit = now
            except Exception as e:
                if self._is_not_modified_error(e):
                    buf.last_edit = now
                    return
                self.logger.warning("Stream edit failed: {}", e)
                raise  # Let ChannelManager handle retry

    async def _flush_stream_overflow(
        self,
        chat_id: int,
        buf: "_StreamBuf",
        thread_kwargs: dict,
    ) -> None:
        """Split an oversized stream buffer mid-flight.

        Edits the current stream message with the first chunk, sends any
        intermediate chunks as standalone messages, then opens a new message
        for the tail so subsequent deltas continue streaming into it.
        """
        chunks = _split_telegram_markdown(buf.text, TELEGRAM_MAX_MESSAGE_LEN)
        if len(chunks) <= 1:
            return
        try:
            await self._call_with_retry(
                self._app.bot.edit_message_text,
                chat_id=chat_id, message_id=buf.message_id,
                text=chunks[0],
            )
        except Exception as e:
            if not self._is_not_modified_error(e):
                self.logger.warning("Stream overflow edit failed: {}", e)
                raise
        for chunk in chunks[1:-1]:
            await self._call_with_retry(
                self._app.bot.send_message,
                chat_id=chat_id, text=chunk, **thread_kwargs,
            )
        tail = chunks[-1]
        sent = await self._call_with_retry(
            self._app.bot.send_message,
            chat_id=chat_id, text=tail, **thread_kwargs,
        )
        buf.message_id = sent.message_id
        buf.text = tail

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.message or not update.effective_user:
            return

        user = update.effective_user
        sender_id = self._sender_id(user)
        if not self.is_allowed(sender_id):
            await self._send_pairing_code_if_private(sender_id, update.message, user)
            return
        await update.message.reply_text(
            f"👋 Hi {user.first_name}! I'm nanobot.\n\n"
            "Send me a message and I'll respond!\n"
            "Type /help to see available commands."
        )

    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command for allowed users only."""
        if not update.message or not update.effective_user:
            return
        user = update.effective_user
        sender_id = self._sender_id(user)
        if not self.is_allowed(sender_id):
            await self._send_pairing_code_if_private(sender_id, update.message, user)
            return
        await update.message.reply_text(build_help_text())

    @staticmethod
    def _sender_id(user) -> str:
        """Build sender_id with username for allowlist matching."""
        sid = str(user.id)
        return f"{sid}|{user.username}" if user.username else sid

    async def _send_pairing_code_if_private(self, sender_id: str, message, user) -> None:
        if message.chat.type != "private":
            return
        await self._handle_message(
            sender_id=sender_id,
            chat_id=str(message.chat_id),
            content="",
            metadata=self._build_message_metadata(message, user),
            is_dm=True,
        )

    @staticmethod
    def _derive_topic_session_key(message) -> str | None:
        """Derive topic-scoped session key for Telegram chats with threads."""
        message_thread_id = getattr(message, "message_thread_id", None)
        if message_thread_id is None:
            return None
        return f"telegram:{message.chat_id}:topic:{message_thread_id}"

    @staticmethod
    def _build_message_metadata(message, user) -> dict:
        """Build common Telegram inbound metadata payload."""
        reply_to = getattr(message, "reply_to_message", None)
        return {
            "message_id": message.message_id,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "is_group": message.chat.type != "private",
            "message_thread_id": getattr(message, "message_thread_id", None),
            "is_forum": bool(getattr(message.chat, "is_forum", False)),
            "reply_to_message_id": getattr(reply_to, "message_id", None) if reply_to else None,
        }

    async def _extract_reply_context(self, message) -> str | None:
        """Extract text from the message being replied to, if any."""
        reply = getattr(message, "reply_to_message", None)
        if not reply:
            return None
        text = getattr(reply, "text", None) or getattr(reply, "caption", None) or ""
        if len(text) > TELEGRAM_REPLY_CONTEXT_MAX_LEN:
            text = text[:TELEGRAM_REPLY_CONTEXT_MAX_LEN] + "..."

        if not text:
            return None

        bot_id, _ = await self._ensure_bot_identity()
        reply_user = getattr(reply, "from_user", None)

        if bot_id and reply_user and getattr(reply_user, "id", None) == bot_id:
            return f"[Reply to bot: {text}]"
        elif reply_user and getattr(reply_user, "username", None):
            return f"[Reply to @{reply_user.username}: {text}]"
        elif reply_user and getattr(reply_user, "first_name", None):
            return f"[Reply to {reply_user.first_name}: {text}]"
        else:
            return f"[Reply to: {text}]"

    async def _download_message_media(
        self, msg, *, add_failure_content: bool = False
    ) -> tuple[list[str], list[str]]:
        """Download media from a message (current or reply). Returns (media_paths, content_parts)."""
        media_file = None
        media_type = None
        if getattr(msg, "photo", None):
            media_file = msg.photo[-1]
            media_type = "image"
        elif getattr(msg, "voice", None):
            media_file = msg.voice
            media_type = "voice"
        elif getattr(msg, "audio", None):
            media_file = msg.audio
            media_type = "audio"
        elif getattr(msg, "document", None):
            media_file = msg.document
            media_type = "file"
        elif getattr(msg, "video", None):
            media_file = msg.video
            media_type = "video"
        elif getattr(msg, "video_note", None):
            media_file = msg.video_note
            media_type = "video"
        elif getattr(msg, "animation", None):
            media_file = msg.animation
            media_type = "animation"
        if not media_file or not self._app:
            return [], []
        try:
            file = await self._app.bot.get_file(media_file.file_id)
            ext = self._get_extension(
                media_type,
                getattr(media_file, "mime_type", None),
                getattr(media_file, "file_name", None),
            )
            media_dir = get_media_dir("telegram")
            unique_id = getattr(media_file, "file_unique_id", media_file.file_id)
            file_path = media_dir / f"{unique_id}{ext}"
            await file.download_to_drive(str(file_path))
            path_str = str(file_path)
            if media_type in ("voice", "audio"):
                transcription = await self.transcribe_audio(file_path)
                if transcription:
                    self.logger.info("Transcribed {}: {}...", media_type, transcription[:50])
                    return [path_str], [f"[transcription: {transcription}]"]
                return [path_str], [f"[{media_type}: {path_str}]"]
            return [path_str], [f"[{media_type}: {path_str}]"]
        except Exception as e:
            self.logger.warning("Failed to download message media: {}", e)
            if add_failure_content:
                return [], [f"[{media_type}: download failed]"]
            return [], []

    async def _ensure_bot_identity(self) -> tuple[int | None, str | None]:
        """Load bot identity once and reuse it for mention/reply checks."""
        if self._bot_user_id is not None or self._bot_username is not None:
            return self._bot_user_id, self._bot_username
        if not self._app:
            return None, None
        bot_info = await self._app.bot.get_me()
        self._bot_user_id = getattr(bot_info, "id", None)
        self._bot_username = getattr(bot_info, "username", None)
        return self._bot_user_id, self._bot_username

    @staticmethod
    def _has_mention_entity(
        text: str,
        entities,
        bot_username: str,
        bot_id: int | None,
    ) -> bool:
        """Check Telegram mention entities against the bot username."""
        handle = f"@{bot_username}".lower()
        for entity in entities or []:
            entity_type = getattr(entity, "type", None)
            if entity_type == "text_mention":
                user = getattr(entity, "user", None)
                if user is not None and bot_id is not None and getattr(user, "id", None) == bot_id:
                    return True
                continue
            if entity_type != "mention":
                continue
            offset = getattr(entity, "offset", None)
            length = getattr(entity, "length", None)
            if offset is None or length is None:
                continue
            if text[offset : offset + length].lower() == handle:
                return True
        return handle in text.lower()

    async def _is_group_message_for_bot(self, message) -> bool:
        """Allow group messages when policy is open, @mentioned, or replying to the bot."""
        if message.chat.type == "private" or self.config.group_policy == "open":
            return True

        bot_id, bot_username = await self._ensure_bot_identity()
        if bot_username:
            text = message.text or ""
            caption = message.caption or ""
            if self._has_mention_entity(
                text,
                getattr(message, "entities", None),
                bot_username,
                bot_id,
            ):
                return True
            if self._has_mention_entity(
                caption,
                getattr(message, "caption_entities", None),
                bot_username,
                bot_id,
            ):
                return True

        reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
        return bool(bot_id and reply_user and reply_user.id == bot_id)

    def _remember_thread_context(self, message) -> None:
        """Cache Telegram thread context by chat/message id for follow-up replies."""
        message_thread_id = getattr(message, "message_thread_id", None)
        if message_thread_id is None:
            return
        key = (str(message.chat_id), message.message_id)
        self._message_threads[key] = message_thread_id
        if len(self._message_threads) > 1000:
            self._message_threads.pop(next(iter(self._message_threads)))

    @staticmethod
    def _queue_key_for_message(message) -> str:
        """Return the final nanobot session key used for ordered Telegram ingress."""
        return TelegramChannel._derive_topic_session_key(message) or f"telegram:{message.chat_id}"

    @staticmethod
    def _sort_key_for_update(update: Update) -> tuple[int, int]:
        """Sort by chat message id first, then Telegram update id."""
        message = getattr(update, "message", None)
        message_id = int(getattr(message, "message_id", 0) or 0)
        update_id = int(getattr(update, "update_id", 0) or 0)
        return (message_id, update_id)

    def _enqueue_ordered_update(
        self,
        *,
        kind: Literal["command", "message"],
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Stage a Telegram update behind a short per-session reorder window."""
        message = update.message
        key = self._queue_key_for_message(message)
        self._inbound_buffers.setdefault(key, []).append(
            _QueuedTelegramUpdate(
                kind=kind,
                update=update,
                context=context,
                sort_key=self._sort_key_for_update(update),
            )
        )
        if key not in self._inbound_workers:
            self._inbound_workers[key] = asyncio.create_task(
                self._drain_ordered_updates(key)
            )

    async def _drain_ordered_updates(self, key: str) -> None:
        """Drain one Telegram session buffer in stable message order."""
        try:
            while self._running:
                await asyncio.sleep(0.2)
                batch = self._inbound_buffers.get(key, [])
                if not batch:
                    break
                self._inbound_buffers[key] = []
                batch.sort(key=lambda item: item.sort_key)
                for item in batch:
                    try:
                        if item.kind == "command":
                            await self._process_forward_command(item.update, item.context)
                        else:
                            await self._process_message_update(item.update, item.context)
                    except Exception as e:
                        self.logger.warning(
                            "Telegram queued update handling failed for {}: {}",
                            key,
                            e,
                        )
            if not self._inbound_buffers.get(key):
                self._inbound_buffers.pop(key, None)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.logger.warning("Telegram ordered update worker failed for {}: {}", key, e)
        finally:
            if not self._inbound_buffers.get(key):
                self._inbound_workers.pop(key, None)

    async def _forward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward slash commands to the bus for unified handling in AgentLoop."""
        if not update.message or not update.effective_user:
            return
        if not self._running:
            await self._process_forward_command(update, context)
            return
        self._enqueue_ordered_update(kind="command", update=update, context=context)

    async def _process_forward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process a queued slash command."""
        message = update.message
        user = update.effective_user
        sender_id = self._sender_id(user)
        if not self.is_allowed(sender_id):
            await self._send_pairing_code_if_private(sender_id, message, user)
            return
        self._remember_thread_context(message)

        # Strip @bot_username suffix if present
        content = message.text or ""
        if content.startswith("/") and "@" in content:
            cmd_part, *rest = content.split(" ", 1)
            cmd_part = cmd_part.split("@")[0]
            content = f"{cmd_part} {rest[0]}" if rest else cmd_part
        content = self._normalize_telegram_command(content)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=str(message.chat_id),
            content=content,
            metadata=self._build_message_metadata(message, user),
            session_key=self._derive_topic_session_key(message),
            is_dm=message.chat.type == "private",
        )

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming messages (text, photos, voice, documents)."""
        if not update.message or not update.effective_user:
            return
        if not self._running:
            await self._process_message_update(update, context)
            return
        self._enqueue_ordered_update(kind="message", update=update, context=context)

    async def _process_message_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Process a queued Telegram message update."""

        message = update.message
        user = update.effective_user
        chat_id = message.chat_id
        sender_id = self._sender_id(user)
        if not self.is_allowed(sender_id):
            await self._send_pairing_code_if_private(sender_id, message, user)
            return
        self._remember_thread_context(message)

        # Store chat_id for replies
        self._chat_ids[sender_id] = chat_id

        if not await self._is_group_message_for_bot(message):
            return

        # Build content from text and/or media
        content_parts = []
        media_paths = []

        # Text content
        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)

        # Location content
        if message.location:
            lat = message.location.latitude
            lon = message.location.longitude
            content_parts.append(f"[location: {lat}, {lon}]")

        # Download current message media
        current_media_paths, current_media_parts = await self._download_message_media(
            message, add_failure_content=True
        )
        media_paths.extend(current_media_paths)
        content_parts.extend(current_media_parts)
        if current_media_paths:
            self.logger.debug("Downloaded message media to {}", current_media_paths[0])

        # Reply context: text and/or media from the replied-to message
        reply = getattr(message, "reply_to_message", None)
        if reply is not None:
            reply_ctx = await self._extract_reply_context(message)
            reply_media, reply_media_parts = await self._download_message_media(reply)
            if reply_media:
                media_paths = reply_media + media_paths
                self.logger.debug("Attached replied-to media: {}", reply_media[0])
            tag = reply_ctx or (f"[Reply to: {reply_media_parts[0]}]" if reply_media_parts else None)
            if tag:
                content_parts.insert(0, tag)
        content = "\n".join(content_parts) if content_parts else "[empty message]"

        self.logger.debug("message from {}: {}...", sender_id, content[:50])

        str_chat_id = str(chat_id)
        metadata = self._build_message_metadata(message, user)
        session_key = self._derive_topic_session_key(message)

        # Telegram media groups: buffer briefly, forward as one aggregated turn.
        if media_group_id := getattr(message, "media_group_id", None):
            key = f"{str_chat_id}:{media_group_id}"
            if key not in self._media_group_buffers:
                self._media_group_buffers[key] = {
                    "sender_id": sender_id, "chat_id": str_chat_id,
                    "contents": [], "media": [],
                    "metadata": metadata,
                    "session_key": session_key,
                }
                self._start_typing(str_chat_id)
                await self._add_reaction(str_chat_id, message.message_id, self.config.react_emoji)
            buf = self._media_group_buffers[key]
            if content and content != "[empty message]":
                buf["contents"].append(content)
            buf["media"].extend(media_paths)
            if key not in self._media_group_tasks:
                self._media_group_tasks[key] = asyncio.create_task(self._flush_media_group(key))
            return

        # Start typing indicator before processing
        self._start_typing(str_chat_id)
        await self._add_reaction(str_chat_id, message.message_id, self.config.react_emoji)

        # Forward to the message bus
        await self._handle_message(
            sender_id=sender_id,
            chat_id=str_chat_id,
            content=content,
            media=media_paths,
            metadata=metadata,
            session_key=session_key,
        )

    async def _flush_media_group(self, key: str) -> None:
        """Wait briefly, then forward buffered media-group as one turn."""
        try:
            await asyncio.sleep(0.6)
            if not (buf := self._media_group_buffers.pop(key, None)):
                return
            content = "\n".join(buf["contents"]) or "[empty message]"
            await self._handle_message(
                sender_id=buf["sender_id"], chat_id=buf["chat_id"],
                content=content, media=list(dict.fromkeys(buf["media"])),
                metadata=buf["metadata"],
                session_key=buf.get("session_key"),
            )
        finally:
            self._media_group_tasks.pop(key, None)

    def _start_typing(self, chat_id: str) -> None:
        """Start sending 'typing...' indicator for a chat."""
        # Cancel any existing typing task for this chat
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    def _stop_typing(self, chat_id: str) -> None:
        """Stop the typing indicator for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _add_reaction(self, chat_id: str, message_id: int, emoji: str) -> None:
        """Add emoji reaction to a message (best-effort, non-blocking)."""
        if not self._app or not emoji:
            return
        try:
            await self._app.bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
        except Exception as e:
            self.logger.debug("reaction failed: {}", e)

    async def _remove_reaction(self, chat_id: str, message_id: int) -> None:
        """Remove emoji reaction from a message (best-effort, non-blocking)."""
        if not self._app:
            return
        try:
            await self._app.bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=message_id,
                reaction=[],
            )
        except Exception as e:
            self.logger.debug("reaction removal failed: {}", e)

    async def _typing_loop(self, chat_id: str) -> None:
        """Repeatedly send 'typing' action until cancelled."""
        try:
            with suppress(asyncio.CancelledError):
                while self._app:
                    await self._app.bot.send_chat_action(chat_id=int(chat_id), action="typing")
                    await asyncio.sleep(4)
        except Exception as e:
            self.logger.debug("Typing indicator stopped for {}: {}", chat_id, e)

    @staticmethod
    def _format_telegram_error(exc: Exception) -> str:
        """Return a short, readable error summary for logs."""
        text = str(exc).strip()
        if text:
            return text
        if exc.__cause__ is not None:
            cause = exc.__cause__
            cause_text = str(cause).strip()
            if cause_text:
                return f"{exc.__class__.__name__} ({cause_text})"
            return f"{exc.__class__.__name__} ({cause.__class__.__name__})"
        return exc.__class__.__name__

    def _on_polling_error(self, exc: Exception) -> None:
        """Keep long-polling network failures to a single readable line."""
        summary = self._format_telegram_error(exc)
        if isinstance(exc, (NetworkError, TimedOut)):
            self.logger.warning("polling network issue: {}", summary)
        else:
            self.logger.error("polling error: {}", summary)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log polling / handler errors instead of silently swallowing them."""
        summary = self._format_telegram_error(context.error)

        if isinstance(context.error, (NetworkError, TimedOut)):
            self.logger.warning("network issue: {}", summary)
        else:
            self.logger.error("error: {}", summary)

    def _get_extension(
        self,
        media_type: str,
        mime_type: str | None,
        filename: str | None = None,
    ) -> str:
        """Get file extension based on media type or original filename."""
        if mime_type:
            ext_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "image/webp": ".webp",
                "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
                "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
                "video/x-matroska": ".mkv", "video/3gpp": ".3gp",
            }
            if mime_type in ext_map:
                return ext_map[mime_type]

        type_map = {"image": ".jpg", "voice": ".ogg", "audio": ".mp3", "video": ".mp4", "file": ""}
        if ext := type_map.get(media_type, ""):
            return ext

        if filename:
            return "".join(Path(filename).suffixes)

        return ""

    def _build_keyboard(self, buttons: list) -> InlineKeyboardMarkup | None:
        """Build inline keyboard markup if inline_keyboards is enabled."""
        if not buttons or not self.config.inline_keyboards:
            return None
        keyboard = [
            [InlineKeyboardButton(label, callback_data=self._safe_callback_data(label)) for label in row]
            for row in buttons
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def _safe_callback_data(label: str) -> str:
        # Telegram caps callback_data at 64 bytes UTF-8; truncate at a char boundary so the keyboard still sends.
        encoded = label.encode("utf-8")
        if len(encoded) <= 64:
            return label
        return encoded[:64].decode("utf-8", errors="ignore")

    @staticmethod
    def _buttons_as_text(buttons: list[list[str]]) -> str:
        # Buttons are semantic options; when we can't render a keyboard, the user still needs to see them.
        return "\n".join(" ".join(f"[{label}]" for label in row) for row in buttons if row)

    async def _on_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard button clicks (callback queries)."""
        if not update.callback_query or not update.effective_user:
            return
        query = update.callback_query
        user = update.effective_user
        chat_id = query.message.chat_id if query.message else None
        sender_id = self._sender_id(user)
        if not chat_id:
            self.logger.warning("Callback query without chat_id")
            return
        if not self.is_allowed(sender_id):
            return
        button_label = query.data or ""
        await query.answer()
        if query.message:
            with suppress(Exception):
                await query.message.edit_reply_markup(reply_markup=None)
        self.logger.debug("Inline button tap from {}: {}", sender_id, button_label)
        self._start_typing(str(chat_id))
        await self._handle_message(
            sender_id=sender_id,
            chat_id=str(chat_id),
            content=button_label,
            metadata={
                "callback_query_id": query.id,
                "button_label": button_label,
                "user_id": user.id,
                "username": user.username,
                "first_name": user.first_name,
                "is_callback": True,
            },
        )
