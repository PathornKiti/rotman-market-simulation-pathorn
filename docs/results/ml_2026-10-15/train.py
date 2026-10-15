"""Train the price and tender models on the recorded journals and export them as plain JSON (models.json).

    PYTHONPATH=<dir with scikit-learn> .venv/bin/python docs/results/ml_2026-10-15/train.py [journals ...]

Default journals: logs/data/*.jsonl. The heats used are listed in models.json ("train_heats"); score.py tests the models
only on heats NOT in that list (out of time). Hyperparameters are fixed here, not tuned on the test heats.
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
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from features import PRICE_COLS, TENDER_COLS, journal, predict  # noqa: E402

warnings.filterwarnings("ignore")
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))


def ridge():
    return make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-2, 4, 20)))


def trees():
    return HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=20,
                                         l2_regularization=1.0, random_state=0)


def export_linear(m, cols):
    sc, rg = m[0], m[-1]
    return dict(kind="linear", cols=cols, mean=sc.mean_.tolist(), scale=sc.scale_.tolist(), coef=rg.coef_.tolist(),
                intercept=float(rg.intercept_), alpha=float(rg.alpha_))


def export_trees(m, cols):
    out = []
    for (pred,) in m._predictors:
        out.append([dict(leaf=bool(n["is_leaf"]), value=float(n["value"]), f=int(n["feature_idx"]),
                         thr=float(n["num_threshold"]), left=int(n["left"]), right=int(n["right"])) for n in pred.nodes])
    return dict(kind="trees", cols=cols, baseline=float(np.ravel(m._baseline_prediction)[0]), trees=out)


def cv_r2(X, y, g, make):
    p = np.zeros(len(y))
    for tr, te in GroupKFold(10).split(X, y, g):
        p[te] = make().fit(X[tr], y[tr]).predict(X[te])
    return float(1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum())


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(glob.glob(os.path.join(ROOT, "logs", "data", "*.jsonl")))
    with ProcessPoolExecutor(8) as ex:
        J = [j for j in ex.map(journal, files) if j]
    P = [r for j in J for r in j["price"]]
    T = [r for j in J for r in j["tender"]]
    model = dict(train_heats=sorted(j["heat"] for j in J), sklearn=sklearn.__version__, horizon_ticks=10, models={},
                 cv={}, n={"heats": len(J), "price_rows": len(P), "tenders": len(T)})
    for name, rows, cols, makers in (("price", P, PRICE_COLS, (("linear", ridge), ("trees", trees))),
                                     ("tender", T, TENDER_COLS, (("linear", ridge),))):
        X = np.array([[r[c] for c in cols] for r in rows], float)
        y = np.array([r["y"] for r in rows])
        g = np.array([r["heat"] for r in rows])
        for kind, make in makers:
            key = f"{name}_{kind}"
            model["cv"][key] = cv_r2(X, y, g, make)
            m = make().fit(X, y)
            model["models"][key] = export_linear(m, cols) if kind == "linear" else export_trees(m, cols)
            sk = m.predict(X[:500])
            py = np.array([predict(model["models"][key], r) for r in rows[:500]])
            assert np.allclose(sk, py, atol=1e-9), f"{key}: pure-Python predictor differs from sklearn"
            print(f"{key:14} rows {len(y):6}  out-of-heat CV R2 {model['cv'][key]:+.3f}  (pure-Python export verified)")
    json.dump(model, open(os.path.join(HERE, "models.json"), "w"))
    print(f"saved models.json: {len(J)} training heats, {J and min(model['train_heats'])} .. {J and max(model['train_heats'])}")
