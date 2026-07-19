from __future__ import annotations

from collections.abc import Awaitable, Callable
from pydantic import BaseModel

type EventHandler = Callable[[BaseModel], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[EventHandler] = []

    def subscribe(self, handler, EventHandler) -> None:
        """注册一个事件处理函数"""
        self._subscribers.append(handler)

    async def publish(self, event: BaseModel) -> None:
        """按顺序依次调用所有订阅者"""
        for handler in self._subscribers:
            await handler(event)
