"""The checks of the suite, from the run folders a suite run left (plan 5.1-5.4).

Each check is a dict: id, section, title, status, summary, details. Statuses:
- pass, warn, fail: the verdict of the check;
- n/a: the check cannot run here (a missing capability, or a mode without the real game), with the reason;
- skipped: the budget or an earlier failure left no data;
- info: a measurement without a verdict (performance).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from .. import dbdiff
from . import exact, perf, series, stats

ORDER = ('fail', 'warn', 'pass', 'info', 'skipped', 'n/a')
SECTIONS = ('setup', 'health', 'trajectory', 'statistics', 'performance')


def check(id, title, status, summary, **details):
    return dict(id=id, section=id.split('.')[0], title=title, status=status, summary=summary, details=details)


@dataclass
class Context:
    out: Path
    settings: object
    caps: dict
    jobs: dict                      # name -> job dict (hostside.Job.to_dict) with status
    real: dict | None               # real/real.json
    db: str | None = None           # the host's game database
    fingerprint: object = None
    hazards: object = None
    cfg: object = None
    phases: list = field(default_factory=list)
    timeline: object = None
    comparisons: dict = field(default_factory=dict)   # filled here: name -> comparison dict, for the report
    statistics: dict = field(default_factory=dict)    # filled here: envelope, bias, reference, ensemble series

    @property
    def real_mode(self):
        """The real game was asked for and the session existed (a missing session makes the run incomplete)."""
        return bool(self.settings.real) and not (self.real or {}).get('unavailable')

    @property
    def no_real_reason(self):
        if not self.settings.real:
            return 'host-only mode: no real game'
        return 'the real-game session is not available (' + '; '.join((self.real or {}).get('errors') or []) + ')'

    def job(self, role):
        return next((j for j in self.jobs.values() if j.get('role') == role), None)

    def job_dir(self, role):
        j = self.job(role)
        return Path(j['runDir']) if j and j.get('runDir') and j.get('state') in ('completed', 'partial') else None

    def real_game(self, name):
        if not self.real:
            return None
        return next((g for g in self.real.get('games', []) if g.get('name') == name), None)

    def real_dir(self, name):
        g = self.real_game(name)
        return Path(g['dir']) if g and g.get('dir') and g.get('state') in ('completed', 'partial') else None


# ---- trajectory ----

def _compare(ctx, key, a, b, host_only):
    if a is None or b is None:
        return None, None
    method = exact.available(a, b, ctx.fingerprint, host_only=host_only)
    if method is None:
        return None, 'no comparison method: no digests in both games, no fingerprint lines' + ('' if not host_only else ', no checkpoints')
    r = exact.compare(a, b, method, ctx.settings.heal_turns, ctx.fingerprint, tuple(ctx.settings.ui_sections))
    # Turns, not states: a difference first seen at the start of turn t leaves t turns matched, and a game of H turns
    # has at most H to match (the fingerprint also has the state after the last turn, the digest does not).
    horizon = ctx.settings.standard_turns if key == 'standard' else ctx.settings.turns
    if horizon and r.get('compared') and r['compared'] > horizon:
        r['statesCompared'] = r['compared']
        r['compared'] = horizon
        r['matchedTurns'] = min(r['matchedTurns'], horizon)
    ctx.comparisons[key] = r
    return r, None


def _missing(ctx, role, what):
    j = ctx.job(role)
    if j is None:
        return 'n/a', f'{what} was not planned'
    if j.get('state') == 'skipped':
        return 'skipped', f"{what} was skipped ({j.get('error')})"
    if j.get('state') == 'failed':
        return 'fail', f"{what} failed: {j.get('error')}"
    return 'skipped', f'{what} has no run folder'


def reference_checks(ctx):
    """A/A, frame skew, heap perturbation and probe on/off: the host against itself (plan 5.2, C.5)."""
    out = []
    base = ctx.job_dir('base')
    specs = [('aa', 'trajectory.aa', 'Host A/A (same seeds twice)', 'reproduce itself'),
             ('skew', 'trajectory.frame-skew', f'Frame skew (--frame-skew {ctx.settings.frame_skew})', 'not depend on the engine frame count'),
             ('perturb', 'trajectory.heap-perturb', f'Heap perturbation (--heap-perturb {ctx.settings.heap_perturb})', 'not depend on memory addresses'),
             ('noprobe', 'trajectory.probe-neutral', 'Probes change nothing (with and without the Lua probes)', 'be the same without the probes')]
    aa = None
    for role, cid, title, meaning in specs:
        if ctx.job(role) is None and role == 'noprobe':
            out.append(check(cid, title, 'n/a', 'no Lua probes ran (the host has no --lua-probe), so there is nothing to switch off'))
            continue
        if base is None:
            status, why = _missing(ctx, 'base', 'the trajectory game')
            out.append(check(cid, title, 'skipped' if status != 'fail' else 'fail', why))
            continue
        other = ctx.job_dir(role)
        if other is None:
            status, why = _missing(ctx, role, f'the {role} game')
            out.append(check(cid, title, status, why))
            continue
        r, why = _compare(ctx, role, base, other, host_only=True)
        if r is None:
            out.append(check(cid, title, 'n/a', why))
            continue
        line = exact.summary(r, 'the two games')
        if role == 'aa':
            aa = r
            status = 'pass' if r['divergence'] is None else 'warn'
            summary = (f"the DLL reproduces itself for {r['matchedTurns']} of {r['compared']} turns" +
                       ('' if status == 'pass' else ' (the DLL or the host is not deterministic; exact checks stop there)'))
        else:
            limit = aa['matchedTurns'] if aa and aa['divergence'] else None
            worse = r['divergence'] is not None and (limit is None or r['matchedTurns'] < limit)
            if role == 'noprobe':
                status = 'fail' if worse else 'pass'
                sparse = ((ctx.job('noprobe') or {}).get('fingerprint') or {}).get('every')
                if sparse:  # no digests: dense fingerprints against sparse ones
                    summary = (f"the per-turn fingerprint probe changes the game from turn {r['divergence']['turn']}" if worse else
                               f"dense and sparse fingerprints (every {sparse} turns) agree at {r['compared']} sampled turns")
                else:
                    summary = ('the Lua probes change the game from turn %d' % r['divergence']['turn']) if worse else 'the probes change nothing'
            else:
                status = 'warn' if worse else 'pass'
                summary = (f"the game should {meaning}, but differs from turn {r['divergence']['turn']}" if worse
                           else f"no difference beyond the A/A's ({r['matchedTurns']} of {r['compared']} turns match)")
        if r['method'] == 'checkpoint':
            summary += ' (coarse: checkpoints only)'
        out.append(check(cid, title, status, summary, comparison=line, method=r['method'], matchedTurns=r['matchedTurns'],
                         compared=r['compared'], divergence=r['divergence'], transients=r['transients'], uiOnly=r['uiOnly']))
    out.append(reseed_check(ctx))
    return out


def reseed_check(ctx):
    """New seeds for a loaded game (docs/saves.md): one save played on with the same seeds twice, and with one seed
    changed each. The same seeds must repeat, and at least one seed must change the game; which one depends on the DLL."""
    cid, title = 'trajectory.reseed', 'New seeds for a loaded game'
    if ctx.job('reseed-a') is None:
        return check(cid, title, 'n/a', 'not planned: the host has no --reseed (an older civ5headless.exe), or reseed_turns is 0')
    for role in ('reseed-save', 'reseed-a', 'reseed-a2', 'reseed-map', 'reseed-sync'):
        if ctx.job_dir(role) is None:
            status, why = _missing(ctx, role, f'the {role} game')
            return check(cid, title, status, why)
    a = ctx.job_dir('reseed-a')
    r, why = _compare(ctx, 'reseed-aa', a, ctx.job_dir('reseed-a2'), host_only=True)
    if r is None:
        return check(cid, title, 'n/a', why)
    # same_game leaves out what holds the seeds and generator states: a seed that only changes those changes no game
    same = {seed: exact.same_game(a, ctx.job_dir(f'reseed-{seed}')) for seed in ('map', 'sync')}
    responds = [seed for seed in ('map', 'sync') if same[seed][0] is False]
    unknown = [seed for seed in ('map', 'sync') if same[seed][0] is None]
    repeats = r['divergence'] is None
    if unknown:
        status, summary = 'n/a', f"the games with another {' and '.join(unknown)} seed could not be compared"
    elif not responds:
        status, summary = 'warn', "neither seed changes this DLL's games from a save: an ensemble from one save would be copies"
    else:
        status = 'pass' if repeats else 'warn'
        summary = (f"this DLL's games from a save respond to the {' and the '.join(responds)} seed"
                   + (' only' if len(responds) == 1 else '') + '; ' +
                   (f"the same seeds repeat ({r['matchedTurns']} of {r['compared']} turns match)" if repeats
                    else f"but the same seeds differ from turn {r['divergence']['turn']}"))
    return check(cid, title, status, summary, responds=responds, comparison=exact.summary(r, 'the two games with the same seeds'),
                 method=r['method'], matchedTurns=r['matchedTurns'], compared=r['compared'], divergence=r['divergence'],
                 mapSeedChangesGame=same['map'][0] is False, syncSeedChangesGame=same['sync'][0] is False,
                 how=sorted({same[s][1] for s in same if same[s][1]}))


def _reproducible(ctx):
    """M: the shortest self-reproduction among the references (turns), and the parts."""
    parts = {key: ctx.comparisons[key]['matchedTurns'] for key in ('aa', 'skew', 'perturb', 'real-aa')
             if ctx.comparisons.get(key) is not None}
    return (min(parts.values()) if parts else None), parts


def real_trajectory_checks(ctx):
    out = []
    title = 'Real game against host (seeded Tiny game)'
    if not ctx.real_mode:
        return [check('trajectory.real', title, 'n/a', ctx.no_real_reason)]
    real_aa_dir, real_dir, base = ctx.real_dir('trajectory-aa'), ctx.real_dir('trajectory'), ctx.job_dir('base')
    if real_aa_dir is not None and real_dir is not None:
        r, why = _compare(ctx, 'real-aa', real_dir, real_aa_dir, host_only=False)
        if r is None:
            out.append(check('trajectory.real-aa', 'Real game A/A', 'n/a', why))
        else:
            out.append(check('trajectory.real-aa', 'Real game A/A', 'pass' if r['divergence'] is None else 'warn',
                             f"the real game reproduces itself for {r['matchedTurns']} of {r['compared']} turns",
                             comparison=exact.summary(r, 'the two real games'), method=r['method'], matchedTurns=r['matchedTurns'],
                             compared=r['compared'], divergence=r['divergence'], transients=r['transients'], uiOnly=r['uiOnly']))
    if real_dir is None:
        g = ctx.real_game('trajectory')
        why = (g or {}).get('error') or ('the real game did not run' if not ctx.real else 'no real trajectory game')
        out.append(check('trajectory.real', title, 'skipped' if (g or {}).get('state') == 'skipped' else 'fail' if g else 'skipped', why))
        return out
    if base is None:
        status, why = _missing(ctx, 'base', 'the host trajectory game')
        out.append(check('trajectory.real', title, status, why))
        return out
    r, why = _compare(ctx, 'real', real_dir, base, host_only=False)
    if r is None:
        out.append(check('trajectory.real', title, 'n/a', why))
        return out
    m, parts = _reproducible(ctx)
    n, h = r['matchedTurns'], r['compared']
    ok = r['divergence'] is None or (m is not None and n >= m)
    summary = f'the host matches the real game for {n} of {h} turns'
    if m is not None:
        summary += f'; the DLL reproduces itself for {m}'
    out.append(check('trajectory.real', title, 'pass' if ok else 'fail', summary, comparison=exact.summary(r, 'real and host'),
                     method=r['method'], matchedTurns=n, compared=h, reproducible=m, references=parts,
                     divergence=r['divergence'], transients=r['transients'], uiOnly=r['uiOnly']))
    sdir, shost = ctx.real_dir('standard'), ctx.job_dir('standard')
    if ctx.settings.standard_turns:
        if sdir is None or shost is None:
            out.append(check('trajectory.standard', 'Real game against host (Standard map)', 'skipped',
                             'the Standard game did not run on both sides'))
        else:
            r, why = _compare(ctx, 'standard', sdir, shost, host_only=False)
            if r is None:
                out.append(check('trajectory.standard', 'Real game against host (Standard map)', 'n/a', why))
            else:
                out.append(check('trajectory.standard', 'Real game against host (Standard map)',
                                 'pass' if r['divergence'] is None else 'warn',
                                 f"the host matches the real Standard game for {r['matchedTurns']} of {r['compared']} turns",
                                 comparison=exact.summary(r, 'real and host'), method=r['method'], divergence=r['divergence'],
                                 transients=r['transients']))
    return out


# ---- setup ----

def setup_checks(ctx):
    out = []
    names = [s.get('name') or f'setup{k + 1}' for k, s in enumerate(ctx.settings.setups)]
    rows, methods, failed = [], set(), False
    any_compared = False
    for name in names:
        a, b = ctx.job_dir(f'setup-{name}-a'), ctx.job_dir(f'setup-{name}-b')
        row = dict(setup=name)
        if a is not None and b is not None:
            r, why = _compare(ctx, f'setup-{name}-aa', a, b, host_only=True)
            if r:
                any_compared = True
                methods.add(r['method'])
                row['hostAA'] = 'identical' if r['identical'] else f"differs at turn {r['firstDifference']['turn']}: {', '.join(r['firstDifference']['sections'][:6])}"
                failed |= not r['identical']
            else:
                row['hostAA'] = why
        else:
            j = ctx.job(f'setup-{name}-a')
            row['hostAA'] = f"host run missing ({(j or {}).get('state')}: {(j or {}).get('error')})"
            failed |= bool(j and j.get('state') == 'failed')
        if ctx.real_mode:
            rd = ctx.real_dir(f'setup-{name}')
            if rd is not None and a is not None:
                r, why = _compare(ctx, f'setup-{name}', rd, a, host_only=False)
                if r:
                    any_compared = True
                    methods.add(r['method'])
                    first = r['firstDifference']
                    row['real'] = 'identical' if r['identical'] else f"differs at turn {first['turn']}: {', '.join(first['sections'][:8])}"
                    failed |= not r['identical']
                else:
                    row['real'] = why
            else:
                g = ctx.real_game(f'setup-{name}')
                row['real'] = f"real game missing ({(g or {}).get('state', 'not run')}{': ' + g['error'] if g and g.get('error') else ''})"
                failed |= bool(g and g.get('state') == 'failed')
        rows.append(row)
    title = 'Turn-0 state, seeded setups' + ('' if ctx.real_mode else ' (host A/A)')
    if not any_compared:
        out.append(check('setup.turn0', title, 'n/a', 'no setup could be compared', setups=rows))
    else:
        status = 'fail' if failed else 'pass'
        n_real = sum(1 for r in rows if r.get('real') == 'identical')
        summary = (f'{n_real} of {len(rows)} setups identical to the real game at turn 0' if ctx.real_mode else
                   f"{sum(1 for r in rows if r.get('hostAA') == 'identical')} of {len(rows)} setups reproduce themselves at turn 0")
        out.append(check('setup.turn0', title, status, summary, setups=rows, methods=sorted(methods)))
    out.append(pregame_check(ctx, names))
    out.append(database_check(ctx))
    return out


def pregame_check(ctx, names):
    title = 'PreGame values after setup'
    if not ctx.real_mode:
        return check('setup.pregame', title, 'n/a', ctx.no_real_reason)
    rows, compared, failed, api = [], 0, False, {'real': set(), 'host': set()}
    pairs = [(f'setup-{n}', f'setup-{n}-a') for n in names] + [('trajectory', 'base')]
    for real_name, role in pairs:
        rd, hd = ctx.real_dir(real_name), ctx.job_dir(role)
        if rd is None or hd is None:
            continue
        a, b = exact.pregame_values(rd), exact.pregame_values(hd)
        if not a or not b:
            continue
        compared += 1
        diff, only_real, only_host = exact.compare_values(a, b, ctx.settings.pregame_ignore)
        failed |= bool(diff)
        api['real'].update(k.split('.')[0] for k in only_real)
        api['host'].update(k.split('.')[0] for k in only_host)
        rows.append(dict(game=real_name, values=len(a), differing=[dict(key=k, real=x, host=y) for k, x, y in diff[:40]],
                         onlyReal=only_real[:40], onlyHost=only_host[:40]))
    if not compared:
        why = ('the host has no --lua-probe, so the PreGame probe cannot run in it' if not ctx.caps.get('luaProbe')
               else 'no game has the PreGame probe lines on both sides')
        return check('setup.pregame', title, 'n/a', why)
    # A getter only one side answers (missing, or nil) is a difference of the PreGame API, not of the game set up:
    # a warning that names them. A value both sides report that differs fails.
    summary = f"{sum(1 for r in rows if not r['differing'])} of {compared} games: every PreGame value both sides report equal"
    if api['real'] or api['host']:
        summary += ('; getters answered only by the real game: ' + (', '.join(sorted(api['real'])) or 'none')
                    + '; only by the host: ' + (', '.join(sorted(api['host'])) or 'none'))
    return check('setup.pregame', title, 'fail' if failed else 'warn' if api['real'] or api['host'] else 'pass', summary,
                 games=rows)


def database_check(ctx):
    title = "Database: host-built against the game's own"
    if not ctx.real_mode:
        return check('setup.database', title, 'n/a', ctx.no_real_reason)
    info = (ctx.real or {}).get('database') or {}
    if not info.get('path'):
        return check('setup.database', title, 'skipped' if not ctx.real else 'fail',
                     info.get('error') or 'the real game did not give its database')
    if not ctx.db:
        return check('setup.database', title, 'n/a', 'the host database is unknown')
    exported = info.get('method') == 'export'  # CREATE TABLE AS copies: no schema, indexes or triggers to compare
    try:
        d = dbdiff.diff(info['path'], ctx.db, data_only=exported)
    except Exception as error:  # noqa: BLE001
        return check('setup.database', title, 'fail', f'cannot compare: {type(error).__name__}: {error}')
    tables = [t['table'] for t in d['tables_differ']]
    summary = ('identical, table by table' + (' (data only: the game exported its database)' if exported else '')) if d['identical'] else (
        f"{len(tables)} table(s) differ" + (f": {', '.join(tables[:8])}" if tables else '') +
        (f"; tables only in the game's: {len(d['tables_only_reference'])}" if d['tables_only_reference'] else '') +
        (f"; only in the host's: {len(d['tables_only_candidate'])}" if d['tables_only_candidate'] else ''))
    return check('setup.database', title, 'pass' if d['identical'] else 'fail', summary,
                 game=info['path'], host=ctx.db, tablesDiffer=d['tables_differ'][:30],
                 onlyGame=d['tables_only_reference'], onlyHost=d['tables_only_candidate'], schemaDiffers=d.get('schema_differs'))


# ---- health ----

def _host_runs(ctx):
    return [Path(j['runDir']) for j in ctx.jobs.values() if j.get('runDir') and j.get('state') in ('completed', 'partial', 'failed')]


def _reports(ctx):
    out = []
    for d in _host_runs(ctx):
        r = series.read_json(d / 'host-report.json')
        if r:
            out.append((d, r))
    return out


def health_checks(ctx):
    out = []
    # Lua errors
    host_errors, real_errors = {}, {}
    for d in _host_runs(ctx):  # Lua.log on both sides, so the messages compare
        for msg, n in exact.lua_errors(d).items():
            host_errors.setdefault(msg, []).append(d.name)
    reported = {}
    for d, r in _reports(ctx):
        for e in ((r.get('luaErrors') or {}).get('list') or []):
            msg = f"{e.get('context')}: {Path(str(e.get('file') or '?')).name}:{e.get('line')}: {e.get('message')} ({e.get('phase')})"
            reported.setdefault(msg, []).append(d.name)
    if ctx.real:
        for g in ctx.real.get('games', []):
            if g.get('dir'):
                for msg in exact.lua_errors(Path(g['dir'])):
                    real_errors[msg] = True
    rows = [dict(message=m, runs=sorted(set(v))[:6], count=len(v)) for m, v in sorted(host_errors.items())]
    title = 'No Lua errors that only the host has'
    if ctx.real_mode and ctx.real:
        only = [r for r in rows if r['message'] not in real_errors]
        out.append(check('health.lua-errors', title, 'fail' if only else 'pass',
                         f'{len(only)} Lua error(s) only in the host' if only else
                         f"no host-only Lua errors ({len(rows)} in both)" if rows else 'no Lua errors',
                         hostOnly=only[:50], both=[r for r in rows if r['message'] in real_errors][:50], realErrors=len(real_errors),
                         hostReports=[dict(message=m, runs=sorted(set(v))[:6]) for m, v in sorted(reported.items())][:50]))
    else:
        out.append(check('health.lua-errors', title, 'warn' if rows else 'pass',
                         f'{len(rows)} Lua error message(s) in the host (no real game to tell which are host-only)' if rows else 'no Lua errors in the host',
                         hostErrors=rows[:50], hostReports=[dict(message=m, runs=sorted(set(v))[:6]) for m, v in sorted(reported.items())][:50]))
    out.append(civ_choice_check(ctx))
    # Stubbed engine calls
    reports = _reports(ctx)
    title = 'No stubbed engine calls outside the known-harmless list'
    if reports:
        unknown = {}
        for d, r in reports:
            for c in ((r.get('stubCalls') or {}).get('calls') or []):
                if not c.get('knownHarmless'):
                    u = unknown.setdefault(c.get('method'), dict(method=c.get('method'), count=0, runs=0, firstPhase=c.get('firstPhase')))
                    u['count'] += c.get('count') or 0
                    u['runs'] += 1
        rows = sorted(unknown.values(), key=lambda u: -u['count'])
        out.append(check('health.stubs', title, 'fail' if rows else 'pass',
                         f'{len(rows)} stubbed engine method(s) called that are not known to be harmless' if rows else
                         f'every stubbed call is on the known-harmless list ({len(reports)} host reports)', unknown=rows[:60]))
    else:
        stubs = {}
        for d in _host_runs(ctx):
            r = series.read_json(d / 'newgame-report.json') or {}
            for c in r.get('engine_calls') or []:
                if c.get('stub'):
                    stubs[c.get('method')] = stubs.get(c.get('method'), 0) + (c.get('count') or 0)
        out.append(check('health.stubs', title, 'n/a',
                         'the host writes no host-report.json (an older civ5headless.exe), so harmlessness is unknown'
                         + (f'; {len(stubs)} stubbed method(s) were called' if stubs else ''),
                         stubbed=[dict(method=m, count=n) for m, n in sorted(stubs.items(), key=lambda x: -x[1])][:60]))
    # UI hazards
    title = 'UI hazard scan clean, or every hazard covered by a profile'
    findings, notes = [], []
    report_path = next((d / 'host-report.json' for d, _ in reports if d.name == (ctx.job('base') or {}).get('name')),
                       reports[0][0] / 'host-report.json' if reports else None)
    if ctx.hazards is not None and report_path is not None:
        try:
            hits = _hazard_scan(ctx.hazards, report_path)
            findings = [dict(file=h.get('file'), line=h.get('line'), call=h.get('call'), kind=h.get('kind'),
                             function=h.get('function'), why=h.get('why'), triggers=h.get('triggers') or [],
                             reach=h.get('reach') or 'game',
                             review=(h.get('review') or {}).get('why')) for h in hits if not h.get('covered')]
            findings.sort(key=lambda f: f['reach'] == 'input')
            notes.append(f'hazard scan of the UI files of {report_path.parent.name}: {len(hits)} call(s), '
                         f'{sum(1 for h in hits if h.get("covered"))} covered (profiles, reviews, the DLL, add-ins the host runs)')
        except Exception as error:  # noqa: BLE001
            notes.append(f'hazard scan failed: {type(error).__name__}: {error}')
    unknown_files = {}
    for d, r in reports:
        ui = r.get('uiFiles') or {}
        for f in ui.get('unknown') or []:
            key = f.get('path') if isinstance(f, dict) else str(f)
            unknown_files[key] = f
    if ctx.hazards is None and not reports:
        out.append(check('health.ui-hazards', title, 'n/a', 'no hazard scan (civ5headless.hazards) and no host-report.json'))
    else:
        game = [f for f in findings if f['reach'] != 'input']
        status = 'warn' if game or unknown_files else 'pass'
        summary = []
        if game:
            summary.append(f'{len(game)} state-changing UI call(s) without a profile that a game can reach')
        if findings and not game:
            summary.append('no uncovered state-changing UI call that a game can reach')
        if len(findings) > len(game):
            summary.append(f'{len(findings) - len(game)} {"more " if game else ""}reached only through player input '
                           '(clicks, buttons, tooltips), which an autoplayed game does not send')
        if unknown_files:
            summary.append(f'{len(unknown_files)} UI file(s) not known to any profile')
        out.append(check('health.ui-hazards', title, status, '; '.join(summary) or 'clean', findings=findings[:80],
                         gameReachable=len(game), inputOnly=len(findings) - len(game),
                         unknownUiFiles=list(unknown_files.values())[:60], notes=notes,
                         scanned=ctx.hazards is not None))
    return out


def civ_choice_check(ctx):
    """Who chose each compared host game's civilizations and city-states (host-report.json dll.hostChoseCivs and
    minorCivs). A DLL that leaves random choices to the exe (the stock DLL) gets them from the host, which cannot draw
    what the exe drew: the specs name the major civilizations, and the host games compared with a real game get its
    city-states (pins.py). Real mode: fail when the host drew anything for a compared game, or when a compared game's
    city-states differ from the real game's (allMatch false); host-only: warn when the host drew anything."""
    title = 'Civilizations and city-states named, or chosen by the DLL; the real game\'s in compared games'
    reports = {d.name: r for d, r in _reports(ctx)}
    if not reports:
        return check('setup.civ-choice', title, 'n/a', 'no host-report.json (an older civ5headless.exe)')
    compared = ([j for j in ctx.jobs.values() if j.get('kind') in ('setup', 'trajectory', 'reference', 'standard')]
                if ctx.real_mode else list(ctx.jobs.values()))
    rows, drew, mismatch, unpinned, others = [], [], [], [], []
    for j in compared:
        r = reports.get(j['name'])
        if r is None:
            continue
        mc = r.get('minorCivs') or {}
        slots = mc.get('slots') or []
        by = {}
        for x in slots:
            by[x.get('chosenBy')] = by.get(x.get('chosenBy'), 0) + 1
        host = bool((r.get('dll') or {}).get('hostChoseCivs')) or by.get('host', 0) > 0
        row = dict(game=j['name'], chosenBy=mc.get('chosenBy'), slots=by, hostDrew=host, pinned=j.get('pinned'),
                   pinNote=j.get('pinNote'), allMatch=mc.get('allMatch'),
                   differing=[dict(slot=x.get('slot'), type=x.get('type'), pinned=x.get('pinnedType'))
                              for x in slots if x.get('match') is False][:12])
        rows.append(row)
        if host:
            drew.append(j['name'])
        if ctx.real_mode and j.get('pinned') and mc.get('allMatch') is False and not host:
            mismatch.append(j['name'])
        if ctx.real_mode and not j.get('pinned'):
            unpinned.append(j['name'])
    if ctx.real_mode:
        others = sorted(n for n, r in reports.items() if (r.get('dll') or {}).get('hostChoseCivs')
                        and n not in {j['name'] for j in compared})
    if not rows:
        return check('setup.civ-choice', title, 'n/a', 'no host-report.json for the compared host games')
    parts = []
    if drew:
        what = 'the real game chooses others, so they differ from the start' if ctx.real_mode else \
            'a real game would choose others; a real-game comparison pins them from the real game'
        parts.append(f'the host drew civilizations or city-states at random in {len(drew)} of {len(rows)} '
                     f"{'compared ' if ctx.real_mode else ''}game(s) (this DLL leaves the choice to the exe): {what}")
    if mismatch:
        parts.append(f"the DLL chose other city-states than the real game in {len(mismatch)} compared game(s): "
                     + ', '.join(mismatch[:6]))
    if not parts:
        dll = sum(1 for r in rows if r['chosenBy'] == 'dll')
        spec = sum(1 for r in rows if (r['slots'] or {}).get('spec'))
        pinned = [r for r in rows if r['pinned']]
        text = f'no draw by the host in {len(rows)} ' + ('compared game(s)' if ctx.real_mode else 'game(s)')
        if pinned and ctx.real_mode:
            text += (f"; {len(pinned)} pinned to the real game's city-states, "
                     + ('the DLL chose the same ones' if dll else 'the host applied them'))
        elif spec:
            text += f'; the specs named the city-states and the host applied them ({spec} game(s))'
            if dll:
                text += f'; the DLL chose them in {dll}'
        elif dll:
            text += '; the DLL chose the city-states'
        parts.append(text)
    if unpinned:
        parts.append(f'{len(unpinned)} compared game(s) played without the real game\'s city-states '
                     f"({next((r['pinNote'] for r in rows if r['game'] in unpinned and r.get('pinNote')), 'see details')})")
    if others:
        parts.append(f'the host drew them in {len(others)} other game(s) (the ensemble: not compared game by game)')
    status = ('fail' if ctx.real_mode else 'warn') if (drew or mismatch) else 'pass'
    return check('setup.civ-choice', title, status, '; '.join(parts), games=rows[:40], hostDrew=drew[:20],
                 differ=mismatch[:20], unpinned=unpinned[:20], otherHostDraws=len(others))


def _hazard_scan(hazards, report_path):
    """The hits of civ5headless.hazards over the UI files a host run loaded, as `hazards scan --report` finds them
    (state-writing calls; lazy caches left out), each with reach and covered/coveredBy/reason (hazards.scan_report)."""
    return hazards.scan_report(report_path)


# ---- statistics ----

def statistics_checks(ctx):
    s = ctx.settings
    out = []
    ensemble_jobs = [j for j in ctx.jobs.values() if j.get('kind') == 'ensemble']
    done = [j for j in ensemble_jobs if j.get('state') in ('completed', 'partial')]
    failed = [j for j in ensemble_jobs if j.get('state') == 'failed']
    skipped = [j for j in ensemble_jobs if j.get('state') == 'skipped']
    early = [j for j in done if (j.get('endTurn') or 0) < s.turns and (j.get('status') or {}).get('endReason') == 'victory']
    status = 'fail' if failed else 'warn' if len(done) < s.min_members else 'pass'
    summary = f'{len(done)} of {len(ensemble_jobs)} ensemble games played'
    if skipped:
        summary += f'; {len(skipped)} trimmed by the budget'
    if failed:
        summary += f'; {len(failed)} failed'
    if early:
        summary += f'; {len(early)} ended in victory before turn {s.turns}'
    vary = ctx.statistics.get('ensembleVary') or {}
    what = 'other map seeds' if vary.get('vary') == 'map' else 'the same map, other sync seeds'
    summary += f"; {what}" + (f" ({vary['reason']})" if vary.get('reason') and vary.get('vary') == 'map' else '')
    out.append(check('statistics.ensemble', 'Host ensemble played', status, summary,
                     failed=[dict(name=j['name'], error=j.get('error')) for j in failed], skipped=len(skipped),
                     victories=len(early), vary=vary.get('vary'), varyReason=vary.get('reason'),
                     seeds=[j.get('seeds') for j in done]))
    ens = [series.game_series(Path(j['runDir']), ctx.fingerprint, s.aggregates, s.exclude_metrics) for j in done]
    ens = [g for g in ens if g]
    if ctx.real_mode:
        ref_dir = ctx.real_dir('trajectory')
        ref_name = 'the real game'
    else:
        ref_dir = ctx.job_dir('base')
        ref_name = 'the host trajectory game (self-test: same seeds as the real game would have)'
    ref = series.game_series(ref_dir, ctx.fingerprint, s.aggregates, s.exclude_metrics) if ref_dir else {}
    # The host twin: the host's trajectory game, same seeds as the real game. A game can be atypical for its seeds
    # (a conquest on this map); the twin shows that, so the real game is judged against the ensemble and its twin.
    twin_dir = ctx.job_dir('base') if ctx.real_mode else None
    twin = series.game_series(twin_dir, ctx.fingerprint, s.aggregates, s.exclude_metrics) if twin_dir else {}
    ctx.statistics.update(reference=ref, twin=twin, ensemble=ens, referenceName=ref_name)
    title_e = 'Envelope: the real game inside the host ensemble' if ctx.real_mode else 'Envelope self-test: a host game inside the ensemble'
    title_b = 'Bias: no metric on one side of the ensemble median' if ctx.real_mode else 'Bias self-test: no metric on one side of the median'
    if not ref:
        why = f'no per-turn series for {ref_name}' + ('' if ref_dir else ' (it did not run)')
        out.append(check('statistics.envelope', title_e, 'skipped' if not ref_dir else 'n/a', why))
        out.append(check('statistics.bias', title_b, 'skipped' if not ref_dir else 'n/a', why))
        return out
    selftest_note = ('host-only mode: the host trajectory game against host games is a self-test of the ensemble and '
                     'the statistics, no verdict on fidelity (an atypical seed shows here); ')
    lo, hi = s.envelope_band
    em = stats.envelope_model(ens, lo, hi, s.min_members, s.stats_from_turn, s.turns)
    env = stats.envelope_score(em, ref, s.envelope_target, s.envelope_alpha, s.envelope_margin)
    env_twin = stats.envelope_score(em, twin, s.envelope_target, s.envelope_alpha, s.envelope_margin) if twin else None
    ctx.statistics['envelope'] = env
    ctx.statistics['envelopeTwin'] = env_twin
    if env['status'] == 'n/a':
        out.append(check('statistics.envelope', title_e, 'n/a', env['reason']))
    else:
        worst = sorted(((n, v) for n, v in env['series'].items() if v['coverage'] is not None), key=lambda x: x[1]['coverage'])[:6]
        summary = (f"{env['coverage']:.1%} of {env['points']} points inside the {lo:.1%}-{hi:.1%} band "
                   f"(threshold {env['threshold']:.1%}; ensemble games get {env['expected']:.1%} on average, {env['members']} games)")
        status = env['status']
        twin_cov = (env_twin or {}).get('coverage')
        if twin_cov is not None:
            summary += f'; the host game with the same seeds: {twin_cov:.1%}'
            if status != 'pass' and env['coverage'] >= twin_cov - s.envelope_margin:
                status = 'pass'
                summary += ' (an atypical game for these seeds, on both sides: no difference between host and real game)'
        if not ctx.real_mode:
            status, summary = 'info', selftest_note + f"would be {env['status']}: " + summary
        out.append(check('statistics.envelope', title_e, status, summary, coverage=env['coverage'], threshold=env['threshold'],
                         expected=env['expected'], pValue=env['pValue'], members=env['members'], points=env['points'],
                         twinCoverage=twin_cov, selfTestStatus=None if ctx.real_mode else env['status'],
                         worst=[dict(series=n, label=series.label(n), coverage=v['coverage'], outsideTurns=v['outsideTurns'][:20]) for n, v in worst]))
    bm = stats.bias_model(ens, s.bias_alpha, s.bias_z_cap, s.min_members, s.stats_from_turn, s.turns)
    b = stats.bias_score(bm, ref, s.bias_one_sided)
    b_twin = stats.bias_score(bm, twin, s.bias_one_sided) if twin else None
    ctx.statistics['bias'] = b
    ctx.statistics['biasTwin'] = b_twin
    if b['status'] == 'n/a':
        out.append(check('statistics.bias', title_b, 'n/a', b['reason']))
    else:
        twin_series = (b_twin or {}).get('series') or {}
        both = [n for n in b['flagged'] if twin_series.get(n, {}).get('flagged') and twin_series[n]['side'] == b['series'][n]['side']]
        real_only = [n for n in b['flagged'] if n not in both]
        flagged = [dict(series=n, label=series.label(n), side=b['series'][n]['side'], signFraction=b['series'][n]['signFraction'],
                        T=b['series'][n]['T'], alsoTwin=n in both) for n in b['flagged']]
        shifts = sorted((v['detectableShift'], n) for n, v in b['series'].items() if v['detectableShift'] is not None)
        median_shift = shifts[len(shifts) // 2][0] if shifts else None
        shown = [f for f in flagged if not f['alsoTwin']]
        summary = (f"{len(shown)} series flagged: " + ', '.join(f"{f['label']} ({f['side']} the median on {f['signFraction']:.0%} of turns)" for f in shown[:4])
                   if shown else f"no series flagged ({len(b['series'])} series; global p = {b['pValue']:.2f})")
        if both:
            summary += (f"; {len(both)} more flagged for the host game with the same seeds too (atypical for these seeds, not a "
                        f"host difference): {', '.join(series.label(n) for n in both[:4])}")
        if median_shift is not None:
            summary += f'; detects shifts of about {median_shift:.0%} in a typical series'
        status = 'warn' if real_only else 'pass'
        if not ctx.real_mode:
            status, summary = 'info', selftest_note + summary
        elif b['members'] < s.bias_min_members:
            status = 'info'
            summary = (f"{b['members']} ensemble games are too few for a calibrated verdict (at least {s.bias_min_members}): "
                       + summary)
        out.append(check('statistics.bias', title_b, status, summary, flagged=flagged, threshold=b['threshold'],
                         pValue=b['pValue'], series=len(b['series']),
                         mostSensitive=[dict(series=n, label=series.label(n), shift=v) for v, n in shifts[:6]],
                         medianDetectableShift=median_shift))
    return out


# ---- performance ----

def host_timing_job(ctx, role='base'):
    j = ctx.job(role)
    return j if j and j.get('state') in ('completed', 'partial') else None


def performance_checks(ctx, history=None, entry=None):
    out = []
    samples = ctx.statistics.get('loadSamples')
    base = host_timing_job(ctx)
    host = perf.host_figures(base) if base else None
    real = perf.real_figures(ctx.real, 'trajectory', samples) if ctx.real_mode else None
    ctx.statistics['performance'] = dict(host=host, real=real)
    title = 'Speed and memory: real game against host' if ctx.real_mode else 'Speed and memory of the host'
    if host is None:
        out.append(check('performance.speed', title, 'skipped', 'the host trajectory game did not run'))
    else:
        rows = _speed_rows(host, real)
        hs = (host.get('perTurn') or {}).get('all')
        rs = ((real or {}).get('perTurn') or {}).get('all')
        if rs and hs:
            summary = (f"host {hs:.2f} s/turn against real {rs:.2f} s/turn: {rs / hs:.1f}x faster; peak working set "
                       f"{host.get('peakWorkingSetMB')} MB against {real.get('peakWorkingSetMB')} MB")
        elif hs:
            summary = f"host {hs:.2f} s/turn over {host.get('turns')} turns, peak working set {host.get('peakWorkingSetMB')} MB"
        else:
            summary = 'no turn times'
        if host.get('meanConcurrency') and host['meanConcurrency'] > 1.5:
            summary += f" (about {host['meanConcurrency']:.0f} host games ran beside it)"
        out.append(check('performance.speed', title, 'info', summary, rows=rows, host=_strip(host), real=_strip(real)))
    rows = perf.throughput(ctx.phases, samples)
    if rows:
        best = max(rows, key=lambda r: r['turnsPerHour'])
        out.append(check('performance.throughput', 'Host throughput by workers', 'info',
                         ', '.join(f"{r['workers']} worker(s): {r['gamesPerHour']:.0f} games/h, {r['turnsPerHour']:,} turns/h" for r in rows)
                         + f"; best at {best['workers']} workers", rows=rows))
    else:
        out.append(check('performance.throughput', 'Host throughput by workers', 'skipped', 'no throughput phase completed'))
    if entry is not None:
        reg = perf.regression(history or [], entry, ctx.settings.regression_threshold)
        title_h = 'History: host speed against recent runs'
        if reg is None:
            out.append(check('performance.history', title_h, 'n/a', 'no earlier run of this setup and mode on this machine'))
        else:
            summary = (f"host {reg['current']:.3f} s/turn against {reg['baseline']:.3f} (median of the last {reg['runs']} runs): "
                       f"{reg['change']:+.0%}" + (f"; changed since the previous run: {', '.join(reg['changed'])}" if reg['changed'] else ''))
            out.append(check('performance.history', title_h, reg['status'], summary,
                             **{k: v for k, v in reg.items() if k != 'status'}))
    if ctx.settings.standard_turns:
        title_s = f'Standard map, {ctx.settings.standard_turns} turns: scale and memory'
        sj = host_timing_job(ctx, 'standard')
        sh = perf.host_figures(sj) if sj else None
        sr = perf.real_figures(ctx.real, 'standard', samples) if ctx.real_mode else None
        if sh is None:
            out.append(check('performance.standard', title_s, 'skipped', 'the host Standard game did not run'))
        else:
            hs = (sh.get('perTurn') or {}).get('all')
            rs = ((sr or {}).get('perTurn') or {}).get('all')
            summary = f"host {hs:.2f} s/turn, peak {sh.get('peakWorkingSetMB')} MB" if hs else 'no turn times'
            if rs and hs:
                summary += f"; real {rs:.2f} s/turn, peak {sr.get('peakWorkingSetMB')} MB: {rs / hs:.1f}x"
            out.append(check('performance.standard', title_s, 'info', summary, rows=_speed_rows(sh, sr), host=_strip(sh), real=_strip(sr)))
    return out


def _strip(fig):
    return {k: v for k, v in (fig or {}).items() if k != 'curve'} or None


def _speed_rows(host, real):
    """[{what, real, host, ratio}] for the speed table."""
    rows = []
    hp, rp = host.get('perTurn') or {}, (real or {}).get('perTurn') or {}
    ranges = hp.get('ranges') or rp.get('ranges') or []
    for k, name in enumerate(('early', 'mid', 'late')):
        label = f'seconds per turn, {name}' + (f' (turns {ranges[k][0]}-{ranges[k][1]})' if k < len(ranges) else '')
        rows.append(_row(label, rp.get(name), hp.get(name)))
    rows.append(_row('seconds per turn, all', rp.get('all'), hp.get('all')))
    rows.append(_row('launch to first turn, s', (real or {}).get('launchToFirstTurn'), host.get('launchToFirstTurn')))
    rows.append(_row('peak working set, MB', (real or {}).get('peakWorkingSetMB'), host.get('peakWorkingSetMB')))
    rows.append(_row('peak private bytes, MB', (real or {}).get('peakPrivateMB'), host.get('peakPrivateMB')))
    rows.append(_row('CPU seconds', (real or {}).get('cpuSeconds'), host.get('cpuSeconds')))
    return rows


def _row(what, real, host):
    ratio = real / host if isinstance(real, (int, float)) and isinstance(host, (int, float)) and host else None
    return dict(what=what, real=real, host=host, ratio=round(ratio, 2) if ratio else None)


def victory_check(ctx):
    jobs = [j for j in ctx.jobs.values() if j.get('kind') == 'victory']
    if not jobs:
        return None
    done = [j for j in jobs if j.get('state') == 'completed']
    won = [j for j in done if (j.get('status') or {}).get('endReason') == 'victory']
    failed = [j for j in jobs if j.get('state') == 'failed']
    status = 'fail' if failed else 'pass' if done and len(won) == len(done) else 'warn' if done else 'skipped'
    turns = sorted(j.get('endTurn') or 0 for j in won)
    summary = f'{len(won)} of {len(jobs)} Tiny games reached a victory' + (f' (turns {turns[0]}-{turns[-1]})' if turns else '')
    if failed:
        summary += f'; {len(failed)} failed'
    return check('health.victory-games', 'Host games to victory', status, summary,
                 games=[dict(name=j['name'], state=j.get('state'), endTurn=j.get('endTurn'),
                             victory=(j.get('status') or {}).get('victory'), winner=(j.get('status') or {}).get('winner'),
                             error=j.get('error')) for j in jobs])


# ---- verdict ----

def verdict(checks, essential_missing=(), strict=False):
    """(verdict, exit code): fail 1, incomplete 3, pass 0 (warnings count as failures with strict)."""
    statuses = [c['status'] for c in checks]
    if 'fail' in statuses:
        return 'fail', 1
    if essential_missing:
        return 'incomplete', 3
    if 'warn' in statuses:
        return 'warn', 1 if strict else 0
    return 'pass', 0
