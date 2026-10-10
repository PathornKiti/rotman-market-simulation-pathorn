"""
Run journal: one JSON object per line next to the text log (logs/<strategy>-<time>.jsonl).

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
            self._f.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            if self._f is not None:
                self._f.close()
                self._f = None


NULL = Journal(None)


def read(path: str | Path) -> list[dict]:
    out = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue                      # a line cut off by a crash
    return out
