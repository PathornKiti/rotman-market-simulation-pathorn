"""
Self-contained HTML for the post-trade reports: no external files, light and dark themes, inline SVG
charts with hover tooltips, and every charted number also in a table.
"""

from __future__ import annotations

import html as _html
import json

TICKERS = ("CRZY", "TAME", "CROC")
SLOT = {"CRZY": "var(--s1)", "TAME": "var(--s2)", "CROC": "var(--s3)"}     # fixed order, never cycled
PART_LABEL = {"edge": "Tender edge vs mid", "spread_maker": "Resting fills vs mid", "spread_taker": "Crossing fills vs mid",
              "fees": "Commissions", "impact": "Own price impact", "inventory": "Inventory moves (luck / crowd)",
              "closeout": "Close-out vs mid", "fines": "Fines"}
STATUS = {"ok": ("good", "✓", "OK"), "fixed": ("good", "↻", "Fixed since"), "gap": ("serious", "!", "Gap"),
          "info": ("muted", "i", "Info")}

CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;
--pos:#2a78d6;--neg:#e34948;--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;
--goodtext:#006300;--wash:rgba(42,120,214,.08)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;
--surface:#1a1a19;
--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--pos:#3987e5;--neg:#e66767;--goodtext:#0ca30c;--wash:rgba(57,135,229,.12)}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--s3:#199e70;--pos:#3987e5;
--neg:#e66767;--goodtext:#0ca30c;--wash:rgba(57,135,229,.12)}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:24px;margin:0 0 4px;font-weight:650}
h2{font-size:18px;margin:36px 0 10px;font-weight:650}
h3{font-size:15px;margin:18px 0 6px;font-weight:600}
p{margin:6px 0 10px;max-width:80ch}
.sub{color:var(--ink2);margin:0 0 4px}
.muted{color:var(--muted)}
a{color:var(--s1)}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--wash);padding:1px 4px;
border-radius:4px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .label{color:var(--ink2);font-size:13px}
.tile .value{font-size:24px;font-weight:600;margin-top:2px}
.tile .note{color:var(--muted);font-size:12px}
.pill{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;border-radius:999px;
padding:1px 9px 1px 3px;
border:1px solid var(--border);white-space:nowrap}
.pill i{font-style:normal;display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;
color:#fff;font-size:11px}
.pill.good i{background:var(--good)}.pill.serious i{background:var(--serious)}
.pill.critical i{background:var(--critical)}
.pill.muted i{background:var(--muted)}.pill.warning i{background:var(--warning);color:#0b0b0b}
.scroll{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink2);font-weight:600;background:var(--surface);position:sticky;top:0}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.hl td{background:var(--wash)}
.pos{color:var(--goodtext)}.neg{color:var(--neg)}
details{margin:4px 0}summary{cursor:pointer;color:var(--ink2)}
pre.log{max-height:520px;overflow:auto;font:11.5px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px;white-space:pre}
details ul{margin:6px 0 4px 18px;padding:0}details li{margin:2px 0;color:var(--ink2);font-size:13px}
.check{display:grid;grid-template-columns:110px 1fr;gap:10px;padding:12px 0;border-bottom:1px solid var(--grid)}
.check:last-child{border-bottom:0}
.check .t{font-weight:600}.check .c{color:var(--muted);font-size:12.5px;font-style:italic}
.explain{border-left:3px solid var(--s1)}.explain p{max-width:95ch}
.imp{border-left:3px solid var(--s1);padding:2px 0 2px 12px;margin:12px 0}
.imp .st{color:var(--ink2);font-size:12.5px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--ink2);margin:4px 0 8px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.key{display:inline-block;width:14px;height:2px;border-radius:1px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px}
svg{display:block;max-width:100%;height:auto;overflow:visible}
svg text{fill:var(--muted);font:11px system-ui,-apple-system,"Segoe UI",sans-serif}
svg .title{fill:var(--ink2);font-weight:600;font-size:12px}
.grid line{stroke:var(--grid);stroke-width:1}
.axis{stroke:var(--axis);stroke-width:1}
.multiples{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
#tip{position:fixed;pointer-events:none;z-index:10;background:var(--surface);color:var(--ink);
border:1px solid var(--border);
border-radius:8px;padding:7px 10px;font-size:12.5px;box-shadow:0 4px 18px rgba(0,0,0,.15);display:none;
max-width:340px}
#tip b{font-weight:650}
.hit{fill:transparent;cursor:default}
[data-tip]:hover{opacity:.85}
.toolbar{float:right;font-size:12.5px}
.toolbar button{font:inherit;background:var(--surface);color:var(--ink2);border:1px solid var(--border);
border-radius:6px;
padding:3px 8px;cursor:pointer}
@media (max-width:640px){.check{grid-template-columns:1fr}.toolbar{float:none;margin-bottom:8px}}
"""

JS = """
(function(){
  var tip=document.getElementById('tip');
  function show(e,el){tip.textContent='';var rows=JSON.parse(el.getAttribute('data-tip'));
    rows.forEach(function(r,i){var d=document.createElement('div');if(i===0){var b=document.createElement('b');
      b.textContent=r;d.appendChild(b);}else{d.textContent=r;}tip.appendChild(d);});
    tip.style.display='block';move(e);}
  function move(e){var x=e.clientX+14,y=e.clientY+14,w=tip.offsetWidth,h=tip.offsetHeight;
    if(x+w>innerWidth-8)x=e.clientX-w-14;if(y+h>innerHeight-8)y=e.clientY-h-14;tip.style.left=x+'px';
tip.style.top=y+'px';}
  document.addEventListener('pointerover',function(e){var el=e.target.closest('[data-tip]');if(el)show(e,el);});
  document.addEventListener('pointermove',function(e){if(tip.style.display==='block')move(e);});
  document.addEventListener('pointerout',function(e){if(e.target.closest('[data-tip]'))tip.style.display='none';});
  document.addEventListener('focusin',function(e){var el=e.target.closest('[data-tip]');if(el){
var r=el.getBoundingClientRect();
    show({clientX:r.right,clientY:r.top},el);}});
  document.addEventListener('focusout',function(){tip.style.display='none';});
  document.querySelectorAll('svg[data-series]').forEach(function(svg){
    var s=JSON.parse(svg.getAttribute('data-series')),hair=svg.querySelector('.hair'),ov=svg.querySelector('.ov');
    if(!ov)return;ov.addEventListener('pointermove',function(e){var pt=svg.createSVGPoint();pt.x=e.clientX;
pt.y=e.clientY;
      var p=pt.matrixTransform(svg.getScreenCTM().inverse()),i=Math.round((p.x-s.x0)/s.dx);
i=Math.max(0,Math.min(s.rows.length-1,i));
      var r=s.rows[i];hair.setAttribute('x1',s.x0+i*s.dx);hair.setAttribute('x2',s.x0+i*s.dx);
hair.style.display='block';
      tip.textContent='';var b=document.createElement('b');b.textContent=r[0];tip.appendChild(b);
      for(var j=1;j<r.length;j++){var d=document.createElement('div');d.textContent=r[j];tip.appendChild(d);}
      tip.style.display='block';move(e);});
    ov.addEventListener('pointerleave',function(){hair.style.display='none';tip.style.display='none';});});
  function openHash(h){var d=h&&document.getElementById(h.slice(1));
    for(var e=d;e;e=e.parentElement){if(e.tagName==='DETAILS'){e.open=true;}}
    if(d&&d.tagName==='SECTION'){var x=d.querySelector('details');if(x)x.open=true;}
    if(d)setTimeout(function(){d.scrollIntoView();},0);}
  document.addEventListener('click',function(e){var a=e.target.closest('a[href^="#"]');if(a)openHash(a.hash);});
  openHash(location.hash);window.addEventListener('hashchange',function(){openHash(location.hash);});
  var t=document.getElementById('theme');if(t)t.addEventListener('click',function(){var r=document.documentElement;
    r.setAttribute('data-theme',r.getAttribute('data-theme')==='dark'?'light':'dark');});
})();
"""


def esc(x) -> str:
    return _html.escape("" if x is None else str(x))


def tip(*rows) -> str:
    return esc(json.dumps([str(r) for r in rows]))


def money(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "-"
    s = f"${abs(x):,.0f}"
    return ("-" + s) if x < 0 else (("+" + s) if signed and x > 0 else s)


def cents(x: float | None, signed: bool = True) -> str:
    if x is None:
        return "-"
    return f"{100 * x:+.1f}c" if signed else f"{100 * x:.1f}c"


def pill(status: str) -> str:
    cls, icon, label = STATUS.get(status, ("muted", "i", status))
    return f'<span class="pill {cls}"><i aria-hidden="true">{icon}</i>{esc(label)}</span>'


def page(title: str, body: str, description: str = "") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
            f'<meta name="description" content="{esc(description)}"><style>{CSS}</style></head><body>'
            f'<main><div class="toolbar"><button id="theme" type="button">Light / dark</button></div>{body}</main>'
            f'<div id="tip" role="tooltip"></div><script>{JS}</script></body></html>')


# ------------------------------------------------------------------------------------------------- charts
def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    import math
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = math.floor(lo / step) * step
    out, v = [], start
    while v <= hi + 1e-9:
        if v >= lo - 1e-9:
            out.append(round(v, 10))
        v += step
    return out


def _fmt_axis(v: float, kind: str) -> str:
    if kind == "money":
        return f"{'-' if v < 0 else ''}${abs(v) / 1000:,.0f}k" if abs(v) >= 1000 else f"{'-' if v < 0 else ''}${abs(v):,.0f}"
    if kind == "shares":
        return f"{v / 1000:+,.0f}k" if v else "0"
    if kind == "count":
        return f"{v:,.0f}"
    if kind == "cents":
        return f"{v:+.0f}c" if v else "0"
    return f"{v:,.2f}"


def price_panels(mids: list[dict], ledger: list[dict], seed_label: str) -> str:
    """Small multiples: the simulated mid of each stock, tenders marked where they arrived (shape = type,
    filled = accepted by at least one bot)."""
    if not mids:
        return ""
    W, H, L, R, T, B = 380, 190, 46, 10, 22, 26
    panels = []
    for tk in TICKERS:
        ys = [m[tk] for m in mids]
        rows = [r for r in ledger if r["ticker"] == tk and r.get("arrival_tick") is not None]
        lo, hi = min(ys), max(ys)
        for r in rows:
            for d in r["decisions"]:
                if d["price"] is not None:
                    lo, hi = min(lo, d["price"]), max(hi, d["price"])
        pad = (hi - lo) * 0.06 or 0.05
        lo, hi = lo - pad, hi + pad
        n = len(ys)
        x = lambda k, n=n: L + (W - L - R) * (k - 1) / max(1, n - 1)            # noqa: E731
        y = lambda v, lo=lo, hi=hi: T + (H - T - B) * (1 - (v - lo) / (hi - lo))  # noqa: E731
        g = [f'<text class="title" x="{L}" y="13">{tk}</text>', '<g class="grid">']
        for v in _ticks(lo, hi, 4):
            g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        g.append("</g>")
        for v in _ticks(lo, hi, 4):
            g.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.2f}</text>')
        for k in (1, 100, 200, 300, 420):
            if k <= n:
                g.append(f'<text x="{x(k):.1f}" y="{H - 8}" text-anchor="middle">{k}</text>')
        g.append(f'<line class="axis" x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}"/>')
        pts = " ".join(f"{x(i + 1):.1f},{y(v):.1f}" for i, v in enumerate(ys))
        g.append(f'<polyline fill="none" stroke="{SLOT[tk]}" stroke-width="2" stroke-linejoin="round" '
                 f'stroke-linecap="round" points="{pts}"/>')
        for r in rows:
            k = r["arrival_tick"]
            acc = any(d["accept"] and not d.get("rejected") for d in r["decisions"])
            px = next((d["price"] for d in r["decisions"] if d["price"] is not None), None)
            yy = y(px) if px is not None else y(mids[min(n, k) - 1][tk])
            fill = "var(--ink)" if acc else "var(--surface)"
            shape = (f'<circle cx="{x(k):.1f}" cy="{yy:.1f}" r="4.5" fill="{fill}" stroke="var(--ink)" stroke-width="1.6"/>'
                     if r["kind"] == "private" else
                     f'<rect x="{x(k) - 4.5:.1f}" y="{yy - 4.5:.1f}" width="9" height="9" '
                     f'transform="rotate(45 {x(k):.1f} {yy:.1f})" fill="{fill}" stroke="var(--ink)" stroke-width="1.6"/>'
                     if r["kind"] == "auction" else
                     f'<rect x="{x(k) - 4:.1f}" y="{yy - 4:.1f}" width="8" height="8" fill="{fill}" stroke="var(--ink)" '
                     f'stroke-width="1.6"/>')
            dec = "; ".join(("ACCEPT" if d["accept"] else "decline") + (f" bid {d['bid']:.2f}" if d["bid"] else "")
                            + (f" est {d['pps']:+.3f}" if d["pps"] is not None else "") for d in r["decisions"]) or "not answered"
            t = tip(f"Tender {r['tid']} · {r['kind']} · {r['action']} {r['qty']:,} {tk}",
                    f"arrived tick {k}, expires {r['expires']}",
                    (f"price {px:.2f}, mid {r['mid_arrival']:.2f}" if px is not None and r['mid_arrival'] else
                     f"reserve {r['reserve']}, rival {r['rival']}") , dec)
            g.append(f'<g data-tip="{t}" tabindex="0"><circle class="hit" cx="{x(k):.1f}" cy="{yy:.1f}" r="12"/>{shape}</g>')
        panels.append(f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{tk} simulated mid with tenders">'
                      + "".join(g) + "</svg>")
    legend = ('<div class="legend"><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="var(--ink)"/></svg>'
              'accepted</span><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="none" stroke="var(--ink)" '
              'stroke-width="1.6"/></svg>declined / not won</span><span>● private</span><span>◆ auction</span>'
              '<span>■ winner-take-all</span><span class="muted">x: tick · y: price · line: simulated mid '
              f'({esc(seed_label)})</span></div>')
    return legend + '<div class="multiples">' + "".join(f'<div class="card">{p}</div>' for p in panels) + "</div>"


def decision_map(ledger: list[dict]) -> str:
    """Scatter: edge vs the mid on arrival (x) against the bot's own profit estimate (y), per decision."""
    pts = [(r, d) for r in ledger for d in r["decisions"] if d["price"] is not None and d["edge"] is not None
           and d["pps"] is not None]
    if len(pts) < 3:
        return ""
    W, H, L, R, T, B = 1100, 380, 56, 16, 16, 40
    xs = [100 * d["edge"] for _, d in pts]
    ys = [max(-60.0, 100 * d["pps"]) for _, d in pts]
    xlo, xhi = min(xs + [-5]) - 2, max(xs + [5]) + 2
    ylo, yhi = min(ys + [-5]) - 2, max(ys + [5]) + 2
    x = lambda v: L + (W - L - R) * (v - xlo) / (xhi - xlo)     # noqa: E731
    y = lambda v: T + (H - T - B) * (1 - (v - ylo) / (yhi - ylo))  # noqa: E731
    g = ['<g class="grid">']
    for v in _ticks(ylo, yhi, 5):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
    g.append("</g>")
    for v in _ticks(ylo, yhi, 5):
        g.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{_fmt_axis(v, "cents")}</text>')
    for v in _ticks(xlo, xhi, 6):
        g.append(f'<text x="{x(v):.1f}" y="{H - 22}" text-anchor="middle">{_fmt_axis(v, "cents")}</text>')
    g.append(f'<line class="axis" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="{T}" y2="{H - B}"/>')
    g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(1):.1f}" y2="{y(1):.1f}" stroke="var(--ink2)" stroke-width="1"/>')
    g.append(f'<text x="{(L + W - R) / 2:.0f}" y="{H - 4}" text-anchor="middle">edge vs the mid when the tender '
             f'arrived (cents per share, + = good for us)</text>')
    g.append(f'<text x="12" y="{(T + H - B) / 2:.0f}" transform="rotate(-90 12 {(T + H - B) / 2:.0f})" '
             f'text-anchor="middle">bot estimate after costs</text>')
    for (r, d), xv, yv in zip(pts, xs, ys):
        cx, cy = x(xv), y(yv)
        rad = 3 + 3 * (r["qty"] / 50000) ** 0.5
        mark = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" fill="{SLOT[r["ticker"]] if r["ticker"] in SLOT else "var(--ink)"}" '
                f'stroke="var(--surface)" stroke-width="2"/>' if d["accept"] else
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rad:.1f}" fill="var(--surface)" '
                f'stroke="{SLOT.get(r["ticker"], "var(--ink)")}" stroke-width="2"/>')
        t = tip(f"Tender {r['tid']} {r['action']} {r['qty']:,} {r['ticker']} @ {d['price']}",
                f"edge on arrival {cents(d['edge'])}, bot estimate {cents(d['pps'])}",
                ("ACCEPTED" if d["accept"] else "declined") + (f" · {d['variant']}" if d.get("variant") else ""))
        g.append(f'<g data-tip="{t}" tabindex="0"><circle class="hit" cx="{cx:.1f}" cy="{cy:.1f}" r="12"/>{mark}</g>')
    legend = ('<div class="legend">' + "".join(f'<span><i class="sw" style="background:{SLOT[t]}"></i>{t}</span>'
                                                 for t in TICKERS)
              + '<span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="var(--ink2)"/></svg>accepted</span>'
              + '<span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="none" stroke="var(--ink2)" '
                'stroke-width="2"/></svg>declined</span><span><i class="key" style="background:var(--ink2);height:1px">'
                '</i>accept threshold (1c after costs)</span><span class="muted">dot size = block size</span></div>')
    return legend + f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Decision map">' + "".join(g) + "</svg>"


def bars(items: list[tuple[str, float, str]], unit: str = "money", width: int = 1100) -> str:
    """Horizontal diverging bars: (label, value, tooltip line). Category labels in their own column on the left,
    then a gutter for the value labels of negative bars, then the plot."""
    if not items:
        return ""
    rowh, label_w, R = 26, 300, 90
    lo, hi = min(0.0, min(v for _, v, _ in items)), max(0.0, max(v for _, v, _ in items))
    gutter = 80 if lo < 0 else 10
    L = label_w + gutter
    H = rowh * len(items) + 30
    if hi == lo:
        hi = lo + 1
    x = lambda v: L + (width - L - R) * (v - lo) / (hi - lo)    # noqa: E731
    g = ['<g class="grid">']
    for v in _ticks(lo, hi, 5):
        g.append(f'<line x1="{x(v):.1f}" x2="{x(v):.1f}" y1="4" y2="{H - 24}"/>')
    g.append("</g>")
    for v in _ticks(lo, hi, 5):
        g.append(f'<text x="{x(v):.1f}" y="{H - 8}" text-anchor="middle">{_fmt_axis(v, unit)}</text>')
    g.append(f'<line class="axis" x1="{x(0):.1f}" x2="{x(0):.1f}" y1="4" y2="{H - 24}"/>')
    for i, (label, v, note) in enumerate(items):
        y0 = 8 + i * rowh
        x0, x1 = sorted((x(0), x(v)))
        w = x1 - x0
        val = money(v, signed=True) if unit == "money" else f"{v:,.0f}" if unit == "count" else f"{v:+,.0f}"
        g.append(f'<text x="{label_w - 10}" y="{y0 + 11}" text-anchor="end" style="fill:var(--ink2)">{esc(label)}</text>')
        mark = ""
        if w >= 0.5:
            col = "var(--pos)" if v >= 0 else "var(--neg)"
            r = min(4.0, w / 2)
            if v >= 0:      # 4px rounded data end, square at the baseline
                path = (f"M{x0:.1f},{y0}h{w - r:.1f}a{r:.1f},{r:.1f} 0 0 1 {r:.1f},{r:.1f}v{14 - 2 * r:.1f}"
                        f"a{r:.1f},{r:.1f} 0 0 1 {-r:.1f},{r:.1f}h{-(w - r):.1f}z")
            else:
                path = (f"M{x1:.1f},{y0}h{-(w - r):.1f}a{r:.1f},{r:.1f} 0 0 0 {-r:.1f},{r:.1f}v{14 - 2 * r:.1f}"
                        f"a{r:.1f},{r:.1f} 0 0 0 {r:.1f},{r:.1f}h{w - r:.1f}z")
            mark = f'<path d="{path}" fill="{col}"/>'
        g.append(f'<g data-tip="{tip(label, val, note)}" tabindex="0"><rect class="hit" x="{label_w}" y="{y0 - 4}" '
                 f'width="{width - label_w - R}" height="{rowh - 2}"/>{mark}</g>')
        tx = x1 + 6 if v >= 0 else x0 - 6
        style = "fill:var(--ink)" if w >= 0.5 else "fill:var(--muted)"
        g.append(f'<text x="{tx:.1f}" y="{y0 + 11}" text-anchor="{"start" if v >= 0 else "end"}" '
                 f'style="{style}">{esc(val)}</text>')
    return f'<svg viewBox="0 0 {width} {H}" role="img" aria-label="bar chart">' + "".join(g) + "</svg>"


def positions_chart(series: list[dict], height: int = 300, width: int = 1100) -> str:
    if not series:
        return ""
    L, R, T, B = 60, 56, 12, 28
    n = len(series)
    vals = [s["pos"].get(t, 0) for s in series for t in TICKERS]
    lo, hi = min(vals + [0]), max(vals + [0])
    pad = (hi - lo) * 0.08 or 1000
    lo, hi = lo - pad, hi + pad
    dx = (width - L - R) / max(1, n - 1)
    x = lambda i: L + dx * i                                      # noqa: E731
    y = lambda v: T + (height - T - B) * (1 - (v - lo) / (hi - lo))  # noqa: E731
    g = ['<g class="grid">']
    for v in _ticks(lo, hi, 5):
        g.append(f'<line x1="{L}" x2="{width - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
    g.append("</g>")
    for v in _ticks(lo, hi, 5):
        g.append(f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{_fmt_axis(v, "shares")}</text>')
    for k in (1, 60, 120, 180, 240, 300, 360, 420):
        i = k - series[0]["tick"]
        if 0 <= i < n:
            g.append(f'<text x="{x(i):.1f}" y="{height - 8}" text-anchor="middle">{k}</text>')
    g.append(f'<line class="axis" x1="{L}" x2="{width - R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    for t in TICKERS:
        pts = " ".join(f"{x(i):.1f},{y(s['pos'].get(t, 0)):.1f}" for i, s in enumerate(series))
        g.append(f'<polyline fill="none" stroke="{SLOT[t]}" stroke-width="2" stroke-linejoin="round" points="{pts}"/>')
        end = series[-1]["pos"].get(t, 0)
        g.append(f'<circle cx="{x(n - 1):.1f}" cy="{y(end):.1f}" r="4" fill="{SLOT[t]}" stroke="var(--surface)" '
                 f'stroke-width="2"/>')
    ends = sorted(((series[-1]["pos"].get(t, 0), t) for t in TICKERS), reverse=True)
    last_y = -99.0
    for v, t in ends:                                       # end labels, never stacked on top of each other
        yy = max(y(v) + 4, last_y + 13)
        last_y = yy
        g.append(f'<text x="{width - R + 8}" y="{yy:.1f}" style="fill:var(--ink2)">{t} {v / 1000:+.0f}k</text>')
    rows = [[f"tick {s['tick']}"] + [f"{t} {s['pos'].get(t, 0):+,}" for t in TICKERS]
            + [f"NLV {money(s.get('nlv'))}"] for s in series]
    data = esc(json.dumps({"x0": L, "dx": dx, "rows": rows}))
    g.append(f'<line class="hair" x1="0" x2="0" y1="{T}" y2="{height - B}" stroke="var(--muted)" stroke-width="1" '
             f'style="display:none"/>')
    g.append(f'<rect class="ov" x="{L}" y="{T}" width="{width - L - R}" height="{height - T - B}" fill="transparent"/>')
    legend = '<div class="legend">' + "".join(f'<span><i class="key" style="background:{SLOT[t]}"></i>{t}</span>'
                                              for t in TICKERS) + "</div>"
    return legend + (f'<svg viewBox="0 0 {width} {height}" data-series="{data}" role="img" '
                     f'aria-label="positions by tick">' + "".join(g) + "</svg>")


# --------------------------------------------------------------------------------------------- sections
def nlv_chart(series: list[dict], height: int = 220, width: int = 1100) -> str:
    """The run's P&L (NLV) tick by tick, with a crosshair."""
    pts = [(p["tick"], p["nlv"]) for p in series if p.get("nlv") is not None]
    if len(pts) < 2:
        return ""
    L, R, T, B = 70, 20, 12, 28
    lo, hi = min(0.0, min(v for _, v in pts)), max(0.0, max(v for _, v in pts))
    pad = (hi - lo) * 0.06 or 1000
    lo, hi = lo - pad, hi + pad
    n = len(pts)
    dx = (width - L - R) / max(1, n - 1)
    x = lambda i: L + dx * i                                      # noqa: E731
    y = lambda v: T + (height - T - B) * (1 - (v - lo) / (hi - lo))  # noqa: E731
    g = ['<g class="grid">'] + [f'<line x1="{L}" x2="{width - R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>'
                                for v in _ticks(lo, hi, 4)] + ["</g>"]
    g += [f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{_fmt_axis(v, "money")}</text>' for v in _ticks(lo, hi, 4)]
    g.append(f'<line class="axis" x1="{L}" x2="{width - R}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    for k in (1, 60, 120, 180, 240, 300, 360, 420):
        i = next((j for j, (t, _) in enumerate(pts) if t >= k), None)
        if i is not None:
            g.append(f'<text x="{x(i):.1f}" y="{height - 8}" text-anchor="middle">{k}</text>')
    g.append('<polyline fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" points="'
             + " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(pts)) + '"/>')
    g.append(f'<circle cx="{x(n - 1):.1f}" cy="{y(pts[-1][1]):.1f}" r="4" fill="var(--s1)" stroke="var(--surface)" stroke-width="2"/>')
    rows = [[f"tick {t}", f"NLV {money(v)}"] for t, v in pts]
    data = esc(json.dumps({"x0": L, "dx": dx, "rows": rows}))
    g.append(f'<line class="hair" x1="0" x2="0" y1="{T}" y2="{height - B}" stroke="var(--muted)" stroke-width="1" style="display:none"/>')
    g.append(f'<rect class="ov" x="{L}" y="{T}" width="{width - L - R}" height="{height - T - B}" fill="transparent"/>')
    return f'<svg viewBox="0 0 {width} {height}" data-series="{data}" role="img" aria-label="NLV by tick">' + "".join(g) + "</svg>"


SEV = {"high": ("serious", "!", "High"), "medium": ("warning", "!", "Medium"), "low": ("muted", "i", "Low")}


def journal_html(j: dict) -> str:
    """Run journal section: P&L path, where it came from, and the struggle distribution."""
    rows = j["tenders"]
    final = j["final_pnl"]
    tiles = (f'<div class="tiles"><div class="tile"><div class="label">Final P&amp;L (NLV)</div><div class="value">{money(final)}</div>'
             f'<div class="note">from the run journal</div></div>'
             f'<div class="tile"><div class="label">Max drawdown</div><div class="value">{money(j["max_drawdown"])}</div>'
             f'<div class="note">at tick {j["drawdown_tick"]}</div></div>'
             f'<div class="tile"><div class="label">Tenders seen / answered / booked</div><div class="value">'
             f'{len(rows)} / {sum(1 for r in rows if r["answer"])} / {sum(1 for r in rows if r["booked_tick"])}</div>'
             f'<div class="note">booked = position jumped by the tender size</div></div>'
             f'<div class="tile"><div class="label">Struggle types</div><div class="value">{len(j["struggles"])}</div>'
             f'<div class="note">{sum(v["count"] for v in j["struggles"].values())} events</div></div></div>')
    parts = bars([(k, v, "") for k, v in j["parts"].items() if v is not None])
    srows = "".join(
        f'<tr><td>{esc(k)}</td><td><span class="pill {SEV[v["severity"]][0]}"><i aria-hidden="true">{SEV[v["severity"]][1]}</i>'
        f'{SEV[v["severity"]][2]}</span></td><td class=n>{v["count"]:,}</td><td class=n>{money(v["value"]) if v["value"] else ""}</td>'
        f'<td>{esc(v["detail"])}</td><td class="muted">{esc(v["hint"])}</td></tr>'
        for k, v in sorted(j["struggles"].items(), key=lambda kv: (-{"high": 3, "medium": 2, "low": 1}[kv[1]["severity"]],
                                                                    -kv[1]["count"])))
    sbars = bars([(k, v["count"], v["detail"]) for k, v in j["struggles"].items()], unit="count")
    trows = "".join(
        f'<tr><td>{r["tid"]}</td><td>{esc("private" if r["fixed"] else "auction")}</td><td>{esc(r["action"])} {esc(r["ticker"])}</td>'
        f'<td class=n>{(r["qty"] or 0):,}</td><td class=n>{"" if r["price"] is None else format(r["price"], ".2f")}</td>'
        f'<td class=n>{r["tick"]}</td><td class=n>{r["decision_tick"] or ""}</td><td class=n>{cents(r["edge"]) if r["edge"] is not None else ""}</td>'
        f'<td class=n>{cents(r["pps"]) if r["pps"] is not None else ""}</td><td>{esc(r["answer"] or "not answered")}</td>'
        f'<td class=n>{r["booked_tick"] or ""}</td></tr>' for r in rows)
    return ("<h2>Run journal: P&amp;L and struggles</h2><p>From the journal <code>ritc run</code> writes next to the log "
            "(every tick's NLV, positions and quotes; every order, tender, answer, error and slow loop). This is the "
            "part of the report that works for live RIT runs too.</p>" + tiles
            + "<h3>P&amp;L through the heat</h3>" + nlv_chart(j["series"])
            + "<h3>Where it came from</h3>" + parts
            + '<p class="muted">Tender edge = (mid when answered - price) x size for every booked tender; inventory moves = '
              "position x mid change tick by tick; the rest is execution (spread, fees, impact), the close-out and fines.</p>"
            + "<h3>What the bot struggled with</h3>" + sbars
            + '<div class="card scroll"><table><tr><th>Struggle</th><th>Severity</th><th class=n>Count</th>'
              "<th class=n>$ at stake</th><th>Detail</th><th>Look at</th></tr>" + srows + "</table></div>"
            + "<h3>Positions</h3>" + positions_chart(j["series"])
            + '<h3>Tenders in the journal</h3><div class="card scroll"><table><tr><th>Tender</th><th>Type</th><th>Side</th>'
              "<th class=n>Size</th><th class=n>Price</th><th class=n>Seen</th><th class=n>Answered</th><th class=n>Edge then</th>"
              "<th class=n>Bot est.</th><th>Answer</th><th class=n>Booked</th></tr>" + trows + "</table></div>")


def _checks_html(checks: list[dict], improvements: dict) -> str:
    out = []
    for c in checks:
        ev = ""
        if c["evidence"]:
            ev = ("<details><summary>Evidence (" + str(len(c["evidence"])) + ")</summary><ul>"
                  + "".join(f"<li>{esc(e)}</li>" for e in c["evidence"]) + "</ul></details>")
        imps = "".join(f' <a href="#imp-{esc(i)}">{esc(improvements[i]["title"])}</a>' for i in c["improvements"]
                       if i in improvements)
        value = f' <span class="muted">({money(c["value"])} at stake)</span>' if c.get("value") else ""
        out.append(f'<div class="check"><div>{pill(c["status"])}</div><div><div class="t">{esc(c["title"])}</div>'
                   + (f'<div class="c">Brief: {esc(c["clause"])}</div>' if c["clause"] else "")
                   + f'<div>{esc(c["detail"])}{value}</div>{ev}'
                   + (f'<div class="muted" style="font-size:12.5px">Improvement:{imps}</div>' if imps else "")
                   + "</div></div>")
    return '<div class="card">' + "".join(out) + "</div>"


def _ledger_html(ledger: list[dict]) -> str:
    head = ("<tr><th>Tender</th><th>Type</th><th>Side</th><th>Stock</th><th class=n>Size</th><th class=n>Price</th>"
            "<th class=n>Arrived</th><th class=n>Expires</th><th class=n>Mid then</th><th class=n>Edge</th>"
            "<th class=n>Bot est.</th><th>Answer</th><th>Auction</th><th>Market</th></tr>")
    rows = []
    for r in ledger:
        decs = r["decisions"] or [None]
        for j, d in enumerate(decs):
            hl = d and ((not d["accept"] and (d["edge"] or 0) > 0.10) or (d["accept"] and (d["edge"] or 0) < 0))
            ans = "not answered" if d is None else ("ACCEPT" if d["accept"] else "decline")
            if d and d.get("rejected"):
                ans += " (rejected)"
            auc = ""
            if d and d.get("bid") is not None:
                auc = f"bid {d['bid']:.2f}"
                if d.get("bid_vs_reserve") is not None:
                    auc += f" · {cents(d['bid_vs_reserve'])} vs reserve {r['reserve']:.2f}"
                if d.get("bid_vs_rival") is not None:
                    auc += f" · {cents(d['bid_vs_rival'])} vs rival"
            first = j == 0
            qty = f"{r['qty']:,}" if first else ""
            price = "" if not d or d["price"] is None else f"{d['price']:.2f}"
            arrived = r["arrival_tick"] if r["arrival_tick"] is not None else r["seen_tick"]
            arrived = arrived if (first and arrived is not None) else ""
            expires = r["expires"] if (first and r["expires"] is not None) else ""
            mid = f"{r['mid_arrival']:.2f}" if (first and r["mid_arrival"]) else ""
            edge = cents(d["edge"]) if d and d["edge"] is not None else ""
            est = cents(d["pps"]) if d and d["pps"] is not None else ""
            variant = (d or {}).get("variant") or ""
            cells = [r["tid"] if first else "", esc(r["kind"]) if first else "", r["action"] if first else "",
                     r["ticker"] if first else ""]
            rows.append(f'<tr class="{"hl" if hl else ""}">' + "".join(f"<td>{c}</td>" for c in cells)
                        + "".join(f"<td class=n>{c}</td>" for c in (qty, price, arrived, expires, mid, edge, est))
                        + f'<td>{esc(ans)}</td><td>{esc(auc)}</td><td class="muted">{esc(variant)}</td></tr>')
    return ('<div class="card scroll"><table>' + head + "".join(rows) + "</table></div>"
            '<p class="muted">Highlighted: declined while 10c+ through the mid, or accepted with the mid against us. '
            "Edge = how far the tender price was inside the mid when it arrived (per share, + = good for us). "
            "Bot est. = the bot's own profit/share after its exit costs.</p>")


def _replay_html(rep: dict, primary: bool) -> str:
    d = rep["decomposition"]["total"]
    items = [(PART_LABEL[k], d[k], {"edge": "q x (mid at booking - tender price), summed over tenders",
                                    "inventory": "what the random walk (and other desks) did to the inventory",
                                    "spread_maker": "resting fills vs the mid at fill time (negative = adverse fills)",
                                    "closeout": "end position x (last price - mid)"}.get(k, ""))
             for k in ("edge", "spread_maker", "spread_taker", "fees", "impact", "inventory", "closeout", "fines")]
    ex, inv, fi = rep["execution"], rep["inventory"], rep["fines"]
    exrows = "".join(
        f'<tr><td>{t}</td><td class=n>{e["maker_sh"]:,}</td><td class=n>{e["taker_sh"]:,}</td>'
        f'<td class=n>{e["adverse_sh"]:,}</td><td class=n>{money(e["maker_capture"], True)}</td>'
        f'<td class=n>{money(e["taker_cost"], True)}</td><td class=n>{money(-e["fees"], True)}</td>'
        f'<td class=n>{inv["end_pos"].get(t, 0):+,}</td></tr>' for t, e in ex.items())
    acc = [r for r in rep["tenders"] if r.get("booked_tick") is not None]
    trows = "".join(
        f'<tr><td>{r["tid"]}</td><td>{esc(r["kind"])}</td><td>{r["action"]} {r["ticker"]}</td><td class=n>{r["qty"]:,}</td>'
        f'<td class=n>{r["booked_price"]:.2f}</td><td class=n>{cents(r.get("pps_est"))}</td>'
        f'<td class=n>{cents(r.get("pnl_ps"))}</td><td class=n>{money(r.get("pnl"), True)}</td>'
        f'<td class=n>{r.get("via_maker", 0):,}</td><td class=n>{r.get("via_taker", 0):,}</td>'
        f'<td class=n>{r.get("via_tender", 0):,}</td><td class=n>{r.get("via_close", 0):,}</td></tr>' for r in acc)
    tiles = (f'<div class="tiles"><div class="tile"><div class="label">Score (NLV less fines)</div>'
             f'<div class="value">{money(rep["score"])}</div><div class="note">seed {rep["seed"]} · {esc(rep["scenario"])}</div></div>'
             f'<div class="tile"><div class="label">Tenders taken</div><div class="value">{len(acc)}</div>'
             f'<div class="note">of {len(rep["tenders"])} offered</div></div>'
             f'<div class="tile"><div class="label">Fines (lenient / strict reading)</div>'
             f'<div class="value">{money(fi["lenient"])} / {money(fi["strict"])}</div>'
             f'<div class="note">{fi["spec_shares"]:,} speculative · {fi["undecided_shares"]:,} in open windows</div></div>'
             f'<div class="tile"><div class="label">Held at the bell</div><div class="value">{inv["end_gross"]:,}</div>'
             f'<div class="note">shares · peak gross {inv["peak_gross"]:,} · peak 1-sd risk {money(inv["peak_risk"])}</div></div></div>')
    body = (tiles + "<h3>Where the P&amp;L came from</h3>" + bars(items)
            + '<div class="card scroll"><table><tr><th>Stock</th><th class=n>Resting fills</th><th class=n>Crossing fills</th>'
              '<th class=n>Adverse resting fills</th><th class=n>Resting vs mid</th><th class=n>Crossing vs mid</th>'
              '<th class=n>Fees</th><th class=n>Held at bell</th></tr>' + exrows + "</table></div>")
    if primary:
        body += "<h3>Position through the heat</h3>" + positions_chart(inv["series"])
        body += ('<h3>Accepted tenders: estimate vs realized</h3><div class="card scroll"><table><tr><th>Tender</th><th>Type</th>'
                 '<th>Side</th><th class=n>Size</th><th class=n>Price</th><th class=n>Est./share</th><th class=n>Realized/share</th>'
                 '<th class=n>Realized</th><th class=n>Out by resting</th><th class=n>Out by crossing</th>'
                 '<th class=n>Netted by tender</th><th class=n>Closed at bell</th></tr>' + trows + "</table></div>"
                 '<p class="muted">Realized = FIFO per stock: the tender\'s shares out through resting fills, crossing fills, a '
                 "later opposite tender, or the close-out at the last price.</p>")
    return body


def _cf_html(rep: dict) -> str:
    cf = rep.get("cf")
    if not cf:
        return ""
    by_tid = {r["tid"]: r for r in rep["tenders"]}
    cats: dict[str, list] = {}
    for c in cf:
        cats.setdefault(c["category"], []).append(c)
    rows = []
    for k in sorted(cats, key=lambda k: -sum(c["skill"] for c in cats[k])):
        xs = cats[k]
        rows.append(f'<tr><td>{esc(k)}</td><td class=n>{len(xs)}</td>'
                    f'<td class=n>{money(sum(c["skill"] for c in xs) / len(xs), True)}</td>'
                    f'<td class=n>{money(sum(c["luck"] for c in xs) / len(xs), True)}</td>'
                    f'<td class=n>{money(sum(c["delta"] for c in xs) / len(xs), True)}</td>'
                    f'<td class=n>{sum(1 for c in xs if c["skill"] > 0)}/{len(xs)}</td></tr>')
    top = sorted(cf, key=lambda c: -c["skill"])
    items = []
    for c in top[:8] + [c for c in top[-4:] if c not in top[:8]]:
        r = by_tid[c["tid"]]
        lab = f"#{c['tid']} {r['kind']} {r['action']} {r['ticker']} {r['qty'] // 1000}k ({'win' if c['mode'] == 'accept' and r['kind'] != 'private' else c['mode']})"
        items.append((lab, c["skill"], f"total {money(c['delta'], True)}, luck {money(c['luck'], True)}"))
    return ('<h3>What each decision was worth (flip it and replay)</h3>'
            '<p>Each tender\'s decision is flipped on its own and the heat replayed: accept a declined tender, win a lost '
            'auction at its hidden reserve (or 1c past the rival), or decline an accepted one. "Skill" is the change '
            'in every part of the P&amp;L except inventory moves; "luck" is what the random walk did to the extra or '
            'missing position. Positive skill = the bot left money there.</p>'
            '<div class="card scroll"><table><tr><th>Decision type</th><th class=n>n</th><th class=n>Avg skill</th>'
            '<th class=n>Avg luck</th><th class=n>Avg total</th><th class=n>Skill &gt; 0</th></tr>' + "".join(rows)
            + "</table></div>" + bars(items))


def _imps_html(ids: list[str], improvements: dict, evidence_extra: dict | None = None, anchors: bool = True) -> str:
    out = []
    for i in ids:
        m = improvements.get(i)
        if not m:
            continue
        ev = (evidence_extra or {}).get(i) or m.get("evidence") or ""
        aid = f' id="imp-{esc(i)}"' if anchors else ""
        out.append(f'<div class="imp"{aid}><div class="t"><b>{esc(m["title"])}</b></div>'
                   f'<div class="st">{esc(m["area"])} · {esc(m["status"])}</div><p>{esc(m["what"])}</p>'
                   + (f'<p class="muted">Evidence: {esc(ev)}</p>' if ev else "") + "</div>")
    return "".join(out)


EXPLAINER = """
<div class="card explain"><h2 style="margin-top:0">What this trading is, in plain words</h2>
<p><b>The game.</b> A 7-minute trading competition (Rotman's "Liquidity Risk Case") with three made-up shares:
CRZY (about $10), TAME ($25) and CROC ($20). Their prices wander up and down at random, so guessing direction is
pure luck, and the rules fine anyone who tries.</p>
<p><b>Where the money comes from: big "block" deals.</b> Every few seconds a large client offers a deal, for example
<i>"buy 30,000 CRZY from me at $9.90"</i> while the market price is $10.00. That is like buying stock at a discount
from someone in a hurry. The catch: we then have to sell those 30,000 shares back into the market, and selling that
many at once pushes the price down. A deal is only good if the discount is bigger than the cost of getting rid of the
shares. Deals come in three kinds: private offers (take it or leave it), auctions (we name our own price; the client
accepts any price better than a secret minimum) and winner-takes-all auctions (only the best price among all teams wins).</p>
<p><b>What the bot does.</b> For each offer it looks at how many shares other traders are willing to buy or sell at
each price (the "order book"), estimates what it would really get when unwinding, takes the deal only if a profit
remains, and then sells the shares off gradually and quietly instead of dumping them. Whatever is still held at the
end is closed automatically at the last market price.</p>
<p><b>The rules that can cost money.</b> Trading that is not unwinding a deal (betting on direction) and trading a
share while its offer is still being decided ("front-running") are fined 20-40 cents per share. Total holdings are
capped (250,000 shares gross, 100,000 net).</p>
<p><b>How to read the numbers.</b> <i>Score</i> = profit in dollars at the end of one 7-minute heat. <i>Worst</i> and
<i>losing heats</i> show the bad days, which matter as much as the average. <i>Seed</i> = one replay of a simulated
market; the same seed is the same market for every setting, so comparisons are fair. <i>Scenario</i> = a harder
version of the market (thinner trading, worse offers, rival teams manipulating prices, ...). <i>Template</i> = the
simple example program the organisers hand out, as a benchmark. <i>Edge</i> = how many cents per share the deal price
was better than the market price. Local = practice on our own simulator; live = the real competition server.</p>
</div>
"""


def run_section(m: dict) -> str:
    """One bot run of the combined report, collapsed under its title (id run-<stamp>)."""
    run = m["run"]
    gaps = [c for c in m["checks"] if c["status"] == "gap"]
    stamp = m["stamp"]
    when = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} {stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}" if len(stamp) == 15 else stamp
    variants = "; ".join(f"seed {k}: " + ", ".join(v["scenario"] for v in vs) for k, vs in m["market"]["variants"].items())
    title = f"Liability run {when} ({m['mode']})"
    acc = run["accepts"]
    covered = "-" if run["first_tick"] is None else f"{max(0, run['first_tick'])}-{min(420, run['last_tick'])}"
    speed = f" · {run['ticks_per_s']:.1f} ticks/s" if run["ticks_per_s"] else ""
    bots = f" · {run['bots']} bots in one file" if run["bots"] > 1 else ""
    orders = "dry run" if run["dry_run"] else "live orders"
    pnl = m["journal"]["final_pnl"] if m.get("journal") else None
    head = (f"<p class=sub>{esc(m['name'])} · bot {esc(run['version_label'])} · {orders}{bots}</p>"
            f"<p class=sub>{esc(m['mode'])}: {esc(m['mode_why'])}" + (f" · market: {esc(variants)}" if variants else "")
            + ' · <a href="#top">back to the overview</a></p>')
    tiles = (f'<div class="tiles"><div class="tile"><div class="label">Tenders in the log</div><div class="value">'
             f'{run["tenders"]}</div><div class="note">{run["decisions"]} decisions · {acc} accepts</div></div>'
             f'<div class="tile"><div class="label">Auction bids rejected at once</div><div class="value">{run["rejected_bids"]}</div>'
             f'<div class="note">competitive bids answered success=false</div></div>'
             f'<div class="tile"><div class="label">Orders sent</div><div class="value">{run["orders"]:,}</div>'
             f'<div class="note">{run["order_shares"]:,} shares (submissions, not fills)</div></div>'
             f'<div class="tile"><div class="label">Gaps found</div><div class="value">{len(gaps)}</div>'
             f'<div class="note">of {len(m["checks"])} checks</div></div>'
             f'<div class="tile"><div class="label">Ticks covered</div><div class="value">{covered}'
             f'</div><div class="note">{run["duration"]:.0f} s wall clock{speed}</div></div></div>')
    body = head + tiles
    body += "<h2>Gap checks against the brief</h2>" + _checks_html(m["checks"], m["improvements"])
    if m.get("journal"):
        body += journal_html(m["journal"])
    if m["ledger"]:
        reps = m["replays"]
        mids = []
        if m["market"]["seeds"]:
            from .simmatch import stream
            mids = stream(m["market"]["seeds"][0])["mids"]
        if mids:
            body += ("<h2>Tenders on the market they arrived in</h2>"
                     + price_panels(mids, [r for r in m["ledger"] if (r.get("stream") or (None,))[0] == m["market"]["seeds"][0]],
                                    f"seed {m['market']['seeds'][0]}, no bot"))
        dm = decision_map(m["ledger"])
        if dm:
            body += ("<h2>The bot's estimate vs the edge on arrival</h2><p>Every fixed-price decision. Right of the vertical "
                     "line the tender was inside the mid (good for us); above the horizontal line the bot expected to make "
                     "at least its 1c threshold after costs. Open circles bottom-right are declined tenders that were "
                     "well inside the mid: the bot's exit-cost estimate (walking a 2x book with fees) is what turned them "
                     "down.</p><div class=card>" + dm + "</div>")
        body += "<h2>Tender ledger (as logged, with the simulator's truth)</h2>" + _ledger_html(m["ledger"])
        if reps:
            body += ("<h2>The current bot on the same market</h2><p>The current bot (config/liability.toml, live, lock-step) "
                     "replayed on this run's simulator seed and market variant, fully instrumented. This is what the bot "
                     "would do today on this exact market; compare it with the logged run above.</p>")
            for i, rep in enumerate(reps):
                if len(reps) > 1:
                    body += f"<h3>Seed {rep['seed']} · {esc(rep['scenario'])}</h3>"
                body += _replay_html(rep, i == 0)
            body += _cf_html(reps[0])
    ids = []
    for c in m["checks"]:
        for i in c["improvements"]:
            if i not in ids:
                ids.append(i)
    for i in ("refill-3", "late-accept", "passive-placement"):
        if m["replays"] and i not in ids:
            ids.append(i)
    if ids:
        body += ("<h2>Improvements this run points to</h2><p>Trading changes only (no logging changes); full list with "
                 'evidence in the <a href="#improvements">improvement catalogue</a>.</p>'
                 + _imps_html(ids, m["improvements"], anchors=False))
    summary = (f"{money(pnl)} P&amp;L · " if pnl is not None else "") + f"{len(gaps)} gaps · {run['tenders']} tenders"
    return (f'<section id="run-{esc(stamp)}"><details><summary><h2 style="display:inline">{esc(title)}</h2> '
            f'<span class="muted">{summary}</span></summary>' + body + "</details></section>")


def ab_table(results: list[dict]) -> str:
    rows = "".join(f"<tr><td>{esc(r['change'])}</td><td>{esc(r['seeds'])}</td><td>{esc(r['base'])}</td>"
                   f"<td>{esc(r['hostile'])}</td><td>{esc(r['expiry'])}</td><td>{esc(r['other'])}</td>"
                   f"<td><b>{esc(r['verdict'])}</b></td></tr>" for r in results)
    return ("<h2>Candidate changes, tested</h2><p>Paired A/B on the simulator: every setting meets the same market "
            "(same seeds). Base = the brief's market; hostile = manipulators and crowded tenders, tenders priced off "
            "the visible mid; crowd at expiry = other desks dump the same blocks when each window closes (the known "
            "weak spot). t = paired t-statistic; the repo's rule keeps a change only with t >= 2, a holdout "
            "confirmation, and no worse tail.</p><div class=\"card scroll\"><table><tr><th>Change</th><th>Seeds</th>"
            "<th>Base</th><th>Hostile</th><th>Crowd at expiry</th><th>Other scenarios</th><th>Verdict</th></tr>"
            + rows + "</table></div>")


def overview(models: list[dict]) -> str:
    """Overview of every run in the combined report: runs table, checks by run, journals, pooled what-ifs, A/B."""
    rows = []
    for m in models:
        run = m["run"]
        g = sum(1 for c in m["checks"] if c["status"] == "gap")
        f = sum(1 for c in m["checks"] if c["status"] == "fixed")
        rep = m["replays"][0] if m["replays"] else None
        rows.append(f'<tr><td><a href="#run-{esc(m["stamp"])}">{esc(m["stamp"])}</a></td><td>{esc(m["mode"])}</td>'
                    f'<td>{esc(run["version"])}</td><td>{"dry" if run["dry_run"] else "live"}</td>'
                    f'<td class=n>{run["bots"]}</td><td>{esc(", ".join(map(str, m["market"]["seeds"])) or "-")}</td>'
                    f'<td class=n>{run["tenders"]}</td><td class=n>{run["accepts"]}</td><td class=n>{g}</td><td class=n>{f}</td>'
                    f'<td class=n>{money(rep["score"]) if rep else "-"}</td></tr>')
    order = ["coverage", "answered", "valuation", "auctions", "front-running", "speculation", "windows", "execution",
             "closeout", "limits", "robustness"]
    names = {"coverage": "Whole heat", "answered": "Answered", "valuation": "Tender value", "auctions": "Auctions",
             "front-running": "Front-running", "speculation": "Speculation", "windows": "Window fills (now)",
             "execution": "Execution",
             "closeout": "Bell", "limits": "Limits", "robustness": "Connection"}
    mrows = []
    for m in models:
        by = {c["id"]: c for c in m["checks"]}
        cells = "".join(f'<td title="{esc(by[k]["detail"])}">{pill(by[k]["status"])}</td>' if k in by else "<td></td>"
                        for k in order)
        mrows.append(f'<tr><td><a href="#run-{esc(m["stamp"])}">{esc(m["stamp"])}</a></td>{cells}</tr>')
    matrix = ("<h2>Checks by run</h2><p>Hover a cell for the finding; open the run for the evidence. \"Fixed since\" = the "
              "gap shows in that (older) log but the current bot no longer has it.</p><div class=\"card scroll\"><table>"
              "<tr><th>Run</th>" + "".join(f"<th>{esc(names[k])}</th>" for k in order) + "</tr>" + "".join(mrows)
              + "</table></div>")
    pooled, seen = {}, set()
    for m in models:
        rep = m["replays"][0] if m["replays"] else None
        if (not rep or not rep.get("cf") or rep["scenario"] != "base (brief)"      # luck vs skill is clean only here
                or (rep["seed"], rep["scenario"]) in seen):
            continue
        seen.add((rep["seed"], rep["scenario"]))
        for c in rep["cf"]:
            pooled.setdefault(c["category"], []).append(c)
    cf_html = ""
    if pooled:
        n_mk = len(seen)
        cf_rows = "".join(
            f'<tr><td>{esc(k)}</td><td class=n>{len(v)}</td><td class=n>{money(sum(c["skill"] for c in v) / len(v), True)}</td>'
            f'<td class=n>{money(sum(c["delta"] for c in v) / len(v), True)}</td>'
            f'<td class=n>{money(sum(c["skill"] for c in v) / n_mk, True)}</td>'
            f'<td class=n>{sum(1 for c in v if c["skill"] > 0)}/{len(v)}</td></tr>'
            for k, v in sorted(pooled.items(), key=lambda kv: -sum(c["skill"] for c in kv[1])))
        cf_html = ("<h2>What each decision type was worth (current bot, pooled)</h2><p>Every tender decision of the current "
                   f"bot on these runs' {n_mk} base-market seeds, flipped one at a time and replayed (see a run's report for "
                   "the method). Positive skill = money the decision rule leaves on the table, before the luck of the "
                   "random walk on the extra position.</p><div class=\"card scroll\"><table><tr><th>Decision type</th>"
                   "<th class=n>n</th><th class=n>Avg skill</th><th class=n>Avg total</th><th class=n>Skill per heat</th>"
                   "<th class=n>Skill &gt; 0</th></tr>" + cf_rows + "</table></div>")
    jm = [m for m in models if m.get("journal")]
    if jm:
        names_s = sorted({k for m in jm for k in m["journal"]["struggles"]})
        head = "".join(f"<th class=n>{esc(k)}</th>" for k in names_s)
        body_rows = "".join(
            f'<tr><td><a href="#run-{esc(m["stamp"])}">{esc(m["stamp"])}</a></td>'
            f'<td>{esc(m["mode"])}</td><td class=n>{money(m["journal"]["final_pnl"])}</td>'
            + "".join(f'<td class=n>{m["journal"]["struggles"].get(k, {}).get("count", "")}</td>' for k in names_s) + "</tr>"
            for m in jm)
        cf_html = ("<h2>Run journals: P&amp;L and struggles across runs</h2><p>Every run with a journal (live RIT runs "
                   "always have one). The same numbers are in this report's <code>.json</code> for the next "
                   'review.</p><div class="card scroll"><table><tr><th>Run</th><th>Mode</th><th class=n>P&amp;L</th>'
                   + head + "</tr>" + body_rows + "</table></div>") + cf_html
    from .findings import AB_RESULTS
    if not models:
        return ""
    return ('<h2>Runs</h2><div class="card scroll"><table><tr><th>Run</th><th>Mode</th><th>Bot</th><th>Orders</th>'
            "<th class=n>Bots</th><th>Seed</th><th class=n>Tenders</th><th class=n>Accepts</th><th class=n>Gaps</th>"
            "<th class=n>Fixed since</th><th class=n>Current bot on this market</th></tr>" + "".join(rows) + "</table></div>"
            + matrix + cf_html + ab_table(AB_RESULTS))


def catalogue(improvements: dict) -> str:
    return ('<h2 id="improvements">Improvement catalogue</h2>' + _imps_html(list(improvements), improvements))
