"""REFIT v2 of the 10-tick price model on every recorded heat, with a held-out TEST DAY (2026-10-16).

    PYTHONPATH=<dir with scikit-learn 1.9.1> .venv/bin/python docs/results/ml_2026-10-16/train.py

Rules fixed BEFORE the test heats were scored (overfitting guard):
  * TRAIN = heats before TEST_FROM, TEST = heats from TEST_FROM on (a whole new practice day, never used to choose
    anything). Hyperparameters are picked by 10-fold GroupKFold (by heat) CV on TRAIN only, from the small GRID below.
  * v2 replaces v1 (docs/results/ml_2026-10-15/models.json) only if, on TEST, v2's R2 >= v1's R2 AND v2 has the lower
    squared error in at least half of the test heats. Otherwise v1 stays.
  * If v2 passes, the SAME hyperparameters are refit once on TRAIN + TEST (models.json, for live use); the TRAIN-only fit
    is kept as models_train_only.json, the exact artifact the test scored. No further tuning after the test.
Writes report.txt next to this file.
"""
import glob
import json
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold

HERE = os.path.dirname(os.path.abspath(__file__))
V1_DIR = os.path.join(HERE, "..", "ml_2026-10-15")
sys.path.insert(0, V1_DIR)
from features import PRICE_COLS, TENDER_COLS, journal, predict  # noqa: E402
from train import cv_r2, export_linear, export_trees, ridge  # noqa: E402

warnings.filterwarnings("ignore")
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
TEST_FROM = "20261016-000000"
GRID = [dict(max_depth=d, max_iter=n, min_samples_leaf=leaf) for d in (2, 3, 4) for n in (100, 200, 400)
        for leaf in (20, 50)]
OUT = []


def say(s: str = "") -> None:
    print(s)
    OUT.append(s)


def trees(max_depth=3, max_iter=200, min_samples_leaf=20):
    return HistGradientBoostingRegressor(max_depth=max_depth, learning_rate=0.05, max_iter=max_iter,
                                         min_samples_leaf=min_samples_leaf, l2_regularization=1.0, random_state=0)


def r2(y, p):
    y, p = np.asarray(y), np.asarray(p)
    return float(1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum())


def xy(rows, cols):
    return (np.array([[r[c] for c in cols] for r in rows], float), np.array([r["y"] for r in rows]),
            np.array([r["heat"] for r in rows]))


def fit_all(rows_p, rows_t, hp, heats):
    """Price linear + trees (hp) and the tender linear model on these rows, exported for the pure-Python predictor."""
    out = dict(train_heats=sorted(heats), sklearn=sklearn.__version__, horizon_ticks=10, models={}, cv={},
               hyperparameters=hp, n={"heats": len(heats), "price_rows": len(rows_p), "tenders": len(rows_t)})
    X, y, g = xy(rows_p, PRICE_COLS)
    for key, make, exp in (("price_linear", ridge, export_linear), ("price_trees", lambda: trees(**hp), export_trees)):
        out["cv"][key] = cv_r2(X, y, g, make)
        m = make().fit(X, y)
        out["models"][key] = exp(m, PRICE_COLS)
        assert np.allclose(m.predict(X[:500]), [predict(out["models"][key], r) for r in rows_p[:500]], atol=1e-9)
    Xt, yt, gt = xy(rows_t, TENDER_COLS)
    out["cv"]["tender_linear"] = cv_r2(Xt, yt, gt, ridge)
    out["models"]["tender_linear"] = export_linear(ridge().fit(Xt, yt), TENDER_COLS)
    return out


if __name__ == "__main__":
    files = sorted(glob.glob(os.path.join(ROOT, "logs", "data", "*.jsonl")))
    with ProcessPoolExecutor(8) as ex:
        J = [j for j in ex.map(journal, files) if j and j["price"] and j["summary"]]
    tr = [j for j in J if j["heat"] < TEST_FROM]
    te = [j for j in J if j["heat"] >= TEST_FROM]
    P_tr = [r for j in tr for r in j["price"]]
    T_tr = [r for j in tr for r in j["tender"]]
    say(f"heats: TRAIN {len(tr)} ({tr[0]['heat']} .. {tr[-1]['heat']}), TEST {len(te)} ({te[0]['heat']} .. {te[-1]['heat']})")
    say(f"price rows: TRAIN {len(P_tr)}, TEST {sum(len(j['price']) for j in te)}")

    # 1. hyperparameters by CV on TRAIN only
    X, y, g = xy(P_tr, PRICE_COLS)
    say("\nCV on TRAIN only (10-fold by heat), R2 of the 10-tick move:")
    cv = []
    for hp in GRID:
        c = cv_r2(X, y, g, lambda hp=hp: trees(**hp))
        fit = r2(y, trees(**hp).fit(X, y).predict(X))
        cv.append((c, hp))
        say(f"  depth {hp['max_depth']} trees {hp['max_iter']:3} leaf {hp['min_samples_leaf']:2}: CV {c:+.3f}   "
            f"in-sample {fit:+.3f}   gap {fit - c:+.3f}")
    best_c, best = max(cv, key=lambda x: x[0])
    say(f"chosen: {best} (CV {best_c:+.3f}); v1 used depth 3 / 200 trees / leaf 20")

    # 2. one look at TEST: v2 (TRAIN only) vs v1 vs linear
    v2 = fit_all(P_tr, T_tr, best, [j["heat"] for j in tr])
    v1 = json.load(open(os.path.join(V1_DIR, "models.json")))
    say("\nTEST day (never used above), per heat R2:  linear_v2   trees_v1   trees_v2")
    Y, PL, P1, P2, wins, d = [], [], [], [], 0, []
    for j in te:
        rows = j["price"]
        yy = [r["y"] for r in rows]
        a = [predict(v2["models"]["price_linear"], r) for r in rows]
        b = [predict(v1["models"]["price_trees"], r) for r in rows]
        c = [predict(v2["models"]["price_trees"], r) for r in rows]
        Y += yy; PL += a; P1 += b; P2 += c
        e1, e2 = np.mean((np.array(yy) - b) ** 2), np.mean((np.array(yy) - c) ** 2)
        wins += e2 <= e1
        d.append(e1 - e2)
        say(f"  {j['heat']}  {r2(yy, a):+.3f}   {r2(yy, b):+.3f}   {r2(yy, c):+.3f}")
    r_l, r_1, r_2 = r2(Y, PL), r2(Y, P1), r2(Y, P2)
    t = float(np.mean(d) / (np.std(d, ddof=1) / len(d) ** 0.5)) if len(d) > 1 and np.std(d) else 0.0
    top = np.argsort(-np.abs(P2))[: len(P2) // 10]
    hit = float(np.mean([(P2[i] > 0) == (Y[i] > 0) for i in top]))
    top1 = np.argsort(-np.abs(P1))[: len(P1) // 10]
    hit1 = float(np.mean([(P1[i] > 0) == (Y[i] > 0) for i in top1]))
    say(f"pooled TEST R2: linear {r_l:+.3f}, trees v1 {r_1:+.3f}, trees v2 {r_2:+.3f}; v2 better in {wins}/{len(te)} heats "
        f"(paired t {t:+.2f}); top-10% signals right: v1 {hit1:.0%}, v2 {hit:.0%}")
    ok = r_2 >= r_1 and wins >= len(te) / 2
    say(f"VERDICT: {'PASS - v2 replaces v1' if ok else 'FAIL - keep v1'}")

    json.dump(v2, open(os.path.join(HERE, "models_train_only.json"), "w"))
    if ok:
        final = fit_all(P_tr + [r for j in te for r in j["price"]], T_tr + [r for j in te for r in j["tender"]], best,
                        [j["heat"] for j in J])
        json.dump(final, open(os.path.join(HERE, "models.json"), "w"))
        say(f"saved models.json: same hyperparameters refit on all {len(J)} heats (CV {final['cv']['price_trees']:+.3f}); "
            "models_train_only.json = the artifact the TEST day scored")
    open(os.path.join(HERE, "report.txt"), "w").write("\n".join(OUT) + "\n")
