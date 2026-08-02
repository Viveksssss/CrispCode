from __future__ import annotations

import asyncio
from datetime import datetime, UTC
import fnmatch
import logging
import uuid
from dataclasses import dataclass
from pydantic import BaseModel
from crispcode.core.bus.envelope import EventPushEnvelope
from crispcode.core.trace.writer import TraceWriter
from crispcode.core.trace.record import TraceRecord

logger = logging.getLogger(__name__)


@dataclass
class _Subscription:
    sub_id: str
    writer: asyncio.StreamWriter
    topics: list[str]
    scope: str


def _now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串"""
    return datetime.now(UTC).isoformat()


class IpcEventBroadcaster:
    def __init__(self, trace: TraceWriter | None = None) -> None:
        # 使用字典：key = sub_id, value = Subscription
        self._subscriptions: dict[str, _Subscription] = {}
        # 反向索引：writer -> [sub_ids]（便于快速清理）
        self._writer_subs: dict[asyncio.StreamWriter, list[str]] = {}
        self._trace = trace

    def subscribe(
        self, writer: asyncio.StreamWriter, topics: list[str], scope: str = "global"
    ) -> str:
        """注册一个客户端订阅，返回 subscription_id"""
        sub_id = f"sub-{uuid.uuid4().hex[:8]}"
        sub = _Subscription(sub_id=sub_id, writer=writer, topics=topics, scope=scope)
        # O(1) 插入
        self._subscriptions[sub_id] = sub
        if writer not in self._writer_subs:
            self._writer_subs[writer] = []
        self._writer_subs[writer].append(sub_id)
        return sub_id

    def unsubscribe(self, writer: asyncio.StreamWriter) -> None:
        """O(1)取消订阅"""
        sub_ids = self._writer_subs.pop(writer, [])
        for sub_id in sub_ids:
            self._subscriptions.pop(sub_id, None)

    async def handle(self, event: BaseModel) -> None:
        """将事件推送到所有匹配的订阅客户端，写入失败时延迟清理死连接"""
        event_dict = event.model_dump()
        event_type: str = event_dict.get("type", "")
        runs_id: str | None = event_dict.get("runs_id")

        dead: list[asyncio.StreamWriter] = []

        for sub in list(self._subscriptions.values()):
            if not self._matches_scope(runs_id, sub.scope):
                continue
            if not self._matches_topic(event_type, sub.topics):
                continue

            try:
                envelope = EventPushEnvelope(event=event_dict)
                sub.writer.write(envelope.model_dump_json().encode() + b"\n")
                await sub.writer.drain()
                if self._trace is not None:
                    client_id = str(sub.writer.get_extra_info("peername", "<unknown>"))
                    self._trace.emit(
                        TraceRecord(
                            ts=_now(),
                            direction="CORE->CLIENT",
                            layer="ipc",
                            kind="push",
                            runs_id=runs_id,
                            client_id=client_id,
                            data={"sub_id": sub.sub_id, "event_type": event_type},
                        )
                    )
            except (ConnectionResetError, BrokenPipeError, OSError):
                logger.debug(
                    "dead connection for sub %s, scheduling cleanup", sub.sub_id
                )
                dead.append(sub.writer)

        for writer in dead:
            self.unsubscribe(writer)

    @staticmethod
    def _matches_topic(event_type: str, topics: list[str]) -> bool:
        """检查事件类型是否匹配订阅的 topic 列表（支持 fnmatch glob 模式）"""
        return any(fnmatch.fnmatch(event_type, pattern) for pattern in topics)

    @staticmethod
    def _matches_scope(runs_id: str | None, scope: str) -> bool:
        """检查事件 runs_id 是否匹配订阅的 scope（global 全通，run:<id> 精确匹配）"""
        if scope == "global":
            return True
        if scope.startswith("run:"):
            return runs_id == scope[4:]
        return False
