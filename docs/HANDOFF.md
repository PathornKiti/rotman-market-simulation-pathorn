# Handoff — Liquidity Risk Case bot (`liability`)

*Updated 2026-10-16 night. Start here. Graded heats: **Sunday 2026-10-18**, ranked on **average** heat P&L; goal =
top 3 consistently. Expect a crowded market (rival bots unwinding the same stocks). Leaderboard is admin-only.*

> **CASE RULE (organisers):** The speculation fine will increase from $0.40 to $0.60 per share for speculation exceeding
> 5,000 shares. The fine for speculation of 5,000 shares or fewer remains unchanged at $0.20 per share.

---

## 1. Current default: ADAPTIVE MF (promoted 2026-10-16, user decision)

`config/liability.toml` = S (10-15) plus three settings. `scripts/review_batch.py check` must print **OK** before a batch.

| Setting | Value | What it does |
|---|---|---|
| `execution.front_cross` | `0.25` | cross the first 25% of every new block at once (~28% live), then rest / sprint the rest |
| `strategy.sprint_model` | `docs/results/ml_2026-10-16/models.json` | the ML price model (v2) predicts the next 10-tick move for the SPRINT rule |
| `strategy.sprint_model_crowd_guard` | `true` | while the crowd alarm is on, SPRINT uses the run + book model instead (log: `ML SPRINT OFF` / `ON again`) |
| `execution.front_skip_favourable` | `0.0` (off) | **new feature, on test** — see section 3 |

Everything from S stays: `drift_horizon_ticks = 20`, `drift_zero_start = true`, `liquidity_model = "flow_guarded"`.

**Profiles** (`config/liability_profiles.toml`):

| Profile | = | Use |
|---|---|---|
| `A_default` | the default above | baseline in every A/B |
| `K_front_skip` | default + `front_skip_favourable = 0.03` | **the A/B to run next** |
| `S_previous` | the 10-15 default (no front-load, no ML sprint) | fallback |
| `P_previous` | the bot before S (frozen b04253e) | fallback |
| `MF_ml_front25` | what ran 10-15/16 (v1 model, no crowd guard) | history |
| `F_front25`, `M_ml_sprint` | default without the ML sprint / without the front-load | split tests |

---

## 2. Next: live A/B — default vs default + front skip

**Run (Windows, one window, before the heat opens):** `scripts\ab_front_skip.bat`
(= `ritc run liability --live --forever --record --profile alternate:A_default,K_front_skip`: A, K, A, K, ...).

**Review (Mac, after copying `logs\*.log` and `logs\data\*.jsonl`):**

```bash
python scripts/review_batch.py review logs/data                       # guards per heat (fines, worst block, exposure)
python scripts/ab_compare.py logs/data --since <first heat, e.g. 20261017-000000>
PYTHONPATH=src .venv/bin/python docs/results/ml_2026-10-16/score.py   # ML v2 on heats it never saw
```

`ab_compare.py` prints per profile: NLV, the tender draw, edge kept, mid move during unwinds, losing blocks, crowd-alarm
heats, FRONT slices and **skips with the mid 20 ticks after each skip** (+ = the skip was right). Then K vs A: Welch t,
back-to-back pairs, how much of the gap is tender luck vs execution, losing blocks (Fisher).

**What to check:**
- `FRONT X skipped` lines: about 1 block in 3; the mid should move our way after them on average.
- No more losing blocks in K than in A; 0 fines.
- In any crowded heat: `ML SPRINT OFF: crowd alarm on` appears, and K skips rarely (the run + book model predicts less).

**Decision rule for Sunday:** switch K on (`front_skip_favourable = 0.03` in `config/liability.toml` + `PROMOTED` in
`review_batch.py`) if its skips move our way and it has no extra losing blocks; otherwise keep the plain default.

---

## 3. Front skip (built 2026-10-16, off by default)

When a block starts, ask the SPRINT predictor (ML v2; run + book model during the first ~21 ticks or while crowded) how
far the price will move **our way** in the next 10 ticks. If ≥ $0.03: skip the front slice and unwind normally. Decided
once per block (`skip_front()` in `src/ritc/strategies/liability.py`).

| Test | Result vs the default's fixed 25% slice |
|---|---|
| Recorded real paths + books, 23 A heats the model never saw, 105 blocks (33 skipped) | **+$2.8k/heat (t 3.0)** resting 1,250 sh/tick; **+$1.7k (t 3.6)** at 2,500; both halves positive; crowd-alarm heats +$2.9–5.0k (n 3); worst block unchanged |
| Simulator, 49 markets × seeds 1601-1616 | **+$2.9k/heat (t 9.3)**, 397 better / 205 worse; rival/hostile +$0.0k (t 0.1); worst heats better (thin books −$21.8k → −$13.6k, impact ×2 −$33.9k → −$8.4k) |
| A bigger slice when the prediction is against us | added nothing (+$0.2–0.7k, t < 1): not built |

---

## 4. Evidence behind the current default

**Live A vs MF** (26 heats, 10-15 23:03 → 10-16 20:51; 2–5 traders but crowd alarm 0/26; 0 fines; no losing heat):

| | A (= S) | MF |
|---|---|---|
| NLV per heat | $50.1k (worst $12.1k) | **$65.1k** (worst $37.3k) |
| Of the gap: tender draw / execution | | +$11.6k / +$3.4k (Welch t 1.7; back-to-back pairs +$16.3k, t 2.3) |
| Edge kept | 64% | **78%** |
| Mid move against during the unwind | −$0.128/sh | **−$0.051/sh** |
| Losing blocks | 12 / 67 | **2 / 61** (Fisher p 0.007) |

**Simulator** (vs S, same seeds): MF −$13.1k all markets / **+$3.6k rival-hostile (t 3.2)**; front-load alone −$8.0k /
+$3.0k (t 4.2); the ML part alone +$0.6k rival and −$10.7k in the worst crowded market → the crowd guard. The sim's
quiet-market loss is the front slice walking thin books (front skip recovers part of it).

**Crowding:** the 10 crowded real heats (10-14 evening, 10-15; crowd impact +0.015 to +0.038 $/10k vs ~0 in the 26 above)
all ran before MF. There, front 25% replayed on the recorded paths is ~neutral and lifts the worst heat −$1.6k → +$5k.
**MF has never run live in a crowded heat.**

**ML v2** (`docs/results/ml_2026-10-16/`: `train.py`, `report.txt`, `models.json`, `models_train_only.json`, `score.py`;
v1 kept in `ml_2026-10-15/`). Rules fixed before testing: train on 62 heats up to 10-15, test on the whole 10-16 day
(19 heats), settings by grouped CV on train only. Chosen depth 4 / 200 trees / leaf 20.

| R² of the 10-tick move, held-out 10-16 day | |
|---|---|
| linear | 0.283 |
| v1 (47 heats) | 0.412 |
| **v2** | **0.416** (better in 12/19 heats, t 0.9: passes the rule, equal in practice) |

No overfitting sign (test R² above CV 0.373). Top-10% signals right 91%. Live file = same settings refit on all 81 heats.
`train.py` needs scikit-learn 1.9.1 (install outside the repo, e.g. `pip install --target <dir>`); `score.py` does not.

---

## 5. Checked and rejected / parked (don't re-propose without new data)

| Idea | Result |
|---|---|
| Trade while flat, waiting for tenders | 10-tick edge ≤ $0.16/sh < $0.20 fine: loses at every threshold |
| Accept declined tenders with edge ≥ 0.25 | +$3.6k (t 2.2) on 55 heats, then −$0.2k on the 26 new heats: failed holdout |
| Tender-model extra accepts | held-out day +$2.2–2.6k/heat, t 1.3–1.5 (7 flips): **parked**, re-score after each batch |
| Hold back the tail when the move is our way | ~$1.1k/heat (t 2.0); the older `ride` lost in crowded heats: parked |
| Cap the front slice by book cost | parked until front skip has run live |
| Stop-losses, racing the crowd harder, cutting pessimism across the board | rejected 10-14/15 (RESEARCH.md) |
| Winning auctions | 0/103 bids won; a block won at x from the mid loses money unless x ≥ ~0.15: correct to lose |

---

## 6. Tools

| Tool | Use |
|---|---|
| `scripts/review_batch.py check` / `review <dirs>` | before a batch: config = the default? after: guards per heat |
| `scripts/ab_compare.py` | live A/B between profiles (section 2) |
| `docs/results/ml_2026-10-16/score.py` | ML v2 out-of-time on new heats |
| `python -m ritc stress --seeds 16 --first-seed 1601 --workers 9 [--set ...] --out X.json` | simulator, 49 markets |
| `docs/results/screening_2026-10-15/pair.py A.json B.json` | pair two stress runs seed by seed |
| `docs/results/exits_2026-10-15/exits.py` | exit rules on recorded paths + books |
| `docs/results/journal_replay.py`, `screening_2026-10-15/valcf.py` | replay recorded heats under any override |

Tests: `PYTHONPATH=src .venv/bin/python -m pytest -q` → **187 passed**. Git: everything since `59fb674` is
uncommitted; the user handles commits (Windows needs `git pull` before the next batch).

---

## 7. History (details: RESEARCH.md, CHANGES.md, git log)

| Date | Decision / finding |
|---|---|
| 10-16 | Adaptive MF promoted; ML v2 refit; crowd guard; front skip built (off); `ab_compare.py`. |
| 10-15 night | ML out-of-time PASS (35 heats, R² 0.405 vs 0.279, 33/35). Front-load (`front_cross`) built from the exit study (189 blocks: worst heat −$44k → −$9k; stop-losses worse at every level). |
| 10-15 late | **S promoted**: `drift_horizon_ticks = 20` + `drift_zero_start` + `flow_guarded` (paired replay 29 heats +$6.9k, t 3.6; live A/S holdout +$3.3–5.1k). ML price model trained (shadow). |
| 10-15 | Screening of "valuation too pessimistic": across-the-board cuts bring back crowded losers; targeted drift fixes pass. Tender edge caps at ~$100k/heat, so rivals' $200k heats are price luck. |
| 10-14 | Evening crowd: rivals' same-tender unwinds eat ~half the edge; racing / valuation fixes don't help. CROC edge floor and soft trend lost → off. Frozen bot b04253e. |
| 10-13 | **Root cause of losing unwinds = TREND** (10-tick momentum r 0.46, t 8.5; holder drift ruled out). Adopted: `trend_learn/trend_exec` (SPRINT), `book_signal` + OFI, `initial_sprint_gate = "crowded"`. |
| 10-12 | `room_wait`; auctions explained (bids stack ~$0.24/sh of haircuts; winning crowds out fixed tenders). Algorithm brief: https://claude.ai/artifact/CMcCv8tLH5qMZjSA7H2rFf |
| 10-11 | Real-server fix: async order booking re-sent unwinds past zero → `order_settle` / in-flight tracking. Fine past 5,000 shares $0.40 → $0.60. DMA support, `ritc suggest`. |

Key lessons that still hold: fixed private tenders are the money (~$0.3/sh edge, 50–59k shares); resting liquidity is
large (50k in 15–25 s); losses come from price runs during the unwind, not volatility; the crowd alarm is the only
reliable sign of rivals.
