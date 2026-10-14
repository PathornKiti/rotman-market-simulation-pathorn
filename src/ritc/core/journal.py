"""
Run journal: one JSON object per line, logs/data/<strategy>-<time>.jsonl next to the readable logs/<...>.log
(older runs: logs/<strategy>-<time>.jsonl).

The text log is for reading during a heat; the journal is for the post-trade report (`ritc report`), which
needs the P&L path, positions, quotes, every order and fill, every tender and how it was answered, and every
error or slow loop of a LIVE run, where no simulator can rebuild the market afterwards. Each line is
{"k": kind, "w": wall-clock seconds, ...}; writes are flushed at once so a crash or Ctrl-C loses nothing.

Kinds: start, tick, order, fill, tender_seen, tender_decision, tender_answer, error, slow_loop, end.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class Journal:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self._f = self.path.open("a", buffering=1) if self.path else None
        self._lock = threading.Lock()
        self.tick: int | None = None

    def write(self, kind: str, **fields) -> None:
        if self._f is None:
            return
        rec = {"k": kind, "w": round(time.time(), 3)}
        if self.tick is not None and "tick" not in fields:
            rec["tick"] = self.tick
        rec.update(fields)
        line = json.dumps(rec, default=str, separators=(",", ":"))
        with self._lock:
            if self._f is not None:          # another thread (the --record recorder) may have closed it
                self._f.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            if self._f is not None:
                self._f.close()
                self._f = None


NULL = Journal(None)


def journal_path(log_path: str | Path) -> Path:
    """Where `ritc run` keeps the journal of a log: logs/data/<log name>.jsonl (the .log stays alone in logs/)."""
    p = Path(log_path)
    return p.parent / "data" / ((p.name[:-4] if p.name.endswith(".log") else p.name) + ".jsonl")


def find_journal(log_path: str | Path) -> Path | None:
    """The journal of a log: logs/data/<name>.jsonl, or <name>.jsonl next to it (runs before 2026-10-14)."""
    p = Path(log_path)
    for c in (journal_path(p), p.parent / ((p.name[:-4] if p.name.endswith(".log") else p.name) + ".jsonl")):
        if c.exists():
            return c
    return None


def journal_files(logs: str | Path = "logs", case: str = "liability") -> list[str]:
    """Every run journal of a case, old (logs/) and new (logs/data/), oldest first."""
    d = Path(logs)
    return sorted({str(p) for p in list(d.glob(f"{case}-*.jsonl")) + list((d / "data").glob(f"{case}-*.jsonl"))},
                  key=lambda s: Path(s).name)


def read(path: str | Path) -> list[dict]:
    out = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue                      # a line cut off by a crash
    return out
