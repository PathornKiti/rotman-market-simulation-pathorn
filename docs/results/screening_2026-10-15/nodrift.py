"""Like tcap.py, but arm 1 = no drift term at all (evaluate_tender with drift_per_tick = 0)."""
import sys
sys.path.insert(0, '/Users/pathorn.kit/rotman-market-simulation-pathorn/src')
import ritc.strategies.liability as L
orig = L.evaluate_tender
ZERO = {'on': False}
def wrapped(tender, book, **kw):
    if ZERO['on']:
        kw['drift_per_tick'] = 0.0
    return orig(tender, book, **kw)
L.evaluate_tender = wrapped
import journal_replay as jr
_rep = jr.replay
def replay(f, ov):
    ZERO['on'] = bool(ov.pop('NODRIFT', False))
    try:
        return _rep(f, ov)
    finally:
        ZERO['on'] = False
jr.replay = replay
