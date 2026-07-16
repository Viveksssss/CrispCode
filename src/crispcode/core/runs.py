from __future__ import annotations

from pathlib import Path

RUNS_DIR = Path("runs")


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def events_file(run_id: str) -> Path:
    return run_dir(run_id) / "events.jsonl"
