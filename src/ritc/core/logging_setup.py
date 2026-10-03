"""Console + timestamped file logging. Every run writes to logs/<strategy>-<time>.log."""

from __future__ import annotations

import logging
import time

from .config import ROOT


def setup(name: str, verbose: bool = False) -> str:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s | %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    fh = logging.FileHandler(path)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(fh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return str(path)
