"""
Parameter tuning / A-B testing on the offline simulator.

    python -m ritc tune liability --grid execution.unwind_mode=block,slice --seeds 8
    python -m ritc tune equity --grid strategy.min_half_spread=0.02,0.03,0.04 \\
                               --grid strategy.size=1000,2000 --seeds 6 --workers 4

Every combination runs the SAME seeds (paired comparison: each setting sees the
same simulated market), each run in its own process with its own simulator, so
runs are independent and use every core. Results are ranked by mean final NLV,
with the worst seed and the number of seeds won shown so you can prefer robust
settings over lucky ones.

Caveats: the simulator's other traders are noise, not teams. Use this to reject
bad settings and to compare variants of the same idea, then confirm the winner
in the official RIT practice case.
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import socket
import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass


def parse_value(raw: str):
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def parse_grid(specs: list[str]) -> dict[str, list]:
    """["strategy.x=1,2", "execution.mode=a,b"] -> {"strategy.x": [1, 2], "execution.mode": ["a", "b"]}"""
    grid: dict[str, list] = {}
    for spec in specs:
        key, sep, vals = spec.partition("=")
        if not sep or not key or not vals:
            raise ValueError(f"bad --grid {spec!r}; expected section.key=v1,v2")
        grid[key.strip()] = [parse_value(v.strip()) for v in vals.split(",") if v.strip()]
    return grid


def combinations(grid: dict[str, list]) -> list[dict]:
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_one(case: str, overrides: dict, seed: int, speed: float, config_path: str | None = None) -> float:
    """Run one full simulated case in THIS process; return final NLV."""
    from .cli import build
    from .sim.server import serve

    logging.disable(logging.CRITICAL)
    port = _free_port()
    os.environ["RIT_URL"] = f"http://127.0.0.1:{port}/v1"
    srv, market = serve(case, port, speed=speed, delay=0.2, seed=seed, block=False)
    try:
        runner, _ = build(case, config_path, live=True, overrides=overrides)
        runner.interval = min(runner.interval, 0.25 / max(speed / 4, 1))
        runner.run()
        return market.nlv()
    finally:
        srv.shutdown()


def _job(args: tuple) -> tuple[int, int, float]:
    combo_idx, seed, case, overrides, speed, cfg = args
    try:
        return combo_idx, seed, run_one(case, overrides, seed, speed, cfg)
    except Exception:                 # noqa: BLE001 - a crashed run scores NaN, not the whole tune
        return combo_idx, seed, float("nan")


@dataclass
class Result:
    overrides: dict
    nlvs: dict[int, float]

    @property
    def values(self) -> list[float]:
        return [v for v in self.nlvs.values() if not math.isnan(v)]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values) if self.values else float("nan")

    @property
    def worst(self) -> float:
        return min(self.values) if self.values else float("nan")

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.values) if len(self.values) > 1 else 0.0


def tune(case: str, grid: dict[str, list], seeds: list[int], speed: float = 40.0, workers: int | None = None,
         config_path: str | None = None, progress=print) -> list[Result]:
    combos = combinations(grid)
    jobs = [(i, s, case, c, speed, config_path) for i, c in enumerate(combos) for s in seeds]
    results = [Result(c, {}) for c in combos]
    workers = workers or max(1, min(len(jobs), (os.cpu_count() or 2)))
    progress(f"tuning {case}: {len(combos)} setting(s) x {len(seeds)} seed(s) = {len(jobs)} runs "
             f"on {workers} worker(s)")
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, (i, seed, nlv) in enumerate(pool.map(_job, jobs), 1):
            results[i].nlvs[seed] = nlv
            progress(f"  [{n}/{len(jobs)}] {combos[i] or 'defaults'} seed {seed}: {nlv:,.0f}")
    return sorted(results, key=lambda r: -r.mean if not math.isnan(r.mean) else math.inf)


def wins(results: list[Result]) -> dict[int, int]:
    """For each result index, on how many seeds it had the best NLV."""
    seeds = set().union(*(r.nlvs for r in results)) if results else set()
    out = {i: 0 for i in range(len(results))}
    for s in seeds:
        scored = [(r.nlvs.get(s, float("nan")), i) for i, r in enumerate(results)]
        scored = [x for x in scored if not math.isnan(x[0])]
        if scored:
            out[max(scored)[1]] += 1
    return out


def report(results: list[Result]) -> str:
    w = wins(results)
    lines = [f"{'rank':<5}{'mean NLV':>12}{'worst':>12}{'stdev':>10}{'wins':>6}  setting"]
    for i, r in enumerate(results):
        setting = ", ".join(f"{k}={v}" for k, v in r.overrides.items()) or "(config defaults)"
        lines.append(f"{i + 1:<5}{r.mean:>12,.0f}{r.worst:>12,.0f}{r.stdev:>10,.0f}{w[i]:>6}  {setting}")
    return "\n".join(lines)
