"""Microsoft Teams channel MVP using a tiny built-in HTTP webhook server.

Scope:
- DM-focused MVP
- text inbound/outbound
- conversation reference persistence
- sender allowlist support
- optional inbound Bot Framework bearer-token validation
- no attachments/cards/polls yet

中文概述：Microsoft Teams 渠道的 MVP 实现。通过内置的轻量 HTTP webhook
服务器接收 Bot Framework 推送的活动（activity），回复时直接调用 Bot
Framework REST API。为保证后续回复能命中正确的会话，会持久化每个会话的
ConversationRef（service_url / conversation_id 等），并按 TTL 过期清理。
入站消息默认会校验 Bot Framework 的 bearer JWT，防止伪造请求。
"""

from __future__ import annotations

import asyncio
import html
import importlib.util
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

try:  # pragma: no cover - Windows fallback path
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

import httpx
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.paths import get_workspace_path
from nanobot.config.schema import Base

# 是否具备 msteams 渠道运行依赖：jwt 校验需要 PyJWT + cryptography，
# 缺失时渠道无法启动（但仍可导入本模块，便于未安装依赖的环境加载配置）。
MSTEAMS_AVAILABLE = (
    importlib.util.find_spec("jwt") is not None
    and importlib.util.find_spec("cryptography") is not None
)

if TYPE_CHECKING:
    import jwt

if MSTEAMS_AVAILABLE:
    import jwt

MSTEAMS_REF_TTL_DAYS = 30
MSTEAMS_WEBCHAT_HOST = "webchat.botframework.com"
# 默认受信 service_url 主机白名单：仅允许向这些主机回发 bearer 请求，
# 防止伪造的 serviceUrl 把请求/令牌发往攻击者控制的域名。覆盖商业版、
# GCC/Gov/DOD 版 Teams 以及 *.botframework.com。
MSTEAMS_DEFAULT_TRUSTED_SERVICE_URL_HOSTS = [
    "smba.trafficmanager.net",
    "smba.infra.gcc.teams.microsoft.com",
    "smba.infra.gov.teams.microsoft.us",
    "smba.infra.dod.teams.microsoft.us",
    "*.botframework.com",
]
MSTEAMS_REF_META_FILENAME = "msteams_conversations_meta.json"
MSTEAMS_REF_LOCK_FILENAME = "msteams_conversations.lock"
# 触摸（刷新 updated_at）的最小间隔：避免高频回复时每次都落盘，仅按需刷新。
MSTEAMS_REF_TOUCH_INTERVAL_S = 300


class MSTeamsConfig(Base):
    """Microsoft Teams channel configuration.

    中文配置说明：
    - app_id/app_password：Azure Bot 应用的凭据，用于换取 Bot Framework
      access token 并校验入站 JWT 的 audience。
    - host/port/path：内置 webhook 服务器监听地址与路径（默认 0.0.0.0:3978/api/messages）。
    - validate_inbound_auth：是否校验入站请求的 Bot Framework bearer JWT；
      关闭后任何知道 webhook URL 者均可伪造消息，仅本地调试可关闭。
    - ref_ttl_days：会话引用（ConversationRef）的过期天数，过期后会被清理。
    - prune_web_chat_refs / prune_non_personal_ref：是否清理 Web Chat 与非
      个人（群/频道）会话引用--本 MVP 仅支持 DM。
    - ref_touch_interval_s：活跃会话 updated_at 的最小刷新间隔，避免高频落盘。
    - trusted_service_url_hosts：允许回发的 service_url 主机白名单（支持 *.domain 通配）。
    """

    enabled: bool = False
    app_id: str = ""
    app_password: str = ""
    tenant_id: str = ""
    host: str = "0.0.0.0"
    port: int = 3978
    path: str = "/api/messages"
    allow_from: list[str] = Field(default_factory=list)
    reply_in_thread: bool = True
    mention_only_response: str = "Hi — what can I help with?"
    validate_inbound_auth: bool = True
    ref_ttl_days: int = Field(default=MSTEAMS_REF_TTL_DAYS, ge=1)
    prune_web_chat_refs: bool = True
    prune_non_personal_refs: bool = True
    ref_touch_interval_s: int = Field(default=MSTEAMS_REF_TOUCH_INTERVAL_S, ge=0)
    trusted_service_url_hosts: list[str] = Field(
        default_factory=lambda: MSTEAMS_DEFAULT_TRUSTED_SERVICE_URL_HOSTS.copy()
    )


@dataclass
class ConversationRef:
    """Minimal stored conversation reference for replies.

    Bot Framework 回复需要 knowing：往哪个 service_url 发、发到哪个 conversation_id、
    以哪个 bot 身份、是否作为某条消息的线程回复（replyToId=activity_id）。
    本结构即这些字段的持久化载体，updated_at 用于 TTL 过期与跨进程合并。
    """

    service_url: str
    conversation_id: str
    bot_id: str | None = None
    activity_id: str | None = None
    conversation_type: str | None = None
    tenant_id: str | None = None
    updated_at: float | None = None


class MSTeamsChannel(BaseChannel):
    """Microsoft Teams channel (DM-first MVP).

    基于 Python 标准库 ThreadingHTTPServer 暴露 webhook，借助 asyncio 的
    run_coroutine_threadsafe 把请求线程里的工作切回事件循环执行；出站走
    Bot Framework REST API。会话引用通过文件锁跨进程安全持久化。
    """

    name = "msteams"
    display_name = "Microsoft Teams"

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return MSTeamsConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = MSTeamsConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: MSTeamsConfig = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._http: httpx.AsyncClient | None = None
        # Bot Framework access token 缓存：提前 60s 失效以避免边界过期
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._botframework_openid_config_url = (
            "https://login.botframework.com/v1/.well-known/openidconfiguration"
        )
        # OpenID 配置与 JWKS 均缓存 1 小时，避免每次入站校验都拉取
        self._botframework_openid_config: dict[str, Any] | None = None
        self._botframework_openid_config_expires_at: float = 0.0
        self._botframework_jwks: dict[str, Any] | None = None
        self._botframework_jwks_expires_at: float = 0.0
        # 会话引用采用“主文件 + meta sidecar”双文件布局：主文件存会话信息，
        # meta sidecar 存 updated_at 时间戳，便于跨进程合并与 TTL 过期。
        self._refs_path = get_workspace_path() / "state" / "msteams_conversations.json"
        self._refs_path.parent.mkdir(parents=True, exist_ok=True)
        self._refs_meta_path = self._refs_path.parent / MSTEAMS_REF_META_FILENAME
        self._refs_lock_path = self._refs_path.parent / MSTEAMS_REF_LOCK_FILENAME
        # 进程内可重入锁：保护 self._conversation_refs 的读写；跨进程靠 _refs_file_lock
        self._refs_guard = threading.RLock()
        self._conversation_refs: dict[str, ConversationRef] = self._load_refs()
        with self._refs_guard:
            # 启动时立即按 TTL/受信主机/Web Chat/非个人规则清理一次过期引用
            if self._prune_conversation_refs():
                self._save_refs_locked(prune=True)

    async def start(self) -> None:
        """Start the Teams webhook listener.

        启动流程：校验依赖与凭据 -> 记录运行事件循环与 httpx 客户端 -> 在守护线程
        里跑内置 ThreadingHTTPServer -> 通过 run_coroutine_threadsafe 把每个入站
        请求切回事件循环执行鉴权与活动处理 -> 主协程空转保活直到 stop()。
        """
        if not MSTEAMS_AVAILABLE:
            self.logger.error("PyJWT not installed. Run: pip install nanobot-ai[msteams]")
            return

        if not self.config.app_id or not self.config.app_password:
            self.logger.error("app_id/app_password not configured")
            return

        if not self.config.validate_inbound_auth:
            self.logger.warning(
                "Inbound auth validation was explicitly DISABLED in config. "
                "Anyone who knows the webhook URL can send messages as any user. "
                "Only disable this for local development or controlled testing."
            )

        # 保存事件循环引用，供 HTTP 线程把回调投递回异步上下文
        self._loop = asyncio.get_running_loop()
        self._http = httpx.AsyncClient(timeout=30.0)
        self._running = True

        channel = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                # 仅接受配置的 webhook 路径，其余一律 404
                if self.path != channel.config.path:
                    self.send_response(404)
                    self.end_headers()
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length > 0 else b"{}"
                    payload = json.loads(raw.decode("utf-8"))
                except Exception as e:
                    channel.logger.warning("Invalid request body: {}", e)
                    self.send_response(400)
                    self.end_headers()
                    return

                auth_header = self.headers.get("Authorization", "")
                if channel.config.validate_inbound_auth:
                    try:
                        # HTTP 线程无法直接 await，需把协程投递回事件循环并阻塞等待结果
                        fut = asyncio.run_coroutine_threadsafe(
                            channel._validate_inbound_auth(auth_header, payload),
                            channel._loop,
                        )
                        fut.result(timeout=15)
                    except Exception as e:
                        channel.logger.warning("Inbound auth validation failed: {}", e)
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(b'{"error":"unauthorized"}')
                        return
                try:
                    # 鉴权通过后再次切回事件循环处理活动；无论处理结果都回 200，
                    # 以免 Bot Framework 反复重投同一条活动
                    fut = asyncio.run_coroutine_threadsafe(
                        channel._handle_activity(payload),
                        channel._loop,
                    )
                    fut.result(timeout=15)
                except Exception as e:
                    channel.logger.warning("Activity handling failed: {}", e)

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, format: str, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer((self.config.host, self.config.port), Handler)
        # 守护线程跑 serve_forever，主协程通过 while+sleep 保活
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="nanobot-msteams",
            daemon=True,
        )
        self._server_thread.start()

        self.logger.info(
            "Webhook listening on http://{}:{}{}",
            self.config.host,
            self.config.port,
            self.config.path,
        )

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the channel.

        优雅关闭：先停 HTTP 服务器（停止接受新请求）并 join 线程，再关闭 httpx 客户端。
        """
        self._running = False
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2)
        self._server_thread = None
        if self._http:
            await self._http.aclose()
            self._http = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a plain text reply into an existing Teams conversation.

        出站流程：查 chat_id 对应的 ConversationRef -> 校验 service_url 受信 ->
        换取 access token -> 调用 Bot Framework v3/conversations/.../activities
        发送消息；若开启 reply_in_thread 且有 activity_id，则以 replyToId 形式
        回到原线程。发送成功后刷新该会话引用的 updated_at 以续期 TTL。
        """
        if not self._http:
            raise RuntimeError("MSTeams HTTP client not initialized")

        ref = self._conversation_refs.get(str(msg.chat_id))
        if not ref:
            raise RuntimeError(f"MSTeams conversation ref not found for chat_id={msg.chat_id}")

        if not self._is_trusted_service_url(ref.service_url):
            raise RuntimeError(
                f"MSTeams conversation ref has untrusted service_url for chat_id={msg.chat_id}"
            )

        token = await self._get_access_token()
        base_url = f"{ref.service_url.rstrip('/')}/v3/conversations/{ref.conversation_id}/activities"
        # 仅当开启线程回复且记录了原始 activity_id 时，才设 replyToId 回到原线程
        use_thread_reply = self.config.reply_in_thread and bool(ref.activity_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "type": "message",
            "text": msg.content or " ",
        }
        if use_thread_reply:
            payload["replyToId"] = ref.activity_id

        try:
            resp = await self._http.post(base_url, headers=headers, json=payload)
            resp.raise_for_status()
            self.logger.info("Message sent to {}", ref.conversation_id)
            # 续期：活跃会话不应因 TTL 过期被清理
            self._touch_conversation_ref(str(msg.chat_id), persist=True)
        except Exception:
            self.logger.exception("Send failed")
            raise

    async def _handle_activity(self, activity: dict[str, Any]) -> None:
        """Handle inbound Teams/Bot Framework activity.

        入站流程：仅处理 message 类型活动 -> 抽取发送者/会话/service_url ->
        校验 service_url 受信 -> 去掉自发消息 -> DM-only 过滤 -> 清洗文本 ->
        权限校验 -> 记录 ConversationRef（供后续回复使用） -> 转交基类 _handle_message。
        """
        if activity.get("type") != "message":
            return

        conversation = activity.get("conversation") or {}
        from_user = activity.get("from") or {}
        recipient = activity.get("recipient") or {}
        channel_data = activity.get("channelData") or {}

        # 发送者优先用 AAD 对象 ID（更稳定），回退到临时 user id
        sender_id = str(from_user.get("aadObjectId") or from_user.get("id") or "").strip()
        conversation_id = str(conversation.get("id") or "").strip()
        service_url = str(activity.get("serviceUrl") or "").strip()
        activity_id = str(activity.get("id") or "").strip()
        conversation_type = str(conversation.get("conversationType") or "").strip()

        if not sender_id or not conversation_id or not service_url:
            return

        if not self._is_trusted_service_url(service_url):
            self.logger.warning(
                "Ignoring MSTeams activity with untrusted serviceUrl host: {}",
                service_url,
            )
            return

        # 机器人自身的消息回环会再触发，直接丢弃
        if recipient.get("id") and from_user.get("id") == recipient.get("id"):
            return

        # DM-only MVP: ignore group/channel traffic for now
        # 本 MVP 仅处理个人（personal）会话；群/频道会话暂不响应
        if conversation_type and conversation_type not in ("personal", ""):
            self.logger.debug("Ignoring non-DM conversation {}", conversation_type)
            return

        text = self._sanitize_inbound_text(activity)
        if not text:
            # 仅 @bot 而无文本时，用配置的默认应答占位，避免空消息被丢弃
            text = self.config.mention_only_response.strip()
            if not text:
                self.logger.debug("Ignoring empty message after Teams text sanitization")
                return

        if not self.is_allowed(sender_id):
            self.logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id, self.name,
            )
            return

        with self._refs_guard:
            # 记录该会话的回复所需信息：service_url、conversation_id、bot 身份、
            # activity_id（用于线程回复）、tenant 等；后续 send() 依赖此记录
            self._conversation_refs[conversation_id] = ConversationRef(
                service_url=service_url,
                conversation_id=conversation_id,
                bot_id=str(recipient.get("id") or "") or None,
                activity_id=activity_id or None,
                conversation_type=conversation_type or None,
                tenant_id=str((channel_data.get("tenant") or {}).get("id") or "") or None,
                updated_at=time.time(),
            )
            self._save_refs_locked()

        await self._handle_message(
            sender_id=sender_id,
            chat_id=conversation_id,
            content=text,
            metadata={
                "msteams": {
                    "activity_id": activity_id,
                    "conversation_id": conversation_id,
                    "conversation_type": conversation_type or "personal",
                    "from_name": from_user.get("name"),
                }
            },
        )

    def _sanitize_inbound_text(self, activity: dict[str, Any]) -> str:
        """Extract the user-authored text from a Teams activity.

        Teams 入站文本可能带 @bot 标记、HTML 实体/nbsp、以及被中继服务包过的
        “引用回复”包装。本方法依次：去掉 bot mention -> 归一化 HTML 空白 ->
        检测是否为回复（replyToId / messageType=reply / 形如 "Replying to" 或
        "Reply wrapper" 的包装头）-> 若是回复则进一步解析引用与正文。
        """
        text = str(activity.get("text") or "")
        text = self._strip_possible_bot_mention(text)
        text = self._normalize_html_whitespace(text)

        channel_data = activity.get("channelData") or {}
        reply_to_id = str(activity.get("replyToId") or "").strip()
        # 构造一个归一化预览，用于检测首行是否为引用回复包装头
        normalized_preview = html.unescape(text).replace("&rsquo", "’").strip()
        normalized_preview = normalized_preview.replace("\xa0", " ")
        normalized_preview = normalized_preview.replace("\r\n", "\n").replace("\r", "\n")
        preview_lines = [line.strip() for line in normalized_preview.split("\n")]
        while preview_lines and not preview_lines[0]:
            preview_lines.pop(0)
        first_line = preview_lines[0] if preview_lines else ""
        # 两种已知的引用回复包装头："Replying to <name>" 或 "Reply wrapper..."
        looks_like_quote_wrapper = first_line.lower().startswith("replying to ") or first_line.startswith("Reply wrapper")

        if reply_to_id or channel_data.get("messageType") == "reply" or looks_like_quote_wrapper:
            text = self._normalize_teams_reply_quote(text)

        return text.strip()

    def _strip_possible_bot_mention(self, text: str) -> str:
        """Remove simple Teams mention markup from message text.

        去掉 <at>...</at> 形式的 @mention 标记，并折叠多余空白与连续空行。
        """
        cleaned = re.sub(r"<at\b[^>]*>.*?</at>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r"[^\S\r\n]+", " ", cleaned)
        cleaned = re.sub(r"(?:\r?\n){3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _normalize_html_whitespace(self, text: str) -> str:
        """Normalize common HTML whitespace/entities from Teams into plain text spacing.

        把 HTML 实体（含未加分号的 &rsquo）、不间断空格(\\xa0)等归一化为普通文本。
        """
        normalized = html.unescape(text).replace("&rsquo", "’")
        normalized = normalized.replace("\xa0", " ")
        return normalized

    def _normalize_teams_reply_quote(self, text: str) -> str:
        """Normalize Teams quoted replies into a compact structured form.

        Teams/中继服务对“引用回复”有多种包装形态，本方法尽力识别并把它们
        统一改写为 "User is replying to: <引用>\\nUser reply: <正文>" 的结构，
        去掉包装噪声，方便模型理解用户实际说了什么。识别顺序对应已观测到的
        三种形态：原生 "Replying to" 头、合成 "Reply wrapper" 头（带空行分隔
        或逐行回退）、以及压缩成单行的回退形态。
        """
        cleaned = self._normalize_html_whitespace(text).strip()
        if not cleaned:
            return ""

        normalized_newlines = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in normalized_newlines.split("\n")]
        while lines and not lines[0]:
            lines.pop(0)

        # Observed native Teams reply wrapper:
        #   Replying to Bob Smith
        #   actual reply text
        # 形态一：原生 Teams 回复，首行为 "Replying to <被引用者>"，其余为正文
        if len(lines) >= 2 and lines[0].lower().startswith("replying to "):
            quoted = lines[0][len("replying to ") :].strip(" :")
            reply = "\n".join(lines[1:]).strip()
            return self._format_reply_with_quote(quoted, reply)

        # Observed reply wrapper where the quoted content is surfaced after a
        # synthetic "Reply wrapper" header, sometimes with a blank line separating quote
        # and reply, and sometimes as a compact line-based fallback shape.
        # 形态二：合成 "Reply wrapper" 头，其后可能用空行分隔引用与正文，
        # 也可能逐行排列（最后一行为正文，其余为引用）
        if lines and lines[0].strip().startswith("Reply wrapper"):
            body = normalized_newlines.split("\n", 1)[1] if "\n" in normalized_newlines else ""
            body = body.lstrip()
            # 先尝试按空行把 body 切成 [引用, 正文] 两段
            parts = re.split(r"\n\s*\n", body, maxsplit=1)
            if len(parts) == 2:
                quoted = re.sub(r"\s+", " ", parts[0]).strip()
                reply = re.sub(r"\s+", " ", parts[1]).strip()
                if quoted or reply:
                    return self._format_reply_with_quote(quoted, reply)

            # 空行分隔失败时退化为逐行切分：末行当正文，其余拼成引用
            body_lines = [line.strip() for line in body.split("\n") if line.strip()]
            if body_lines:
                quoted = " ".join(body_lines[:-1]).strip()
                reply = body_lines[-1].strip()
                if quoted and reply:
                    return self._format_reply_with_quote(quoted, reply)

        # Observed compact fallback where the relay flattens quote and reply into
        # a single line after the synthetic Reply wrapper prefix.
        # 形态三：中继把引用与正文压成单行，去掉 "Reply wrapper " 前缀后，
        # 按句末标号(. ! ? …)切分，最后一段（且不超过 160 字）当作正文
        compact = re.sub(r"\s+", " ", normalized_newlines).strip()
        if compact.startswith("Reply wrapper "):
            compact = compact[len("Reply wrapper ") :].strip()
            for boundary in (". ", "! ", "? ", "… "):
                idx = compact.rfind(boundary)
                if idx == -1:
                    continue
                quoted = compact[: idx + 1].strip()
                reply = compact[idx + len(boundary) :].strip()
                if quoted and reply and len(reply) <= 160:
                    return self._format_reply_with_quote(quoted, reply)

        return cleaned

    def _format_reply_with_quote(self, quoted: str, reply: str) -> str:
        """Format a reply-with-context message for the model without Teams wrapper noise.

        统一输出 "User is replying to: <引用>\\nUser reply: <正文>"，让模型
        明确区分被引用内容与用户真正输入的回复。
        """
        quoted = quoted.strip()
        reply = reply.strip()
        if quoted and reply:
            return f"User is replying to: {quoted}\nUser reply: {reply}"
        if reply:
            return reply
        return quoted

    async def _validate_inbound_auth(self, auth_header: str, activity: dict[str, Any]) -> None:
        """Validate inbound Bot Framework bearer token.

        入站鉴权：校验请求的 Bearer JWT 确由 Bot Framework 签发且未伪造。
        流程：取 kid -> 拉取并缓存 Bot Framework JWKS -> 用对应 JWK 公钥
        以 RS256 解码 -> 校验 audience=app_id、issuer=api.botframework.com、
        必含 exp/nbf/iss/aud 声明 -> 比对 JWT 中的 serviceUrl 与活动一致。
        """
        if not MSTEAMS_AVAILABLE:
            raise RuntimeError("PyJWT not installed. Run: pip install nanobot-ai[msteams]")

        if not auth_header.lower().startswith("bearer "):
            raise ValueError("missing bearer token")

        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise ValueError("empty bearer token")

        # kid 标识签发本 token 的密钥，需在 JWKS 中找到匹配项
        header = jwt.get_unverified_header(token)
        kid = str(header.get("kid") or "").strip()
        if not kid:
            raise ValueError("missing token kid")

        jwks = await self._get_botframework_jwks()
        keys = jwks.get("keys") or []
        jwk = next((key for key in keys if key.get("kid") == kid), None)
        if not jwk:
            raise ValueError(f"signing key not found for kid={kid}")

        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        claims = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256"],
            audience=self.config.app_id,
            issuer="https://api.botframework.com",
            options={
                "require": ["exp", "nbf", "iss", "aud"],
            },
        )

        # 额外比对 JWT 中的 serviceUrl 与活动体一致，防止 token 被挪用到其它服务端点
        claim_service_url = str(
            claims.get("serviceurl") or claims.get("serviceUrl") or "",
        ).strip()
        activity_service_url = str(activity.get("serviceUrl") or "").strip()
        if claim_service_url and activity_service_url and claim_service_url != activity_service_url:
            raise ValueError("serviceUrl claim mismatch")

    async def _get_botframework_openid_config(self) -> dict[str, Any]:
        """Fetch and cache Bot Framework OpenID configuration.

        拉取并缓存 Bot Framework 的 OpenID 配置（含 jwks_uri），缓存 1 小时，
        避免每次入站鉴权都请求。
        """

        now = time.time()
        if self._botframework_openid_config and now < self._botframework_openid_config_expires_at:
            return self._botframework_openid_config

        if not self._http:
            raise RuntimeError("MSTeams HTTP client not initialized")

        resp = await self._http.get(self._botframework_openid_config_url)
        resp.raise_for_status()
        self._botframework_openid_config = resp.json()
        self._botframework_openid_config_expires_at = now + 3600
        return self._botframework_openid_config

    async def _get_botframework_jwks(self) -> dict[str, Any]:
        """Fetch and cache Bot Framework JWKS.

        先取（缓存的）OpenID 配置拿到 jwks_uri，再拉取 JWKS 公钥集合并缓存 1 小时。
        """

        now = time.time()
        if self._botframework_jwks and now < self._botframework_jwks_expires_at:
            return self._botframework_jwks

        if not self._http:
            raise RuntimeError("MSTeams HTTP client not initialized")

        openid_config = await self._get_botframework_openid_config()
        jwks_uri = str(openid_config.get("jwks_uri") or "").strip()
        if not jwks_uri:
            raise RuntimeError("Bot Framework OpenID config missing jwks_uri")

        resp = await self._http.get(jwks_uri)
        resp.raise_for_status()
        self._botframework_jwks = resp.json()
        self._botframework_jwks_expires_at = now + 3600
        return self._botframework_jwks

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            out = float(value)
            if out > 0:
                return out
        except (TypeError, ValueError):
            return None
        return None

    def _normalize_ref_record(self, value: Any) -> ConversationRef | None:
        """Normalize a stored ref record from legacy/current schema.

        把磁盘上的一条记录（dict）归一化为 ConversationRef；缺关键字段
        （service_url / conversation_id）则视为无效返回 None。
        """
        if not isinstance(value, dict):
            return None
        service_url = str(value.get("service_url") or "").strip()
        conversation_id = str(value.get("conversation_id") or "").strip()
        if not service_url or not conversation_id:
            return None
        return ConversationRef(
            service_url=service_url,
            conversation_id=conversation_id,
            bot_id=str(value.get("bot_id") or "") or None,
            activity_id=str(value.get("activity_id") or "") or None,
            conversation_type=str(value.get("conversation_type") or "") or None,
            tenant_id=str(value.get("tenant_id") or "") or None,
            updated_at=self._safe_float(value.get("updated_at")),
        )

    def _load_refs_raw(self) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """Load raw refs/main+meta JSON payloads.

        分别读取主文件（会话信息）与 meta sidecar（updated_at 时间戳），
        并返回 meta sidecar 是否存在（用于后续兼容性判断）。
        """
        main_data: dict[str, Any] = {}
        meta_data: dict[str, Any] = {}
        meta_exists = self._refs_meta_path.exists()

        if self._refs_path.exists():
            try:
                loaded = json.loads(self._refs_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    main_data = loaded
            except Exception as e:
                self.logger.warning("Failed to load conversation refs: {}", e)

        if meta_exists:
            try:
                loaded_meta = json.loads(self._refs_meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded_meta, dict):
                    meta_data = loaded_meta
            except Exception as e:
                self.logger.warning("Failed to load conversation refs metadata: {}", e)

        return main_data, meta_data, meta_exists

    def _load_refs_from_disk(self) -> dict[str, ConversationRef]:
        """Load refs from disk with compatibility fallback for legacy layouts.

        加载并归一化磁盘引用，并补全 updated_at：优先用 meta sidecar 的时间戳；
        若 meta sidecar 尚不存在（首次升级到双文件布局），把旧记录的 updated_at
        初始化为 now 以免被立即清理；否则缺时间戳的也补 now。
        """
        main_data, meta_data, meta_exists = self._load_refs_raw()
        if not main_data:
            return {}

        out: dict[str, ConversationRef] = {}
        now = time.time()
        for key, value in main_data.items():
            ref = self._normalize_ref_record(value)
            if not ref:
                continue

            meta_entry = meta_data.get(key) if isinstance(meta_data, dict) else None
            meta_ts = None
            if isinstance(meta_entry, dict):
                meta_ts = self._safe_float(meta_entry.get("updated_at"))
            elif meta_entry is not None:
                meta_ts = self._safe_float(meta_entry)

            if meta_ts is not None:
                ref.updated_at = meta_ts
            elif not meta_exists:
                # First run after introducing meta sidecar: keep legacy refs alive
                # by initializing timestamps to "now" instead of purging immediately.
                # 首次升级到双文件布局：用 now 初始化旧记录，避免立即被 TTL 清理
                ref.updated_at = now
            elif ref.updated_at is None:
                ref.updated_at = now

            out[key] = ref
        return out

    def _load_refs(self) -> dict[str, ConversationRef]:
        """Load stored conversation references."""
        return self._load_refs_from_disk()

    @contextmanager
    def _refs_file_lock(self):
        """Cross-process lock while merging and writing refs state.

        跨进程文件锁：在 POSIX 上用 fcntl.flock 排他锁；Windows 无 fcntl，
        退化为仅靠进程内 _refs_guard 保护（多进程场景受限）。
        """
        self._refs_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fp = self._refs_lock_path.open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            finally:
                lock_fp.close()

    def _is_webchat_service_url(self, service_url: str) -> bool:
        """Return True when service URL points to unsupported Bot Framework Web Chat.

        判断 service_url 是否指向 Bot Framework Web Chat（本 MVP 不支持），
        通过主机名精确/后缀匹配 webchat.botframework.com。
        """
        normalized = service_url.strip()
        if not normalized:
            return False
        host = (urlparse(normalized).hostname or "").strip().lower()
        if host:
            return host == MSTEAMS_WEBCHAT_HOST or host.endswith(f".{MSTEAMS_WEBCHAT_HOST}")
        return MSTEAMS_WEBCHAT_HOST in normalized.lower()

    def _is_trusted_service_url(self, service_url: str) -> bool:
        """Return True for HTTPS Bot Framework service URLs trusted for bearer replies.

        受信 service_url 校验：必须 HTTPS，且主机名匹配白名单。白名单支持
        ``*.domain`` 通配（匹配 domain 的任意子域，但不匹配 domain 自身）或
        精确主机名。防止伪造 serviceUrl 把 bearer 请求/令牌发往外部域名。
        """
        parsed = urlparse(service_url.strip())
        if parsed.scheme.lower() != "https":
            return False

        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host:
            return False

        for pattern in self.config.trusted_service_url_hosts:
            trusted_host = str(pattern or "").strip().lower().rstrip(".")
            if not trusted_host:
                continue
            if trusted_host.startswith("*."):
                # *.domain 形式：host 须以 .domain 结尾，且不能等于 domain 自身
                suffix = trusted_host[1:]
                if host.endswith(suffix) and host != suffix.lstrip("."):
                    return True
                continue
            if host == trusted_host:
                return True
        return False

    def _prune_conversation_refs(self, *, now: float | None = None) -> bool:
        """Remove stale and unsupported conversation refs from memory.

        清理策略（满足任一即剔除）：service_url 不受信、指向 Web Chat（若开启）、
        非个人会话（若开启）、或 updated_at 超过 ref_ttl_days。返回是否有清理。
        """
        if not self._conversation_refs:
            return False

        now_ts = time.time() if now is None else now
        ttl_days = int(self.config.ref_ttl_days)
        stale_before = now_ts - (ttl_days * 24 * 60 * 60)
        keys_to_drop: list[str] = []

        for key, ref in self._conversation_refs.items():
            if not self._is_trusted_service_url(ref.service_url):
                keys_to_drop.append(key)
                continue

            if self.config.prune_web_chat_refs and self._is_webchat_service_url(ref.service_url):
                keys_to_drop.append(key)
                continue

            conv_type = str(ref.conversation_type or "").strip().lower()
            if self.config.prune_non_personal_refs and conv_type and conv_type != "personal":
                keys_to_drop.append(key)
                continue

            try:
                updated_at = float(ref.updated_at) if ref.updated_at is not None else 0.0
            except (TypeError, ValueError):
                updated_at = 0.0
            if updated_at <= 0 or updated_at < stale_before:
                keys_to_drop.append(key)

        if not keys_to_drop:
            return False

        for key in keys_to_drop:
            self._conversation_refs.pop(key, None)
        self.logger.info(
            "Pruned {} stale/unsupported conversation refs (ttl={} days)",
            len(keys_to_drop),
            ttl_days,
        )
        return True

    def _merge_refs_from_disk_locked(self) -> None:
        """Merge disk refs into memory to reduce lost updates across processes.

        跨进程合并：先读磁盘最新状态，对每个 key 若内存没有则采用磁盘版本，
        若都有则按 updated_at 取较新者，避免本进程覆盖其它进程刚写入的新引用。
        """
        disk_refs = self._load_refs_from_disk()
        for key, disk_ref in disk_refs.items():
            mem_ref = self._conversation_refs.get(key)
            if mem_ref is None:
                self._conversation_refs[key] = disk_ref
                continue
            disk_ts = self._safe_float(disk_ref.updated_at) or 0.0
            mem_ts = self._safe_float(mem_ref.updated_at) or 0.0
            if disk_ts > mem_ts:
                self._conversation_refs[key] = disk_ref

    def _touch_conversation_ref(self, chat_id: str, *, persist: bool = False) -> None:
        """Refresh updated_at for an active ref to keep it from expiring while used.

        续期活跃会话的 updated_at，避免被 TTL 清理。为减少落盘开销，受
        ref_touch_interval_s 节流：距上次刷新不足该间隔则跳过。persist=True
        时立即落盘（用于回复成功等关键时机）。
        """
        with self._refs_guard:
            ref = self._conversation_refs.get(str(chat_id))
            if not ref:
                return
            now = time.time()
            prev = self._safe_float(ref.updated_at) or 0.0
            min_interval = max(0, int(self.config.ref_touch_interval_s))
            if min_interval > 0 and prev > 0 and now - prev < min_interval:
                return
            ref.updated_at = now
            if persist:
                self._save_refs_locked()

    def _write_json_atomically(self, path, data: dict[str, Any]) -> None:
        """Write refs JSON atomically to reduce corruption risk during crashes.

        原子写：先写临时文件并 fsync 落盘，再 os.replace 覆盖目标文件，
        避免写入中途崩溃导致 JSON 损坏。finally 中清理残留临时文件。
        """
        payload = json.dumps(data, indent=2)
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent),
                prefix=f"{path.name}.",
                suffix=".tmp",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                with suppress(OSError):
                    os.unlink(tmp_path)

    def _save_refs_locked(self, *, prune: bool = True) -> None:
        """Persist conversation references (caller must hold _refs_guard).

        落盘流程：取跨进程文件锁 -> 先合并磁盘最新状态（防止覆盖它进程写入）->
        按需清理过期 -> 分别原子写主文件（会话信息）与 meta sidecar（时间戳）。
        任一异常仅告警不抛出，避免阻塞调用方。
        """
        try:
            with self._refs_file_lock():
                self._merge_refs_from_disk_locked()
                if prune:
                    self._prune_conversation_refs()
                refs_data = {
                    key: {
                        "service_url": ref.service_url,
                        "conversation_id": ref.conversation_id,
                        "bot_id": ref.bot_id,
                        "activity_id": ref.activity_id,
                        "conversation_type": ref.conversation_type,
                        "tenant_id": ref.tenant_id,
                    }
                    for key, ref in self._conversation_refs.items()
                }
                refs_meta = {
                    key: {
                        "updated_at": self._safe_float(ref.updated_at),
                    }
                    for key, ref in self._conversation_refs.items()
                }
                self._write_json_atomically(self._refs_path, refs_data)
                self._write_json_atomically(self._refs_meta_path, refs_meta)
        except Exception as e:
            self.logger.warning("Failed to save conversation refs: {}", e)

    def _save_refs(self, *, prune: bool = True) -> None:
        """Persist conversation references."""
        with self._refs_guard:
            self._save_refs_locked(prune=prune)

    async def _get_access_token(self) -> str:
        """Fetch an access token for Bot Framework / Azure Bot auth.

        用 client_credentials 流程向 Azure AD（或 botframework.com）换取
        Bot Framework access token；缓存到 expires_at 前 60s，避免每次发送都请求。
        """

        now = time.time()
        # 提前 60s 视为过期，规避边界过期
        if self._token and now < self._token_expires_at - 60:
            return self._token

        if not self._http:
            raise RuntimeError("MSTeams HTTP client not initialized")

        tenant = (self.config.tenant_id or "").strip() or "botframework.com"
        token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
        data = {
            "grant_type": "client_credentials",
            "client_id": self.config.app_id,
            "client_secret": self.config.app_password,
            "scope": "https://api.botframework.com/.default",
        }
        resp = await self._http.post(token_url, data=data)
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = now + int(payload.get("expires_in", 3600))
        return self._token
