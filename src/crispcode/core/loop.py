from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from crispcode.core.bus.events import StepFinishedEvent, StepStartedEvent
from crispcode.core.compact.compactor import Compactor
from crispcode.core.context import ExecutionContext
from crispcode.core.events.bus import EventBus
from crispcode.core.llm.provider import (
    LLMProvider,
    AnthropicProvider,
    OpenAIProvider,
)
import traceback
from crispcode.core.llm.types import ModelProvider
from crispcode.core.permissions.manager import PermissionManager
from crispcode.core.tools.invocation import invoke_tool
from crispcode.core.tools.registry import ToolRegistry


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AgentLoop:
    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        bus: EventBus,
        *,
        permission_manager: PermissionManager | None = None,
        compactor: Compactor | None = None,
        compact_threshold: float = 0.80,
        session_id: str = "",
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._bus = bus
        self._permission_manager = permission_manager
        self._session_id = session_id
        self._compact_threshold = compact_threshold
        self._compactor = compactor

    async def run(self, context: ExecutionContext) -> None:
        while not context.is_done():
            context.step += 1
            await self._bus.publish(
                StepStartedEvent(runs_id=context.runs_id, step=context.step, ts=_now())
            )

            try:
                response = await self._provider.chat(
                    messages=context.messages,
                    tool_schemas=self._registry.tool_schemas(),
                    bus=self._bus,
                    runs_id=context.runs_id,
                    step=context.step,
                    system=context.system_prompt(
                        "You are a helpful AI assistant. "
                        "Use the available tools to complete the user's goal. "
                        "When the goal is fully achieved, respond with a final answer "
                        "and do not call any more tools."
                    ),
                )
            except asyncio.CancelledError:
                """向上传播取消异常，以便在外部取消时正确处理"""
                context.mark_failed("cancelled")
                raise
            except Exception as e:
                traceback.print_exc()
                context.mark_failed("llm_error")
                print(f"{e}")
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
                        self._registry,
                        tc,
                        self._bus,
                        context.runs_id,
                        permission_manager=self._permission_manager,
                        session_id=self._session_id,
                    )
                    context.add_tool_result(
                        tc.id, result.content, is_error=result.is_error
                    )

            if response.stop_reason == "end_turn":
                context.result = response.text or ""
                context.mark_success()
            elif context.step >= context.max_steps:
                context.mark_failed("exceeded_max_steps")
            else:
                print("*" * 50)
                print(response.stop_reason)
                print("*" * 50)

            if (
                not context.is_done()
                and response.stop_reason == "tool_use"
                and self._compactor is not None
                and self._compact_threshold > 0
                and response.usage is not None
                and response.usage.context_pct >= self._compact_threshold
            ):
                await self._compactor.compact(context, self._provider)

            await self._bus.publish(
                StepFinishedEvent(runs_id=context.runs_id, step=context.step, ts=_now())
            )
