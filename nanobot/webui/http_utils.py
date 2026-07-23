"""Shared HTTP helpers for the embedded WebUI gateway.

WebUI 网关的 HTTP 工具函数集：路径归一化、Host 头安全校验、响应构造、
查询参数解析、本机连接判定与令牌/密钥校验。这些函数被 ws_http.py 等网关
层复用，统一处理裸 HTTP 请求的解析与防御性校验。
"""

from __future__ import annotations

import email.utils
import hmac
import http
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from websockets.datastructures import Headers
from websockets.http11 import Response

QueryParams = dict[str, list[str]]


def strip_trailing_slash(path: str) -> str:
    # 去掉路径末尾的斜杠以统一路由匹配。根路径 "/" 必须保留（len<=1 不处理），
    # 空值/None 兜底返回 "/"，避免路由匹配时把空串误判为根。
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path or "/"


def normalize_config_path(path: str) -> str:
    return strip_trailing_slash(path)


def case_insensitive_header(headers: Any, key: str) -> str:
    """Read a header from websockets/http test stubs without assuming casing.

    大小写不敏感地读取一个请求头。优先用原 key 取，失败再尝试小写形式。
    用 try/except 兜底是因为 websockets 的真实 Headers 与单元测试中的
    桩对象行为不同（取不到时有的抛异常、有的返回 None），此处统一吸收差异。
    """
    try:
        value = headers.get(key)
    except Exception:
        value = None
    if value is None:
        try:
            value = headers.get(key.lower())
        except Exception:
            value = None
    return str(value or "").strip()


def safe_host_header(value: str) -> str:
    """Return a safe Host header value, or empty when it should not be echoed.

    校验 Host 头防止 Host 头注入攻击：仅放行 IPv6 字面量（带方括号）或
    合法主机名/IPv4（可带端口），其余一律返回空串（不回显）。校验通过才
    允许将该值回显或用于跳转，避免恶意 Host 被注入页面。
    """
    value = value.strip()
    if not value:
        return ""
    # IPv6 字面量形如 [::1] 或 [::1]:8080，方括号内仅允许十六进制与冒号。
    if re.fullmatch(r"\[[0-9A-Fa-f:.]+\](?::\d{1,5})?", value):
        return value
    # 普通主机名或 IPv4，可带端口。
    if re.fullmatch(r"[A-Za-z0-9.-]+(?::\d{1,5})?", value):
        return value
    return ""


def host_for_url(host: str, port: int) -> str:
    # 构造用于 URL 的 host:port。绑定到通配地址（0.0.0.0 / ::）时无法直接
    # 访问，故替换为 127.0.0.1；含冒号的 IPv6 地址需用方括号包裹。
    host = host.strip()
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"{host}:{port}"


def http_json_response(data: dict[str, Any], *, status: int = 200) -> Response:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    headers = Headers(
        [
            ("Date", email.utils.formatdate(usegmt=True)),
            ("Connection", "close"),
            ("Content-Length", str(len(body))),
            ("Content-Type", "application/json; charset=utf-8"),
        ]
    )
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, headers, body)


def http_response(
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "text/plain; charset=utf-8",
    extra_headers: list[tuple[str, str]] | None = None,
) -> Response:
    headers = [
        ("Date", email.utils.formatdate(usegmt=True)),
        ("Connection", "close"),
        ("Content-Length", str(len(body))),
        ("Content-Type", content_type),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    reason = http.HTTPStatus(status).phrase
    return Response(status, reason, Headers(headers), body)


def http_error(status: int, message: str | None = None) -> Response:
    body = (message or http.HTTPStatus(status).phrase).encode("utf-8")
    return http_response(body, status=status)


def parse_request_path(path_with_query: str) -> tuple[str, QueryParams]:
    """Parse normalized path and query parameters in one pass.

    一次性解析出归一化路径与查询参数。这里给输入前拼一个 "ws://x" 伪前缀
    是因为 ``urlparse`` 需要带 scheme 才能正确区分 path 与 query；否则形如
    "/p?a=1" 的输入会被整体当作 path。keep_blank_values=True 保留空值参数
    （如 ?a= 中的 a），便于区分"未传"与"传了空"。
    """
    parsed = urlparse("ws://x" + path_with_query)
    path = strip_trailing_slash(parsed.path or "/")
    return path, parse_qs(parsed.query, keep_blank_values=True)


def normalize_http_path(path_with_query: str) -> str:
    return parse_request_path(path_with_query)[0]


def parse_query(path_with_query: str) -> QueryParams:
    return parse_request_path(path_with_query)[1]


def query_first(query: QueryParams, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def is_localhost(connection: Any) -> bool:
    # 判断连接是否来自本机，用于豁免某些仅限本机的接口。需处理 IPv4-mapped
    # IPv6 地址（形如 ::ffff:127.0.0.1）--剥去前缀后再比对已知本机地址。
    addr = getattr(connection, "remote_address", None)
    if not addr:
        return False
    host = addr[0] if isinstance(addr, tuple) else addr
    if not isinstance(host, str):
        return False
    if host.startswith("::ffff:"):
        host = host[7:]
    return host in {"127.0.0.1", "::1", "localhost"}


def bearer_token(headers: Any) -> str | None:
    # 从 Authorization 头提取 Bearer 令牌；缺失或非 Bearer 方案时返回 None。
    auth = headers.get("Authorization") or headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return None


def issue_route_secret_matches(headers: Any, configured_secret: str) -> bool:
    # 校验请求是否携带正确的网关路由密钥。未配置密钥时视为开放（直接放行）。
    # 比较使用 hmac.compare_digest 做常量时间比较，避免通过计时差异枚举密钥。
    # 优先识别 Authorization: Bearer，其次识别自定义 X-Nanobot-Auth 头。
    if not configured_secret:
        return True
    authorization = headers.get("Authorization") or headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, configured_secret)
    header_token = headers.get("X-Nanobot-Auth") or headers.get("x-nanobot-auth")
    if not header_token:
        return False
    return hmac.compare_digest(header_token.strip(), configured_secret)
