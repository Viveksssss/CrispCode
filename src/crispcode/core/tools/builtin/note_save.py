from __future__ import annotations

from crispcode.core.session.store import SessionStore
from crispcode.core.tools.base import BaseTool, ToolResult


class NoteSaveTool(BaseTool):
    name = "note_save"
    description = (
        "Save a concise fact or decision to this session's notes. "
        "These notes are visible in future turns of the same session."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The durable fact or decision to remember.",
            },
        },
        "required": ["content"],
    }

    def __init__(self, store: SessionStore, session_id: str, runs_id: str) -> None:
        """绑定当前 session 与 run，使工具调用能写入对应 notes.md"""
        self._store = store
        self._session_id = session_id
        self._runs_id = runs_id

    async def invoke(self, params: dict[str, object]) -> ToolResult:
        """将非空 content 追加到 session notes.md"""
        content = str(params.get("content", "")).strip()
        if not content:
            return ToolResult(
                is_error=True, content="Empty content", error_type="runtime_error"
            )
        self._store.append_note(self._session_id, content, self._runs_id)
        return ToolResult(content="saved")
