"""v3 (refit 2026-10-19 morning, see report.txt; features from ../ml_2026-10-15). OUT-OF-TIME test of models.json on journals recorded AFTER training (any heat not in models.json "train_heats").
No sklearn needed; the bot does not use these models (shadow test only).

    PYTHONPATH=src .venv/bin/python docs/results/ml_2026-10-16/score.py [journals ...]     # default: logs/data/*.jsonl

Pass rules, fixed before any new heat was seen (2026-10-15):
  PRICE  (trees vs the linear model, which is about what the bot's sprint uses): after >= 10 new heats,
         trees R2 >= 0.25 on the new heats, trees beat linear heat by heat (paired t >= 2 on squared error), and the
         unwind proxy is higher for trees. Then the tree is worth a shadow signal in the bot, then a live A/B.
  TENDER (linear model's accept rule vs the bot's live answers): after >= 10 new heats, better in total AND not worse
         in the heats where the bot's crowd alarm was on. Otherwise the model only learned "accept more" on quiet servers.
"""
import glob
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "ml_2026-10-15"))
from features import journal, predict  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
COST = 0.05           # sprint: cross when the predicted move against the block exceeds this ($/share)
HAIRCUTS = (0.03, 0.054)
MIN_HEATS = 10


def r2(y, p):
    my = st.mean(y)
    return 1 - sum((a - b) ** 2 for a, b in zip(y, p)) / sum((a - my) ** 2 for a in y)


def tstat(xs):
    return st.mean(xs) / (st.stdev(xs) / len(xs) ** 0.5) if len(xs) > 1 and st.stdev(xs) else 0.0


def unwind_proxy(rows, preds):
    """$ from crossing when the predicted 10-tick move against the held block > COST (one signal per 10 ticks)."""
    tot, n, last = 0.0, 0, {}
    for r, p in zip(rows, preds):
        if not r["pos"]:
            continue
        sg = 1 if r["pos"] > 0 else -1
        if -sg * p <= COST or r["tick"] - last.get(r["tk"], -99) < 10:
            continue
        last[r["tk"]] = r["tick"]
        tot += (-sg * r["y"] - COST) * abs(r["pos"])
        n += 1
    return tot, n


if __name__ == "__main__":
    M = json.load(open(os.path.join(HERE, "models.json")))
    train = set(M["train_heats"])
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(ROOT, "logs", "data", "*.jsonl")))
    files = [f for f in files if os.path.basename(f)[10:25] not in train]
    J = [j for j in map(journal, files) if j and j["price"] and j["summary"]]      # complete heats only
    print(f"models.json: trained on {len(train)} heats up to {max(train)}; training CV R2 "
          + ", ".join(f"{k} {v:+.3f}" for k, v in M["cv"].items()))
    print(f"OUT-OF-TIME heats: {len(J)}" + ("" if J else " - record more heats (--record) and run again"))
    if not J:
        sys.exit(0)

    # ---------------- PRICE
    pl, pt = M["models"]["price_linear"], M["models"]["price_trees"]
    print(f"\nPRICE (next {M['horizon_ticks']} ticks)   {'heat':16} {'rows':>5} {'R2 linear':>9} {'R2 trees':>9} "
          f"{'proxy lin $':>11} {'proxy trees $':>13}")
    Y, PL, PT, dmse, prox_l, prox_t = [], [], [], [], [], []
    for j in J:
        rows = j["price"]
        y = [r["y"] for r in rows]
        a = [predict(pl, r) for r in rows]
        b = [predict(pt, r) for r in rows]
        Y += y; PL += a; PT += b
        dmse.append(st.mean((u - v) ** 2 for u, v in zip(y, a)) - st.mean((u - v) ** 2 for u, v in zip(y, b)))
        prox_l.append(unwind_proxy(rows, a)[0]); prox_t.append(unwind_proxy(rows, b)[0])
        print(f"{'':28}{j['heat']:16} {len(rows):5} {r2(y, a):+9.3f} {r2(y, b):+9.3f} {prox_l[-1]:+11,.0f} {prox_t[-1]:+13,.0f}")
    top = sorted(range(len(PT)), key=lambda i: -abs(PT[i]))[: max(1, len(PT) // 10)]
    hit = st.mean(1.0 if (PT[i] > 0) == (Y[i] > 0) else 0.0 for i in top)
    r_l, r_t, t_d = r2(Y, PL), r2(Y, PT), tstat(dmse)
    print(f"  pooled: R2 linear {r_l:+.3f}, trees {r_t:+.3f} (training CV {M['cv']['price_linear']:+.3f} / "
          f"{M['cv']['price_trees']:+.3f}); trees better in {sum(d > 0 for d in dmse)}/{len(dmse)} heats, paired t {t_d:+.2f};"
          f" top-10% tree signals right {hit:.0%}")
    print(f"  unwind proxy $/heat: linear {st.mean(prox_l):+,.0f}, trees {st.mean(prox_t):+,.0f} "
          f"(training: ~+13.2k / +15.2k; crude, ignores how crossing changes later fills)")
    if len(J) < MIN_HEATS:
        print(f"  PRICE verdict: wait ({len(J)} of {MIN_HEATS} heats)")
    else:
        ok = r_t >= 0.25 and t_d >= 2 and st.mean(prox_t) > st.mean(prox_l)
        print(f"  PRICE verdict: {'PASS - worth a shadow signal in the bot' if ok else 'FAIL - keep the linear sprint model'}")

    # ---------------- TENDER
    tm = M["models"]["tender_linear"]
    T = [(j, r, predict(tm, r)) for j in J for r in j["tender"]]
    if T:
        crowded = {j["heat"]: bool(((j["summary"] or {}).get("crowd") or {}).get("crowded")) for j in J}
        print(f"\nTENDER  {len(T)} fixed tenders, R2 {r2([r['y'] for _, r, _ in T], [p for _, _, p in T]):+.3f} "
              f"(training CV {M['cv']['tender_linear']:+.3f}); crowded heats {sum(crowded.values())}/{len(J)}")
        res = {}
        for hc in HAIRCUTS:
            for lab, sel in (("all", lambda h: True), ("crowd alarm ON", lambda h: crowded[h]),
                             ("crowd alarm off", lambda h: not crowded[h])):
                hs = {j["heat"] for j in J if sel(j["heat"])}
                if not hs:
                    continue
                val = lambda acc: sum((r["y"] - hc) * r["qty"] for j, r, p in T if j["heat"] in hs and acc(r, p)) / len(hs)
                res[(hc, lab)] = (val(lambda r, p: r["live_accept"]), val(lambda r, p: p > hc))
                print(f"  haircut {hc:.3f} {lab:16} ({len(hs):2} heats) $/heat: bot live {val(lambda r, p: r['live_accept']):+9,.0f}"
                      f" | model {val(lambda r, p: p > hc):+9,.0f} | hindsight {val(lambda r, p: r['y'] > hc):+9,.0f}")
        if len(J) < MIN_HEATS:
            print(f"  TENDER verdict: wait ({len(J)} of {MIN_HEATS} heats)")
        else:
            hc = HAIRCUTS[-1]                                  # the strict haircut decides
            bot, mod = res[(hc, "all")]
            cb, cm = res.get((hc, "crowd alarm ON"), (None, None))
            if cb is None:
                print("  TENDER verdict: wait (no heat with the crowd alarm on yet)")
            else:
                ok = mod > bot and cm >= cb
                print(f"  TENDER verdict: {'PASS' if ok else 'FAIL'} (all {mod - bot:+,.0f}/heat, crowded {cm - cb:+,.0f}/heat vs the bot)")
