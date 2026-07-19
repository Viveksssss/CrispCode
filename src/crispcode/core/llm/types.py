from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class UsageState:
    input_token: int
    output_token: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class ToolCallBlock:
    id: str
    name: str
    input: dict[str, object]


@dataclass
class LlmResponse:
    stop_reason: str
    tool_calls: list[ToolCallBlock] = field(default=list)
    text: str = ""
    usage: UsageState | None = None
