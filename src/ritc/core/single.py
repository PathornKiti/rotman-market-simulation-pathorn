"""
One live bot per account on this machine. Two `ritc run --live` windows trade the SAME positions: both accept
tenders and both unwind them, each sizing orders from positions the other is moving, so the position goes
through zero (speculation fines) and every block is crossed twice. The OS file lock is released automatically
when the process exits or crashes, so a stale lock never blocks a restart.
"""

from __future__ import annotations

import os
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


def acquire(path: str | Path):
    """Hold an exclusive lock on `path` for the life of the process; raises AlreadyRunning if another holds it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.close()
        raise AlreadyRunning(str(path)) from exc
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh          # keep a reference: closing it releases the lock
