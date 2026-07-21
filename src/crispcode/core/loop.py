from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from crispcode.core.bus.events import StepFinishedEvent, StepStartedEvent
from crispcode.core.context import ExecutionContext
from crispcode.core.events.bus import EventBus
from crispcode.core.llm.provider import (
    LLMProvider,
    AnthropicProvider,
    OpenAIProvider,
)

from crispcode.core.llm.types import ModelProvider
from crispcode.core.tools.invocation import invoke_tool
from crispcode.core.tools.registry import ToolRegistry


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentLoop:
    def __init__(
        self, provider: LLMProvider, registry: ToolRegistry, bus: EventBus
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._bus = bus

    async def run(self, context: ExecutionContext) -> None:
        while not context.is_done():
            context.step += 1
            await self._bus.publish(
                StepStartedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )

            try:
                response = await self._provider.chat(
                    messages=context.messages,
                    tool_schemas=self._registry.tool_schemas(),
                    bus=self._bus,
                    run_id=context.run_id,
                )
            except asyncio.CancelledError:
                context.mark_failed("cancelled")
                raise
            except Exception:
                context.mark_failed("llm_error")
                break

            blocks: list[dict[str, object]] = []
            if response.text:
                blocks.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.input,
                    }
                )

            context.add_assistant_message(blocks)

            if response.stop_reason == "tool_use":
                for tc in response.tool_calls:
                    result = await invoke_tool(
                        self._registry, tc, self._bus, context.run_id
                    )
                    context.add_tool_result(
                        tc.id, result.content, is_error=result.is_error
                    )
            if response.stop_reason == "end_trun":
                context.mark_success()
            elif context.step >= context.max_steps:
                context.mark_failed("exceeded_max_steps")

            await self._bus.publish(
                StepFinishedEvent(run_id=context.run_id, step=context.step, ts=_now())
            )
