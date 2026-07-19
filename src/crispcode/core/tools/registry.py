from __future__ import annotations

from crispcode.core.tools.base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具,按先后顺序同名覆盖"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """按名称查找工具,不存在返回None"""
        return self._tools.get(name)

    def tool_schemas(self) -> list[dict[str, object]]:
        """返回所有工具的 Anthropic 格式的schema列表"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]
