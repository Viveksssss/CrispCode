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
from crispcode.core.runs import RUNS_DIR, new_run_id
from crispcode.core.tools.builtin.read_file import ReadFileTool
from crispcode.core.tools.registry import ToolRegistry


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentRunner:
    def __init__(
        self,
        config: CrispConfig,
        *,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        run_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._extra_handler = extra_handlers or []
        self._run_dir = run_dir or RUNS_DIR

    async def run(self, goal: str) -> None:
        run_id = new_run_id()
        run_path = self._run_dir / run_id
        run_path.mkdir(parents=True, exist_ok=True)

        bus = EventBus()
        for h in self._extra_handler:
            bus.subscribe(h)

        provider = self._provider or AnthropicProvider(self._config.llm.default_model)
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        loop = AgentLoop(provider, registry, bus)

        context = ExecutionContext(
            run_id,
            goal,
            max_steps=self._config.agent.max_steps,
        )

        async with EventWriter(run_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await bus.publish(
                RunStartedEvent(
                    run_id=run_id,
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
                    run_id=run_id,
                    status=context.status,
                    reason=context.reason,
                    steps=context.step,
                    ts=_now(),
                )
            )
        if cancelled:
            raise asyncio.CancelledError()
