from __future__ import annotations


import logging
from pathlib import Path
from typing import IO

from pydantic import BaseModel

from crispcode.core.events.bus import EventBus

logger = logging.getLogger(__name__)


class EventWriter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: IO[str] | None = None

    async def __aenter__(self) -> EventWriter:
        """打开事件文件(追加模式),供async with使用"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")
        return self

    async def __aexit__(self, *args: object) -> None:
        """关闭事件文件"""
        if self._file is not None:
            self._file.close()
            self._file = None

    async def handle(self, event: BaseModel) -> None:
        """将事件序列化为JSON行写入文件,写入失败时记录日志但不抛出异常"""
        if self._file is None:
            return
        try:
            self._file.write(event.model_dump_json() + "\n")
            self._file.flush()
        except (OSError, ValueError) as e:
            logger.error("EventWriter: failed to write event: %s", e)

    def subscribe(self, bus: EventBus) -> None:
        """将handle注册为bus订阅者"""
        bus.subscribe(self.handle)
