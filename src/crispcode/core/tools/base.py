from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from pydantic import BaseModel


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    error_type: str | None = None  # "runtime_error" | "timeout" | "schema_error"


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict[str, object]
    params_model: ClassVar[type[BaseModel] | None] = None

    @abstractmethod
    # 执行工具调用,返回结果/错误
    async def invoke(self, params: dict[str, object]) -> ToolResult: ...
