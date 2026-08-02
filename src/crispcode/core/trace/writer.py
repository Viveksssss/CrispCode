from __future__ import annotations

import asyncio

from pathlib import Path
from crispcode.core.trace.record import TraceRecord


class TraceWriter:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._queue: asyncio.Queue[TraceRecord] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """创建目录,启动后台,drtai task"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._drain())

    async def stop(self) -> None:
        await self._queue.join()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _drain(self) -> None:
        """后台任务,将队列中的数据写入文件"""
        with open(self._path, "a") as f:
            while True:
                record: TraceRecord = await self._queue.get()
                try:
                    f.write(record.model_dump_json() + "\n")
                    f.flush()
                finally:
                    self._queue.task_done()

    def emit(self, record: TraceRecord) -> None:
        """将数据放入队列"""
        self._queue.put_nowait(record)
