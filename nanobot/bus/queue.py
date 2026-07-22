"""Async message queue for decoupling channel-agent communication.

异步消息队列，用于解耦"渠道"与"agent 核心"。
这是 nanobot 架构的中枢：渠道只负责把外部消息丢进 inbound 队列，
agent 只负责从 inbound 取消息处理、把回复丢进 outbound 队列，
二者互不直接调用，从而解耦并发与生命周期。
"""

import asyncio

from nanobot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    Async message bus that decouples chat channels from the agent core.

    异步消息总线，解耦聊天渠道与 agent 核心。
    渠道把消息推入 inbound 队列，agent 处理后把回复推入 outbound 队列。

    Channels push messages to the inbound queue, and the agent processes
    them and pushes responses to the outbound queue.
    """

    def __init__(self):
        # 两条独立的 asyncio.Queue：inbound（渠道->agent）、outbound（agent->渠道）。
        # 都是阻塞式异步队列，get() 在队列空时会挂起等待。
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish a message from a channel to the agent. 渠道把消息发布给 agent。"""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available).

        取出下一条入站消息；队列空时会挂起等待，直到有消息可用。
        """
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """Publish a response from the agent to channels. agent 把回复发布给渠道。"""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available).

        取出下一条出站消息；队列空时会挂起等待。渠道侧的发送协程通常在此阻塞。
        """
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages. 待处理的入站消息数。"""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages. 待处理的出站消息数。"""
        return self.outbound.qsize()
