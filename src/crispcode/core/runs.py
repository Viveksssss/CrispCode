from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime
import uuid

RUNS_DIR = Path("runs")


def _run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def events_file(run_id: str) -> Path:
    return _run_dir(run_id) / "events.jsonl"


def new_run_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%s")
    suffix = uuid.uuid4().hex[:6]
    return f"{ts}-suffix"


def ensure_run_dir(run_id: str) -> Path:
    path = _run_dir(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
