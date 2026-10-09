"""
Parameter tuning / A-B testing on the offline simulator.

    python -m ritc tune liability --grid execution.unwind_mode=block,slice --seeds 8
    python -m ritc tune equity --grid strategy.min_half_spread=0.02,0.03,0.04 \\
                               --grid strategy.size=1000,2000 --seeds 6 --workers 4

By default the simulator runs in LOCK-STEP (`--speed 0`): the market advances one
tick every `LOOPS_PER_TICK` bot loops instead of on a wall clock. A run then
depends only on the seed and the settings, so two settings compared on seed 3
see exactly the same market. On a wall clock, thread timing changed which quotes
were resting when prices jumped, and a market maker's results swung by thousands
between identical runs. `--speed N` (ticks/second) restores real-time mode.

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


LOOPS_PER_TICK = 4          # ~ a 0.25 s loop against RIT's 1 tick per second


def run_one(case: str, overrides: dict, seed: int, speed: float, config_path: str | None = None,
            hostile: float = 0.0, queue: bool = False) -> float:
    """Run one full simulated case in THIS process; return the final score (NLV less official
    penalties, e.g. the delta-limit fine). speed <= 0 = lock-step."""
    from .cli import build
    from .sim.server import InProcessAdapter, serve

    logging.disable(logging.CRITICAL)
    port = _free_port()
    os.environ["RIT_URL"] = f"http://127.0.0.1:{port}/v1"
    srv, market = serve(case, port, speed=speed, delay=0.2, seed=seed, block=False, hostile=hostile,
                       queue=queue)
    try:
        runner, _ = build(case, config_path, live=True, overrides=overrides)
        if speed <= 0:
            runner.client.adapter = InProcessAdapter(market)   # no loopback HTTP: same answers, much faster
            runner.interval = 0.0
            runner.on_loop = lambda: market.advance() if runner.loops % LOOPS_PER_TICK == 0 else None
        else:
            runner.interval = min(runner.interval, 0.25 / max(speed / 4, 1))
        runner.run()
        return market.score()
    finally:
        srv.shutdown()


def _job(args: tuple) -> tuple[int, int, float]:
    combo_idx, seed, case, overrides, speed, cfg, hostile, queue = args
    try:
        return combo_idx, seed, run_one(case, overrides, seed, speed, cfg, hostile, queue)
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
    def cvar(self) -> float:
        """Expected shortfall: mean NLV of the worst 25% of seeds (at least one)."""
        v = sorted(self.values)
        k = max(1, len(v) // 4)
        return statistics.fmean(v[:k]) if v else float("nan")

    @property
    def negatives(self) -> int:
        return sum(x < 0 for x in self.values)

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.values) if len(self.values) > 1 else 0.0


def tune(case: str, grid: dict[str, list], seeds: list[int], speed: float = 0.0, workers: int | None = None,
         config_path: str | None = None, progress=print, hostile: float = 0.0,
         queue: bool = False) -> list[Result]:
    combos = combinations(grid)
    if {} not in combos:
        combos.insert(0, {})           # always run the config baseline: every setting is judged against it
    jobs = [(i, s, case, c, speed, config_path, hostile, queue) for i, c in enumerate(combos) for s in seeds]
    results = [Result(c, {}) for c in combos]
    workers = workers or max(1, min(len(jobs), (os.cpu_count() or 2)))
    progress(f"tuning {case}: {len(combos)} setting(s) x {len(seeds)} seed(s) = {len(jobs)} runs "
             f"on {workers} worker(s){f', hostile market {hostile:g}' if hostile else ''}"
             f"{', queue model' if queue else ''}")
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, (i, seed, nlv) in enumerate(pool.map(_job, jobs), 1):
            results[i].nlvs[seed] = nlv
            progress(f"  [{n}/{len(jobs)}] {combos[i] or 'defaults'} seed {seed}: {nlv:,.0f}")
    return sorted(results, key=lambda r: -r.mean if not math.isnan(r.mean) else math.inf)


def wins(results: list[Result]) -> dict[int, int]:
    """For each result index, on how many seeds it had the strictly best NLV."""
    seeds = set().union(*(r.nlvs for r in results)) if results else set()
    out = {i: 0 for i in range(len(results))}
    for s in seeds:
        scored = [(r.nlvs.get(s, float("nan")), i) for i, r in enumerate(results)]
        scored = [x for x in scored if not math.isnan(x[0])]
        best = max(scored)[0] if scored else None
        top = [i for v, i in scored if v == best]
        if len(top) == 1:              # a tie (e.g. a setting that changes nothing) is nobody's win
            out[top[0]] += 1
    return out


def paired(r: Result, base: Result) -> tuple[float, float, float, int, int]:
    """Paired stats vs the baseline over shared seeds: (mean diff, SE, t, seeds beaten, n)."""
    d = [r.nlvs[s] - base.nlvs[s] for s in r.nlvs
         if s in base.nlvs and not math.isnan(r.nlvs[s]) and not math.isnan(base.nlvs[s])]
    if len(d) < 2:
        return float("nan"), float("nan"), float("nan"), 0, len(d)
    mean, se = statistics.fmean(d), statistics.stdev(d) / math.sqrt(len(d))
    t = mean / se if se > 0 else (0.0 if mean == 0 else math.copysign(math.inf, mean))
    return mean, se, t, sum(x > 0 for x in d), len(d)


def t_pvalue(t: float, df: int) -> float:
    """Two-sided p-value of Student's t with integer df (exact series, A&S 26.7.3-4; no scipy)."""
    if math.isnan(t) or df < 1:
        return float("nan")
    if math.isinf(t):
        return 0.0
    th = math.atan(abs(t) / math.sqrt(df))
    c2, term, acc = math.cos(th) ** 2, 1.0, 1.0
    if df % 2:                                   # odd df
        for k in range(3, df - 1, 2):            # 1 + 2/3 c^2 + (2*4)/(3*5) c^4 + ...
            term *= c2 * (k - 1) / k
            acc += term
        inside = (2 / math.pi) * (th + (math.sin(th) * math.cos(th) * acc if df > 1 else 0.0))
    else:                                        # even df
        for k in range(2, df - 1, 2):            # 1 + 1/2 c^2 + (1*3)/(2*4) c^4 + ...
            term *= c2 * (k - 1) / k
            acc += term
        inside = math.sin(th) * acc
    return min(1.0, max(0.0, 1.0 - inside))


def t_critical(p: float, df: int) -> float:
    """|t| at which the two-sided p-value equals p (bisection on t_pvalue)."""
    lo, hi = 0.0, 1e3
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if t_pvalue(mid, df) > p else (lo, mid)
    return hi


def report(results: list[Result], alpha: float = 0.05) -> str:
    """
    Ranked table. `vs base` is the PAIRED difference to the config baseline (same seeds,
    same market), with its standard error and t. Treat |t| < 2 as "no evidence of a
    difference". `CVaR25` is the mean of the worst quarter of seeds and `neg` the number
    of losing seeds: the tail a risk change should improve.

    Multiple testing: with m settings compared against the baseline, some reach t >= 2 by
    luck alone (the winner's curse; Lopez de Prado's "deflated" statistics). `p adj` is the
    two-sided paired p-value times m (Bonferroni), and the footer prints the |t| a setting
    needs for family-wise error `alpha` given how many settings were tried. Even a row that
    passes must still be re-run alone on fresh seeds (--first-seed 101) before adopting it.
    """
    w = wins(results)
    base = next((r for r in results if not r.overrides), None)
    stats = {id(r): paired(r, base) for r in results if base is not None and r is not base}
    m = sum(s[4] >= 2 for s in stats.values())   # comparisons actually made (crashed rows excluded)
    lines = [f"{'rank':<5}{'mean NLV':>12}{'worst':>12}{'CVaR25':>10}{'neg':>5}{'stdev':>10}{'wins':>6}"
             f"{'vs base':>10}{'SE':>8}{'t':>7}{'p adj':>7}{'beat':>7}  setting"]
    for i, r in enumerate(results):
        setting = ", ".join(f"{k}={v}" for k, v in r.overrides.items()) or "(config defaults)"
        if id(r) in stats:
            d, se, t, beat, n = stats[id(r)]
            padj = min(1.0, t_pvalue(t, n - 1) * m)
            cmp = f"{d:>+10,.0f}{se:>8,.0f}{t:>7.2f}{padj:>7.3f}{f'{beat}/{n}':>7}"
        else:
            cmp = f"{'':>10}{'':>8}{'':>7}{'':>7}{'':>7}"
        lines.append(f"{i + 1:<5}{r.mean:>12,.0f}{r.worst:>12,.0f}{r.cvar:>10,.0f}{r.negatives:>5}"
                     f"{r.stdev:>10,.0f}{w[i]:>6}{cmp}  {setting}")
    if m:
        df = max(s[4] for s in stats.values()) - 1
        lines.append(f"\n{m} setting(s) compared with the baseline on {df + 1} seeds: |t| >= "
                     f"{t_critical(alpha / m, df):.2f} needed for family-wise {alpha:.0%} "
                     f"(single test: {t_critical(alpha, df):.2f}). Then confirm on --first-seed 101.")
    return "\n".join(lines)
