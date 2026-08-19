from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SessionStatus = Literal["active", "idle", "closed"]
SessionMode = Literal["one_shot", "chat"]


@dataclass
class Session:
    id: str
    mode: SessionMode
    status: SessionStatus
    title: str
    created_at: str
    updated_at: str
    runs_ids: list[str] = field(default_factory=list)
    context_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mode": self.mode,
            "status": self.status,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "runs_ids": list(self.runs_ids),
            "context_pct": self.context_pct,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            id=str(data["id"]),
            mode=data["mode"],
            status=data["status"],
            title=str(data.get("title", "")),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            runs_ids=[str(x) for x in data.get("runs_ids", [])],
            context_pct=float(data.get("context_pct", 0.0)),
        )
