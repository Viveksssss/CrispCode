from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from crispcode.core.events.bus import EventBus
from crispcode.core.llm.types import ToolCallBlock
from crispcode.core.permissions.manager import PermissionManager
from crispcode.core.tools.base import BaseTool, ToolResult
from crispcode.core.tools.invocation import invoke_tool
from crispcode.core.tools.registry import ToolRegistry

# --- stub tools --------------------------------------------------------------


class _EchoTool(BaseTool):
    name = "echo"
    description = "Echoes the msg param"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        return ToolResult(content=str(params["msg"]))


class _SlowTool(BaseTool):
    name = "slow"
    description = "Sleeps forever"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        await asyncio.sleep(60)
        return ToolResult(content="done")


class _BrokenTool(BaseTool):
    name = "broken"
    description = "Always raises"
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        raise RuntimeError("boom")


# --- helpers -----------------------------------------------------------------


def _call(
    name: str, inp: dict[str, object] | None = None, uid: str = "t1"
) -> ToolCallBlock:
    return ToolCallBlock(id=uid, name=name, input=inp or {})


async def _run(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    timeout: float = 5.0,
    *,
    permission_manager: PermissionManager | None = None,
) -> tuple[ToolResult, list[BaseModel]]:
    bus = EventBus()
    events: list[BaseModel] = []

    async def _collect(e: BaseModel) -> None:
        events.append(e)

    bus.subscribe(_collect)
    result = await invoke_tool(
        registry,
        tool_call,
        bus,
        runs_id="r1",
        timeout=timeout,
        permission_manager=permission_manager,
    )
    return result, events


class _AllowPermissionManager(PermissionManager):
    """模拟权限审批：总是允许，先推送 permission.requested 再返回 allow"""

    async def check_and_wait(
        self,
        tool_use_id: str,
        tool_name: str,
        params: dict[str, Any],
        session_id: str,
        event_emitter: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> tuple[bool, str]:
        await event_emitter(
            {
                "type": "permission.requested",
                "tool_use_id": tool_use_id,
                "tool_name": tool_name,
                "params": params,
                "param_preview": "",
                "session_id": session_id,
                "ts": "2026-08-10T00:00:00Z",
            }
        )
        return True, "allow_once"


# --- tests -------------------------------------------------------------------


async def test_success_returns_content_and_finished_event() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    result, events = await _run(registry, _call("echo", {"msg": "hi"}))
    assert not result.is_error
    assert result.content == "hi"
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert types[0] == "tool.call_started"
    assert "tool.call_finished" in types
    assert "tool.call_failed" not in types


async def test_unknown_tool_returns_runtime_error() -> None:
    result, events = await _run(ToolRegistry(), _call("nonexistent"))
    assert result.is_error
    assert result.error_type == "runtime_error"
    assert "unknown tool" in result.content
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert "tool.call_started" in types
    assert "tool.call_failed" in types
    assert "tool.call_finished" not in types


async def test_missing_required_param_gives_schema_error() -> None:
    registry = ToolRegistry()
    registry.register(_EchoTool())
    result, events = await _run(registry, _call("echo", {}))  # "msg" is required
    assert result.is_error
    assert result.error_type == "schema_error"
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert "tool.call_failed" in types


async def test_timeout_gives_timeout_error() -> None:
    registry = ToolRegistry()
    registry.register(_SlowTool())
    result, events = await _run(registry, _call("slow"), timeout=0.05)
    assert result.is_error
    assert result.error_type == "timeout"
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert "tool.call_failed" in types


async def test_runtime_exception_gives_runtime_error() -> None:
    registry = ToolRegistry()
    registry.register(_BrokenTool())
    result, events = await _run(registry, _call("broken"))
    assert result.is_error
    assert result.error_type == "runtime_error"
    assert "boom" in result.content


async def test_started_event_always_first() -> None:
    result, events = await _run(ToolRegistry(), _call("nonexistent"))
    assert events[0].type == "tool.call_started"  # type: ignore[attr-defined]


async def test_allow_decision_publishes_granted_and_runs_tool() -> None:
    """权限允许路径：应发布 permission.granted 并正常执行工具（回归：此前缺少事件实例会抛 TypeError）"""
    registry = ToolRegistry()
    registry.register(_EchoTool())
    result, events = await _run(
        registry,
        _call("echo", {"msg": "hi"}),
        permission_manager=_AllowPermissionManager(),
    )
    assert not result.is_error
    assert result.content == "hi"
    types = [e.type for e in events]  # type: ignore[attr-defined]
    assert "permission.requested" in types
    assert "permission.granted" in types
    assert "tool.call_finished" in types
    assert "tool.call_failed" not in types
