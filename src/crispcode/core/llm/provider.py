from __future__ import annotations

import json
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
            base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
            self._client: Any = Anthropic.AsyncAnthropic(
                api_key=api_key, base_url=base_url
            )

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


class OpenAIProvider:
    """OpenAI Provider"""

    def __init__(self, model: str, client: Any = None) -> None:
        if client is None:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise SystemExit("OPENAI_API_KEY not set")
            base_url = os.environ.get("OPENAI_BASE_URL")
            self._client: Any = openai.AsyncOpenAI(api_key=api_key, base_url=base_url)
        else:
            self._client: Any = client

        self._model = model
        self.provider = ModelProvider.OPENAI

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

        formatted_messages = self._convert_messages(messages)
        tools = self._convert_tools(tool_schemas)

        kwargs: dict[str, object] = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": formatted_messages,
        }

        if tools:
            kwargs["tools"] = tools

        text_parts: list[str] = []
        tool_calls_data = []

        async with self._client.chat.completions.stream(**kwargs) as stream:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    await bus.publish(
                        LlmTokenEvent(run_id=run_id, token=text, ts=_now())
                    )
                    text_parts.append(text)
            final_message = await stream.get_final_message()

        tool_calls: list[ToolCallBlock] = []
        for tc in final_message.tool_calls or []:
            tool_calls.append(
                ToolCallBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=json.loads(tc.function.arguments),
                )
            )
        # 使用量（OpenAI 不同）
        usage = final_message.usage
        # OpenAI 没有 cache_read 概念，设为 0
        cache_read = 0
        cache_create = 0

        await bus.publish(
            LlmUsageEvent(
                run_id=run_id,
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
                ts=_now(),
            )
        )

        return LlmResponse(
            stop_reason=final_message.choices[0].finish_reason or "end_turn",
            tool_calls=tool_calls,
            text="".join(text_parts),
            usage=UsageState(
                input_token=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                cache_read_input_tokens=cache_read,
                cache_creation_input_tokens=cache_create,
            ),
        )

    def _convert_messages(
        self, messages: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """
        将Anthropic 格式的消息转换为 OpenAI 格式
            Anthropic                                             OpenAI
        1.system
            {"role": "system", "content": "你是一个助手"}	        完全相同
        2.user
            {"role": "user", "content": "你好"}	                                                                                    {"role": "user", "content": "你好"}
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "北京天气"}]}	                     {"role": "tool", "tool_call_id": "1", "content": "北京天气"}
            {"role": "user", "content": [{"type": "text", "text": "请继续"}]}	                                                     {"role": "user", "content": "请继续"}
            关键变化：
            工具结果角色变更：Anthropic 把工具结果放在 user 消息里，OpenAI 有专门的 tool 角色
            字段名映射：tool_use_id → tool_call_id（命名风格统一）
        3.assistant
            anthropoc:
            {
                "role": "assistant",
                "content": [
                {"type": "text", "text": "我来帮你查天气"},
                {"type": "tool_use", "id": "1", "name": "get_weather", "input": {"city": "北京"}}
                ]
            }
           openai:
           {
             "role": "assistant",
             "content": "我来帮你查天气",
             "tool_calls": [
               {
                 "id": "1",
                 "type": "function",
                 "function": {
                   "name": "get_weather",
                   "arguments": "{\"city\": \"北京\"}"
                 }
               }
             ]
           }

        """
        converted = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                converted.append({"role": "system", "content": content})

            elif role == "user":
                if isinstance(content, list):
                    # content 是列表，可能包含 tool_result 或 text
                    for block in content:
                        if block.get("type") == "tool_result":
                            converted.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": block.get("tool_use_id"),
                                    "content": block.get("content"),
                                }
                            )
                        else:
                            # 普通文本块（如 {"type": "text", "text": "..."}）
                            converted.append(
                                {"role": "user", "content": block.get("text", "")}
                            )
                else:
                    # 简单字符串
                    converted.append({"role": "user", "content": content})

            elif role == "assistant":
                if isinstance(content, list):
                    # 构建 OpenAI 格式
                    openai_msg = {"role": "assistant", "content": ""}
                    tool_calls = []

                    for block in content:
                        if block.get("type") == "text":
                            openai_msg["content"] = block.get("text", "")
                        elif block.get("type") == "tool_use":
                            tool_calls.append(
                                {
                                    "id": block.get("id"),
                                    "type": "function",
                                    "function": {
                                        "name": block.get("name"),
                                        "arguments": json.dumps(block.get("input", {})),
                                    },
                                }
                            )
                        # 忽略其他未知类型（不处理）

                    if tool_calls:
                        openai_msg["tool_calls"] = tool_calls

                    converted.append(openai_msg)
                else:
                    # content 是字符串
                    converted.append({"role": "assistant", "content": content})

        return converted  # ✅ 返回所有转换后的消息

    def _convert_tools(
        self, tool_schemas: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """将 Anthropic 工具格式转化成OpenAI格式"""
        openai_tools = []
        for tool in tool_schemas:
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {}),
                    },
                }
            )
        return openai_tools


def create_provider(provider: ModelProvider, model: str, client: Any = None):
    """创建对应的 Provider"""
    if provider == ModelProvider.ANTHROPIC:
        return AnthropicProvider(model, client)
    elif provider == ModelProvider.OPENAI:
        return OpenAIProvider(model, client)
    else:
        raise ValueError(f"Unsupported provider: {provider}")
