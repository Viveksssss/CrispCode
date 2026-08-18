from __future__ import annotations

import asyncio
from asyncio.log import logger
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


from crispcode.core.bus.events import RunFinishedEvent, RunStartedEvent
from crispcode.core.compact.compactor import Compactor
from crispcode.core.config import CrispConfig
from crispcode.core.context import ExecutionContext
from crispcode.core.events.bus import EventBus, EventHandler
from crispcode.core.events.writer import EventWriter
from crispcode.core.llm.provider import AnthropicProvider, LLMProvider
from crispcode.core.loop import AgentLoop
from crispcode.core.memory import load_context_file
from crispcode.core.permissions.manager import PermissionManager
from crispcode.core.runs import (
    RUNS_DIR,
    new_runs_id,
    ensure_run_dir,
    events_file,
    run_dir_old,
)
from crispcode.core.session.model import Session
from crispcode.core.session.store import SessionStore
from crispcode.core.task.manager import TaskManager
from crispcode.core.tools.builtin import (
    BashTool,
    ListDirTool,
    NoteSaveTool,
    ReadFileTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskUpdateTool,
    WriteFileTool,
)
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
        permission_manager: PermissionManager | None = None,
    ) -> None:
        self._config = config
        self._provider = provider
        self._bus = bus
        self._extra_handler = extra_handlers or []
        self._runs_dir = runs_dir or RUNS_DIR
        self._trace = trace
        self._permission_manager = permission_manager

    def _build_registry(
        self,
        task_manager: TaskManager,
        *,
        session: Session | None = None,
        store: SessionStore | None = None,
        runs_id: str | None = None,
    ) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(ReadFileTool())
        registry.register(ListDirTool())
        registry.register(WriteFileTool())
        registry.register(BashTool())
        registry.register(TaskCreateTool(task_manager))
        registry.register(TaskUpdateTool(task_manager))
        registry.register(TaskListTool(task_manager))
        registry.register(TaskGetTool(task_manager))
        if session is not None and store is not None and runs_id is not None:
            registry.register(NoteSaveTool(store, session.id, runs_id))

        return registry

    async def run(self, goal: str, *, runs_id: str | None = None) -> None:
        await self.run_and_capture(goal, runs_id=runs_id)

    async def run_and_capture(
        self,
        goal: str,
        *,
        runs_id: str | None = None,
        session: Session | None = None,
        store: SessionStore | None = None,
        system_prompt_override: str | None = None,
        tool_whitelist: list[str] | None = None,
    ) -> RunOutcome:
        runs_id = runs_id or new_runs_id()

        if session is not None and store is not None:
            run_path = store.runs_dir(session.id) / runs_id
            history = store.read_messages(session.id)
            notes = store.read_notes(session.id)
        else:
            run_path = run_dir_old(runs_id).parent
            history = [{"role": "user", "content": goal}]
            notes = ""
        run_path.mkdir(parents=True, exist_ok=True)

        global_ctx = load_context_file(Path("~/.crispcode/context.md"))
        project_ctx = load_context_file(Path(".crispcode/context.md"))
        task_manager = TaskManager(run_path / ".tasks")

        bus = self._bus if self._bus is not None else EventBus()
        for h in self._extra_handler:
            bus.subscribe(h)

        context = ExecutionContext(
            runs_id,
            goal,
            max_steps=self._config.agent.max_steps,
            prefill_messages=history,
            session_notes=notes,
            global_context=global_ctx,
            project_context=project_ctx,
            system_prompt_override=system_prompt_override,
        )

        prefill_len = len(history)

        async with EventWriter(run_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await bus.publish(
                RunStartedEvent(
                    runs_id=runs_id,
                    goal=goal,
                    ts=_now(),
                )
            )

            registry = self._build_registry(
                task_manager=task_manager,
                session=session,
                store=store,
                runs_id=runs_id,
            )

            cancelled = False

            try:
                provider: LLMProvider = self._provider or AnthropicProvider(
                    self._config.llm.default_model
                )

                if self._trace is not None:
                    provider = TracingProvider(
                        inner=provider,
                        trace=self._trace,
                        include_payload=self._config.trace.include_llm_payload,
                    )
                session_dir = (
                    store.session_dir(session.id)
                    if session is not None and store is not None
                    else run_path
                )
                session_id_str = session.id if session is not None else ""
                compactor = Compactor(bus, session_dir, session_id_str)

                loop = AgentLoop(
                    provider=provider,
                    registry=registry,
                    bus=bus,
                    permission_manager=self._permission_manager,
                    session_id=session.id if session is not None else "",
                    compactor=compactor,
                    compact_threshold=self._config.compaction.auto_threshold,
                )
                await loop.run(context)
            except asyncio.CancelledError:
                cancelled = True
                if not context.is_done():
                    context.mark_failed("cancelled")
            except Exception as e:
                # 👇 添加这两行
                import traceback

                traceback.print_exc()  # ← 打印堆栈到控制台
                logger.error(f"Run error: {e}", exc_info=True)
                if not context.is_done():
                    context.mark_failed(f"llm_error: {type(e).__name__}: {e}")

            await bus.publish(
                RunFinishedEvent(
                    runs_id=runs_id,
                    status=context.status,
                    reason=context.reason,
                    steps=context.step,
                    ts=_now(),
                )
            )
        if session is not None and store is not None:
            store.append_messages(
                session.id, context.messages[prefill_len:], runs_id=runs_id
            )
        if cancelled:
            raise asyncio.CancelledError()

        return RunOutcome(
            status=context.status,
            result=context.result,
            reason=context.reason,
        )
