from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError

from crispcode.core.bus.events import (
    PermissionDeniedEvent,
    PermissionGrantedEvent,
    PermissionRequestedEvent,
    ToolCallFailedEvent,
    ToolCallFinishedEvent,
    ToolCallStartedEvent,
)
from crispcode.core.events.bus import EventBus
from crispcode.core.llm.types import ToolCallBlock
from crispcode.core.permissions.manager import PermissionManager
from crispcode.core.tools.base import ToolResult
from crispcode.core.tools.registry import ToolRegistry
from crispcode.core.tools.errors import RateLimitedError

_DEFAULT_TIMEOUT: float = 120.0
_MAX_RETRIES: int = 2
_RETRY_BASE_S: float = 2.0  # backoff base; tests can monkeypatch to 0
_RETRYABLE: frozenset[str] = frozenset({"runtime_error", "rate_limited"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _fail(
    bus: EventBus,
    runs_id: str,
    tool_call: ToolCallBlock,
    error_class: str,
    error_message: str,
    elapsed_ms: int,
) -> ToolResult:
    """发布 ToolCallFailedEvent 并返回对应 ToolResult"""
    await bus.publish(
        ToolCallFailedEvent(
            runs_id=runs_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            error_class=error_class,
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            ts=_now(),
        )
    )
    return ToolResult(content=error_message, is_error=True, error_type=error_class)


async def invoke_tool(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    bus: EventBus,
    runs_id: str,
    timeout: float = _DEFAULT_TIMEOUT,
    *,
    permission_manager: PermissionManager | None = None,
    session_id: str = "",
) -> ToolResult:
    """校验参数、限时调用工具、发布进度事件，返回 ToolResult（不抛异常）"""

    t0 = time.monotonic()

    await bus.publish(
        ToolCallStartedEvent(
            runs_id=runs_id,
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            ts=_now(),
        )
    )

    def elapsed() -> int:
        return int((time.monotonic() - t0) * 1000)

    tool = registry.get(tool_call.name)
    if tool is None:
        return await _fail(
            bus,
            runs_id,
            tool_call,
            "runtime_error",
            f"unknown tool: {tool_call.name}",
            elapsed(),
        )

    required: list[str] = cast(list[str], tool.input_schema.get("required", []))
    missing = [p for p in required if p not in tool_call.input]
    if missing:
        return await _fail(
            bus,
            runs_id,
            tool_call,
            "schema_error",
            f"missing required parameters: {', '.join(missing)}",
            elapsed(),
        )

    if tool.params_model is not None:
        try:
            tool.params_model.model_validate(dict(tool_call.input))
        except ValidationError as exc:
            return await _fail(
                bus,
                runs_id,
                tool_call,
                "schema_error",
                str(exc),
                elapsed(),
            )

    if permission_manager is not None:

        async def _emit_permission(raw: dict[str, Any]) -> None:
            await bus.publish(PermissionRequestedEvent(**raw, runs_id=runs_id))

        allowed, decision = await permission_manager.check_and_wait(
            tool_use_id=tool_call.id,
            tool_name=tool_call.name,
            params=dict(tool_call.input),
            session_id=session_id,
            event_emitter=_emit_permission,
        )

        if allowed:
            if decision not in ("auto_allow",):
                await bus.publish(
                    runs_id=runs_id,
                    tool_use_id=tool_call.id,
                    decision=decision,
                    ts=_now(),
                )

        else:
            if decision != "auto_deny":
                await bus.publish(
                    PermissionDeniedEvent(
                        runs_id=runs_id,
                        tool_use_id=tool_call.id,
                        decision=decision,
                        ts=_now(),
                    )
                )
            return await _fail(
                bus,
                runs_id,
                tool_call,
                "permission_denied",
                "Permission denied by user. You may not execute this command. "
                "Try an alternative approach or ask the user what to do.",
                elapsed(),
            )

    for attempt in range(1, _MAX_RETRIES + 2):
        error_class: str | None = None
        error_message: str | None = None

        try:
            result = await asyncio.wait_for(
                tool.invoke(dict(tool_call.input)), timeout=timeout
            )
            ms = elapsed()
            if result.is_error:

                error_class = result.error_type or "runtime_errpr"
                error_message = result.content
            else:

                await bus.publish(
                    ToolCallFinishedEvent(
                        runs_id=runs_id,
                        tool_use_id=tool_call.id,
                        tool_name=tool_call.name,
                        elapsed_ms=ms,
                        ts=_now(),
                        output=result.content,
                    )
                )
                return result
        except RateLimitedError as exc:
            error_class = "rate_limited"
            error_message = str(exc)
        except TimeoutError:
            return await _fail(
                bus,
                runs_id,
                tool_call,
                "timeout",
                f"tool timed out after {timeout}s",
                elapsed(),
            )
        except Exception as exc:
            error_class = "runtime_error"
            error_message = str(exc)

        assert error_class is not None and error_message is not None
        ms = elapsed()

        if error_class in _RETRYABLE and attempt <= _MAX_RETRIES:
            await bus.publish(
                ToolCallFailedEvent(
                    runs_id=runs_id,
                    tool_use_id=tool_call.id,
                    tool_name=tool_call.name,
                    error_class=error_class,
                    error_message=error_message,
                    elapsed_ms=ms,
                    attempt=attempt,
                    ts=_now(),
                )
            )
            await asyncio.sleep(_RETRY_BASE_S * (2 ** (attempt - 1)))
            continue

        return await _fail(
            bus,
            runs_id,
            tool_call,
            error_class,
            error_message,
            ms,
            attempt=attempt,
        )
    return ToolResult(
        content="internal error", is_error=True, error_type="runtime_error"
    )
