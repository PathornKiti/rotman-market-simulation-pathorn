"""
Replay a REAL heat's journal (logs/data/*.jsonl with "market" rows) through the CURRENT strategy code, dry-run, and
print every tender decision. Positions / books / trades are the recorded ones (what actually happened), so the
decisions are what the bot would have answered at each moment given the real state.

    PYTHONPATH=src .venv/bin/python journal_replay.py logs/data/x.jsonl '{"strategy.early_probe_ticks":0}'
"""
import json
import logging
import sys
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, "src")


class JournalAdapter(requests.adapters.BaseAdapter):
    def __init__(self, rows):
        super().__init__()
        self.mk = [r for r in rows if r["k"] == "market"]
        self.seen = {r["tid"]: r for r in rows if r["k"] == "tender_seen"}
        self.i = 0
        self.answered: dict[int, str] = {}
        self.tas_all: dict[str, list] = {}
        for r in self.mk:                      # accumulate time & sales with their ids
            for t, lst in (r.get("tas") or {}).items():
                self.tas_all.setdefault(t, []).extend(lst)
        self.oid = 10_000_000

    @property
    def row(self):
        return self.mk[min(self.i, len(self.mk) - 1)]

    def tenders(self):
        tick = self.row["case"]["tick"]
        out = []
        for tid, s in self.seen.items():
            if s["tick"] <= tick < s["expires"] and tid not in self.answered:
                out.append({"tender_id": tid, "period": 1, "tick": s["tick"], "expires": s["expires"],
                            "caption": s.get("caption"), "ticker": s["ticker"], "quantity": s["qty"],
                            "action": s["action"], "is_fixed_bid": s["fixed"], "price": s["price"]})
        return out

    def route(self, method, path, q):
        r = self.row
        if method == "GET":
            if path.endswith("/case"):
                c = dict(r["case"])
                if self.i >= len(self.mk) - 1:
                    c["status"] = "STOPPED"
                return c
            if path.endswith("/trader"):
                return r["trader"]
            if path.endswith("/limits"):
                return r["limits"]
            if path.endswith("/securities/book"):
                return r["books"].get(q.get("ticker"), {"bids": [], "asks": []})
            if path.endswith("/securities/tas"):
                after = int(q.get("after") or -1)
                tick = r["case"]["tick"]
                return [x for x in self.tas_all.get(q.get("ticker"), [])
                        if int(x.get("id", 0)) > after and int(x.get("tick", 0)) <= tick]
            if path.endswith("/securities"):
                secs = r["securities"]
                return [s for s in secs if s["ticker"] == q["ticker"]] if q.get("ticker") else secs
            if path.endswith("/tenders"):
                return self.tenders()
            if path.endswith("/orders"):
                return []
            if path.endswith("/news"):
                return []
            return []
        if "/tenders/" in path:
            self.answered[int(path.rsplit("/", 1)[1])] = method
            return {"success": True}
        if path.endswith("/orders") and method == "POST":
            self.oid += 1
            return {"order_id": self.oid, "ticker": q.get("ticker"), "quantity": float(q.get("quantity", 0)),
                    "quantity_filled": 0.0, "action": q.get("action"), "price": float(q.get("price") or 0),
                    "status": "OPEN", "type": q.get("type")}
        return {"success": True}

    def send(self, request, **kwargs):
        u = urlparse(request.url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        obj = self.route(request.method, u.path, q)
        resp = requests.Response()
        resp.status_code, resp._content, resp.url, resp.request = 200, json.dumps(obj).encode(), request.url, request
        resp.headers["Content-Type"] = "application/json"
        resp.encoding = "utf-8"
        return resp

    def close(self):
        pass


class Keep(logging.Handler):
    def __init__(self, ad):
        super().__init__(logging.INFO)
        self.ad, self.lines = ad, []

    def emit(self, rec):
        self.lines.append(f"t{self.ad.row['case']['tick']:>3} {rec.getMessage()}")


def replay(path, overrides):
    import os
    os.environ["RIT_URL"] = "http://127.0.0.1:9/v1"
    from ritc.cli import build
    rows = [json.loads(line) for line in open(path)]
    ad = JournalAdapter(rows)
    root = logging.getLogger()
    root.handlers = [h := Keep(ad)]
    root.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)
    runner, cfg = build("liability", None, live=True, overrides=overrides)
    runner.s.ex.dry_run = False      # orders and answers go to the journal adapter (fake), so accepted tenders count as ours
    runner.client.adapter = ad
    runner.client.min_interval = 0.0
    runner.interval = 0.0
    runner.use_feed = False
    runner.s.ex.settle = 0.0
    runner.on_loop = lambda: setattr(ad, "i", ad.i + 1)
    runner.run()
    return h.lines


if __name__ == "__main__":
    ov = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    for line in replay(sys.argv[1], ov):
        if any(k in line for k in ("TENDER", "PROBE", "CROWD", "LIQ", "SPRINT", "SOFT", "BLOCK")):
            print(line[:230])
