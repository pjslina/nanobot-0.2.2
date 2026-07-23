"""Email channel implementation using IMAP polling + SMTP replies.

邮件渠道：入站采用 IMAP 轮询（按固定间隔拉取未读邮件并解析），出站通过
SMTP 回复发送方。该渠道为"拉取式"而非实时推送，频率受 ``poll_interval_seconds``
控制；同时支持 SPF/DKIM 反伪造校验、附件提取、以及处理后的删除/移动等后续动作。
"""

import asyncio
import html
import imaplib
import mimetypes
import re
import smtplib
import ssl
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from email import policy
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import Field

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.paths import get_media_dir
from nanobot.config.schema import Base
from nanobot.utils.helpers import safe_filename


class EmailConfig(Base):
    """Email channel configuration (IMAP inbound + SMTP outbound).

    邮件渠道配置：包含 IMAP 入站（主机/端口/邮箱/SSL）、SMTP 出站（主机/端口/
    TLS/SSL/发件地址）、轮询与回复策略、反伪造校验（DKIM/SPF）开关、以及附件
    类型与大小限制等。``consent_granted`` 必须显式置为 True 才会真正启动渠道，
    以避免在用户未授权时自动读取/发送邮件。
    """

    enabled: bool = False
    consent_granted: bool = False

    imap_host: str = ""
    imap_port: int = 993
    imap_username: str = ""
    imap_password: str = ""
    imap_mailbox: str = "INBOX"
    imap_use_ssl: bool = True

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    from_address: str = ""

    auto_reply_enabled: bool = True
    poll_interval_seconds: int = 30
    mark_seen: bool = True
    post_action: Literal["delete", "move"] | None = None
    post_action_move_mailbox: str | None = None
    post_action_expunge: bool = False
    post_action_ignore_skipped: bool = True
    max_body_chars: int = 12000
    subject_prefix: str = "Re: "
    allow_from: list[str] = Field(default_factory=list)

    # Email authentication verification (anti-spoofing)
    # 邮件反伪造校验：要求入站邮件 Authentication-Results 头含对应 pass
    verify_dkim: bool = True   # Require Authentication-Results with dkim=pass
    verify_spf: bool = True    # Require Authentication-Results with spf=pass

    # Attachment handling — set allowed types to enable (e.g. ["application/pdf", "image/*"], or ["*"] for all)
    allowed_attachment_types: list[str] = Field(default_factory=list)
    max_attachment_size: int = 2_000_000  # 2MB per attachment
    max_attachments_per_email: int = 5


@dataclass
class _ServerFeatures:
    """IMAP 服务器能力探测结果，在一次会话内缓存以避免重复探测。

    ``move``/``uidplus`` 来自 CAPABILITY 响应；``uid_store`` 在运行时学习
    （None=未测试 / True=支持 / False=不支持 UID STORE），用于在后续 UID 上
    跳过已知不可用的路径。
    """

    move: bool
    uidplus: bool
    uid_store: bool | None = None


class EmailChannel(BaseChannel):
    """
    Email channel.

    Inbound:
    - Poll IMAP mailbox for unread messages.
    - Convert each message into an inbound event.

    Outbound:
    - Send responses via SMTP back to the sender address.

    中文说明：邮件渠道以"轮询"方式工作。入站侧定期连接 IMAP 拉取未读邮件，
    逐一解析发件人、主题、正文与附件，并做发件人归属判断、SPF/DKIM 反伪造
    校验与权限校验，通过后封装为 InboundMessage 发布到总线。出站侧通过 SMTP
    将回复发送给原发件人，并在邮件头中带上 In-Reply-To/References 以维持线程。
    """

    name = "email"
    display_name = "Email"
    _IMAP_MONTHS = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )
    _IMAP_RECONNECT_MARKERS = (
        "disconnected for inactivity",
        "eof occurred in violation of protocol",
        "socket error",
        "connection reset",
        "broken pipe",
        "bye",
    )
    _IMAP_MISSING_MAILBOX_MARKERS = (
        "mailbox doesn't exist",
        "select failed",
        "no such mailbox",
        "can't open mailbox",
        "does not exist",
    )

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return EmailConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = EmailConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: EmailConfig = config
        self._self_addresses = self._collect_self_addresses()
        self._last_subject_by_chat: dict[str, str] = {}
        self._last_message_id_by_chat: dict[str, str] = {}
        self._processed_uids: set[str] = set()  # Capped to prevent unbounded growth
        self._MAX_PROCESSED_UIDS = 100000

    async def start(self) -> None:
        """Start polling IMAP for inbound emails.

        启动 IMAP 轮询主循环：每隔 ``poll_interval_seconds``（下限 5 秒）拉取一次
        未读邮件，解析并投递到消息总线，再对已处理邮件执行配置的后续动作（删除/移动）。
        单轮异常被捕获并记录，不中断循环；收到停止信号后退出。
        """
        if not self.config.consent_granted:
            self.logger.warning(
                "Email channel disabled: consent_granted is false. "
                "Set channels.email.consentGranted=true after explicit user permission."
            )
            return

        if not self._validate_config():
            return

        self._running = True
        if not self.config.verify_dkim and not self.config.verify_spf:
            self.logger.warning(
                "DKIM and SPF verification are both DISABLED. "
                "Emails with spoofed From headers will be accepted. "
                "Set verify_dkim=true and verify_spf=true for anti-spoofing protection."
            )
        self.logger.info("Starting Email channel (IMAP polling mode)...")

        poll_seconds = max(5, int(self.config.poll_interval_seconds))
        while self._running:
            try:
                # IMAP 为阻塞式同步调用，放到线程池执行以免阻塞事件循环
                inbound_items, skipped_uids = await asyncio.to_thread(self._fetch_new_messages)
                should_apply_post_action = self._should_apply_post_action()
                post_actions_uids: set[str] = set()
                for item in inbound_items:
                    sender = item["sender"]
                    subject = item.get("subject", "")
                    message_id = item.get("message_id", "")

                    if subject:
                        self._last_subject_by_chat[sender] = subject
                    if message_id:
                        self._last_message_id_by_chat[sender] = message_id

                    try:
                        await self._handle_message(
                            sender_id=sender,
                            chat_id=sender,
                            content=item["content"],
                            media=item.get("media") or None,
                            metadata=item.get("metadata", {}),
                        )
                    except Exception:
                        self.logger.exception("Error delivering email from {}", sender)
                        continue

                    uid = str((item.get("metadata") or {}).get("uid") or "")
                    if uid and should_apply_post_action:
                        post_actions_uids.add(uid)

                # post_action_ignore_skipped=False 时，连被跳过（未授权/反伪造失败）
                # 的邮件也纳入后续动作，避免它们一直以 UNSEEN 状态被反复拉取
                if should_apply_post_action and not self.config.post_action_ignore_skipped:
                    post_actions_uids.update(skipped_uids)

                if post_actions_uids:
                    await asyncio.to_thread(self._apply_post_actions_batch, sorted(post_actions_uids))
            except Exception:
                self.logger.exception("Polling error")

            await asyncio.sleep(poll_seconds)

    async def stop(self) -> None:
        """Stop polling loop.

        停止轮询：仅置位 ``_running=False``，当前 ``sleep`` 结束后主循环自然退出。
        IMAP/SMTP 连接按需创建、用完即关，故无需在此显式关闭长连接。
        """
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Send email via SMTP.

        通过 SMTP 发送一封回复邮件：依据 ``chat_id``（即收件人地址）是否曾收到过
        其来信判断是否为"回复"；构造主题（带 Re: 前缀）、追加失败附件的占位文本、
        设置 In-Reply-To/References 头以维持邮件线程，最后经线程池执行 SMTP 投递。
        进度类消息（``_progress``）会被跳过，避免每次工具调用后发出空邮件。
        """
        if not self.config.consent_granted:
            self.logger.warning("Skip email send: consent_granted is false")
            return

        if not self.config.smtp_host:
            self.logger.warning("SMTP host not configured")
            return

        # Skip progress messages to prevent sending an empty email after each tool call
        if (msg.metadata or {}).get("_progress"):
            self.logger.debug("Skip progress message to {}", msg.chat_id)
            return

        to_addr = msg.chat_id.strip()
        if not to_addr:
            self.logger.warning("Missing recipient address")
            return

        # Determine if this is a reply (recipient has sent us an email before)
        # 用"是否曾收到过该地址的来信"判断是否为回复：仅回复场景受 auto_reply_enabled 约束
        is_reply = to_addr in self._last_subject_by_chat
        force_send = bool((msg.metadata or {}).get("force_send"))

        # autoReplyEnabled only controls automatic replies, not proactive sends
        if is_reply and not self.config.auto_reply_enabled and not force_send:
            self.logger.info("Skip automatic reply to {}: auto_reply_enabled is false", to_addr)
            return

        base_subject = self._last_subject_by_chat.get(to_addr, "nanobot reply")
        subject = self._reply_subject(base_subject)
        if msg.metadata and isinstance(msg.metadata.get("subject"), str):
            override = msg.metadata["subject"].strip()
            if override:
                subject = override

        attachments: list[tuple[bytes, str, str, str]] = []
        failed_attachments: list[str] = []
        max_attachment_size = max(0, int(self.config.max_attachment_size))
        max_attachment_count = max(0, int(self.config.max_attachments_per_email))
        for media_path in msg.media or []:
            path = Path(media_path)
            filename = path.name or "attachment"
            if len(attachments) >= max_attachment_count:
                failed_attachments.append(f"[attachment: {filename} - too many attachments]")
                self.logger.warning("Attachment count limit reached, skipping: {}", media_path)
                continue
            if not path.is_file():
                failed_attachments.append(f"[attachment: {filename} - send failed]")
                self.logger.warning("Attachment not found, skipping: {}", media_path)
                continue
            try:
                size = path.stat().st_size
                if max_attachment_size <= 0 or size > max_attachment_size:
                    failed_attachments.append(f"[attachment: {filename} - too large]")
                    self.logger.warning(
                        "Attachment too large, skipping: {} ({} > {} bytes)",
                        media_path,
                        size,
                        max_attachment_size,
                    )
                    continue
                data = path.read_bytes()
                ctype, _ = mimetypes.guess_type(str(path))
                if ctype is None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                attachments.append((data, maintype, subtype, filename))
                self.logger.info("Attached file: {}", filename)
            except Exception:
                failed_attachments.append(f"[attachment: {filename} - send failed]")
                self.logger.exception("Failed to attach file {}", media_path)

        content = msg.content or ""
        if failed_attachments:
            fallback = "\n".join(failed_attachments)
            content = f"{content.rstrip()}\n\n{fallback}" if content.strip() else fallback

        email_msg = EmailMessage()
        email_msg["From"] = self.config.from_address or self.config.smtp_username or self.config.imap_username
        email_msg["To"] = to_addr
        email_msg["Subject"] = subject
        email_msg.set_content(content)

        for data, maintype, subtype, filename in attachments:
            email_msg.add_attachment(
                data,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )

        in_reply_to = self._last_message_id_by_chat.get(to_addr)
        if in_reply_to:
            # 设置 In-Reply-To / References 头，使回复挂接到原邮件线程
            email_msg["In-Reply-To"] = in_reply_to
            email_msg["References"] = in_reply_to

        try:
            await asyncio.to_thread(self._smtp_send, email_msg)
        except Exception:
            self.logger.exception("Error sending to {}", to_addr)
            raise

    def _validate_config(self) -> bool:
        missing = []
        if not self.config.imap_host:
            missing.append("imap_host")
        if not self.config.imap_username:
            missing.append("imap_username")
        if not self.config.imap_password:
            missing.append("imap_password")
        if not self.config.smtp_host:
            missing.append("smtp_host")
        if not self.config.smtp_username:
            missing.append("smtp_username")
        if not self.config.smtp_password:
            missing.append("smtp_password")

        if self.config.post_action == "move" and not (self.config.post_action_move_mailbox or "").strip():
            missing.append("post_action_move_mailbox")

        if missing:
            self.logger.error("Channel not configured, missing: {}", ', '.join(missing))
            return False
        return True

    def _smtp_send(self, msg: EmailMessage) -> None:
        """通过 SMTP 发送邮件，按配置选择 SSL 直连或 STARTTLS 升级。"""
        timeout = 30
        if self.config.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                self.config.smtp_host,
                self.config.smtp_port,
                timeout=timeout,
            ) as smtp:
                smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(msg)
            return

        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=timeout) as smtp:
            if self.config.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.config.smtp_username, self.config.smtp_password)
            smtp.send_message(msg)

    def _fetch_new_messages(self) -> tuple[list[dict[str, Any]], set[str]]:
        """Poll IMAP and return parsed unread messages plus skipped message UIDs.

        轮询 IMAP 拉取未读（UNSEEN）邮件：返回已解析的入站消息列表，以及被跳过
        （自发件、反伪造失败、未授权等）的邮件 UID 集合，供后续动作判断使用。
        """
        return self._fetch_messages(
            search_criteria=("UNSEEN",),
            mark_seen=self.config.mark_seen,
            dedupe=True,
            limit=0,
        )

    def fetch_messages_between_dates(
        self,
        start_date: date,
        end_date: date,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Fetch messages in [start_date, end_date) by IMAP date search.

        This is used for historical summarization tasks (e.g. "yesterday").

        按日期区间拉取邮件（SINCE/BEFORE），用于历史摘要类任务（如"昨天的邮件"）。
        不标记已读、不去重，仅返回限定数量的消息。
        """
        if end_date <= start_date:
            return []

        messages, _ = self._fetch_messages(
            search_criteria=(
                "SINCE",
                self._format_imap_date(start_date),
                "BEFORE",
                self._format_imap_date(end_date),
            ),
            mark_seen=False,
            dedupe=False,
            limit=max(1, int(limit)),
        )
        return messages

    def _fetch_messages(
        self,
        search_criteria: tuple[str, ...],
        mark_seen: bool,
        dedupe: bool,
        limit: int,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """按给定 IMAP 搜索条件拉取邮件，并在连接失效时自动重试一次。

        IMAP 长连接易因空闲超时被服务端断开，故捕获到"stale"类错误时重连重试
        一次；其它异常直接抛出。
        """
        messages: list[dict[str, Any]] = []
        skipped_uids: set[str] = set()
        cycle_uids: set[str] = set()

        for attempt in range(2):
            try:
                self._fetch_messages_once(
                    search_criteria,
                    mark_seen,
                    dedupe,
                    limit,
                    messages,
                    skipped_uids,
                    cycle_uids,
                )
                return messages, skipped_uids
            except Exception as exc:
                if attempt == 1 or not self._is_stale_imap_error(exc):
                    raise
                self.logger.warning("IMAP connection went stale, retrying once: {}", exc)

        return messages, skipped_uids

    def _fetch_messages_once(
        self,
        search_criteria: tuple[str, ...],
        mark_seen: bool,
        dedupe: bool,
        limit: int,
        messages: list[dict[str, Any]],
        skipped_uids: set[str],
        cycle_uids: set[str],
    ) -> None:
        """Fetch messages by arbitrary IMAP search criteria.

        单次 IMAP 拉取的核心实现：打开邮箱、搜索匹配邮件、逐封抓取并解析。每封
        邮件依次经过：UID 去重 -> 发件人归属判断（忽略自发件）-> SPF/DKIM 反伪造
        校验 -> 权限校验 -> 正文/附件提取 -> 标记已读。被任一环节跳过的邮件记入
        ``skipped_uids`` 并登记 UID 以免重复处理。
        """
        mailbox = self.config.imap_mailbox or "INBOX"

        client = self._open_imap_client(mailbox=mailbox, missing_mailbox_ok=True)
        if client is None:
            return messages

        try:
            status, data = client.search(None, *search_criteria)
            if status != "OK" or not data:
                return messages

            ids = data[0].split()
            if limit > 0 and len(ids) > limit:
                # 仅取最近 limit 封（序列号按时间递增，取尾部即最新）
                ids = ids[-limit:]
            for imap_id in ids:
                # BODY.PEEK[] 读取正文但不自动标记已读，UID 一并取回用于去重
                status, fetched = client.fetch(imap_id, "(BODY.PEEK[] UID)")
                if status != "OK" or not fetched:
                    continue

                raw_bytes = self._extract_message_bytes(fetched)
                if raw_bytes is None:
                    continue

                uid = self._extract_uid(fetched)
                if uid and uid in cycle_uids:
                    continue
                if dedupe and uid and uid in self._processed_uids:
                    continue

                parsed = BytesParser(policy=policy.default).parsebytes(raw_bytes)
                sender = parseaddr(parsed.get("From", ""))[1].strip().lower()
                if not sender:
                    continue
                if self._is_self_address(sender):
                    self.logger.info("From {} ignored: matches bot-owned address", sender)
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    if mark_seen:
                        client.store(imap_id, "+FLAGS", "\\Seen")
                    if uid:
                        skipped_uids.add(uid)
                    continue

                # --- Anti-spoofing: verify Authentication-Results ---
                # 反伪造校验：解析 Authentication-Results 头中的 SPF/DKIM 结论
                spf_pass, dkim_pass = self._check_authentication_results(parsed)
                if self.config.verify_spf and not spf_pass:
                    self.logger.warning(
                        "From {} rejected: SPF verification failed "
                        "(no 'spf=pass' in Authentication-Results header)",
                        sender,
                    )
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    if uid:
                        skipped_uids.add(uid)
                    continue
                if self.config.verify_dkim and not dkim_pass:
                    self.logger.warning(
                        "From {} rejected: DKIM verification failed "
                        "(no 'dkim=pass' in Authentication-Results header)",
                        sender,
                    )
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    if uid:
                        skipped_uids.add(uid)
                    continue

                if not self.is_allowed(sender):
                    self._remember_processed_uid(uid, dedupe, cycle_uids)
                    if mark_seen:
                        client.store(imap_id, "+FLAGS", "\\Seen")
                    if uid:
                        skipped_uids.add(uid)
                    continue

                subject = self._decode_header_value(parsed.get("Subject", ""))
                date_value = parsed.get("Date", "")
                message_id = parsed.get("Message-ID", "").strip()
                body = self._extract_text_body(parsed)

                if not body:
                    body = "(empty email body)"

                body = body[: self.config.max_body_chars]
                # 拼装带 [EMAIL-CONTEXT] 前缀的上下文，让 LLM 明确这是一封邮件及其元信息
                content = (
                    f"[EMAIL-CONTEXT] Email received.\n"
                    f"From: {sender}\n"
                    f"Subject: {subject}\n"
                    f"Date: {date_value}\n\n"
                    f"{body}"
                )

                # --- Attachment extraction ---
                attachment_paths: list[str] = []
                if self.config.allowed_attachment_types:
                    saved = self._extract_attachments(
                        parsed,
                        uid or "noid",
                        allowed_types=self.config.allowed_attachment_types,
                        max_size=self.config.max_attachment_size,
                        max_count=self.config.max_attachments_per_email,
                    )
                    for p in saved:
                        attachment_paths.append(str(p))
                        content += f"\n[attachment: {p.name} — saved to {p}]"

                metadata = {
                    "message_id": message_id,
                    "subject": subject,
                    "date": date_value,
                    "sender_email": sender,
                    "uid": uid,
                }
                messages.append(
                    {
                        "sender": sender,
                        "subject": subject,
                        "message_id": message_id,
                        "content": content,
                        "metadata": metadata,
                        "media": attachment_paths,
                    }
                )

                self._remember_processed_uid(uid, dedupe, cycle_uids)

                if mark_seen:
                    client.store(imap_id, "+FLAGS", "\\Seen")
        finally:
            self._close_imap_client(client)

    def _open_imap_client(self, mailbox: str, *, missing_mailbox_ok: bool = False) -> Any | None:
        """建立 IMAP 连接并选中邮箱；可选地容忍邮箱不存在。

        ``missing_mailbox_ok=True`` 时，若目标邮箱不存在则返回 None 而非抛出，
        便于轮询在邮箱暂时不可用时跳过本轮而非崩溃。
        """
        if self.config.imap_use_ssl:
            client: Any = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port)
        else:
            client = imaplib.IMAP4(self.config.imap_host, self.config.imap_port)

        try:
            client.login(self.config.imap_username, self.config.imap_password)
            try:
                status, _ = client.select(mailbox)
            except Exception as exc:
                if missing_mailbox_ok and self._is_missing_mailbox_error(exc):
                    self.logger.warning("Mailbox unavailable, skipping poll for {}: {}", mailbox, exc)
                    self._close_imap_client(client)
                    return None
                raise

            if status != "OK":
                self.logger.warning("Mailbox select returned {}, skipping poll for {}", status, mailbox)
                self._close_imap_client(client)
                return None
        except Exception:
            self._close_imap_client(client)
            raise

        return client

    @staticmethod
    def _close_imap_client(client: Any) -> None:
        with suppress(Exception):
            client.logout()

    def _collect_self_addresses(self) -> set[str]:
        """Return normalized email addresses owned by this channel instance.

        收集本渠道自身拥有的邮箱地址（来自 from_address、smtp_username、imap_username），
        归一化后用于识别"自发件"邮件并忽略，避免机器人回复自己造成循环。
        """
        candidates = (
            self.config.from_address,
            self.config.smtp_username,
            self.config.imap_username,
        )
        normalized = {
            addr
            for candidate in candidates
            if (addr := self._normalize_address(candidate))
        }
        return normalized

    @staticmethod
    def _normalize_address(value: str) -> str:
        """Normalize an address or mailbox-like identifier for comparisons.

        归一化邮箱地址：去掉首尾空白、解析出真实邮箱部分并转小写，用于发件人比较。
        """
        raw = (value or "").strip()
        if not raw:
            return ""
        parsed = parseaddr(raw)[1].strip().lower()
        if parsed:
            return parsed
        if "@" in raw:
            return raw.lower()
        return ""

    def _is_self_address(self, sender: str) -> bool:
        """Return True when an inbound sender belongs to the bot itself."""
        normalized_sender = self._normalize_address(sender)
        return bool(normalized_sender) and normalized_sender in self._self_addresses

    def _remember_processed_uid(self, uid: str, dedupe: bool, cycle_uids: set[str]) -> None:
        """Track a fetched UID so skipped messages are not reprocessed forever.

        登记 UID 防止被跳过的邮件被反复处理：``cycle_uids`` 记录本轮已处理 UID，
        ``_processed_uids`` 在去重开启时跨轮持久记忆。后者有上限，超限时淘汰一半
        （mark_seen 是主要去重手段，此集合仅为安全网）。
        """
        if not uid:
            return
        cycle_uids.add(uid)
        if dedupe:
            self._processed_uids.add(uid)
            # mark_seen is the primary dedup; this set is a safety net
            if len(self._processed_uids) > self._MAX_PROCESSED_UIDS:
                # Evict a random half to cap memory; mark_seen is the primary dedup
                self._processed_uids = set(list(self._processed_uids)[len(self._processed_uids) // 2:])

    def _should_apply_post_action(self) -> bool:
        return self.config.post_action in {"delete", "move"}

    def _apply_post_actions_batch(self, post_actions_uids: list[str]) -> None:
        """在单个 IMAP 会话内批量执行后续动作（删除/移动），减少连接开销。"""
        if not self._should_apply_post_action() or not post_actions_uids:
            return

        mailbox = self.config.imap_mailbox or "INBOX"
        client = self._open_imap_client(mailbox=mailbox)
        if client is None:
            return

        try:
            features = self._server_features(client)
            # Apply all post-actions in one IMAP session. `features` also carries
            # session-learned behavior (e.g. UID STORE support) so later UIDs can
            # skip known-broken paths.
            for uid in post_actions_uids:
                if uid:
                    self._apply_post_action(client, uid, features)
        finally:
            self._close_imap_client(client)

    def _apply_post_action(
        self,
        client: Any,
        uid: str,
        features: _ServerFeatures,
    ) -> None:
        """对单封邮件执行配置的后续动作：删除或移动到指定邮箱。

        移动优先使用 UID MOVE；不支持时退化为 COPY + 标记删除 + 清理。删除则
        标记 ``\\Deleted`` 后按能力选择 UID EXPUNGE 或全量 EXPUNGE。
        """
        action = self.config.post_action

        if action == "delete":
            if not self._uid_store_deleted(client, uid, features):
                return
            self._uid_expunge_or_fallback(client, uid, features)
            return

        if action == "move":
            target = (self.config.post_action_move_mailbox or "").strip()
            if features.move:
                status, _ = client.uid("MOVE", uid, target)
                if status != "OK":
                    self.logger.warning("Post-action move failed (UID MOVE) for UID {} to mailbox {}", uid, target)
                return

            status, _ = client.uid("COPY", uid, target)
            if status != "OK":
                self.logger.warning("Post-action move failed (UID COPY) for UID {} to mailbox {}", uid, target)
                return
            if not self._uid_store_deleted(client, uid, features):
                return
            self._uid_expunge_or_fallback(client, uid, features)

    @staticmethod
    def _server_features(client: Any) -> _ServerFeatures:
        """探测 IMAP 服务器能力（MOVE / UIDPLUS），用于选择后续动作的实现路径。"""
        caps: set[str] = set()
        with suppress(Exception):
            status, data = client.capability()
            if status == "OK" and data:
                for raw in data:
                    if isinstance(raw, (bytes, bytearray)):
                        caps.update(token.upper() for token in raw.decode("utf-8", errors="ignore").split())
                    elif isinstance(raw, str):
                        caps.update(token.upper() for token in raw.split())
        return _ServerFeatures(move="MOVE" in caps, uidplus="UIDPLUS" in caps)

    @staticmethod
    def _lookup_imap_id_by_uid(client: Any, uid: str) -> bytes | None:
        # IMAP exposes two message identifiers: UID (stable) and sequence number
        # (session-local). We target by UID first, but some servers may reject
        # UID STORE. In that case we resolve the current sequence number for the
        # UID and retry with STORE using that sequence id.
        # IMAP 有两种消息标识：UID（稳定）与序列号（仅当前会话有效）。优先用 UID，
        # 若服务器拒绝 UID STORE 则回退为按序列号操作。
        status, data = client.search(None, "UID", uid)
        if status != "OK" or not data or not data[0]:
            return None
        return data[0].split()[0]

    def _uid_store_deleted(self, client: Any, uid: str, features: _ServerFeatures) -> bool:
        # Optimistic path: try UID STORE first because UID is stable and avoids
        # sequence-number lookup. If this fails once for the session, remember it
        # and use the sequence STORE fallback directly for remaining UIDs.
        # 乐观路径：优先 UID STORE（UID 稳定、免查序列号）。本会话内若失败一次，
        # 则记下并在后续 UID 上直接走序列号 STORE 回退，避免重复试探。
        if features.uid_store is not False:
            status, _ = client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            if status == "OK":
                features.uid_store = True
                return True
            features.uid_store = False

        # Compatibility fallback for servers where UID STORE is unavailable or
        # unreliable: resolve the current sequence number from UID and use STORE.
        # 兼容回退：对不支持/不可靠的 UID STORE，按 UID 解析出当前序列号再 STORE
        imap_id = self._lookup_imap_id_by_uid(client, uid)
        if not imap_id:
            self.logger.warning("Post-action skipped: UID {} not found", uid)
            return False

        status, _ = client.store(imap_id, "+FLAGS", "\\Deleted")
        if status != "OK":
            self.logger.warning("Post-action failed: could not mark UID {} as deleted", uid)
            return False
        return True

    def _uid_expunge_or_fallback(self, client: Any, uid: str, features: _ServerFeatures) -> None:
        # Prefer UID-scoped expunge when supported to avoid expunging unrelated
        # messages already marked \Deleted in the selected mailbox.
        # 优先用 UID 范围 EXPUNGE，避免误删当前邮箱中其它已标记 \\Deleted 的无关邮件
        if features.uidplus:
            status, _ = client.uid("EXPUNGE", uid)
            if status == "OK":
                return
            self.logger.warning("UID EXPUNGE failed for UID {}, falling back to EXPUNGE", uid)
        if self.config.post_action_expunge:
            client.expunge()

    @classmethod
    def _is_stale_imap_error(cls, exc: Exception) -> bool:
        """判断异常是否为 IMAP 连接失效（空闲断开/连接重置等），用于触发重连重试。"""
        message = str(exc).lower()
        return any(marker in message for marker in cls._IMAP_RECONNECT_MARKERS)

    @classmethod
    def _is_missing_mailbox_error(cls, exc: Exception) -> bool:
        """判断异常是否为"邮箱不存在"，用于在 missing_mailbox_ok 时跳过而非崩溃。"""
        message = str(exc).lower()
        return any(marker in message for marker in cls._IMAP_MISSING_MAILBOX_MARKERS)

    @classmethod
    def _format_imap_date(cls, value: date) -> str:
        """Format date for IMAP search (always English month abbreviations).

        格式化 IMAP 日期搜索字符串（DD-Mon-YYYY）。强制使用英文月份缩写，避免
        本机 locale 为非英文时导致 IMAP SEARCH 解析失败。
        """
        month = cls._IMAP_MONTHS[value.month - 1]
        return f"{value.day:02d}-{month}-{value.year}"

    @staticmethod
    def _extract_message_bytes(fetched: list[Any]) -> bytes | None:
        for item in fetched:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                return bytes(item[1])
        return None

    @staticmethod
    def _extract_uid(fetched: list[Any]) -> str:
        for item in fetched:
            if isinstance(item, tuple) and item and isinstance(item[0], (bytes, bytearray)):
                head = bytes(item[0]).decode("utf-8", errors="ignore")
                m = re.search(r"UID\s+(\d+)", head)
                if m:
                    return m.group(1)
        return ""

    @staticmethod
    def _decode_header_value(value: str) -> str:
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:
            return value

    @classmethod
    def _extract_text_body(cls, msg: Any) -> str:
        """Best-effort extraction of readable body text.

        尽力提取可读正文：多部分邮件优先收集 text/plain，其次将 text/html 转为
        纯文本；非多部分邮件按内容类型直接取或转换。附件部分（content_disposition
        为 attachment）被跳过。
        """
        if msg.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                content_type = part.get_content_type()
                try:
                    payload = part.get_content()
                except Exception:
                    payload_bytes = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    payload = payload_bytes.decode(charset, errors="replace")
                if not isinstance(payload, str):
                    continue
                if content_type == "text/plain":
                    plain_parts.append(payload)
                elif content_type == "text/html":
                    html_parts.append(payload)
            if plain_parts:
                return "\n\n".join(plain_parts).strip()
            if html_parts:
                return cls._html_to_text("\n\n".join(html_parts)).strip()
            return ""

        try:
            payload = msg.get_content()
        except Exception:
            payload_bytes = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            payload = payload_bytes.decode(charset, errors="replace")
        if not isinstance(payload, str):
            return ""
        if msg.get_content_type() == "text/html":
            return cls._html_to_text(payload).strip()
        return payload.strip()

    @staticmethod
    def _check_authentication_results(parsed_msg: Any) -> tuple[bool, bool]:
        """Parse Authentication-Results headers for SPF and DKIM verdicts.

        Returns:
            A tuple of (spf_pass, dkim_pass) booleans.

        解析 Authentication-Results 头中的 SPF/DKIM 结论：用正则匹配 ``spf=pass``
        与 ``dkim=pass``。该头通常由接收方 MTA 注入，是判断发件人是否伪造的依据。
        """
        spf_pass = False
        dkim_pass = False
        for ar_header in parsed_msg.get_all("Authentication-Results") or []:
            ar_lower = ar_header.lower()
            if re.search(r"\bspf\s*=\s*pass\b", ar_lower):
                spf_pass = True
            if re.search(r"\bdkim\s*=\s*pass\b", ar_lower):
                dkim_pass = True
        return spf_pass, dkim_pass

    @classmethod
    def _extract_attachments(
        cls,
        msg: Any,
        uid: str,
        *,
        allowed_types: list[str],
        max_size: int,
        max_count: int,
    ) -> list[Path]:
        """Extract and save email attachments to the media directory.

        Returns list of saved file paths.

        提取并保存邮件附件到媒体目录：仅保存类型匹配 ``allowed_types``（支持 fnmatch
        通配，如 ``image/*``）且未超大小/数量限制的附件，文件名经 ``safe_filename``
        净化并以 UID 为前缀落盘，避免路径穿越与重名。
        """
        if not msg.is_multipart():
            return []

        saved: list[Path] = []
        media_dir = get_media_dir("email")

        for part in msg.walk():
            if len(saved) >= max_count:
                break
            if part.get_content_disposition() != "attachment":
                continue

            content_type = part.get_content_type()
            if not any(fnmatch(content_type, pat) for pat in allowed_types):
                logger.debug("Attachment skipped (type {}): not in allowed list", content_type)
                continue

            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            if len(payload) > max_size:
                logger.warning(
                    "Attachment skipped: size {} exceeds limit {}",
                    len(payload),
                    max_size,
                )
                continue

            raw_name = part.get_filename() or "attachment"
            sanitized = safe_filename(raw_name) or "attachment"
            dest = media_dir / f"{uid}_{sanitized}"

            try:
                dest.write_bytes(payload)
                saved.append(dest)
                logger.info("Attachment saved: {}", dest)
            except Exception as exc:
                logger.warning("Failed to save attachment {}: {}", dest, exc)

        return saved

    @staticmethod
    def _html_to_text(raw_html: str) -> str:
        """将 HTML 粗略转为纯文本：把 <br>/</p> 转为换行、剥离其余标签、反转义实体。"""
        # 先把 <br> 与 </p> 转成换行，保留段落结构
        text = re.sub(r"<\s*br\s*/?>", "\n", raw_html, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*p\s*>", "\n", text, flags=re.IGNORECASE)
        # 再剥离所有剩余标签，最后把 &amp; 等 HTML 实体反转义回普通字符
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text)

    def _reply_subject(self, base_subject: str) -> str:
        """构造回复主题：已带 Re: 前缀则原样返回，否则加上配置的前缀。"""
        subject = (base_subject or "").strip() or "nanobot reply"
        prefix = self.config.subject_prefix or "Re: "
        if subject.lower().startswith("re:"):
            return subject
        return f"{prefix}{subject}"
