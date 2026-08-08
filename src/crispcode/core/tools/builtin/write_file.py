from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from crispcode.core.tools.base import BaseTool, ToolResult

_MAX_BYTES = 1 * 1024 * 1024  # 1 MB


class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="ignore")
    path: str
    content: str


class WriteFileTool(BaseTool):
    params_model = WriteFileParams
    name = "write_file"
    description = (
        "Write text content to a file, creating it (and any parent directories) if it "
        "does not exist, or overwriting it if it does. "
        "Path must be relative to the current working directory. "
        "Content size is limited to 1 MB."
    )

    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path to the file (relative to current working directory).",
            },
            "content": {
                "type": "string",
                "description": "Text content to write.",
            },
        },
        "required": ["path", "content"],
    }

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """写入文件内容；超 1MB 拒绝；禁止 .. 路径遍历；自动创建父目录"""
        p = WriteFileParams.model_validate(params)
        path_str = p.path
        content = p.content
        
        if ".." in path_str:
            raise PermissionError(f"Path traversal is not allowed: {path_str}.")

        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_BYTES:
            return ToolResult(
                content=f"Content exceeds {_MAX_BYTES} bytes limit.",
                is_error=True,
                error_type="content_too_large",
            )

        path = Path(path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        return ToolResult(
            content=f"Successfully wrote {len(encoded)} bytes to {path_str}.",
        )
