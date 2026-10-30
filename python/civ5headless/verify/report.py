"""The suite's report as one self-contained HTML page: inline CSS and SVG, no scripts, no external files.

html(doc) renders verify.json's dict: the verdict and its headline, the checks by section, the trajectory
comparisons, the envelope charts per metric, the performance tables, the load timeline and the history trend.
"""
from __future__ import annotations
import json

from ..html import CSS, STATUS_TEXT, badge, fmt, h, line_svg, pct  # noqa: F401 (shared with the run reports)
from ..html import path_d as _path, scale as _scale, short as _short, ticks as _ticks

SECTION_TITLES = {'setup': 'Exact setup', 'health': 'Health', 'trajectory': 'Trajectory', 'statistics': 'Statistical fidelity',
                  'performance': 'Performance'}


# ---- SVG ----

def envelope_svg(c, width=300, height=150):
    turns = c['turns']
    if not turns:
        return ''
    vals = [v for k in ('lo', 'hi', 'reference', 'median') for v in c[k] if v is not None]
    y0, y1 = min(vals), max(vals)
    if y0 == y1:
        y0, y1 = y0 - 1, y1 + 1
    pad = (y1 - y0) * 0.06
    y0, y1 = y0 - pad, y1 + pad
    left, right, top, bottom = 38, width - 6, 6, height - 18
    X = _scale(turns[0], turns[-1], left, right)
    Y = _scale(y0, y1, bottom, top)
    band = [(X(t), Y(v)) for t, v in zip(turns, c['hi'])] + [(X(t), Y(v)) for t, v in reversed(list(zip(turns, c['lo'])))]
    med = [(X(t), Y(v)) for t, v in zip(turns, c['median'])]
    ref = [(X(t), Y(v)) for t, v in zip(turns, c['reference']) if v is not None]
    twin = [(X(t), Y(v)) for t, v in zip(turns, c.get('twin') or []) if v is not None]
    out_pts = [(X(t), Y(v)) for t, v, a, b in zip(turns, c['reference'], c['lo'], c['hi'])
               if v is not None and not (a - 1e-9 <= v <= b + 1e-9)]
    ticks = ''.join(f'<text x="{left - 4}" y="{Y(v) + 3:.1f}" text-anchor="end" font-size="9" fill="var(--muted)">{_short(v)}</text>'
                    f'<line x1="{left}" x2="{right}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--line)" stroke-width="0.5"/>'
                    for v in _ticks(y0, y1))
    xt = ''.join(f'<text x="{X(t):.1f}" y="{height - 5}" text-anchor="middle" font-size="9" fill="var(--muted)">{t}</text>'
                 for t in _ticks(turns[0], turns[-1], 4, integer=True))
    dots = ''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="var(--ref)"/>' for x, y in out_pts[:200])
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{h(c["label"])}">{ticks}{xt}'
            f'<path d="{_path(band)}Z" fill="var(--band)" opacity="0.55"/>'
            f'<path d="{_path(med)}" fill="none" stroke="var(--median)" stroke-width="1" stroke-dasharray="3 2"/>'
            + (f'<path d="{_path(twin)}" fill="none" stroke="var(--info)" stroke-width="1.2" stroke-dasharray="1 2"/>' if twin else '')
            + f'<path d="{_path(ref)}" fill="none" stroke="var(--ref)" stroke-width="1.4"/>{dots}</svg>')


def trajectory_svg(rows, width=760):
    """One strip per comparison: matched turns, transients, the lasting difference."""
    if not rows:
        return ''
    horizon = max((r['compared'] or 0) + (r.get('firstTurn') or 0) for _, r in rows) or 1
    rh, left = 22, 150
    height = rh * len(rows) + 22
    X = _scale(0, horizon, left, width - 8)
    out = []
    for k, (name, r) in enumerate(rows):
        y = 6 + k * rh
        out.append(f'<text x="{left - 6}" y="{y + 12}" text-anchor="end" font-size="11" fill="var(--fg)">{h(name)}</text>')
        first = r.get('firstTurn') or 0
        end = first + (r['compared'] or 0)
        div = (r.get('divergence') or {}).get('turn')
        ok_end = div if div is not None else end
        out.append(f'<rect x="{X(first):.1f}" y="{y}" width="{max(X(ok_end) - X(first), 0):.1f}" height="14" fill="var(--ok)" opacity="0.75"/>')
        if div is not None:
            out.append(f'<rect x="{X(div):.1f}" y="{y}" width="{max(X(end) - X(div), 1):.1f}" height="14" fill="var(--bad)" opacity="0.75"/>')
        for t in r.get('transients') or []:
            out.append(f'<rect x="{X(t["turn"]):.1f}" y="{y - 2}" width="{max(X(t["until"]) - X(t["turn"]), 2):.1f}" height="18" fill="var(--warn)"/>')
    for t in _ticks(0, horizon, 6, integer=True):
        out.append(f'<text x="{X(t):.1f}" y="{height - 4}" text-anchor="middle" font-size="10" fill="var(--muted)">{t}</text>')
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="trajectory comparisons">{"".join(out)}</svg>'


# ---- page ----

def _details(d):
    items = {k: v for k, v in (d or {}).items() if v not in (None, [], {}, '')}
    if not items:
        return ''
    return f'<details><summary>details</summary><pre class="mono">{h(json.dumps(items, indent=1, default=str)[:20000])}</pre></details>'


def html(doc):
    v = doc.get('verdict')
    inputs = doc.get('inputs') or {}
    machine = doc.get('machine') or {}
    parts = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             f'<title>c5h verify report</title><style>{CSS}</style></head><body><main>']
    parts.append(f'<h1>c5h verify: {h(doc.get("mode"))}</h1><div class="muted">{h(doc.get("utc"))} &middot; {h(doc.get("run"))}</div>')
    secs = doc.get('seconds')
    parts.append(f'<div class="verdict">{badge(v)}<div><div class="big">{h(doc.get("headline"))}</div>'
                 f'<div class="muted">exit code {h(doc.get("exitCode"))} &middot; {fmt((secs or 0) / 60, 1)} of {fmt(doc.get("budgetSeconds", 0) / 60, 0)} minutes'
                 + (f' &middot; missing: {h(", ".join(doc["essentialMissing"]))}' if doc.get('essentialMissing') else '') + '</div></div></div>')
    dll, exe = inputs.get('dll') or {}, inputs.get('exe') or {}
    mods = ', '.join(m.get('name') or m.get('id') for m in inputs.get('mods') or []) or 'none'
    parts.append('<div class="kv">'
                 f'<div class="muted">DLL</div><div class="mono">{h(dll.get("path"))} ({h((dll.get("sha256") or "")[:8])})</div>'
                 f'<div class="muted">Mods</div><div>{h(mods)}</div>'
                 f'<div class="muted">Game</div><div>{h(inputs.get("setup"))}</div>'
                 f'<div class="muted">Host</div><div class="mono">{h(exe.get("path"))} ({h((exe.get("sha256") or "")[:8])})</div>'
                 f'<div class="muted">Machine</div><div>{h(machine.get("cpu"))}, {h(machine.get("logicalCPUs"))} logical CPUs</div></div>')
    for note in doc.get('notes') or []:
        parts.append(f'<p class="muted">Note: {h(note)}</p>')
    # Checks
    parts.append('<h2>Checks</h2>')
    by = {}
    for c in doc.get('checks') or []:
        by.setdefault(c['section'], []).append(c)
    for section in ('setup', 'health', 'trajectory', 'statistics', 'performance'):
        cs = by.get(section)
        if not cs:
            continue
        parts.append(f'<h3>{h(SECTION_TITLES[section])}</h3><table><tbody>')
        for c in cs:
            parts.append(f'<tr><td style="width:96px">{badge(c["status"])}</td><td style="width:32%"><b>{h(c["title"])}</b>'
                         f'<div class="muted mono">{h(c["id"])}</div></td><td>{h(c["summary"])}{_details(c.get("details"))}</td></tr>')
        parts.append('</tbody></table>')
    # Trajectory
    comps = doc.get('comparisons') or {}
    names = {'real': 'real / host', 'real-aa': 'real A/A', 'aa': 'host A/A', 'skew': 'frame skew', 'perturb': 'heap perturbation',
             'noprobe': 'probe off', 'reseed-aa': 'a save, the same new seeds', 'standard': 'real / host Standard'}
    rows = [(names[k], comps[k]) for k in names if comps.get(k)]
    if rows:
        parts.append('<h2>Trajectory</h2><div class="legend"><span><i class="sw" style="background:var(--ok)"></i>identical</span>'
                     '<span><i class="sw" style="background:var(--warn)"></i>transient (heals within '
                     f'{h(rows[0][1].get("healTurns"))} turns)</span><span><i class="sw" style="background:var(--bad)"></i>lasting difference</span></div>')
        parts.append(f'<div class="scroll">{trajectory_svg(rows)}</div><table><thead><tr><th>comparison</th><th>method</th>'
                     '<th class="num">matched</th><th class="num">compared</th><th>lasting difference</th><th>transients</th><th>UI-only</th></tr></thead><tbody>')
        for name, r in rows:
            d = r.get('divergence')
            lasting = 'turn %s: %s' % (d['turn'], ', '.join(d['sections'][:8])) if d else 'none'
            transients = ', '.join('T%s-%s' % (t['turn'], t['until']) for t in r.get('transients') or []) or 'none'
            ui = ', '.join('T%s' % u['turn'] for u in (r.get('uiOnly') or [])[:10]) or 'none'
            parts.append(f'<tr><td>{h(name)}</td><td>{h(r.get("method"))}</td><td class="num">{fmt(r.get("matchedTurns"))}</td>'
                         f'<td class="num">{fmt(r.get("compared"))}</td><td>{h(lasting)}</td><td>{h(transients)}</td><td>{h(ui)}</td></tr>')
        parts.append('</tbody></table>')
    # Statistics
    st = doc.get('statistics') or {}
    charts = st.get('charts') or []
    if charts:
        env, b = st.get('envelope') or {}, st.get('bias') or {}
        parts.append('<h2>Statistical fidelity</h2>')
        vary = (st.get('vary') or {}).get('vary')
        what = 'other map seeds (other maps of the same script and size)' if vary == 'map' else 'the same map seed and other sync seeds'
        parts.append(f'<p>Reference: {h(st.get("reference"))}; ensemble: {h(st.get("members"))} host games with {h(what)}. '
                     f'Envelope: {pct(env.get("coverage"))} of {fmt(env.get("points"))} points inside the '
                     f'{pct((env.get("band") or [None])[0])}-{pct((env.get("band") or [None, None])[1])} band; threshold {pct(env.get("threshold"))} '
                     f'(ensemble games get {pct(env.get("expected"))} on average). Bias: {len(b.get("flagged") or [])} series flagged '
                     f'(max-T threshold {fmt(b.get("threshold"))}).</p>')
        parts.append('<div class="legend"><span><i class="sw" style="background:var(--band)"></i>ensemble band</span>'
                     '<span><i class="sw" style="background:var(--median)"></i>ensemble median</span>'
                     '<span><i class="sw" style="background:var(--ref)"></i>reference (dots: outside the band)</span>'
                     + ('<span><i class="sw" style="background:var(--info)"></i>host game with the same seeds</span>'
                        if st.get('twinCoverage') is not None else '') + '</div>')
        flagged = set(b.get('flagged') or [])
        ordered = sorted(charts, key=lambda c: (c['series'] not in flagged, c['coverage'] if c['coverage'] is not None else 2, c['series']))
        parts.append('<div class="grid">')
        for c in ordered:
            bias = c.get('bias') or {}
            flag = ' <span class="badge warn">bias</span>' if c['series'] in flagged else ''
            side = f" &middot; {bias.get('side')} median {bias.get('signFraction', 0):.0%}" if bias else ''
            parts.append(f'<div class="chart"><div class="t"><b>{h(c["label"])}</b><span class="muted">{pct(c["coverage"])}{side}{flag}</span></div>'
                         f'{envelope_svg(c)}</div>')
        parts.append('</div>')
    stt = doc.get('selfTest')
    if stt and stt.get('summary'):
        sm = stt['summary']
        parts.append('<h3>Statistics self-test</h3>'
                     f'<p>{sm["holdouts"]} held-out ensemble games: envelope pass {sm["envelopePass"]}, warn {sm["envelopeWarn"]}, '
                     f'fail {sm["envelopeFail"]}; bias quiet {sm["biasQuiet"]}. Shifting one metric by {stt["shift"]:.0%}: the bias check '
                     f'flags {sm["shiftDetected"]} of {sm["metrics"]} metrics. Foreign games caught: {sm["foreignCaught"]} of {sm["foreign"]}.</p>')
        parts.append('<table><thead><tr><th>metric</th><th class="num">bias flags</th><th class="num">envelope objects</th>'
                     '<th class="num">detectable shift</th></tr></thead><tbody>')
        for m, r in sorted(stt['shifts'].items(), key=lambda x: (x[1]['medianDetectableShift'] or 9)):
            parts.append(f'<tr><td>{h(r["label"])}</td><td class="num">{r["biasFlagged"]}/{r["tries"]}</td>'
                         f'<td class="num">{r["envelopeObjects"]}/{r["tries"]}</td><td class="num">{pct(r["medianDetectableShift"])}</td></tr>')
        parts.append('</tbody></table>')
    # Performance
    perf = doc.get('performance') or {}
    speed = next((c for c in doc.get('checks') or [] if c['id'] == 'performance.speed'), None)
    parts.append('<h2>Performance</h2>')
    if speed and speed['details'].get('rows'):
        parts.append('<table><thead><tr><th></th><th class="num">real game</th><th class="num">host</th><th class="num">real / host</th></tr></thead><tbody>')
        for r in speed['details']['rows']:
            parts.append(f'<tr><td>{h(r["what"])}</td><td class="num">{fmt(r["real"])}</td><td class="num">{fmt(r["host"])}</td>'
                         f'<td class="num">{fmt(r["ratio"])}</td></tr>')
        parts.append('</tbody></table>')
        real = perf.get('real') or {}
        if real.get('load'):
            parts.append('<p class="muted">Machine load during the real game (mean busy cores of '
                         f'{h(machine.get("logicalCPUs"))}; the real game\'s own; host games running): '
                         + '; '.join(f"{k}: {fmt((l or {}).get('busyCores'), 1)}, {fmt((l or {}).get('gameBusyCores'), 1)}, {fmt((l or {}).get('hosts'), 1)}"
                                     for k, l in real['load'].items()) + '</p>')
        host = perf.get('host') or {}
        curves = []
        if host.get('curve'):
            curves.append(('host', 'var(--bar)', [(t, s) for t, s in host['curve']], False))
        if real.get('curve'):
            curves.append(('real game', 'var(--ref)', [(t, s) for t, s in real['curve']], False))
        if curves:
            parts.append('<h3>Autoplay seconds by turn (cumulative)</h3><div class="legend">' +
                         ''.join(f'<span><i class="sw" style="background:{c}"></i>{h(n)}</span>' for n, c, _, _ in curves) +
                         f'</div><div class="chart">{line_svg(curves, xlabel="turn")}</div>')
    tp = perf.get('throughput') or []
    if tp:
        parts.append('<h3>Host throughput</h3><table><thead><tr><th>phase</th><th class="num">workers</th><th class="num">games</th>'
                     '<th class="num">turns/game</th><th class="num">seconds</th><th class="num">games/hour</th><th class="num">turns/hour</th>'
                     '<th class="num">busy cores</th><th class="num">real game cores</th></tr></thead><tbody>')
        for r in tp:
            load = r.get('load') or {}
            parts.append(f'<tr><td>{h(r["phase"])}</td><td class="num">{r["workers"]}</td><td class="num">{r["games"]}</td>'
                         f'<td class="num">{r["turnsPerGame"]}</td><td class="num">{fmt(r["seconds"], 0)}</td><td class="num">{fmt(r["gamesPerHour"], 1)}</td>'
                         f'<td class="num">{fmt(r["turnsPerHour"])}</td><td class="num">{fmt(load.get("busyCores"), 1)}</td>'
                         f'<td class="num">{fmt(load.get("gameBusyCores"), 1)}</td></tr>')
        parts.append('</tbody></table>')
    samples = perf.get('loadSamples') or []
    if samples:
        parts.append('<h3>Machine load over the run</h3><div class="legend"><span><i class="sw" style="background:var(--bar)"></i>busy cores</span>'
                     '<span><i class="sw" style="background:var(--ref)"></i>real game cores</span><span><i class="sw" style="background:var(--bar2)"></i>host games running</span></div>')
        parts.append('<div class="chart">' + line_svg([
            ('busy', 'var(--bar)', [(s['t'], s['busyCores']) for s in samples], False),
            ('game', 'var(--ref)', [(s['t'], s['gameBusyCores']) for s in samples], False),
            ('hosts', 'var(--bar2)', [(s['t'], s['hosts']) for s in samples], True)], xlabel='seconds') + '</div>')
    hist = doc.get('history') or []
    if len(hist) > 1:
        hs = [(k, ((e.get('host') or {}).get('perTurn') or {}).get('all')) for k, e in enumerate(hist)]
        rs = [(k, ((e.get('real') or {}).get('perTurn') or {}).get('all')) for k, e in enumerate(hist)]
        curves = [('host', 'var(--bar)', [p for p in hs if p[1] is not None], False)]
        if any(p[1] is not None for p in rs):
            curves.append(('real', 'var(--ref)', [p for p in rs if p[1] is not None], False))
        parts.append('<h3>History on this machine (same game setup): seconds per turn by run</h3><div class="legend">'
                     '<span><i class="sw" style="background:var(--bar)"></i>host</span><span><i class="sw" style="background:var(--ref)"></i>real game</span></div>'
                     f'<div class="chart">{line_svg(curves, xlabel="run")}</div>')
        parts.append('<table><thead><tr><th>run</th><th>date</th><th>mode</th><th>DLL</th><th>mods</th><th>host</th>'
                     '<th class="num">host s/turn</th><th class="num">real s/turn</th><th>verdict</th></tr></thead><tbody>')
        prev = None
        for k, e in enumerate(hist):
            changed = lambda key: ' style="font-weight:600"' if prev and prev.get(key) != e.get(key) else ''
            parts.append(f'<tr><td class="num">{k}</td><td>{h((e.get("utc") or "")[:16])}</td><td>{h(e.get("mode"))}</td>'
                         f'<td class="mono"{changed("dllSHA256")}>{h((e.get("dllSHA256") or "")[:8])}</td>'
                         f'<td class="mono"{changed("modsKey")}>{h((e.get("modsKey") or "")[:8])}</td>'
                         f'<td class="mono"{changed("exeSHA256")}>{h((e.get("exeSHA256") or "")[:8])}</td>'
                         f'<td class="num">{fmt(hs[k][1], 3)}</td><td class="num">{fmt(rs[k][1], 3)}</td><td>{badge(e.get("verdict")) if e.get("verdict") else "this run"}</td></tr>')
            prev = e
        parts.append('</tbody></table>')
    # Runs
    parts.append('<h2>Runs</h2><table><thead><tr><th>phase</th><th class="num">workers</th><th class="num">games</th>'
                 '<th class="num">seconds</th></tr></thead><tbody>')
    for p in doc.get('phases') or []:
        parts.append(f'<tr><td>{h(p["name"])}</td><td class="num">{p["workers"]}</td><td class="num">{p.get("completed")}/{len(p.get("jobs") or [])}</td>'
                     f'<td class="num">{fmt(p.get("seconds"), 0)}</td></tr>')
    pinning = any(j.get('pin') for j in doc.get('jobs') or [])
    parts.append('</tbody></table><details><summary>every host game</summary><table><thead><tr><th>game</th><th>kind</th><th>state</th>'
                 '<th class="num">turns</th><th class="num">seconds</th><th class="num">estimate</th>'
                 + ('<th>city-states</th>' if pinning else '') + '<th>error</th></tr></thead><tbody>')
    for j in doc.get('jobs') or []:
        secs = (j['finished'] - j['started']) if j.get('finished') is not None and j.get('started') is not None else None
        cs = ''
        if pinning:
            cs = (f"the real game's ({len(j['pinned'])}; waited {fmt(j.get('pinWait'), 0)} s)" if j.get('pinned') else
                  j.get('pinNote') or ('' if j.get('pin') else 'not pinned'))
            cs = f'<td>{h(cs)}</td>'
        parts.append(f'<tr><td class="mono">{h(j["name"])}</td><td>{h(j.get("kind"))}</td><td>{h(j.get("state"))}</td>'
                     f'<td class="num">{fmt(j.get("endTurn"))}</td><td class="num">{fmt(secs, 0)}</td><td class="num">{fmt(j.get("estimate"), 0)}</td>'
                     f'{cs}<td>{h(j.get("error"))}</td></tr>')
    parts.append('</tbody></table></details>')
    real = doc.get('real')
    if real:
        parts.append('<h3>Real game session</h3><table><thead><tr><th>game</th><th>state</th><th class="num">turns</th>'
                     '<th class="num">start s</th><th class="num">autoplay s</th><th>error</th></tr></thead><tbody>')
        for g in real.get('games') or []:
            parts.append(f'<tr><td>{h(g.get("name"))}</td><td>{h(g.get("state"))}</td><td class="num">{fmt(g.get("endTurn"))}</td>'
                         f'<td class="num">{fmt((g.get("start") or {}).get("seconds"), 1)}</td><td class="num">{fmt(g.get("autoplaySeconds"), 1)}</td>'
                         f'<td>{h(g.get("error"))}</td></tr>')
        parts.append('</tbody></table>')
        for e in real.get('errors') or []:
            parts.append(f'<p class="muted">Real game: {h(e)}</p>')
    caps = doc.get('capabilities') or {}
    parts.append('<h2>Capabilities</h2><div class="kv">'
                 f'<div class="muted">host --lua-probe</div><div>{h("yes" if caps.get("luaProbe") else "no")}</div>'
                 f'<div class="muted">Lua fingerprint</div><div>{h("yes" if caps.get("fingerprint") else "no")}</div>'
                 f'<div class="muted">real-game session</div><div>{h("yes" if caps.get("realGameSession") else "no")}</div>'
                 f'<div class="muted">UI hazard scan</div><div>{h("yes" if caps.get("hazards") else "no")}</div></div>')
    for k, why in (caps.get('notes') or {}).items():
        parts.append(f'<div class="muted">{h(k)}: {h(why)}</div>')
    parts.append('<p class="muted">docs/verification.md explains each check and what it can and cannot tell.</p></main></body></html>')
    return ''.join(parts)
