"""Lua fingerprints (probes/fingerprint.lua): reading and comparing the per-turn state hashes the probe prints.

  python -m civ5headless.fingerprint compare A B [--heal-turns 2] [--json OUT]
  python -m civ5headless.fingerprint show A [--metrics]

A and B are Lua.log files, run folders (Logs/Lua.log) or Logs folders. The probe prints the same lines in the real
game (injected through FireTuner) and in the host (--lua-probe), so any DLL can be compared, digest additions or not.

Line format (version 1; found anywhere in a line, whatever the log puts before it):
  C5HFP|v=1|meta|turn=T|plots=N|width=W|height=H|maxMajors=M|maxCivs=C|every=K
  C5HFP|v=1|player|turn=T|player=ID|team=T|civ=CIVILIZATION_X|leader=LEADER_Y        (or minor=MINOR_CIV_Z)
  C5HFP|v=1|turn=T|point=P|part=I/N|total=XXXXXXXX|game=XXXXXXXX|map=...|p00=...|t00=...|d00=...
  C5HM|v=1|turn=T|player=ID|alive=1|score=..|gold=..|cities=..|population=..|techs=..|policies=..|units=..|...
  C5HFP|v=1|turn=T|point=P|error=MESSAGE
A meta line starts a game; a log with several games (a real-game session that started more than one) is read as
the last one unless read(path, game=N) says otherwise.

  read(path, game=-1) -> Fingerprint
    .sections  {turn: {section: hash}}            (total, game, map, players, techs, diplomacy, pNN, tNN, dNN)
    .metrics   {turn: {player: {name: float}}}    (major civs)
    .meta      {name: str}                        (the meta line: turn, plots, width, height, maxMajors, ...)
    .players   {player: {name: str}}              (team, civ, leader or minor)
    .points    {turn: point}, .errors {turn: message}, .incomplete [turns missing a part], .games, .path
  compare(a, b, heal_turns=2) -> dict (paths or Fingerprints), shaped like the digest comparison:
    identical, common, onlyA, onlyB, lastTurnA, lastTurnB, first {turn, point, sections, metrics}, differing
    {turn: [sections]}, runs [{turn, point, until, sections}], transients, divergence, lastPointIdentical, healTurns,
    errors {A: [turns], B: [turns]}
  summary(result) -> one line
  probe_path() -> Path of fingerprint.lua (the repository's probes/, else the package's data/)
"""
import argparse
import json
import sys
from pathlib import Path

from .compare import mixing

VERSION = 1
FP = 'C5HFP|'
METRIC = 'C5HM|'
HEAD = {'v', 'turn', 'point', 'part', 'error'}
AGGREGATE = ('total', 'players', 'techs', 'diplomacy')


class Fingerprint:
    """One game's fingerprint lines from a Lua.log."""

    def __init__(self, path=None):
        self.path = path
        self.sections = {}
        self.metrics = {}
        self.meta = {}
        self.players = {}
        self.points = {}
        self.errors = {}
        self.incomplete = []
        self.games = 0
        self.version = None
        self._parts = {}

    def turns(self):
        return sorted(self.sections)

    def metric(self, name, player=None):
        """{turn: {player: value}} of one metric, or {turn: value} for one player."""
        out = {}
        for turn in sorted(self.metrics):
            per = {p: m[name] for p, m in self.metrics[turn].items() if name in m}
            if player is None:
                out[turn] = per
            elif player in per:
                out[turn] = per[player]
        return out


def _fields(text):
    """('C5HFP', {key: value}, [flags], [key order]) of a line's fingerprint part."""
    parts = text.rstrip('\r\n').split('|')
    fields, flags, order = {}, [], []
    for part in parts[1:]:
        if '=' in part:
            k, v = part.split('=', 1)
            fields[k] = v
            order.append(k)
        elif part:
            flags.append(part)
    return parts[0], fields, flags, order


def probe_path():
    """The fingerprint probe (fingerprint.lua): probes/ of the repository this module runs from, else the package's
    data/ (an installed package). Raises FileNotFoundError when neither has it."""
    here = Path(__file__).resolve().parent
    candidates = (here.parents[1] / 'probes' / 'fingerprint.lua', here / 'data' / 'fingerprint.lua')
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError('fingerprint.lua not found in ' + ' or '.join(str(p.parent) for p in candidates))


def log_path(run):
    """A Lua.log, or the one in a run folder (Logs/) or a Logs folder."""
    p = Path(run)
    if p.is_file():
        return p
    for candidate in (p / 'Logs' / 'Lua.log', p / 'Lua.log'):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f'no Lua.log in {p}')


def games(path):
    """Every game's Fingerprint in a Lua.log, in log order (lines before the first meta line form a game too)."""
    out = []
    fp = None
    for line in log_path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        i = line.find(FP)
        kind = FP
        if i < 0:
            i = line.find(METRIC)
            kind = METRIC
            if i < 0:
                continue
        _, f, flags, order = _fields(line[i:])
        if 'meta' in flags or fp is None:
            fp = Fingerprint(str(path))
            out.append(fp)
        if fp.version is None and 'v' in f:
            fp.version = int(f['v'])
        try:
            turn = int(f['turn'])
        except (KeyError, ValueError):
            continue
        if kind == METRIC:
            try:
                player = int(f['player'])
            except (KeyError, ValueError):
                continue
            values = {}
            for k in order:
                if k in ('v', 'turn', 'player'):
                    continue
                try:
                    values[k] = float(f[k])
                except ValueError:
                    pass
            fp.metrics.setdefault(turn, {})[player] = values
        elif 'meta' in flags:
            fp.meta = dict(f)
        elif 'player' in flags:
            try:
                fp.players[int(f['player'])] = {k: v for k, v in f.items() if k not in ('v', 'turn', 'player')}
            except ValueError:
                pass
        elif 'error' in f:
            fp.errors[turn] = f['error']
            fp.points[turn] = f.get('point')
        else:
            if turn not in fp._parts or f.get('part', '1/1').split('/')[0] == '1':
                # A turn printed again (a reload) replaces the earlier lines.
                fp.sections[turn] = {}
                fp._parts[turn] = (set(), f.get('part', '1/1').split('/')[-1])
            fp.points[turn] = f.get('point')
            fp._parts[turn][0].add(f.get('part', '1/1').split('/')[0])
            for k in order:
                if k not in HEAD:
                    fp.sections[turn][k] = f[k]
    for fp in out:
        fp.games = len(out)
        fp.incomplete = sorted(t for t, (seen, n) in fp._parts.items() if len(seen) != int(n or 1))
    return out


def read(path, game=-1):
    """The Fingerprint of one game in a Lua.log (default: the last one; empty when the log has none)."""
    found = games(path)
    if not found:
        fp = Fingerprint(str(path))
        return fp
    return found[game]


def _ordered(names):
    """Sections in report order: the per-player, per-team and map/game ones, then the aggregates."""
    return [n for n in names if n not in AGGREGATE] + [n for n in AGGREGATE if n in names]


def differing_sections(sa, sb, partial=False):
    """Section names whose hashes differ; with partial (a turn missing a part), only names both sides have."""
    names = list(sa) + [n for n in sb if n not in sa]
    if partial:
        names = [n for n in names if n in sa and n in sb]
    return _ordered([n for n in names if sa.get(n) != sb.get(n)])


def metric_differences(a, b, turn):
    """{player: {metric: [A, B]}} of the metrics that differ at a turn."""
    ma, mb = a.metrics.get(turn, {}), b.metrics.get(turn, {})
    out = {}
    for player in sorted(set(ma) | set(mb)):
        pa, pb = ma.get(player, {}), mb.get(player, {})
        diff = {k: [pa.get(k), pb.get(k)] for k in list(pa) + [k for k in pb if k not in pa] if pa.get(k) != pb.get(k)}
        if diff:
            out[player] = diff
    return out


def compare(a, b, heal_turns=2):
    """Compare two fingerprints turn by turn over their common turns (see the module docstring for the result)."""
    fa = a if isinstance(a, Fingerprint) else read(a)
    fb = b if isinstance(b, Fingerprint) else read(b)
    turns = sorted(set(fa.sections) & set(fb.sections))
    differing, runs, open_run = {}, [], None
    for turn in turns:
        partial = turn in fa.incomplete or turn in fb.incomplete
        diff = differing_sections(fa.sections[turn], fb.sections[turn], partial)
        if diff:
            differing[turn] = diff
            if open_run is None:
                open_run = dict(turn=turn, point=fa.points.get(turn), until=None, sections=[])
            open_run['sections'] += [n for n in diff if n not in open_run['sections']]
        elif open_run is not None:
            open_run['until'] = turn
            runs.append(open_run)
            open_run = None
    if open_run is not None:
        runs.append(open_run)
    for r in runs:
        r['sections'] = _ordered(r['sections'])
    transients = [r for r in runs if r['until'] is not None and r['until'] - r['turn'] <= heal_turns]
    divergence = next((r for r in runs if r not in transients), None)
    first = None
    if runs:
        t = runs[0]['turn']
        first = dict(turn=t, point=runs[0]['point'], sections=differing[t], metrics=metric_differences(fa, fb, t))
    points = [t for t in turns if fa.points.get(t) != fb.points.get(t)]
    return dict(identical=not runs, common=len(turns), onlyA=len(set(fa.sections) - set(fb.sections)),
                onlyB=len(set(fb.sections) - set(fa.sections)), lastTurnA=max(fa.sections, default=None),
                lastTurnB=max(fb.sections, default=None), first=first, differing=differing, runs=runs,
                transients=transients, divergence=divergence,
                lastPointIdentical=bool(turns) and (not runs or runs[-1]['until'] is not None), healTurns=heal_turns,
                errors=dict(A=sorted(fa.errors), B=sorted(fb.errors)), pointMismatch=points,
                incomplete=dict(A=fa.incomplete, B=fb.incomplete))


def summary(r):
    """One line for a compare() result."""
    if r['identical']:
        text = (f"identical on {r['common']} common turns (only in A: {r['onlyA']}, only in B: {r['onlyB']})"
                + (f"; last turns {r['lastTurnA']}/{r['lastTurnB']}" if r['lastTurnA'] != r['lastTurnB'] else ''))
    else:
        f = r['first']
        text = f"first difference: turn {f['turn']}: sections {', '.join(f['sections'][:12])}"
        if r['divergence']:
            d = r['divergence']
            if d is not r['runs'][0]:
                text += f"; diverges at turn {d['turn']}: {', '.join(d['sections'][:12])}"
        else:
            text += f" (transient: equal again at turn {r['runs'][0]['until']})"
        if r['transients']:
            text += f"; {len(r['transients'])} transient(s) healed within {r['healTurns']} turns"
        text += f"; {len(r['differing'])} differing turns of {r['common']}"
    if r['errors']['A'] or r['errors']['B']:
        text += f"; probe errors on turns A {r['errors']['A'][:5]} B {r['errors']['B'][:5]}"
    if r['common'] == 0:
        text += ' (no common turns)'
    return text


def cmd_compare(a, b, heal_turns=2, json_out=None):
    r = compare(a, b, heal_turns)
    r['aiLogging'], warning = mixing.check(a, b)
    print(summary(r))
    if r['first'] and r['first']['metrics']:
        for player, diff in r['first']['metrics'].items():
            print(f"  turn {r['first']['turn']} player {player}: "
                  + ', '.join(f'{k} {_num(v[0])} / {_num(v[1])}' for k, v in diff.items()))
    if warning:
        print(warning)
    if json_out:
        Path(json_out).write_text(json.dumps(r, indent=1) + '\n', encoding='utf-8')
    return 0 if r['identical'] and r['common'] else 1


def _num(v):
    return '-' if v is None else ('%d' % v if v == int(v) else '%g' % v)


def cmd_show(path, metrics=False):
    fp = read(path)
    turns = fp.turns()
    print(f'{fp.path}: {fp.games} game(s) in the log; {len(turns)} fingerprinted turns'
          + (f' ({turns[0]}..{turns[-1]})' if turns else ''))
    if fp.meta:
        print('meta: ' + ', '.join(f'{k}={v}' for k, v in fp.meta.items() if k != 'v'))
    for player, info in sorted(fp.players.items()):
        print(f'  player {player}: ' + ', '.join(f'{k}={v}' for k, v in info.items()))
    if fp.errors:
        print(f'probe errors: {fp.errors}')
    if fp.incomplete:
        print(f'turns missing a line part: {fp.incomplete}')
    if metrics and turns:
        last = max(fp.metrics, default=None)
        if last is not None:
            print(f'metrics at turn {last}:')
            for player, m in sorted(fp.metrics[last].items()):
                print(f'  player {player}: ' + ', '.join(f'{k} {_num(v)}' for k, v in m.items()))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m civ5headless.fingerprint',
                                 description='Read and compare Lua fingerprints (probes/fingerprint.lua).')
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('compare', help='compare two runs; exit code 0 when identical on at least one common turn')
    c.add_argument('a')
    c.add_argument('b')
    c.add_argument('--heal-turns', type=int, default=2, help='a difference that heals within this many turns is a transient')
    c.add_argument('--json', help='write the full comparison here')
    s = sub.add_parser('show', help='what a log holds')
    s.add_argument('a')
    s.add_argument('--metrics', action='store_true', help='print the last turn\'s metrics')
    a = ap.parse_args(argv)
    if a.cmd == 'compare':
        return cmd_compare(a.a, a.b, a.heal_turns, a.json)
    return cmd_show(a.a, a.metrics)


if __name__ == '__main__':
    sys.exit(main())
