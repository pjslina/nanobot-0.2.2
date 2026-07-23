"""Token state for the embedded WebUI gateway."""

# 内嵌 WebUI 网关的令牌（token）状态管理。网关为单个进程内的所有 WebSocket 连接与
# HTTP API 请求签发短时令牌，本模块维护两套令牌表：一次性 WebSocket 握手令牌与可
# 复用的 API 令牌。所有状态仅存于内存，进程重启即失效，故令牌 TTL 设计得较短。

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from websockets.http11 import Request as WsRequest

from nanobot.webui.http_utils import bearer_token, parse_query, query_first


@dataclass
class GatewayTokenStore:
    """Own short-lived WebSocket and WebUI API tokens for one gateway process."""
    # 单个网关进程的令牌存储。两类令牌分别存放：
    # - issued_tokens：一次性握手令牌，校验后即被消费（pop），防止重放；
    # - api_tokens：带 TTL 的可复用 API 令牌，在过期前可多次用于鉴权。
    # 采用惰性清理（访问时顺带清过期项）而非后台定时器，避免额外协程开销。

    max_tokens: int = 10_000
    issued_tokens: dict[str, float] = field(default_factory=dict)
    api_tokens: dict[str, float] = field(default_factory=dict)

    def check_api_token(self, request: WsRequest) -> bool:
        # 校验 API 令牌：优先取 Authorization 头的 Bearer 令牌，其次取查询串的 token 参数。
        # 命中且未过期返回 True，过期或不存在则返回 False（并顺手清掉过期项）。
        self._purge_expired_api_tokens()
        token = bearer_token(request.headers) or query_first(
            parse_query(request.path), "token"
        )
        if not token:
            return False
        expiry = self.api_tokens.get(token)
        if expiry is None or time.monotonic() > expiry:
            self.api_tokens.pop(token, None)
            return False
        return True

    def can_issue(self, *, include_api_token: bool = False) -> bool:
        # 容量检查：令牌数未达 max_tokens 上限时才允许签发，防止恶意或异常情况下令牌表
        # 无限增长导致内存膨胀。include_api_token 时同时检查 API 令牌表是否已满。
        self._purge_expired_issued_tokens()
        self._purge_expired_api_tokens()
        if len(self.issued_tokens) >= self.max_tokens:
            return False
        if include_api_token and len(self.api_tokens) >= self.max_tokens:
            return False
        return True

    def issue_token(self, ttl_s: int | float, *, api_token: bool = False) -> str:
        # 签发新令牌：前缀 "nbwt_" 便于辨识来源，主体用 secrets.token_urlsafe(32) 保证
        # 足够熵值。expiry 用单调时钟 time.monotonic() 计算，不受系统时间回拨影响。
        # api_token=True 时同时写入 api_tokens 表，使其可用于 HTTP API 鉴权。
        token_value = f"nbwt_{secrets.token_urlsafe(32)}"
        expiry = time.monotonic() + float(ttl_s)
        self.issued_tokens[token_value] = expiry
        if api_token:
            self.api_tokens[token_value] = expiry
        return token_value

    def take_issued_token_if_valid(self, token_value: str | None) -> bool:
        # 一次性消费握手令牌：用 pop 取出并移除，校验未过期。取出即作废，即使校验失败
        # 该令牌也无法再次使用，从根本上防止握手令牌被重放攻击。
        if not token_value:
            return False
        self._purge_expired_issued_tokens()
        expiry = self.issued_tokens.pop(token_value, None)
        if expiry is None:
            return False
        if time.monotonic() > expiry:
            return False
        return True

    def clear(self) -> None:
        self.issued_tokens.clear()
        self.api_tokens.clear()

    def _purge_expired_api_tokens(self) -> None:
        # 惰性清理：遍历副本删除过期项。用 list() 快照避免遍历时修改字典报错。
        now = time.monotonic()
        for token_key, expiry in list(self.api_tokens.items()):
            if now > expiry:
                self.api_tokens.pop(token_key, None)

    def _purge_expired_issued_tokens(self) -> None:
        now = time.monotonic()
        for token_key, expiry in list(self.issued_tokens.items()):
            if now > expiry:
                self.issued_tokens.pop(token_key, None)


def token_response_payload(token: str, expires_in: Any) -> dict[str, Any]:
    # 令牌签发接口的标准返回负载：token 本体与剩余有效秒数，供客户端保存并在后续请求中携带。
    return {"token": token, "expires_in": expires_in}
