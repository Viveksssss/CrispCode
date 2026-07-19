from __future__ import annotations

import asyncio
import json
import sys
import time

from pydantic import BaseModel

from crispcode.core.config import CrispConfig


class StdoutPrinter:
    def __init__(self) -> None:
        self._inline = False
        self._run_start: float = 0.0

    def _ensure_newline(self) -> None:
        if self.inline:
            print()
            self._inline = False

    async def handle(self, goal: str) -> None: ...


def cmd_run(goal: str, config: CrispConfig) -> None:
    printer = StdoutPrinter()
    # runner = AgentRunner()
