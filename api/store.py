"""Persistent store for dashboard runs (reviews and Q&A questions).

The Phase 4 trace files hold node timings only. The dashboard needs the actual
findings and answers, so every run is recorded here as one JSON file under
data/runs/. Flat files, not a database: single-user, local, and trivially
inspectable — the Tier-2 upgrade to Postgres is documented, not pretended.

A run moves queued -> running -> done|error. The web page polls until terminal.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Kind = Literal["review", "ask"]
Status = Literal["queued", "running", "done", "error"]

TERMINAL: set[str] = {"done", "error"}


@dataclass
class Run:
    id: str
    kind: Kind
    title: str
    status: Status = "queued"
    created_at: str = ""
    finished_at: str = ""
    request: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def duration_s(self) -> float | None:
        if not (self.created_at and self.finished_at):
            return None
        start = datetime.fromisoformat(self.created_at)
        end = datetime.fromisoformat(self.finished_at)
        return round((end - start).total_seconds(), 1)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()   # the webhook worker writes from another thread

    def create(self, kind: Kind, title: str, request: dict) -> Run:
        run = Run(id=uuid.uuid4().hex[:12], kind=kind, title=title,
                  created_at=_now(), request=request)
        self._write(run)
        return run

    def update(self, run_id: str, **fields: Any) -> Run | None:
        with self._lock:
            run = self.get(run_id)
            if run is None:
                return None
            for k, v in fields.items():
                setattr(run, k, v)
            if run.status in TERMINAL and not run.finished_at:
                run.finished_at = _now()
            self._write(run)
            return run

    def get(self, run_id: str) -> Run | None:
        path = self.root / f"{run_id}.json"
        if not path.exists():
            return None
        return Run(**json.loads(path.read_text(encoding="utf-8")))

    def list(self, limit: int = 50) -> list[Run]:
        runs = []
        for p in self.root.glob("*.json"):
            try:
                runs.append(Run(**json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, TypeError):
                continue        # a half-written file must not break the dashboard
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:limit]

    def _write(self, run: Run) -> None:
        tmp = self.root / f".{run.id}.tmp"
        tmp.write_text(json.dumps(run.to_dict(), indent=1), encoding="utf-8")
        tmp.replace(self.root / f"{run.id}.json")   # atomic: readers never see a partial file
