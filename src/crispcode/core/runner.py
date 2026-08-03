from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
from crispcode.core.task.manager import TaskManager
from crispcode.core.tools.builtin.bash import BashTool
from crispcode.core.tools.builtin.list_dir import ListDirTool
from crispcode.core.tools.builtin.read_file import ReadFileTool
from crispcode.core.tools.builtin.task_create import TaskCreateTool
from crispcode.core.tools.builtin.task_get import TaskGetTool
from crispcode.core.tools.builtin.task_list import TaskListTool
from crispcode.core.tools.builtin.task_update import TaskUpdateTool
from crispcode.core.tools.builtin.write_file import WriteFileTool
from crispcode.core.tools.registry import ToolRegistry
from crispcode.core.trace.provider import TracingProvider
from crispcode.core.trace.writer import TraceWriter


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunOutcome:
    status: str
    result: str
    reason: str | None


class AgentRunner:
    def __init__(
        self,
        config: CrispConfig,
        *,
        bus: EventBus | None = None,
        provider: LLMProvider | None = None,
        extra_handlers: list[EventHandler] | None = None,
        runs_dir: Path | None = None,
        trace: TraceWriter | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._bus = bus
        self._extra_handler = extra_handlers or []
        self._runs_dir = runs_dir or RUNS_DIR
        self._trace = trace

    def _build_registry(self, task_manager) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        registry.register(ListDirTool())
        registry.register(WriteFileTool())
        registry.register(BashTool())
        registry.register(TaskCreateTool(task_manager))
        registry.register(TaskUpdateTool(task_manager))
        registry.register(TaskListTool(task_manager))
        registry.register(TaskGetTool(task_manager))
        return registry

    async def run(self, goal: str, *, runs_id: str | None = None) -> None:
        await self.run_and_capture(goal, runs_id=runs_id)

    async def run_and_capture(
        self, goal: str, *, runs_id: str | None = None
    ) -> RunOutcome:
        runs_id = runs_id if runs_id else new_runs_id()

        # ✅ 使用 self._runs_dir 而不是全局函数
        run_dir = ensure_run_dir(runs_dir=self._runs_dir, runs_id=runs_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = run_dir / "events.jsonl"

        task_manager = TaskManager(run_dir / ".tasks")

        bus = self._bus if self._bus is not None else EventBus()
        for h in self._extra_handler:
            bus.subscribe(h)

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

            provider: LLMProvider = self._provider or AnthropicProvider(
                self._config.llm.default_model
            )

            if self._trace is not None:
                provider = TracingProvider(
                    inner=provider,
                    trace=self._trace,
                    include_payload=self._config.trace.include_llm_payload,
                )

            registry = self._build_registry(task_manager=task_manager)
            loop = AgentLoop(
                provider=provider,
                registry=registry,
                bus=bus,
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

        return RunOutcome(
            status=context.status,
            result=context.result,
            reason=context.reason,
        )
