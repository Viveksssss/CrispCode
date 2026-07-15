from __future__ import annotations

import asyncio
import datetime
import logging
import signal
import time
from typing import Any

import crispcode
from crispcode.core.bus.commands import PongResult
from crispcode.core.config import get_config
from crispcode.core.logging import setup_logging
from crispcode.core.transport.socket_server import SocketServer

logger = logging.getLogger(__name__)


class CoreApp:
    def __init__(self) -> None:
        self._start_time = time.monotonic()

    async def _ping_handler(self, params: dict[str, Any]) -> PongResult:
        client = params.get("client", "unknown")
        logger.debug("ping from %s", client)
        return PongResult(
            server_version=crispcode.__version__,
            uptime_ms=int(time.monotonic() - self._start_name) * 1000,
            received_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )

    async def run(self) -> None:
        self._start_name = time.monotonic()
        config = get_config()
        setup_logging(config)

        server = SocketServer(config.host, config.port)
        server.register("core.ping", self._ping_handler)

        addr = await server.start()
        logger.info("crisp-core %s listening addr=%s", crispcode.__version__, addr)
        logger.info("config: %s", config)

        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        loop.add_signal_handler(signal.SIGINT, shutdown.set)
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)

        await shutdown.wait()

        logger.info("shutting down")
        await server.stop()


def run() -> None:
    asyncio.run(CoreApp().run())
