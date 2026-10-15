#!/bin/zsh
# One override set per arm, same seeds 1601-1616 as arm0 (the frozen bot). Baseline first, from the repo root:
#   PYTHONPATH=src .venv/bin/python -m ritc stress --seeds 16 --first-seed 1601 --workers 9 --out $OUT/arm0.json
# then pair: .venv/bin/python docs/results/screening_2026-10-15/pair.py $OUT/arm0.json $OUT/arm_<name>.json
S=${OUT:-/tmp/screening}; mkdir -p $S      # where the arm JSONs go
cd /Users/pathorn.kit/rotman-market-simulation-pathorn
typeset -A ARMS
ARMS=(
  flow        "--set strategy.liquidity_model=flow"
  refill15    "--set strategy.refill_factor=1.5"
  risk015     "--set strategy.risk_aversion=0.15"
  budget20k   "--set execution.hold_risk_budget=20000"
  aclam1e6    "--set execution.ac_risk_aversion=1e-6"
  horizon1540 "--set execution.horizon_min_ticks=15 --set execution.horizon_max_ticks=40"
  sprint003   "--set strategy.trend_cross_cost=0.03"
)
for name in flow refill15 risk015 budget20k aclam1e6 horizon1540 sprint003; do
  [[ -f $S/arm_$name.json ]] && continue
  PYTHONPATH=src .venv/bin/python -m ritc stress --seeds 16 --first-seed 1601 --workers 9 ${=ARMS[$name]} \
      --out $S/arm_$name.json > $S/arm_$name.txt 2>&1
  echo "== $name done $(date +%H:%M)"
done
