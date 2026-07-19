from __future__ import annotations

import os
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol, Any

from anthropic import Anthropic, AsyncStream
from anthropic.types import Message, Usage
import openai  # 添加 openai 依赖

from crispcode.core.events.bus import EventBus
from crispcode.core.llm.types import LlmResponse, ToolCallBlock, UsageState
from crispcode.core.bus.events import (
    LlmModelSelectedEvent,
    LlmTokenEvent,
    LlmUsageEvent,
)
from crispcode.core.events.bus import EventBus

from .types import ModelProvider
from .formatters import get_formatter


class ModelProvider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    UNKNOWN = "unknown"


_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. "
    "Use the available tools to complete the user's goal. "
    "When the goal is fully achieved, respond with a final answer and do not call any more tools."
)


class LLMProvider(Protocol):
    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
    ) -> LlmResponse: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AnthropicProvider:
    """Anthropic Claude Provider"""

    def __init__(self, model: str, client: Any = None) -> None:
        if client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise SystemExit("ANTHROPIC_API_KEY is not setted")
            self._client: Any = Anthropic.AsyncAnthropic(api_key=api_key)

        else:
            self._client = client
        self._model = model
        self.provider = ModelProvider.ANTHROPIC

    async def chat(
        self,
        messages: list[dict[str, object]],
        tool_schemas: list[dict[str, object]],
        bus: EventBus,
        run_id: str,
    ) -> LlmResponse:
        await bus.publish(
            LlmModelSelectedEvent(
                run_id=run_id, model=self._model, strategy="static", ts=_now()
            )
        )

        system: list[dict[str, object]] = [
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        tools: list[dict[str, object]] = list(tool_schemas)

        if tools:
            last = dict(tools[-1])
            last["cache_control"] = {"type": "ephemeral"}
            tools = tools[:-1] + [last]

        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system,
            "messages": messages,
        }

        if tools:
            kwargs["tools"] = tools

        text_parts: list[str] = []

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                await bus.publish(LlmTokenEvent(run_id=run_id, token=text, ts=_now()))
                text_parts.append(text)
            final_message: Message = await stream.get_final_message()

        usage: Usage = final_message.usage
        cache_read: int = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create: int = getattr(usage, "cache_creation_input_tokens", 0) or 0

        await bus.publish(
            LlmUsageEvent(
                run_id=run_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                ts=_now(),
            )
        )

        tool_calls = list[ToolCallBlock] = []
        for block in final_message.content:
            if block.type == "tool_use":
                tool_calls.append(
                    ToolCallBlock(id=block.id, name=block.name, input=dict(block.input))
                )

        return LlmResponse(
            stop_reason=final_message.stop_reason or "end_turn",
            tool_calls=tool_calls,
            text="".join(text_parts),
            usage=UsageState(
                input_tokens=usage.input_tokens,
                output_token=usage.output_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
            ),
        )
