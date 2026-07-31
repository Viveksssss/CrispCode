from __future__ import annotations

import asyncio
import datetime
import fnmatch
import json
import logging
import signal
import time
from typing import Any

import crispcode
from crispcode.core.bus.commands import (
    PongResult,
    AgentRunResult,
    AgentRunCommand,
    AgentRunResult,
    EventSubscribeCommand,
    EventSubscribeResult,
)
from crispcode.core.bus.envelope import EventPushEnvelope
from crispcode.core.config import CrispConfig, CrispConfig, get_config
from crispcode.core.events.bus import EventBus
from crispcode.core.logging import setup_logging
from crispcode.core.runner import AgentRunner
from crispcode.core.runs import events_file, new_runs_id
from crispcode.core.transport.ipc_broadcaster import IpcEventBroadcaster
from crispcode.core.transport.socket_server import SocketServer, get_connection_writer

logger = logging.getLogger(__name__)


class CoreApp:
    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._bus = EventBus()
        self._broadcaster = IpcEventBroadcaster()
        self._bus.subscribe(self._broadcaster.handle)
        self._current_run_task: asyncio.Task[None] | None = None
        self._config: CrispConfig | None = None

    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        """处理 core.ping 请求，返回服务版本、运行时长和接收时间"""
        client = params.get("client", "unknown")
        logger.debug("ping from %s", client)
        return PongResult(
            server_version=crispcode.__version__,
            uptime_ms=int(time.monotonic() - self._start_time) * 1000,
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    async def _agent_run_handler(self, params: dict[str, Any]) -> AgentRunResult:
        """启动一次 agent run：立即返回 runs_id，后台 task 执行 runner.run()"""
        assert self._config is not None
        cmd = AgentRunCommand.model_validate(params)

        if self._current_run_task is not None and not self._current_run_task.done():
            raise RuntimeError("a run is already in progress")

        runs_id = new_runs_id()
        runner = AgentRunner(self._config, bus=self._bus)
        self._current_run_task = asyncio.create_task(
            runner.run(cmd.goal, runs_id=runs_id)
        )
        return AgentRunResult(runs_id=runs_id)

    async def _subscribe_handler(self, params: dict[str, Any]) -> EventSubscribeResult:
        """注册客户端事件订阅，可选先回放 events.jsonl 历史再接收实时流"""
        cmd = EventSubscribeCommand.model_validate(params)
        writer = get_connection_writer()

        # 已回放的事件条数
        replayed_count = 0
        if cmd.replayed_from_run is not None:
            replayed_count = await self._replay_events(
                cmd.replayed_from_run, writer, cmd.topics
            )

        sub_id = self._broadcaster.subscribe(writer, cmd.topics, cmd.scope)
        return EventSubscribeResult(
            subscription_id=sub_id, replayed_count=replayed_count
        )

    async def _replay_events(
        self, runs_id: str, writer: asyncio.StreamWriter, topics: list[str]
    ) -> int:
        path = events_file(runs_id)
        if not path.exists():
            return 0

        count = 0
        for line in path.read_text().splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type: str = event.get("type", "")
            if not any(fnmatch.fnmatch(event_type, p) for p in topics):
                continue

            envelope = EventPushEnvelope(event=event)
            writer.write(envelope.model_dump_json().encode() + b"\n")
            count += 1
        if count:
            await writer.drain()
        return count

    async def run(self) -> None:
        self._start_name = time.monotonic()
        self._config = get_config()
        setup_logging(self._config)

        server = SocketServer(self._config.host, self._config.port)
        server.register("core.ping", self._ping_handler)
        server.register("agent.run", self._agent_run_handler)
        server.register("event.subscribe", self._subscribe_handler)

        addr = await server.start()
        logger.info("crisp-core %s listening addr=%s", crispcode.__version__, addr)
        logger.info("config: %s", self._config)

        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        await shutdown.wait()

        logger.info("shutting down")
        await server.stop()


def run() -> None:
    asyncio.run(CoreApp().run())
