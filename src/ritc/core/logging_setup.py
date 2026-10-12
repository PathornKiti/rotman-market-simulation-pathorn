"""Console + timestamped file logging. Every run writes to logs/<strategy>-<time>.log."""

from __future__ import annotations

import logging
import re
import time

from .config import ROOT


def file_tag(profile: str | None, overrides: dict | None) -> str:
    """
    What a run changed, for its file name: `C_auctions__liquidity_model=auctions+crowd_robust=True`. Section
    prefixes are dropped, characters Windows refuses in file names are replaced, and it is cut to 100 chars.
    """
    parts = [f"{k.rsplit('.', 1)[-1]}={v}" for k, v in (overrides or {}).items() if k != "strategy.profile"]
    tag = "__".join(x for x in (profile, "+".join(parts)) if x)
    return re.sub(r"[^A-Za-z0-9_.=+-]", "-", tag)[:100]


def setup(name: str, verbose: bool = False, tag: str = "") -> str:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}{'__' + tag if tag else ''}.log"
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s | %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    for h in root.handlers:                  # `run --forever`: close the previous heat's log file
        h.close()
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
