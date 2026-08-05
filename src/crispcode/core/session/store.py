from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crispcode.core.session.model import Session

logger = logging.getLogger(__name__)

MessageContent = str | list[dict[str, Any]]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    def __init__(self, root: Path) -> None:
        """初始化 session 文件存储根目录"""
        self._root = root.expanduser()
        self._root.mkdir(exist_ok=True, parents=True)

    def session_dir(self, sid: str) -> Path:
        """返回指定 session 的目录路径"""
        return self._root / sid

    def runs_dir(self, sid: str) -> Path:
        """返回指定 session 下的 runs 目录路径"""
        return self.session_dir(sid) / "runs"

    def write_meta(self, session: Session) -> None:
        """将 session meta 写入 meta.json"""
        path = self.session_dir(session.id)
        path.mkdir(parents=True, exist_ok=True)
        (path / "meta.json").write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_meta(self, sid: str) -> Session:
        """从 meta.json 读取 session meta"""
        data = json.loads(
            (self.session_dir(sid) / "meta.json").read_text(encoding="utf-8")
        )
        return Session.from_dict(data)

    def append_message(
        self, sid: str, role: str, content: MessageContent, runs_id: str | None = None
    ) -> None:
        """追加一条 Anthropic API 消息到 thread.jsonl"""
        row: dict[str, Any] = {
            "ts": _now(),
            "role": role,
            "content": content,
        }
        if runs_id is not None:
            row["runs_id"] = runs_id
        path = self.session_dir(sid)
        path.mkdir(exist_ok=True, parents=True)
        with (path / "thread.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def append_messages(
        self,
        sid: str,
        messages: list[dict[str, Any]],
        runs_id: str,
    ) -> None:
        """批量追加一次 run 新产生的消息到 thread.jsonl"""
        for msg in messages:
            self.append_message(
                sid, role=str(msg["role"]), content=msg["content"], runs_id=runs_id
            )

    def read_messages(self, sid: str) -> list[dict[str, Any]]:
        """读取完整 thread 并返回可直接传给 Anthropic 的 messages"""
        path = self.session_dir(sid) / "thread.jsonl"
        if not path.exists():
            return []
        messages: list[dict[str, Any]] = []
        for line_nu, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("skip broken thread row sid=%s,line=%s", sid, line_nu)
                continue

            role = row.get("role")
            if role not in ("user", "assistant"):
                logger.warning(
                    "skip unknown thread role sid=%s,line=%s,role=%s",
                    sid,
                    line_nu,
                    role,
                )
                continue
            messages.append({"role": role, "content": row.get("content", "")})
        return self._trim_orphan_tool_use(messages)

    def _trim_orphan_tool_use(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """裁掉尾部未配对 tool_use 以及其后的消息，避免 Anthropic messages.invalid"""
        """
        
        messages = [
            # idx=1
            {
                "role": "user",
                "content": "项目用什么 Python 版本？"
            },
            # idx=2
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "call_001", "name": "read_file", "input": {"path": "pyproject.toml"}}
                ]
            },
            # idx=3
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_001", "content": "requires-python = \">=3.12\""}
                ]
            },
            # idx=4
            {
                "role": "assistant",
                "content": "项目使用 Python 3.12"
            }
        ]
        
        扫描过程：
        
        idx=1：role=user，无 tool_use → pending=set() → last_balanced=1
        
        idx=2：role=assistant，发现 tool_use id=call_001 → pending={"call_001"} → 非空，不更新
        
        idx=3：role=user，发现 tool_result tool_use_id=call_001 → 配对成功，pending=set() → last_balanced=3
        
        idx=4：role=assistant，无 tool_use → pending=set() → last_balanced=4
        """

        pending: set[str] = set()
        last_balanced = 0
        for idx, msg in enumerate(messages, start=1):
            content = msg.get("content")
            if isinstance(content, list):
                if msg.get("role") == "assistant":
                    for block in content:
                        if block.get("type") == "tool_use":
                            pending.add(str(block.get("id", "")))
                elif msg.get("role") == "user":
                    for block in content:
                        if block.get("type") == "tool_result":
                            pending.discard(str(block.get("tool_use_id", "")))

            if not pending:
                last_balanced = idx

        if pending:
            logger.warning("trim orphan tool_use blocks from thread")
            return messages[:last_balanced]

        return messages

    def read_notes(self, sid: str) -> str:
        """读取 notes.md 全文，文件不存在时返回空字符串"""
        path = self.session_dir(sid) / "notes.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def append_note(self, sid: str, content: str, runs_id: str) -> None:
        """将一条主动笔记追加到 notes.md"""
        path = self.session_dir(sid)
        path.mkdir(exist_ok=True, parents=True)
        with (path / "notes.md").open("a", encoding="utf-8") as f:
            f.write(f"## Note ({_now()}, {runs_id})\n{content}\n")
