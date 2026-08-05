from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime
import uuid

RUNS_DIR = Path.home() / ".crispcode" / "sessions"


def _run_dir(runs_id: str) -> Path:
    return RUNS_DIR / runs_id


def run_dir_old(runs_id: str) -> Path:
    path = Path.cwd().parts[1:]
    path_str = "-".join(path)
    return Path.home() / ".crispcode" / "projects" / path_str / runs_id
    
if __name__ == "__main__":
    print(run_dir_old("20260731-10441785465884-082c54/") / "events.jsonl")
    
def events_file(runs_id: str) -> Path:
    return _run_dir(runs_id) / "events.jsonl"


def new_runs_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%s")
    suffix = uuid.uuid4().hex[:6]
    return f"{ts}-{suffix}"


def ensure_run_dir(runs_id: str) -> Path:
    path = _run_dir(runs_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
