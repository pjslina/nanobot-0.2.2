"""Media gateway services shared by WebUI HTTP routes and WebSocket frames.

WebUI 媒体网关：为 HTTP 路由与 WebSocket 帧提供统一的媒体 URL 签名及
markdown/媒体增强服务。本模块是一个轻量门面（facade），持有进程级 HMAC
密钥与媒体目录解析器，将具体实现委派给 ``media_api`` 与 ``transcript``，
使上层无需直接处理密钥与路径解析细节。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from nanobot.config.paths import get_media_dir
from nanobot.webui.media_api import (
    attach_signed_media_urls,
    serve_signed_media,
    sign_media_path,
    sign_or_stage_media_path,
    signed_media_attachments,
)
from nanobot.webui.transcript import rewrite_local_markdown_images


class WebUIMediaGateway:
    """Own media URL signing and WebUI markdown/media augmentation.

    持有本实例的签名密钥与媒体目录解析策略，向外暴露签名、静态媒体服务、
    本地 markdown 图片改写以及 payload/转录媒体的签名 URL 注入等能力。
    各方法多为对 ``media_api`` / ``transcript`` 中无状态函数的薄封装，
    关键差异在于统一绑定 ``self.secret`` 与 ``self._media_dir``。
    """

    def __init__(
        self,
        *,
        workspace_path: Path,
        logger: Any,
        media_dir: Callable[[str | None], Path] | None = None,
        secret: bytes | None = None,
    ) -> None:
        self.workspace_path = workspace_path
        self.logger = logger
        # 媒体目录解析器：按渠道返回对应媒体目录；未传入则回退到全局 get_media_dir
        self._media_dir = media_dir or (lambda channel=None: get_media_dir(channel))
        # HMAC 签名密钥：未显式提供时每次启动随机生成 32 字节，
        # 故重启后旧签名 URL 即失效（进程级临时密钥，不持久化）
        self.secret = secret or secrets.token_bytes(32)

    def serve_signed_media(
        self,
        sig: str,
        payload: str,
        *,
        request: WsRequest | None = None,
    ) -> Response:
        """校验签名并提供对应的静态媒体文件响应（绑定本实例密钥与媒体目录）。"""
        return serve_signed_media(
            sig,
            payload,
            secret=self.secret,
            request=request,
            media_dir=self._media_dir,
        )

    def sign_media_path(self, abs_path: Path) -> str | None:
        """为工作区内已存在的媒体绝对路径生成签名 URL；不可达路径返回 None。"""
        return sign_media_path(
            abs_path,
            secret=self.secret,
            media_dir=self._media_dir,
        )

    def sign_or_stage_media_path(self, path: Path) -> dict[str, str] | None:
        """为媒体路径签名；对外部文件先“暂存”进媒体目录再签名（见 media_api）。"""
        return sign_or_stage_media_path(
            path,
            secret=self.secret,
            media_dir=self._media_dir,
            logger=self.logger,
        )

    def rewrite_local_markdown_images(
        self,
        text: str,
        *,
        workspace_path: Path | None = None,
    ) -> str:
        """将 markdown 中的本地图片路径改写为签名 URL，便于前端直接访问。"""
        return rewrite_local_markdown_images(
            text,
            workspace_path=workspace_path or self.workspace_path,
            sign_path=self.sign_or_stage_media_path,
        )

    def augment_media_urls(self, payload: dict[str, Any]) -> None:
        """为 payload 中的媒体引用就地附加签名 URL（用于 WebSocket 推送的消息体）。"""
        attach_signed_media_urls(payload, sign_path=self.sign_media_path)

    def augment_transcript_media(self, paths: list[str]) -> list[dict[str, Any]]:
        """将转录中的媒体路径列表转为带签名 URL 的附件对象列表。"""
        return signed_media_attachments(
            paths,
            sign_path=self.sign_or_stage_media_path,
        )

    def augment_transcript_user_media(self, paths: list[str]) -> list[dict[str, Any]]:
        """用户消息媒体入口：当前与转录媒体处理一致，保留独立方法以便后续差异化。"""
        return self.augment_transcript_media(paths)
