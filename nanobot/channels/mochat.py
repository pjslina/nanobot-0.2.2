"""Mochat channel implementation using Socket.IO with HTTP polling fallback.

Mochat 渠道实现：通过 Socket.IO 长连接接收 Mochat 平台消息，并在 Socket.IO
不可用（SDK 未安装或连接失败）时自动降级为 HTTP 长轮询（watch / poll）模式。
支持会话（session）与面板（panel）两类目标，按游标（cursor）追踪进度、用
消息 ID 去重，并在面板场景下提供“仅 @提及即触发，否则延迟合并”的可配置回复策略。
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.paths import get_runtime_subdir
from nanobot.config.schema import Base

try:
    import socketio
    SOCKETIO_AVAILABLE = True
except ImportError:
    socketio = None
    SOCKETIO_AVAILABLE = False

try:
    import msgpack  # noqa: F401
    MSGPACK_AVAILABLE = True
except ImportError:
    MSGPACK_AVAILABLE = False

MAX_SEEN_MESSAGE_IDS = 2000  # 每个 target 最多保留的已见消息 ID 数，超出后按 FIFO 淘汰
CURSOR_SAVE_DEBOUNCE_S = 0.5  # 游标落盘的去抖动窗口：在 0.5s 内的多次更新合并为一次写入


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MochatBufferedEntry:
    """Buffered inbound entry for delayed dispatch.

    缓存的入站消息条目，用于“延迟合并”模式下：在面板非提及消息到达后不立即派发，
    而是先缓冲，待被 @提及、定时器到期或新消息打断时再一并发送。
    """
    raw_body: str
    author: str
    sender_name: str = ""
    sender_username: str = ""
    timestamp: int | None = None
    message_id: str = ""
    group_id: str = ""


@dataclass
class DelayState:
    """Per-target delayed message state.

    单个 target 的延迟消息状态：``entries`` 为待发送缓冲，``lock`` 保护并发
    入队/刷新，``timer`` 为“最近一条非提及消息后启动的延迟刷新任务”。
    """
    entries: list[MochatBufferedEntry] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timer: asyncio.Task | None = None


@dataclass
class MochatTarget:
    """Outbound target resolution result.

    出站目标解析结果：``id`` 为归一化后的会话/面板 ID，``is_panel`` 标记是否
    为面板（群）目标，决定后续走 panel 接口还是 session 接口。
    """
    id: str
    is_panel: bool


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _safe_dict(value: Any) -> dict:
    """Return *value* if it's a dict, else empty dict."""
    return value if isinstance(value, dict) else {}


def _str_field(src: dict, *keys: str) -> str:
    """Return the first non-empty str value found for *keys*, stripped."""
    for k in keys:
        v = src.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _make_synthetic_event(
    message_id: str, author: str, content: Any,
    meta: Any, group_id: str, converse_id: str,
    timestamp: Any = None, *, author_info: Any = None,
) -> dict[str, Any]:
    """Build a synthetic ``message.add`` event dict.

    构造一个与 Socket.IO 推送格式一致的 ``message.add`` 事件 dict。
    用于把 HTTP 轮询/notify 通道收到的原始消息“伪装”成统一事件，从而复用
    ``_process_inbound_event`` 这一条入站处理路径，避免在多处重复解析逻辑。
    """
    payload: dict[str, Any] = {
        "messageId": message_id, "author": author,
        "content": content, "meta": _safe_dict(meta),
        "groupId": group_id, "converseId": converse_id,
    }
    if author_info is not None:
        payload["authorInfo"] = _safe_dict(author_info)
    return {
        "type": "message.add",
        "timestamp": timestamp or datetime.utcnow().isoformat(),
        "payload": payload,
    }


def normalize_mochat_content(content: Any) -> str:
    """Normalize content payload to text.

    将 Mochat 多变的 content 字段归一化为纯文本：字符串直接 strip；
    None 返回空串；对象/列表则 JSON 序列化（保留中文），序列化失败再退回 str()。
    """
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)


def resolve_mochat_target(raw: str) -> MochatTarget:
    """Resolve id and target kind from user-provided target string.

    将用户/上层传入的目标字符串解析为 (id, is_panel)。支持以下前缀：
    ``mochat:`` 仅去前缀（不强制面板）；``group:`` / ``channel:`` / ``panel:``
    去前缀且强制为面板。无前缀时按惯例判断——以 ``session_`` 开头视为会话，
    否则视为面板。空串返回空目标。
    """
    trimmed = (raw or "").strip()
    if not trimmed:
        return MochatTarget(id="", is_panel=False)

    lowered = trimmed.lower()
    cleaned, forced_panel = trimmed, False
    for prefix in ("mochat:", "group:", "channel:", "panel:"):
        if lowered.startswith(prefix):
            cleaned = trimmed[len(prefix):].strip()
            forced_panel = prefix in {"group:", "channel:", "panel:"}
            break

    if not cleaned:
        return MochatTarget(id="", is_panel=False)
    # 未强制面板时，按 ID 是否以 "session_" 开头自动判定会话/面板
    return MochatTarget(id=cleaned, is_panel=forced_panel or not cleaned.startswith("session_"))


def extract_mention_ids(value: Any) -> list[str]:
    """Extract mention ids from heterogeneous mention payload.

    从 Mochat 不同形态的 @提及载荷中提取用户 ID 列表。载荷可能是字符串数组，
    也可能是对象数组（取 id / userId / _id 字段，命中即跳到下一项）。
    Mochat 不同接口返回的字段名不统一，故逐一兼容。
    """
    if not isinstance(value, list):
        return []
    ids: list[str] = []
    for item in value:
        if isinstance(item, str):
            if item.strip():
                ids.append(item.strip())
        elif isinstance(item, dict):
            for key in ("id", "userId", "_id"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    ids.append(candidate.strip())
                    break
    return ids


def resolve_was_mentioned(payload: dict[str, Any], agent_user_id: str) -> bool:
    """Resolve mention state from payload metadata and text fallback.

    判定本机器人是否在当前消息中被 @提及。优先看 meta 中的 mentioned /
    wasMentioned 标志及各类 mentions 字段；若 meta 未声明，则退化为在正文
    中查找 ``<@agent_user_id>`` 或 ``@agent_user_id`` 字面量（兜底）。
    """
    meta = payload.get("meta")
    if isinstance(meta, dict):
        if meta.get("mentioned") is True or meta.get("wasMentioned") is True:
            return True
        for f in ("mentions", "mentionIds", "mentionedUserIds", "mentionedUsers"):
            if agent_user_id and agent_user_id in extract_mention_ids(meta.get(f)):
                return True
    if not agent_user_id:
        return False
    content = payload.get("content")
    if not isinstance(content, str) or not content:
        return False
    return f"<@{agent_user_id}>" in content or f"@{agent_user_id}" in content


def resolve_require_mention(config: MochatConfig, session_id: str, group_id: str) -> bool:
    """Resolve mention requirement for group/panel conversations.

    解析“当前面板是否要求必须被 @提及才回复”。优先级：
    1. 该面板的 group_id 对应的 groups 规则；
    2. 否则看 session_id 规则；
    3. 否则看通配 ``*`` 规则；
    4. 都未配置则回落到全局 ``mention.require_in_groups``。
    """
    groups = config.groups or {}
    for key in (group_id, session_id, "*"):
        if key and key in groups:
            return bool(groups[key].require_mention)
    return bool(config.mention.require_in_groups)


def build_buffered_body(entries: list[MochatBufferedEntry], is_group: bool) -> str:
    """Build text body from one or more buffered entries.

    将一条或多条缓冲条目拼接为最终发送的正文。单条直接返回其原文；多条时若为
    群聊，则在每条前加 ``发送者: `` 前缀以便区分发言者，私聊则不加前缀，
    最后用换行连接并去除首尾空白。
    """
    if not entries:
        return ""
    if len(entries) == 1:
        return entries[0].raw_body
    lines: list[str] = []
    for entry in entries:
        if not entry.raw_body:
            continue
        if is_group:
            label = entry.sender_name.strip() or entry.sender_username.strip() or entry.author
            if label:
                lines.append(f"{label}: {entry.raw_body}")
                continue
        lines.append(entry.raw_body)
    return "\n".join(lines).strip()


def parse_timestamp(value: Any) -> int | None:
    """Parse event timestamp to epoch milliseconds.

    把 ISO 8601 字符串（含 ``Z`` 结尾）解析为 epoch 毫秒。Mochat 时间戳格式
    不固定且可能为空，解析失败时返回 None 而非抛异常，供下游安全使用。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Config classes
# ---------------------------------------------------------------------------

class MochatMentionConfig(Base):
    """Mochat mention behavior configuration."""

    require_in_groups: bool = False


class MochatGroupRule(Base):
    """Mochat per-group mention requirement."""

    require_mention: bool = False


class MochatConfig(Base):
    """Mochat channel configuration.

    Mochat 渠道配置。关键项：
    - ``claw_token``：访问 Mochat OpenAPI 的鉴权令牌（X-Claw-Token 头），必填；
    - ``socket_*``：Socket.IO 连接参数，``socket_disable_msgpack`` 可禁用 msgpack
      二进制序列化退回 JSON；
    - ``sessions`` / ``panels``：订阅目标列表，含 ``*`` 时启用自动发现；
    - ``mention`` / ``groups``：面板场景下的 @提及触发策略；
    - ``reply_delay_mode`` / ``reply_delay_ms``：面板非提及消息的延迟合并策略，
      ``non-mention`` 表示只有未被 @的消息延迟处理（被 @则立即触发并清空缓冲）。
    """

    enabled: bool = False
    base_url: str = "https://mochat.io"
    socket_url: str = ""
    socket_path: str = "/socket.io"
    socket_disable_msgpack: bool = False
    socket_reconnect_delay_ms: int = 1000
    socket_max_reconnect_delay_ms: int = 10000
    socket_connect_timeout_ms: int = 10000
    refresh_interval_ms: int = 30000
    watch_timeout_ms: int = 25000
    watch_limit: int = 100
    retry_delay_ms: int = 500
    max_retry_attempts: int = 0
    claw_token: str = ""
    agent_user_id: str = ""
    sessions: list[str] = Field(default_factory=list)
    panels: list[str] = Field(default_factory=list)
    allow_from: list[str] = Field(default_factory=list)
    mention: MochatMentionConfig = Field(default_factory=MochatMentionConfig)
    groups: dict[str, MochatGroupRule] = Field(default_factory=dict)
    reply_delay_mode: str = "non-mention"
    reply_delay_ms: int = 120000


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------

class MochatChannel(BaseChannel):
    """Mochat channel using socket.io with fallback polling workers.

    Mochat 渠道主类。优先用 Socket.IO 长连接订阅会话/面板事件；连接失败或
    SDK 缺失时自动降级为 HTTP 长轮询（session watch / panel poll）。
    内部维护每会话游标（持久化到磁盘、去抖落盘）、消息 ID 去重、面板延迟合并
    等有状态逻辑。出站消息依据目标解析走 session/panel 发送接口。
    """

    name = "mochat"
    display_name = "Mochat"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return MochatConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = MochatConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: MochatConfig = config
        self._http: httpx.AsyncClient | None = None
        self._socket: Any = None
        # _ws_connected：传输层已连；_ws_ready：已订阅完成、可走 WS 路径。
        # 两者分离是为了在订阅失败时仍能切回 fallback，而断线时不误判为 ready。
        self._ws_connected = self._ws_ready = False

        self._state_dir = get_runtime_subdir("mochat")
        self._cursor_path = self._state_dir / "session_cursors.json"
        self._session_cursor: dict[str, int] = {}  # session_id -> 已处理到的 seq
        self._cursor_save_task: asyncio.Task | None = None

        self._session_set: set[str] = set()
        self._panel_set: set[str] = set()
        # 配置中含 "*" 时开启自动发现：周期性拉取会话/面板列表并补充订阅
        self._auto_discover_sessions = self._auto_discover_panels = False

        self._cold_sessions: set[str] = set()  # 首次订阅的会话：丢弃首批历史事件，避免回放旧消息
        self._session_by_converse: dict[str, str] = {}  # converseId -> sessionId，用于 notify inbox 反查会话

        # 按 target 去重的消息 ID 集合与 FIFO 队列（限制最多 MAX_SEEN_MESSAGE_IDS 条）
        self._seen_set: dict[str, set[str]] = {}
        self._seen_queue: dict[str, deque[str]] = {}
        self._delay_states: dict[str, DelayState] = {}  # 面板延迟合并状态，按 target key 索引

        self._fallback_mode = False  # 是否处于 HTTP 轮询降级模式
        self._session_fallback_tasks: dict[str, asyncio.Task] = {}
        self._panel_fallback_tasks: dict[str, asyncio.Task] = {}
        self._refresh_task: asyncio.Task | None = None  # 周期刷新/自动发现任务
        self._target_locks: dict[str, asyncio.Lock] = {}  # 按 target 串行化事件处理，避免乱序

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start Mochat channel workers and websocket connection.

        渠道启动流程：
        1. 校验 ``claw_token``；
        2. 创建 HTTP 客户端、加载游标、用配置中的 sessions/panels 初始化目标集合；
        3. 立即拉取一次目标列表（不订阅新发现项），覆盖自动发现场景；
        4. 尝试建立 Socket.IO 连接，失败则启动 HTTP 长轮询降级 worker；
        5. 启动周期刷新循环，并阻塞在 sleep 循环中保持渠道存活。
        """
        if not self.config.claw_token:
            self.logger.error("claw_token not configured")
            return

        self._running = True
        self._http = httpx.AsyncClient(timeout=30.0)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        await self._load_session_cursors()
        self._seed_targets_from_config()
        await self._refresh_targets(subscribe_new=False)

        # Socket.IO 连接失败 -> 降级为 HTTP 长轮询
        if not await self._start_socket_client():
            await self._ensure_fallback_workers()

        self._refresh_task = asyncio.create_task(self._refresh_loop())
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop all workers and clean up resources.

        停止渠道：取消刷新循环与降级 worker、取消所有延迟合并定时器、
        断开 Socket.IO、取消游标去抖任务并最终落盘一次游标、关闭 HTTP 客户端。
        """
        self._running = False
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None

        await self._stop_fallback_workers()
        await self._cancel_delay_timers()

        if self._socket:
            with suppress(Exception):
                await self._socket.disconnect()
            self._socket = None

        if self._cursor_save_task:
            self._cursor_save_task.cancel()
            self._cursor_save_task = None
        await self._save_session_cursors()

        if self._http:
            await self._http.aclose()
            self._http = None
        self._ws_connected = self._ws_ready = False

    async def send(self, msg: OutboundMessage) -> None:
        """Send outbound message to session or panel.

        发送出站消息：合并正文与媒体 URL（媒体以 URL 形式拼接到正文末尾），
        解析目标类型（session/panel），分别调用对应的 Mochat OpenAPI 发送接口。
        面板发送需额外携带 groupId（从入站 metadata 透传），否则平台无法正确归档。
        """
        if not self.config.claw_token:
            self.logger.warning("claw_token missing, skip send")
            return

        parts = ([msg.content.strip()] if msg.content and msg.content.strip() else [])
        if msg.media:
            parts.extend(m for m in msg.media if isinstance(m, str) and m.strip())
        content = "\n".join(parts).strip()
        if not content:
            return

        target = resolve_mochat_target(msg.chat_id)
        if not target.id:
            self.logger.warning("outbound target is empty")
            return

        is_panel = (target.is_panel or target.id in self._panel_set) and not target.id.startswith("session_")
        try:
            if is_panel:
                await self._api_send("/api/claw/groups/panels/send", "panelId", target.id,
                                     content, msg.reply_to, self._read_group_id(msg.metadata))
            else:
                await self._api_send("/api/claw/sessions/send", "sessionId", target.id,
                                     content, msg.reply_to)
        except Exception:
            self.logger.exception("Failed to send message")
            raise

    # ---- config / init helpers ---------------------------------------------

    def _seed_targets_from_config(self) -> None:
        """从配置初始化会话/面板目标集合，并记录“冷启动”会话。

        “*” 不作为具体 ID 加入集合，而是开启自动发现标志。
        配置中显式列出的会话若尚无游标，则标记为冷启动（cold），
        首次订阅返回的历史事件将被丢弃，避免回复旧消息。
        """
        sessions, self._auto_discover_sessions = self._normalize_id_list(self.config.sessions)
        panels, self._auto_discover_panels = self._normalize_id_list(self.config.panels)
        self._session_set.update(sessions)
        self._panel_set.update(panels)
        for sid in sessions:
            if sid not in self._session_cursor:
                self._cold_sessions.add(sid)

    @staticmethod
    def _normalize_id_list(values: list[str]) -> tuple[list[str], bool]:
        """清洗 ID 列表：去空白、去重、排序，并分离出 ``*`` 通配符标志。

        返回 (去重排序后的具体 ID 列表, 是否包含 "*" 即开启自动发现)。
        """
        cleaned = [str(v).strip() for v in values if str(v).strip()]
        return sorted({v for v in cleaned if v != "*"}), "*" in cleaned

    # ---- websocket ---------------------------------------------------------

    async def _start_socket_client(self) -> bool:
        """建立 Socket.IO 长连接并注册事件处理器。

        返回是否成功连接。关键点：
        - 优先使用 msgpack 二进制序列化（更省带宽），未安装则回退 JSON；
        - ``connect`` 成功后立即订阅所有目标，订阅成功才置 ``_ws_ready``，
          失败则切回 fallback worker；``disconnect`` 时同样切回 fallback；
        - 注册两类事件：``claw.session.events`` / ``claw.panel.events`` 为批量
          事件流，``notify:chat.*`` 为单条增量通知（inbox/message 增删改）。
        """
        if not SOCKETIO_AVAILABLE:
            self.logger.warning("python-socketio not installed, using polling fallback")
            return False

        serializer = "default"
        if not self.config.socket_disable_msgpack:
            if MSGPACK_AVAILABLE:
                serializer = "msgpack"
            else:
                self.logger.warning("msgpack not installed but socket_disable_msgpack=false; using JSON")

        client = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=self.config.max_retry_attempts or None,
            reconnection_delay=max(0.1, self.config.socket_reconnect_delay_ms / 1000.0),
            reconnection_delay_max=max(0.1, self.config.socket_max_reconnect_delay_ms / 1000.0),
            logger=False, engineio_logger=False, serializer=serializer,
        )

        @client.event
        async def connect() -> None:
            self._ws_connected, self._ws_ready = True, False
            self.logger.info("websocket connected")
            subscribed = await self._subscribe_all()
            self._ws_ready = subscribed
            # 订阅成功才停掉 fallback；否则继续保留 fallback 兜底
            await (self._stop_fallback_workers() if subscribed else self._ensure_fallback_workers())

        @client.event
        async def disconnect() -> None:
            if not self._running:
                return
            self._ws_connected = self._ws_ready = False
            self.logger.warning("websocket disconnected")
            # 断线立即切回 fallback，避免漏消息
            await self._ensure_fallback_workers()

        @client.event
        async def connect_error(data: Any) -> None:
            self.logger.error("websocket connect error: {}", data)

        @client.on("claw.session.events")
        async def on_session_events(payload: dict[str, Any]) -> None:
            await self._handle_watch_payload(payload, "session")

        @client.on("claw.panel.events")
        async def on_panel_events(payload: dict[str, Any]) -> None:
            await self._handle_watch_payload(payload, "panel")

        # notify:chat.* 为单条增量通知：inbox 新增、message 增/改/撤/删
        for ev in ("notify:chat.inbox.append", "notify:chat.message.add",
                    "notify:chat.message.update", "notify:chat.message.recall",
                    "notify:chat.message.delete"):
            client.on(ev, self._build_notify_handler(ev))

        socket_url = (self.config.socket_url or self.config.base_url).strip().rstrip("/")
        socket_path = (self.config.socket_path or "/socket.io").strip().lstrip("/")

        try:
            self._socket = client
            await client.connect(
                socket_url, transports=["websocket"], socketio_path=socket_path,
                auth={"token": self.config.claw_token},
                wait_timeout=max(1.0, self.config.socket_connect_timeout_ms / 1000.0),
            )
            return True
        except Exception:
            self.logger.exception("Failed to connect websocket")
            with suppress(Exception):
                await client.disconnect()
            self._socket = None
            return False

    def _build_notify_handler(self, event_name: str):
        """为 notify:chat.* 事件构造分发处理器：inbox.append 走收件箱路径，
        其余 message.* 走面板消息路径。用闭包捕获事件名以复用同一 handler 工厂。"""
        async def handler(payload: Any) -> None:
            if event_name == "notify:chat.inbox.append":
                await self._handle_notify_inbox_append(payload)
            elif event_name.startswith("notify:chat.message."):
                await self._handle_notify_chat_message(payload)
        return handler

    # ---- subscribe ---------------------------------------------------------

    async def _subscribe_all(self) -> bool:
        """订阅所有已知的会话与面板。任一失败仍继续尝试其余，整体返回是否全成功。

        若开启自动发现，则顺带刷新一次目标列表以订阅新发现项。
        """
        ok = await self._subscribe_sessions(sorted(self._session_set))
        ok = await self._subscribe_panels(sorted(self._panel_set)) and ok
        if self._auto_discover_sessions or self._auto_discover_panels:
            await self._refresh_targets(subscribe_new=True)
        return ok

    async def _subscribe_sessions(self, session_ids: list[str]) -> bool:
        """订阅会话事件流，并处理订阅时返回的初始事件。

        通过 ``com.claw.im.subscribeSessions`` 调用，传入当前游标，平台据此回放
        自游标之后的事件。返回的初始事件可能是列表、``{sessions: [...]}`` 或单条，
        此处统一展开后逐条交给 ``_handle_watch_payload`` 处理。
        """
        if not session_ids:
            return True
        for sid in session_ids:
            if sid not in self._session_cursor:
                self._cold_sessions.add(sid)

        ack = await self._socket_call("com.claw.im.subscribeSessions", {
            "sessionIds": session_ids, "cursors": self._session_cursor,
            "limit": self.config.watch_limit,
        })
        if not ack.get("result"):
            self.logger.error("subscribeSessions failed: {}", ack.get('message', 'unknown error'))
            return False

        data = ack.get("data")
        items: list[dict[str, Any]] = []
        # 平台返回结构不统一，三种形态都要兼容
        if isinstance(data, list):
            items = [i for i in data if isinstance(i, dict)]
        elif isinstance(data, dict):
            sessions = data.get("sessions")
            if isinstance(sessions, list):
                items = [i for i in sessions if isinstance(i, dict)]
            elif "sessionId" in data:
                items = [data]
        for p in items:
            await self._handle_watch_payload(p, "session")
        return True

    async def _subscribe_panels(self, panel_ids: list[str]) -> bool:
        """订阅面板（群）事件流。未开启自动发现且无面板 ID 时直接视为成功。"""
        if not self._auto_discover_panels and not panel_ids:
            return True
        ack = await self._socket_call("com.claw.im.subscribePanels", {"panelIds": panel_ids})
        if not ack.get("result"):
            self.logger.error("subscribePanels failed: {}", ack.get('message', 'unknown error'))
            return False
        return True

    async def _socket_call(self, event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """通过 Socket.IO ``call`` 发起一次带超时的 RPC 请求。

        统一捕获异常并归一化返回为 dict：成功透传服务端返回；失败返回
        ``{result: False, message: ...}``；非 dict 返回包装为 ``{result: True, data}``。
        """
        if not self._socket:
            return {"result": False, "message": "socket not connected"}
        try:
            raw = await self._socket.call(event_name, payload, timeout=10)
        except Exception as e:
            return {"result": False, "message": str(e)}
        return raw if isinstance(raw, dict) else {"result": True, "data": raw}

    # ---- refresh / discovery -----------------------------------------------

    async def _refresh_loop(self) -> None:
        """周期性刷新目标列表的循环。

        每个间隔（refresh_interval_ms）刷新一次自动发现的目标；若处于降级模式，
        同时确保 fallback worker 仍存活（可能因前次失败未启动）。异常不中断循环。
        """
        interval_s = max(1.0, self.config.refresh_interval_ms / 1000.0)
        while self._running:
            await asyncio.sleep(interval_s)
            try:
                await self._refresh_targets(subscribe_new=self._ws_ready)
            except Exception as e:
                self.logger.warning("refresh failed: {}", e)
            if self._fallback_mode:
                await self._ensure_fallback_workers()

    async def _refresh_targets(self, subscribe_new: bool) -> None:
        """按需刷新会话/面板目录：仅在对应自动发现标志开启时执行。"""
        if self._auto_discover_sessions:
            await self._refresh_sessions_directory(subscribe_new)
        if self._auto_discover_panels:
            await self._refresh_panels(subscribe_new)

    async def _refresh_sessions_directory(self, subscribe_new: bool) -> None:
        """拉取会话目录，发现新会话则加入集合、订阅并维护 converseId 反查表。"""
        try:
            response = await self._post_json("/api/claw/sessions/list", {})
        except Exception as e:
            self.logger.warning("listSessions failed: {}", e)
            return

        sessions = response.get("sessions")
        if not isinstance(sessions, list):
            return

        new_ids: list[str] = []
        for s in sessions:
            if not isinstance(s, dict):
                continue
            sid = _str_field(s, "sessionId")
            if not sid:
                continue
            if sid not in self._session_set:
                self._session_set.add(sid)
                new_ids.append(sid)
                if sid not in self._session_cursor:
                    self._cold_sessions.add(sid)
            # 维护 converseId -> sessionId 映射，供 notify inbox 反查会话
            cid = _str_field(s, "converseId")
            if cid:
                self._session_by_converse[cid] = sid

        if not new_ids:
            return
        if self._ws_ready and subscribe_new:
            await self._subscribe_sessions(new_ids)
        if self._fallback_mode:
            await self._ensure_fallback_workers()

    async def _refresh_panels(self, subscribe_new: bool) -> None:
        """拉取工作区面板列表，发现新面板则加入集合并订阅。

        过滤条件：``type`` 非 0 的不是普通面板（可能是子频道/特殊类型），跳过。
        """
        try:
            response = await self._post_json("/api/claw/groups/get", {})
        except Exception as e:
            self.logger.warning("getWorkspaceGroup failed: {}", e)
            return

        raw_panels = response.get("panels")
        if not isinstance(raw_panels, list):
            return

        new_ids: list[str] = []
        for p in raw_panels:
            if not isinstance(p, dict):
                continue
            pt = p.get("type")
            if isinstance(pt, int) and pt != 0:
                continue
            pid = _str_field(p, "id", "_id")
            if pid and pid not in self._panel_set:
                self._panel_set.add(pid)
                new_ids.append(pid)

        if not new_ids:
            return
        if self._ws_ready and subscribe_new:
            await self._subscribe_panels(new_ids)
        if self._fallback_mode:
            await self._ensure_fallback_workers()

    # ---- fallback workers --------------------------------------------------

    async def _ensure_fallback_workers(self) -> None:
        """确保每个已知的会话/面板都有一个存活的 fallback worker。

        在 Socket.IO 不可用或断线时启用 HTTP 长轮询兜底。对每个目标只在
        “无任务或任务已结束”时创建新任务，避免重复创建。
        """
        if not self._running:
            return
        self._fallback_mode = True
        for sid in sorted(self._session_set):
            t = self._session_fallback_tasks.get(sid)
            if not t or t.done():
                self._session_fallback_tasks[sid] = asyncio.create_task(self._session_watch_worker(sid))
        for pid in sorted(self._panel_set):
            t = self._panel_fallback_tasks.get(pid)
            if not t or t.done():
                self._panel_fallback_tasks[pid] = asyncio.create_task(self._panel_poll_worker(pid))

    async def _stop_fallback_workers(self) -> None:
        """取消所有 fallback worker 并清空任务表，退出降级模式。"""
        self._fallback_mode = False
        tasks = [*self._session_fallback_tasks.values(), *self._panel_fallback_tasks.values()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._session_fallback_tasks.clear()
        self._panel_fallback_tasks.clear()

    async def _session_watch_worker(self, session_id: str) -> None:
        """会话级 HTTP 长轮询 worker：调用 ``/api/claw/sessions/watch`` 阻塞等待新事件。

        服务端在 ``watch_timeout_ms`` 内有新事件即返回，否则超时返回空，循环再拉。
        与 Socket.IO 路径共用 ``_handle_watch_payload`` 处理逻辑，保证降级透明。
        """
        while self._running and self._fallback_mode:
            try:
                payload = await self._post_json("/api/claw/sessions/watch", {
                    "sessionId": session_id, "cursor": self._session_cursor.get(session_id, 0),
                    "timeoutMs": self.config.watch_timeout_ms, "limit": self.config.watch_limit,
                })
                await self._handle_watch_payload(payload, "session")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.warning("watch fallback error ({}): {}", session_id, e)
                await asyncio.sleep(max(0.1, self.config.retry_delay_ms / 1000.0))

    async def _panel_poll_worker(self, panel_id: str) -> None:
        """面板级轮询 worker：定期拉取最近消息，过滤已处理后派发。

        面板无 watch 长轮询接口，只能定时拉取消息列表（``reversed`` 让最旧的先处理，
        便于按时间顺序与去重对齐）。把每条原始消息构造成 synthetic event，
        复用 ``_process_inbound_event`` 入站路径。
        """
        sleep_s = max(1.0, self.config.refresh_interval_ms / 1000.0)
        while self._running and self._fallback_mode:
            try:
                resp = await self._post_json("/api/claw/groups/panels/messages", {
                    "panelId": panel_id, "limit": min(100, max(1, self.config.watch_limit)),
                })
                msgs = resp.get("messages")
                if isinstance(msgs, list):
                    # 逆序处理：API 通常返回最新优先，反转后按时间顺序派发
                    for m in reversed(msgs):
                        if not isinstance(m, dict):
                            continue
                        evt = _make_synthetic_event(
                            message_id=str(m.get("messageId") or ""),
                            author=str(m.get("author") or ""),
                            content=m.get("content"),
                            meta=m.get("meta"), group_id=str(resp.get("groupId") or ""),
                            converse_id=panel_id, timestamp=m.get("createdAt"),
                            author_info=m.get("authorInfo"),
                        )
                        await self._process_inbound_event(panel_id, evt, "panel")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.warning("panel polling error ({}): {}", panel_id, e)
            await asyncio.sleep(sleep_s)

    # ---- inbound event processing ------------------------------------------

    async def _handle_watch_payload(self, payload: dict[str, Any], target_kind: str) -> None:
        """处理一次 watch/订阅返回的批量事件载荷。

        关键步骤：
        1. 按 ``{kind}:{target_id}`` 取锁串行化，避免同一目标的并发事件乱序；
        2. 推进游标（session 场景）：用 payload.cursor 或每条事件的 seq 更新，
           且只前进不回退，防止重复处理；
        3. 冷启动会话直接丢弃首批事件（避免回放历史）；
        4. 对 ``message.add`` 类型事件交由 ``_process_inbound_event`` 处理。
        """
        if not isinstance(payload, dict):
            return
        target_id = _str_field(payload, "sessionId")
        if not target_id:
            return

        lock = self._target_locks.setdefault(f"{target_kind}:{target_id}", asyncio.Lock())
        async with lock:
            prev = self._session_cursor.get(target_id, 0) if target_kind == "session" else 0
            pc = payload.get("cursor")
            if target_kind == "session" and isinstance(pc, int) and pc >= 0:
                self._mark_session_cursor(target_id, pc)

            raw_events = payload.get("events")
            if not isinstance(raw_events, list):
                return
            # 冷启动会话的首批事件视为历史回放，丢弃整批
            if target_kind == "session" and target_id in self._cold_sessions:
                self._cold_sessions.discard(target_id)
                return

            for event in raw_events:
                if not isinstance(event, dict):
                    continue
                seq = event.get("seq")
                # 用每条事件自身的 seq 推进游标（仅在更大时），保证断点续传精度
                if target_kind == "session" and isinstance(seq, int) and seq > self._session_cursor.get(target_id, prev):
                    self._mark_session_cursor(target_id, seq)
                if event.get("type") == "message.add":
                    await self._process_inbound_event(target_id, event, target_kind)

    async def _process_inbound_event(self, target_id: str, event: dict[str, Any], target_kind: str) -> None:
        """处理单条入站 ``message.add`` 事件，决定是否及如何派发到消息总线。

        流程：过滤自身消息与未授权发送者 -> 消息 ID 去重 -> 解析正文与发言者信息 ->
        判定是否被 @提及与是否要求提及 -> 根据 reply_delay_mode 决定“立即派发”或
        “进入延迟缓冲”。面板 + non-mention 模式下：被 @提及立即触发并刷新缓冲，
        未被 @则入队等待 ``reply_delay_ms`` 后或下一条 @触发时合并发送。
        """
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return

        author = _str_field(payload, "author")
        # 跳过机器人自己发的消息，避免回环
        if not author or (self.config.agent_user_id and author == self.config.agent_user_id):
            return
        if not self.is_allowed(author):
            return

        message_id = _str_field(payload, "messageId")
        seen_key = f"{target_kind}:{target_id}"
        # 消息 ID 去重：已见过则跳过（Socket.IO 与 fallback 可能重复投递）
        if message_id and self._remember_message_id(seen_key, message_id):
            return

        raw_body = normalize_mochat_content(payload.get("content")) or "[empty message]"
        ai = _safe_dict(payload.get("authorInfo"))
        sender_name = _str_field(ai, "nickname", "email")
        sender_username = _str_field(ai, "agentId")

        group_id = _str_field(payload, "groupId")
        is_group = bool(group_id)
        was_mentioned = resolve_was_mentioned(payload, self.config.agent_user_id)
        require_mention = target_kind == "panel" and is_group and resolve_require_mention(self.config, target_id, group_id)
        # non-mention 模式：仅未被 @的消息走延迟缓冲；被 @的立即触发
        use_delay = target_kind == "panel" and self.config.reply_delay_mode == "non-mention"

        # 要求提及却未被提及，且不启用延迟缓冲 -> 直接丢弃
        if require_mention and not was_mentioned and not use_delay:
            return

        entry = MochatBufferedEntry(
            raw_body=raw_body, author=author, sender_name=sender_name,
            sender_username=sender_username, timestamp=parse_timestamp(event.get("timestamp")),
            message_id=message_id, group_id=group_id,
        )

        if use_delay:
            delay_key = seen_key
            if was_mentioned:
                # 被 @提及：立即刷新缓冲（连同本条一起发送），打断延迟定时器
                await self._flush_delayed_entries(delay_key, target_id, target_kind, "mention", entry)
            else:
                # 未被 @：入队等待延迟定时器或下一条 @触发
                await self._enqueue_delayed_entry(delay_key, target_id, target_kind, entry)
            return

        await self._dispatch_entries(target_id, target_kind, [entry], was_mentioned)

    # ---- dedup / buffering -------------------------------------------------

    def _remember_message_id(self, key: str, message_id: str) -> bool:
        """记录已见消息 ID，返回是否重复。

        用 set 做 O(1) 查重，同时用 deque 维护插入顺序，超过 MAX_SEEN_MESSAGE_IDS
        时按 FIFO 淘汰最旧 ID 并从 set 中移除，避免无界增长。
        """
        seen_set = self._seen_set.setdefault(key, set())
        seen_queue = self._seen_queue.setdefault(key, deque())
        if message_id in seen_set:
            return True
        seen_set.add(message_id)
        seen_queue.append(message_id)
        while len(seen_queue) > MAX_SEEN_MESSAGE_IDS:
            seen_set.discard(seen_queue.popleft())
        return False

    async def _enqueue_delayed_entry(self, key: str, target_id: str, target_kind: str, entry: MochatBufferedEntry) -> None:
        """将一条非提及消息入队，并（重）启动延迟刷新定时器。

        每次入队都会取消旧的定时器并重启，实现“最后一条消息后等待 reply_delay_ms
        再刷新”的滑动窗口语义。
        """
        state = self._delay_states.setdefault(key, DelayState())
        async with state.lock:
            state.entries.append(entry)
            if state.timer:
                state.timer.cancel()
            state.timer = asyncio.create_task(self._delay_flush_after(key, target_id, target_kind))

    async def _delay_flush_after(self, key: str, target_id: str, target_kind: str) -> None:
        """延迟刷新任务：等待 reply_delay_ms 后触发一次刷新。"""
        await asyncio.sleep(max(0, self.config.reply_delay_ms) / 1000.0)
        await self._flush_delayed_entries(key, target_id, target_kind, "timer", None)

    async def _flush_delayed_entries(self, key: str, target_id: str, target_kind: str, reason: str, entry: MochatBufferedEntry | None) -> None:
        """刷新并派发缓冲中的条目。

        ``reason`` 为 ``"mention"`` 时表示被 @提及触发（``was_mentioned`` 透传 True），
        ``"timer"`` 表示延迟定时器到期。``entry`` 不为空时先追加到缓冲。
        关键点：用 ``asyncio.current_task()`` 判断当前任务是否就是 state.timer，
        避免定时器任务被自身取消；刷新后清空缓冲与定时器。
        """
        state = self._delay_states.setdefault(key, DelayState())
        async with state.lock:
            if entry:
                state.entries.append(entry)
            current = asyncio.current_task()
            # 仅取消“非自身”的定时器，避免当前 mention 触发把自己取消掉
            if state.timer and state.timer is not current:
                state.timer.cancel()
            state.timer = None
            entries = state.entries[:]
            state.entries.clear()
        if entries:
            await self._dispatch_entries(target_id, target_kind, entries, reason == "mention")

    async def _dispatch_entries(self, target_id: str, target_kind: str, entries: list[MochatBufferedEntry], was_mentioned: bool) -> None:
        """把若干缓冲条目合并为一条正文，调用基类 _handle_message 转发到消息总线。

        metadata 携带最后一条条目的消息 ID、时间戳、群信息、缓冲条数等，
        供后续出站回复（如 reply_to、group_id 透传）使用。
        """
        if not entries:
            return
        last = entries[-1]
        is_group = bool(last.group_id)
        body = build_buffered_body(entries, is_group) or "[empty message]"
        await self._handle_message(
            sender_id=last.author, chat_id=target_id, content=body,
            metadata={
                "message_id": last.message_id, "timestamp": last.timestamp,
                "is_group": is_group, "group_id": last.group_id,
                "sender_name": last.sender_name, "sender_username": last.sender_username,
                "target_kind": target_kind, "was_mentioned": was_mentioned,
                "buffered_count": len(entries),
            },
        )

    async def _cancel_delay_timers(self) -> None:
        """停止时取消所有延迟刷新定时器并清空状态。"""
        for state in self._delay_states.values():
            if state.timer:
                state.timer.cancel()
        self._delay_states.clear()

    # ---- notify handlers ---------------------------------------------------

    async def _handle_notify_chat_message(self, payload: Any) -> None:
        """处理 ``notify:chat.message.*`` 单条面板消息通知。

        过滤掉非面板目标（不在 _panel_set 中），把原始通知包装成 synthetic
        ``message.add`` 事件，复用面板入站处理路径。
        """
        if not isinstance(payload, dict):
            return
        group_id = _str_field(payload, "groupId")
        panel_id = _str_field(payload, "converseId", "panelId")
        if not group_id or not panel_id:
            return
        if self._panel_set and panel_id not in self._panel_set:
            return

        evt = _make_synthetic_event(
            message_id=str(payload.get("_id") or payload.get("messageId") or ""),
            author=str(payload.get("author") or ""),
            content=payload.get("content"), meta=payload.get("meta"),
            group_id=group_id, converse_id=panel_id,
            timestamp=payload.get("createdAt"), author_info=payload.get("authorInfo"),
        )
        await self._process_inbound_event(panel_id, evt, "panel")

    async def _handle_notify_inbox_append(self, payload: Any) -> None:
        """处理 ``notify:chat.inbox.append`` 收件箱新增通知（会话场景）。

        收件箱通知只携带 converseId，不含 sessionId，需通过 ``_session_by_converse``
        反查；映射缺失时立即刷新一次会话目录补全映射，再反查一次。
        带 groupId 的视为群消息（走面板路径），此处跳过。
        """
        if not isinstance(payload, dict) or payload.get("type") != "message":
            return
        detail = payload.get("payload")
        if not isinstance(detail, dict):
            return
        if _str_field(detail, "groupId"):
            return
        converse_id = _str_field(detail, "converseId")
        if not converse_id:
            return

        session_id = self._session_by_converse.get(converse_id)
        if not session_id:
            # 映射缺失：刷新会话目录补全 converseId -> sessionId
            await self._refresh_sessions_directory(self._ws_ready)
            session_id = self._session_by_converse.get(converse_id)
        if not session_id:
            return

        evt = _make_synthetic_event(
            message_id=str(detail.get("messageId") or payload.get("_id") or ""),
            author=str(detail.get("messageAuthor") or ""),
            content=str(detail.get("messagePlainContent") or detail.get("messageSnippet") or ""),
            meta={"source": "notify:chat.inbox.append", "converseId": converse_id},
            group_id="", converse_id=converse_id, timestamp=payload.get("createdAt"),
        )
        await self._process_inbound_event(session_id, evt, "session")

    # ---- cursor persistence ------------------------------------------------

    def _mark_session_cursor(self, session_id: str, cursor: int) -> None:
        if cursor < 0 or cursor < self._session_cursor.get(session_id, 0):
            return
        self._session_cursor[session_id] = cursor
        if not self._cursor_save_task or self._cursor_save_task.done():
            self._cursor_save_task = asyncio.create_task(self._save_cursor_debounced())

    async def _save_cursor_debounced(self) -> None:
        await asyncio.sleep(CURSOR_SAVE_DEBOUNCE_S)
        await self._save_session_cursors()

    async def _load_session_cursors(self) -> None:
        if not self._cursor_path.exists():
            return
        try:
            data = json.loads(self._cursor_path.read_text("utf-8"))
        except Exception as e:
            self.logger.warning("Failed to read cursor file: {}", e)
            return
        cursors = data.get("cursors") if isinstance(data, dict) else None
        if isinstance(cursors, dict):
            for sid, cur in cursors.items():
                if isinstance(sid, str) and isinstance(cur, int) and cur >= 0:
                    self._session_cursor[sid] = cur

    async def _save_session_cursors(self) -> None:
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._cursor_path.write_text(json.dumps({
                "schemaVersion": 1, "updatedAt": datetime.utcnow().isoformat(),
                "cursors": self._session_cursor,
            }, ensure_ascii=False, indent=2) + "\n", "utf-8")
        except Exception as e:
            self.logger.warning("Failed to save cursor file: {}", e)

    # ---- HTTP helpers ------------------------------------------------------

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._http:
            raise RuntimeError("Mochat HTTP client not initialized")
        url = f"{self.config.base_url.strip().rstrip('/')}{path}"
        response = await self._http.post(url, headers={
            "Content-Type": "application/json", "X-Claw-Token": self.config.claw_token,
        }, json=payload)
        if not response.is_success:
            raise RuntimeError(f"Mochat HTTP {response.status_code}: {response.text[:200]}")
        try:
            parsed = response.json()
        except Exception:
            parsed = response.text
        if isinstance(parsed, dict) and isinstance(parsed.get("code"), int):
            if parsed["code"] != 200:
                msg = str(parsed.get("message") or parsed.get("name") or "request failed")
                raise RuntimeError(f"Mochat API error: {msg} (code={parsed['code']})")
            data = parsed.get("data")
            return data if isinstance(data, dict) else {}
        return parsed if isinstance(parsed, dict) else {}

    async def _api_send(self, path: str, id_key: str, id_val: str,
                        content: str, reply_to: str | None, group_id: str | None = None) -> dict[str, Any]:
        """Unified send helper for session and panel messages."""
        body: dict[str, Any] = {id_key: id_val, "content": content}
        if reply_to:
            body["replyTo"] = reply_to
        if group_id:
            body["groupId"] = group_id
        return await self._post_json(path, body)

    @staticmethod
    def _read_group_id(metadata: dict[str, Any]) -> str | None:
        if not isinstance(metadata, dict):
            return None
        value = metadata.get("group_id") or metadata.get("groupId")
        return value.strip() if isinstance(value, str) and value.strip() else None
