from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from collections.abc import ABC, abstractmethod
from enum import Enum
from crispcode.core.llm.formatters import get_formatter, MessageFormatter, ModelProvider


@dataclass
class ExecutionContext:
    run_id: str
    goal: str
    max_steps: int
    provider: ModelProvider = ModelProvider.ANTHROPIC  # 新增
    messages: list[dict[str, Any]] = field(default_factory=list)
    step: int = 0
    statue: str = "running"  # "running" | "success" | "failed"
    reason: str | None = None

    def __post_init__(self) -> None:
        """初始化将goal加入到消息历史"""
        self._formatter = get_formatter(self.provider)
        if not self.messages:
            initial_msg = self._formatter.format_initial_user_message(self.goal)
            self.messages.append(initial_msg)

    def add_assistant_message(self, content: dict[Any]) -> None:
        """添加助手消息（自动适配格式）"""
        formatted = self._formatter.format_assistant_message(content)
        self.messages.append(formatted)

    def add_tool_result(
        self, tool_use_id: str, content: str, is_error: bool = False
    ) -> None:
        """添加工具结果（自动适配格式）"""
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }

        formatted = self._formatter.format_tool_result(tool_use_id, content, is_error)

        if is_error:
            block["is_error"] = True

        # Anthropic 特殊处理：合并 tool_result 到最后的 user 消息
        if self.provider == ModelProvider.ANTHROPIC:
            last = self.messages[-1] if self.messages else None
            if (
                last is not None
                and last["role"] == "user"
                and isinstance(last.get("content"), list)
                and last["content"]
                and all(b.get("type") == "tool_result" for b in last["content"])
            ):
                last["content"].append(formatted["content"][0])
                return

        self.messages.append(formatted)

    def is_done(self) -> None:
        """返回True表示loop应该停止,状态不再是running"""
        return self.status != "running"

    def mark_success(self) -> None:
        self.status = "success"

    def mark_failed(self, reason: str) -> None:
        self.status = "failed"
        self.reason = reason

    def extract_tool_calls(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """从消息中提取工具调用"""
        return self._formatter.extract_tool_calls(message)
