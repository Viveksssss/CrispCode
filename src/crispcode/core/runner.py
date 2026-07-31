from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path


from crispcode.core.bus.events import RunFinishedEvent, RunStartedEvent
from crispcode.core.config import CrispConfig
from crispcode.core.context import ExecutionContext
from crispcode.core.events.bus import EventBus, EventHandler
from crispcode.core.events.writer import EventWriter
from crispcode.core.llm.provider import AnthropicProvider, LLMProvider
from crispcode.core.loop import AgentLoop
from crispcode.core.runs import RUNS_DIR, new_runs_id, ensure_run_dir, events_file
from crispcode.core.tools.builtin.read_file import ReadFileTool
from crispcode.core.tools.registry import ToolRegistry


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentRunner:
    def __init__(
        self,
        config: CrispConfig,
        *,
        bus: EventBus | None = None,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._bus = bus
        self._extra_handler = extra_handlers or []
        self._runs_dir = runs_dir or RUNS_DIR

    async def run(self, goal: str, *, runs_id: str | None = None) -> None:
        runs_id = runs_id if runs_id else new_runs_id()

        # ✅ 使用 self._runs_dir 而不是全局函数
        run_dir = ensure_run_dir(runs_dir=self._runs_dir, runs_id=runs_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = run_dir / "events.jsonl"

        bus = self._bus if self._bus is not None else EventBus()
        for h in self._extra_handler:
            bus.subscribe(h)

        provider = self._provider or AnthropicProvider(self._config.llm.default_model)
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        loop = AgentLoop(provider, registry, bus)

        context = ExecutionContext(
            runs_id,
            goal,
            max_steps=self._config.agent.max_steps,
        )

        async with EventWriter(events_path) as writer:
            writer.subscribe(bus)
            await bus.publish(
                RunStartedEvent(
                    runs_id=runs_id,
                    goal=goal,
                    ts=_now(),
                )
            )

            cancelled = False
            try:
                await loop.run(context)
            except asyncio.CancelledError:
                cancelled = True
                if not context.is_done():
                    context.mark_failed("cancelled")

            await bus.publish(
                RunFinishedEvent(
                    runs_id=runs_id,
                    status=context.status,
                    reason=context.reason,
                    steps=context.step,
                    ts=_now(),
                )
            )
        if cancelled:
            raise asyncio.CancelledError()
