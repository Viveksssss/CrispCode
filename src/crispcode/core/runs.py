from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime
import uuid

RUNS_DIR = Path.home() / ".crispcode" / "projects"


def _run_dir(runs_id: str) -> Path:
    """
    将当前项目的目录转换成特定格式字符串
    例如 /home/user/pro -> home-user-pro(path)
    然后将持久化文件存放入指定地点: ~/.crispcode/projects/$(path)/runs_id/events.jsonl
    """

    parts = Path.cwd().parts[1:]
    path = "-".join(parts)
    return RUNS_DIR / path / runs_id


def events_file(runs_id: str) -> Path:
    return _run_dir(runs_id) / "events.jsonl"


def new_runs_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%s")
    suffix = uuid.uuid4().hex[:6]
    return f"{ts}-{suffix}"


def ensure_run_dir(runs_dir: str | None = None, *, runs_id: str) -> Path:
    if runs_dir is not None:
        parts = Path.cwd().parts[1:]
        path = Path("-".join(parts))
        path.mkdir(parents=True, exist_ok=True)
        return runs_dir / path / runs_id
    path = _run_dir(runs_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
