"""WebUI transcription envelope handling.

The WebSocket channel owns transport and subscription fan-out. This module owns
the WebUI-specific audio transcription action carried over that socket.

本模块负责处理 WebUI 侧的音频转录请求信封（envelope）：从 WebSocket 收到
携带音频 data_url 的请求后，调用转录引擎得到文本，再返回对应的事件名与
载荷供 WebSocket 通道下发。与传输层解耦，仅关注转录动作本身。
"""

from __future__ import annotations

from typing import Any

from nanobot.audio.transcription import (
    TranscriptionIngressError,
    resolve_transcription_config,
    transcribe_audio_data_url,
)
from nanobot.config.loader import load_config

_MAX_REQUEST_ID_LENGTH = 80


async def webui_transcription_event(envelope: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return the WS event name and payload for one WebUI transcription request.

    处理单条 WebUI 转录请求：校验 request_id 后调用转录引擎，成功返回
    ``transcription_result`` 事件，失败返回 ``transcription_error`` 事件。
    返回值首元素为事件名，次元素为载荷，供 WebSocket 通道直接下发。
    """
    request_id = envelope.get("request_id")
    # request_id 必须是非空且长度受限的字符串，用于客户端关联请求与响应
    valid_request_id = (
        isinstance(request_id, str)
        and 0 < len(request_id) <= _MAX_REQUEST_ID_LENGTH
    )

    def error(detail: str, **extra: Any) -> tuple[str, dict[str, Any]]:
        # 错误载荷始终带 detail；仅当 request_id 合法时才回带，便于客户端关联
        payload: dict[str, Any] = {"detail": detail, **extra}
        if valid_request_id:
            payload["request_id"] = request_id
        return "transcription_error", payload

    if not valid_request_id:
        return error("invalid_request")

    try:
        # 每次请求都重新加载配置并解析转录参数，确保配置变更即时生效
        text = await transcribe_audio_data_url(
            envelope.get("data_url"),
            resolve_transcription_config(load_config()),
            duration_ms=envelope.get("duration_ms"),
        )
    except TranscriptionIngressError as exc:
        # 转录入口校验失败（如音频格式/大小不合规），透传其 detail 与附加信息
        return error(exc.detail, **exc.extra)
    return "transcription_result", {"request_id": request_id, "text": text}
