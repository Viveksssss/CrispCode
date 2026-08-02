from __future__ import annotations
import dataclasses
import time
from datetime import UTC, datetime
from typing import Any, Literal

from crispcode.core.events.bus import EventBus
from crispcode.core.llm.provider import LLMProvider
from crispcode.core.llm.types import LlmResponse
from crispcode.core.trace.record import TraceRecord
from crispcode.core.trace.writer import TraceWriter


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TracingProvider:
    """包裹真实 LLMProvider，在每次 chat() 调用前后向 TraceWriter 写入完整 API I/O 记录"""

    def __init__(
        self, inner: LLMProvider, trace: TraceWriter, *, include_payload: bool = True
    ) -> None:
        self._inner = inner
        self._trace = trace
        self._include_payload = include_payload

    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        runs_id: str,
        *,
        step: int = 0,
    ) -> LlmResponse:
        call_data: dict[str, Any]
        if self._include_payload:
            call_data = {
                "message": messages,
                "tool_schemas": tool_schemas,
            }
        else:
            call_data = {
                "message_count": len(messages),
                "tool_count": len(tool_schemas),
            }
        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="CORE->LLM",
                layer="llm",
                kind="api_call",
                runs_id=runs_id,
                step=step,
                data=call_data,
            )
        )

        t0 = time.monotonic()
        result: LlmResponse = await self._inner.chat(
            messages, tool_schemas, bus, runs_id, step=step
        )
        latency_ms = int((time.monotonic() - t0) * 1000)

        resp_data: dict[str, Any]
        if self._include_payload:
            resp_data = {
                "stop_reason": result.stop_reason,
                "text": result.text,
                "tool_calls": [dataclasses.asdict(tc) for tc in result.tool_calls],
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }
        else:
            resp_data = {
                "stop_reason": result.stop_reason,
                "usage": dataclasses.asdict(result.usage) if result.usage else {},
                "latency_ms": latency_ms,
            }

        self._trace.emit(
            TraceRecord(
                ts=_now(),
                direction="LLM->CORE",
                layer="llm",
                kind="api_response",
                runs_id=runs_id,
                step=step,
                data=resp_data,
            )
        )

        return result
