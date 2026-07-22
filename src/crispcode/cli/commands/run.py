from __future__ import annotations

import asyncio
import json
import sys
import time

from pydantic import BaseModel
from crispcode.core.bus.events import (
    LlmTokenEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
from crispcode.core.config import CrispConfig
from crispcode.core.runner import AgentRunner

"""
CLI (cmd_run)                Runner                    AgentLoop                  EventBus              StdoutPrinter
    |                           |                          |                          |                      |
    |---创建 StdoutPrinter------>|                          |                          |                      |
    |---注册 handler------------>|                          |                          |                      |
    |                           |---创建 EventBus--------->|                          |                      |
    |                           |---注册 handler---------->|                          |                      |
    |                           |---创建 AgentLoop-------->|                          |                      |
    |                           |                          |                          |                      |
    |                           |                          |---publish(RunStarted)---->|                      |
    |                           |                          |                          |---handle(event)----->|
    |                           |                          |                          |                      |---打印 "[run] xxx"
    |                           |                          |                          |                      |
    |                           |                          |---publish(StepStarted)--->|                      |
    |                           |                          |                          |---handle(event)----->|
    |                           |                          |                          |                      |---打印 "[step 1]"
    |                           |                          |                          |                      |
    |                           |                          |---publish(Token)--------->|                      |
    |                           |                          |                          |---handle(event)----->|
    |                           |                          |                          |                      |---实时打印 token
    |                           |                          |                          |                      |
    |                           |                          |---publish(StepFinished)-->|                      |
    |                           |                          |                          |---handle(event)----->|
    |                           |                          |                          |                      |---打印 "[step 1] done"
    |                           |                          |                          |                      |
    |                           |                          |---publish(RunFinished)--->|                      |
    |                           |                          |                          |---handle(event)----->|
    |                           |                          |                          |                      |---打印 "[run] success"

"""


class StdoutPrinter:
    def __init__(self) -> None:
        self._inline = False
        self._run_start: float = 0.0

    def _ensure_newline(self) -> None:
        if self._inline:
            print()
            self._inline = False

    async def handle(self, event: BaseModel) -> None:
        if isinstance(event, RunStartedEvent):
            self._run_start = time.monotonic()
            print(f"[run] {event.runs_id}")

        elif isinstance(event, StepStartedEvent):
            self._ensure_newline()
            print(f"[step {event.step}] planning...")

        elif isinstance(event, LlmTokenEvent):
            print(event.token, end="", flush=True)
            self._inline = True

        elif isinstance(event, ToolCallStartedEvent):
            self._ensure_newline()
            params_str = json.dumps(event.params, ensure_ascii=False)
            print(f"[tool] {event.tool_name} {params_str}")

        elif isinstance(event, ToolCallFinishedEvent):
            print(f"[tool] {event.tool_name} ✓  {event.elapsed_ms}ms")

        elif isinstance(event, ToolCallFailedEvent):
            print(
                f"[tool] {event.tool_name} ✗  {event.error_message}",
                file=sys.stderr,
            )

        elif isinstance(event, StepFinishedEvent):
            self._ensure_newline()
            print(f"[step {event.step}] done")

        elif isinstance(event, RunFinishedEvent):
            self._ensure_newline()
            elapsed = time.monotonic() - self._run_start
            print(f"[run] {event.status}  {event.steps} steps  {elapsed:.1f}s")


def cmd_run(goal: str, config: CrispConfig) -> None:
    printer = StdoutPrinter()
    runner = AgentRunner(config, extra_handlers=[printer.handle])
    try:
        asyncio.run(runner.run(goal))
    except KeyboardInterrupt:
        sys.exit(130)
