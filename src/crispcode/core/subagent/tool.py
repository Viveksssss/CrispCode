from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from crispcode.core.agents.loader import AgentProfile, AgentProfileLoader
from crispcode.core.bus.events import SubagentFinishedEvent, SubagentStartedEvent
from crispcode.core.context import ExecutionContext
from crispcode.core.events.bus import EventBus
from crispcode.core.events.writer import EventWriter
from crispcode.core.loop import AgentLoop
from crispcode.core.runs import new_runs_id
from crispcode.core.subagent.registry import BackgroundTaskRegistry
from crispcode.core.tools.base import BaseTool, ToolResult
from crispcode.core.tools.builtin.bash import BashTool
from crispcode.core.tools.builtin.list_dir import ListDirTool
from crispcode.core.tools.builtin.read_file import ReadFileTool
from crispcode.core.tools.builtin.task_create import TaskCreateTool
from crispcode.core.tools.builtin.task_get import TaskGetTool
from crispcode.core.tools.builtin.task_list import TaskListTool
from crispcode.core.tools.builtin.task_update import TaskUpdateTool
from crispcode.core.tools.builtin.write_file import WriteFileTool
from crispcode.core.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from crispcode.core.llm.provider import LLMProvider
    from crispcode.core.permissions.manager import PermissionManager
_profile_loader = AgentProfileLoader()


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SpawnAgentParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    description: str
    prompt: str
    runs_in_background: bool = False
    subagent_type: str = ""


class SpawnAgentTool(BaseTool):
    """在隔离的冷启动上下文中派生子 agent，支持前台阻塞和后台并行两种模式"""

    name = "spawn_agent"
    description = (
        "Spawn an isolated sub-agent to handle a self-contained sub-task. "
        "The sub-agent starts with a clean context containing only the provided prompt — "
        "it does not inherit the current conversation history. "
        "Use run_in_background=true to run in parallel; retrieve result later with agent_result."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "3-5 word task description shown in progress display",
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Complete task description including all context the sub-agent needs. "
                    "The sub-agent cannot see the parent conversation, so be explicit."
                ),
            },
            "runs_in_background": {
                "type": "boolean",
                "description": "When true, returns immediately with a runs_id; use agent_result to poll.",  # noqa: E501
            },
            "subagent_type": {
                "type": "string",
                "description": "Agent role profile (planner/executor/reviewer). Leave empty for default.",  # noqa: E501
            },
        },
        "required": ["description", "prompt"],
    }

    params_model = SpawnAgentParams

    def __init__(
        self,
        provider: LLMProvider,
        parent_bus: EventBus,
        parent_runs_id: str,
        permission_manager: PermissionManager | None,
        max_steps: int,
        task_registry: BackgroundTaskRegistry,
        runs_dir: Path,
        session_id: str,
        depth: int = 0,
    ) -> None:
        self._provider = provider
        self._parent_bus = parent_bus
        self._parent_runs_id = parent_runs_id
        self._permission_manager = permission_manager
        self._max_steps = max_steps
        self._task_registry = task_registry
        self._runs_dir = runs_dir
        self._session_id = session_id
        self._depth = depth

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """派生子 agent，前台时阻塞直到完成并返回结果，后台时立即返回 runs_id"""
        p = SpawnAgentParams.model_validate(params)

        if self._depth >= 2:
            return ToolResult(
                content="Subagent nesting limit (2) reached; cannot spawn further subagents.",
                is_error=True,
                error_type="runtime_error",
            )

        profile: AgentProfile | None = None
        if p.subagent_type:
            profile = _profile_loader.load(p.subagent_type)

        child_runs_id = new_runs_id()
        child_context = ExecutionContext(
            runs_id=child_runs_id,
            goal=p.prompt,
            max_steps=self._max_steps,
            system_prompt_override=profile.system_prompt if profile else None,
        )

        child_bus = EventBus()

        # 只把子 agent 的摘要事件桥接到父 bus，避免低层事件（llm.token/thinking/token/tool 等）
        # 混入父 TUI 的渲染流。
        _SUMMARY_EVENTS = (SubagentStartedEvent, SubagentFinishedEvent)

        async def _bridge(event: BaseModel) -> None:
            if isinstance(event, _SUMMARY_EVENTS):
                await self._parent_bus.publish(event)

        child_bus.subscribe(_bridge)

        child_registry = self._build_child_registry(child_bus, child_runs_id, profile)
        child_loop = AgentLoop(
            self._provider,
            child_registry,
            child_bus,
            permission_manager=self._permission_manager,
            session_id=self._session_id,
        )

        await self._parent_bus.publish(
            SubagentStartedEvent(
                runs_id=child_runs_id,
                parent_runs_id=self._parent_runs_id,
                description=p.description,
                ts=_now(),
            )
        )

        child_runs_path = self._runs_dir / child_runs_id
        child_runs_path.mkdir(parents=True, exist_ok=True)

        if p.runs_in_background:
            task: asyncio.Task[None] = asyncio.create_task(
                self._run_background(
                    child_loop,
                    child_context,
                    child_bus,
                    child_runs_path,
                    child_runs_id,
                )
            )

            self._task_registry.register(child_runs_id, task, child_context)
            return ToolResult(
                content=(
                    f"Subagent started in background. runs_id={child_runs_id}. "
                    f"Use agent_result(runs_id='{child_runs_id}') to retrieve result."
                )
            )

        async with EventWriter(child_runs_path / "events.jsonl") as writer:
            writer.subscribe(child_bus)
            await child_loop.run(child_context)

        await self._parent_bus.publish(
            SubagentFinishedEvent(
                runs_id=child_runs_id,
                parent_runs_id=self._parent_runs_id,
                status=child_context.status,
                ts=_now(),
            )
        )

        if child_context.status == "success":
            return ToolResult(
                content=child_context.result
                or "Subagent completed with no text output."
            )
        return ToolResult(
            content=(
                child_context.result
                or f"Subagent failed (status={child_context.status}, reason={child_context.reason})"
            ),
            is_error=True,
            error_type="runtime_error",
        )

    async def _run_background(
        self,
        loop: AgentLoop,
        context: ExecutionContext,
        bus: EventBus,
        runs_path: Path,
        runs_id: str,
    ) -> None:
        async with EventWriter(runs_path / "events.jsonl") as writer:
            writer.subscribe(bus)
            await loop.run(context)

        await self._parent_bus.publish(
            SubagentFinishedEvent(
                runs_id=runs_id,
                parent_runs_id=self._parent_runs_id,
                status=context.status,
                ts=_now(),
            )
        )

    def _build_child_registry(
        self,
        child_bus: EventBus,
        child_runs_id: str,
        profile: AgentProfile | None,
    ) -> ToolRegistry:
        """构造子 registry；基于角色配置过滤工具，深度允许时注册嵌套 SpawnAgentTool"""
        from crispcode.core.task.manager import TaskManager

        allowed: set[str] | None = (
            set(profile.allowed_tools) if profile and profile.allowed_tools else None
        )

        def _allowed(name: str) -> bool:
            return allowed is None or name in allowed

        registry = ToolRegistry()
        _all_tools = [
            ReadFileTool(),
            BashTool(),
            WriteFileTool(),
            ListDirTool(),
        ]
        for t in _all_tools:
            if _allowed(t.name):
                registry.register(t)

        child_task_manager = TaskManager(self._runs_dir / child_runs_id / ".tasks")
        for t in [
            TaskCreateTool(child_task_manager),
            TaskUpdateTool(child_task_manager),
            TaskListTool(child_task_manager),
            TaskGetTool(child_task_manager),
        ]:
            if _allowed(t.name):
                registry.register(t)

        if self._depth < 1:
            nested = SpawnAgentTool(
                provider=self._provider,
                parent_bus=child_bus,
                parent_runs_id=child_runs_id,
                permission_manager=self._permission_manager,
                max_steps=self._max_steps,
                task_registry=self._task_registry,
                runs_dir=self._runs_dir,
                session_id=self._session_id,
                depth=self._depth + 1,
            )
            if _allowed("spawn_agent"):
                registry.register(nested)
            if _allowed("agent_result"):
                registry.register(AgentResultTool(self._task_registry))

        return registry


class AgentResultParams(BaseModel):
    runs_id: str


class AgentResultTool(BaseTool):
    """查询后台 subagent 的执行状态和最终结果"""

    name = "agent_result"
    description = (
        "Retrieve the result of a background sub-agent previously started with spawn_agent. "
        "Returns 'still running' if the sub-agent has not yet completed."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "runs_id": {
                "type": "string",
                "description": "The runs_id returned by spawn_agent(run_in_background=true)",
            },
        },
        "required": ["runs_id"],
    }
    params_model = AgentResultParams

    # 初始化，持有共享的后台任务注册表
    def __init__(self, task_registry: BackgroundTaskRegistry) -> None:
        self._task_registry = task_registry

    # 查询指定 runs_id 的后台任务状态，返回结果或错误
    async def invoke(self, params: dict[str, object]) -> ToolResult:
        p = AgentResultParams.model_validate(params)
        entry = self._task_registry.get(p.runs_id)
        if entry is None:
            return ToolResult(
                content=f"Unknown runs_id: {p.runs_id}. Only background subagents can be queried.",
                is_error=True,
                error_type="runtime_error",
            )
        task, context = entry
        if not task.done():
            return ToolResult(content="still running")
        if task.cancelled():
            return ToolResult(
                content="Subagent was cancelled.",
                is_error=True,
                error_type="runtime_error",
            )
        exc = task.exception()
        if exc is not None:
            return ToolResult(
                content=f"Subagent raised an exception: {exc}",
                is_error=True,
                error_type="runtime_error",
            )
        return ToolResult(
            content=context.result or "Subagent completed with no text result."
        )
