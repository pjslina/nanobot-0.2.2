"""Chat channels module with plugin architecture.

聊天渠道包：定义各聊天平台（Telegram、Discord、Slack 等）与 nanobot 消息总线
对接的统一接口。渠道通过 pkgutil 扫描 + entry_points 插件自动发现，由
ChannelManager 统一初始化与协调。BaseChannel 为所有渠道的抽象基类。
"""

from nanobot.channels.base import BaseChannel
from nanobot.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
