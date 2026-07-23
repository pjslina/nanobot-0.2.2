"""Logging helpers for the WebUI WebSocket server surface.

提供 WebSocket 服务端日志相关的辅助工具：通过日志过滤器抑制浏览器在重启
窗口期已断开连接所触发的"opening handshake failed"噪声，避免日志被无意义的
握手失败刷屏。`websockets_server_logger` 负责按需挂载该过滤器。
"""

from __future__ import annotations

import logging

from websockets.exceptions import ConnectionClosed

OPENING_HANDSHAKE_FAILED_MESSAGE = "opening handshake failed"


def _exception_chain_has_disconnect(exc: BaseException | None) -> bool:
    """沿异常因果链（__cause__ / __context__）向下遍历，判断链中是否含有
    表示连接中断的异常类型。用于区分"客户端已断开"与"真正的握手协议错误"。"""
    seen: set[int] = set()
    while exc is not None:
        ident = id(exc)
        # 用 id 去重防止异常链出现环（理论上罕见但可能存在），避免无限循环
        if ident in seen:
            return False
        seen.add(ident)
        if isinstance(exc, (
            BrokenPipeError,
            ConnectionAbortedError,
            ConnectionResetError,
            ConnectionClosed,
        )):
            return True
        # 优先沿显式原因(__cause__)回溯，其次隐式上下文(__context__)
        exc = exc.__cause__ or exc.__context__
    return False


class WebSocketHandshakeNoiseFilter(logging.Filter):
    """Suppress restart-time handshakes where the browser already disconnected.

    日志过滤器：仅对 "opening handshake failed" 这条消息生效——若其异常链
    含连接中断类异常则丢弃该日志（视为噪声），其余情况照常放行。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.getMessage() != OPENING_HANDSHAKE_FAILED_MESSAGE:
            return True
        exc_info = record.exc_info
        # exc_info 为 (type, value, tb) 三元组时取出异常实例，否则视为无异常
        exc = exc_info[1] if isinstance(exc_info, tuple) and len(exc_info) >= 2 else None
        return not _exception_chain_has_disconnect(exc)


def websockets_server_logger() -> logging.Logger:
    # 幂等挂载过滤器：已存在同类型过滤器时不再重复添加，避免重复过滤
    ws_logger = logging.getLogger("websockets.server")
    if not any(isinstance(f, WebSocketHandshakeNoiseFilter) for f in ws_logger.filters):
        ws_logger.addFilter(WebSocketHandshakeNoiseFilter())
    return ws_logger
