from __future__ import annotations

from typing import Any, Protocol
import json
from enum import Enum
from crispcode.core.llm.provider import ModelProvider


class MessageFormatter(Protocol):
    """消息格式化接口"""

    def format_assistant_message(self, content: list[Any]) -> dict[str, Any]:
        """格式化助手信息"""
        ...

    def format_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> dict[str, Any]:
        """格式化工具结果"""
        ...

    def format_initial_user_message(self, goal: str) -> dict[str, Any]:
        """格式化初始用户信息"""
        ...

    def extract_tool_calls(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """从消息中提取工具调用"""
        ...


class AnthropicFormatter:
    """Anthropic格式"""

    def format_assistant_message(self, content: list[Any]) -> dict[str, Any]:
        """格式化助手信息"""
        return {"role": "assistant", "content": content}

    def format_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> dict[str, Any]:
        """格式化工具结果"""
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True
        return {"role": "user", "content": [block]}

    def format_initial_user_message(self, goal: str) -> dict[str, Any]:
        """格式化初始用户信息"""
        return {"role": "user", "content": goal}

    def extract_tool_calls(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """从消息中提取工具调用"""
        tool_calls = []
        content = message.get("content", [])
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    tool_calls.append(
                        {
                            "id": block.get("tool_use_id"),
                            "name": block.get("name"),
                            "input": block.get("input", {}),
                        }
                    )

        return tool_calls


class OpenAIFormatter:
    """OpenAI 格式"""

    def format_assistant_message(self, content: list[Any]) -> dict[str, Any]:
        """将 content 列表转换为 OpenAI 格式"""
        message = {"role": "assistant"}

        text_parts = []
        tool_calls = []

        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
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

        # OpenAI 格式：content 可以为 None，但如果有 text 就设置
        text = "".join(text_parts)
        message["content"] = text if text else None

        if tool_calls:
            message["tool_calls"] = tool_calls

        return message

    def format_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> dict[str, Any]:
        # OpenAI 使用独立的 tool 消息
        return {"role": "tool", "tool_call_id": tool_use_id, "content": content}

    def format_initial_user_message(self, goal: str) -> dict[str, Any]:
        return {"role": "user", "content": goal}

    def extract_tool_calls(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        tool_calls = []
        for tc in message.get("tool_calls", []):
            try:
                args = json.loads(tc.get("function", {}).get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                {
                    "id": tc.get("id"),
                    "name": tc.get("function", {}).get("name"),
                    "input": args,
                }
            )
        return tool_calls


def get_formatter(provider: ModelProvider) -> MessageFormatter:
    """获取对应的格式化器"""
    if provider == ModelProvider.ANTHROPIC:
        return AnthropicFormatter()
    elif provider == ModelProvider.OPENAI:
        return OpenAIFormatter()
    else:
        raise ValueError(f"Unsupported provider: {provider}")
