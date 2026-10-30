"""The DLL's per-turn state digest (docs/dll-additions.md): reading, comparing, verifying.

  c5h compare A B [--heal-turns 2] [--json OUT]   two runs (run folders or HeadlessDigest.log files)
  c5h digest verify DUMP_FOLDER                    recompute section hashes of a Game.DumpHeadlessState folder
  c5h digest compare LOG_A LOG_B                   as c5h compare
  c5h digest hash FILE...                          xxHash64 (seed 0) of files

The hash is xxHash64 with seed 0; the total is xxHash64 over, for each section in the total,
its name, a NUL byte and its 8-byte little-endian hash.

compare(a, b, heal_turns=2) is the comparison the parity tools and the verification suite use: the first differing
digest point, the differing sections per turn, and runs of differences, a run that heals (the digests are equal
again) within heal_turns turns being a transient. savedb (UI state the host does not reproduce) is outside the total
and never a difference; the first turn it differs is reported apart.
"""
import argparse
import json
import struct
import sys
from pathlib import Path

from . import mixing

P1 = 11400714785074694791
P2 = 14029467366897019727
P3 = 1609587929392839161
P4 = 9650029242287828579
P5 = 2870177450012600261
M = (1 << 64) - 1


def _rotl(x, r):
    return ((x << r) | (x >> (64 - r))) & M


def _round(acc, lane):
    acc = (acc + lane * P2) & M
    return (_rotl(acc, 31) * P1) & M


def _merge(acc, val):
    acc ^= _round(0, val)
    return (acc * P1 + P4) & M


def xxh64(data: bytes, seed: int = 0) -> int:
    n = len(data)
    p = 0
    if n >= 32:
        v1 = (seed + P1 + P2) & M
        v2 = (seed + P2) & M
        v3 = seed & M
        v4 = (seed - P1) & M
        while p + 32 <= n:
            a, b, c, d = struct.unpack_from('<4Q', data, p)
            v1, v2, v3, v4 = _round(v1, a), _round(v2, b), _round(v3, c), _round(v4, d)
            p += 32
        h = (_rotl(v1, 1) + _rotl(v2, 7) + _rotl(v3, 12) + _rotl(v4, 18)) & M
        for v in (v1, v2, v3, v4):
            h = _merge(h, v)
    else:
        h = (seed + P5) & M
    h = (h + n) & M
    while p + 8 <= n:
        h ^= _round(0, struct.unpack_from('<Q', data, p)[0])
        h = (_rotl(h, 27) * P1 + P4) & M
        p += 8
    if p + 4 <= n:
        h ^= (struct.unpack_from('<I', data, p)[0] * P1) & M
        h = (_rotl(h, 23) * P2 + P3) & M
        p += 4
    while p < n:
        h ^= (data[p] * P5) & M
        h = (_rotl(h, 11) * P1) & M
        p += 1
    h ^= h >> 33
    h = (h * P2) & M
    h ^= h >> 29
    h = (h * P3) & M
    h ^= h >> 32
    return h


def parse_line(line):
    parts = line.rstrip('\n').split('|')
    fields = {}
    order = []
    for part in parts[1:]:
        if '=' in part:
            k, v = part.split('=', 1)
            fields[k] = v
            order.append(k)
    return parts[0], fields, order


META = {'turn', 'point', 'total'}
EXCLUDED = {'savedb'}


def total_of(fields, order):
    buf = b''
    for name in order:
        if name in META or name in EXCLUDED:
            continue
        buf += name.encode() + b'\0' + struct.pack('<Q', int(fields[name], 16))
    return xxh64(buf)


def cmd_verify(folder):
    folder = Path(folder)
    kind, fields, order = parse_line((folder / 'digest.txt').read_text().splitlines()[0])
    bad = 0
    for name in order:
        if name in META:
            continue
        got = '%016x' % xxh64((folder / (name + '.bin')).read_bytes())
        if got != fields[name]:
            print(f'{name}: file {got} != logged {fields[name]}')
            bad += 1
    total = '%016x' % total_of(fields, order)
    if total != fields['total']:
        print(f'total: recomputed {total} != logged {fields["total"]}')
        bad += 1
    print('ok' if not bad else f'{bad} mismatches')
    return 1 if bad else 0


def digest_lines(path):
    """{(point, turn): (fields, field order)} of the HHDIGEST lines of a HeadlessDigest.log."""
    out = {}
    for line in Path(path).read_text(errors='replace').splitlines():
        if line.startswith('HHDIGEST|'):
            _, fields, order = parse_line(line)
            out[(fields.get('point'), int(fields['turn']))] = (fields, order)
    return out


def log_path(run):
    """A HeadlessDigest.log, or the one in a run folder (Logs/), a real-game reference folder or a Logs folder."""
    p = Path(run)
    if p.is_file():
        return p
    for candidate in (p / 'Logs/HeadlessDigest.log', p / 'HeadlessDigest.log'):
        if candidate.is_file():
            return candidate
    try:
        detail = json.loads((p / 'status.json').read_text(encoding='utf-8')).get('detail')
    except (OSError, ValueError, AttributeError):
        detail = None
    raise FileNotFoundError(f'no HeadlessDigest.log in {p}' + (
        ' (a summary-detail run keeps no digests: play it with --detail standard or full)' if detail == 'summary' else
        ' (a DLL without the digest additions; c5h fingerprint compare works with any DLL)' if detail else ''))


def differing_sections(fa, oa, fb, ob):
    """Sections (savedb included) whose hashes differ, in A's order then B's extra ones."""
    diff = [n for n in oa if n not in META and fa.get(n) != fb.get(n)]
    return diff + [n for n in ob if n not in META and n not in fa]


def compare(a, b, heal_turns=2):
    """Compare two HeadlessDigest.log files (or two parsed digest_lines dicts) point by point.

    Returns a dict:
      identical          no common point differs (savedb aside)
      common, onlyA, onlyB, lastTurnA, lastTurnB
      first              {turn, point, sections} of the first differing point, or None
      differing          {turn: [sections]} for every differing turn
      runs               [{turn, point, until, sections}]: consecutive differing points; until is the turn of the
                         next equal point, or None when the run lasts to the last common point
      transients         the runs that healed within heal_turns turns (until - turn <= heal_turns)
      divergence         the first run that is not a transient, as {turn, point, until, sections}, or None
      lastPointIdentical the last common point is equal
      savedbFirstTurn    first turn savedb differs (not a difference), or None
    """
    da = a if isinstance(a, dict) else digest_lines(a)
    db = b if isinstance(b, dict) else digest_lines(b)
    keys = sorted(set(da) & set(db), key=lambda k: (k[1], k[0] or ''))
    differing, runs, open_run, savedb = {}, [], None, None
    for key in keys:
        fa, oa = da[key]
        fb, ob = db[key]
        diff = differing_sections(fa, oa, fb, ob)
        if 'savedb' in diff and savedb is None:
            savedb = key[1]
        diff = [n for n in diff if n not in EXCLUDED]
        if fa.get('total') != fb.get('total') or diff:
            differing.setdefault(key[1], [])
            differing[key[1]] += [n for n in diff if n not in differing[key[1]]]
            if open_run is None:
                open_run = dict(turn=key[1], point=key[0], until=None, sections=[])
            open_run['sections'] += [n for n in diff if n not in open_run['sections']]
        elif open_run is not None:
            open_run['until'] = key[1]
            runs.append(open_run)
            open_run = None
    if open_run is not None:
        runs.append(open_run)
    transients = [r for r in runs if r['until'] is not None and r['until'] - r['turn'] <= heal_turns]
    divergence = next((r for r in runs if r not in transients), None)
    first = None
    if runs:
        t = runs[0]['turn']
        first = dict(turn=t, point=runs[0]['point'], sections=differing[t])
    return dict(identical=not runs, common=len(keys), onlyA=len(set(da) - set(db)), onlyB=len(set(db) - set(da)),
                lastTurnA=max((k[1] for k in da), default=None), lastTurnB=max((k[1] for k in db), default=None),
                first=first, differing=differing, runs=runs, transients=transients, divergence=divergence,
                lastPointIdentical=bool(keys) and (not runs or runs[-1]['until'] is not None), savedbFirstTurn=savedb,
                healTurns=heal_turns)


def summary(r):
    """One line for a compare() result."""
    if r['identical']:
        return (f"identical on {r['common']} common digest points (only in A: {r['onlyA']}, only in B: {r['onlyB']})"
                + (f"; last turns {r['lastTurnA']}/{r['lastTurnB']}" if r['lastTurnA'] != r['lastTurnB'] else ''))
    f = r['first']
    text = f"first difference: turn {f['turn']} point {f['point']}: sections {', '.join(f['sections'])}"
    if r['divergence']:
        d = r['divergence']
        if d is not r['runs'][0]:
            text += f"; diverges at turn {d['turn']}: {', '.join(d['sections'][:12])}"
    else:
        text += f" (transient: equal again at turn {r['runs'][0]['until']})"
    if r['transients']:
        text += f"; {len(r['transients'])} transient(s) healed within {r['healTurns']} turns"
    return text + f"; {len(r['differing'])} differing turns of {r['common']} points"


def cmd_compare(a, b, heal_turns=2, json_out=None):
    r = compare(log_path(a), log_path(b), heal_turns)
    r['aiLogging'], warning = mixing.check(a, b)
    print(summary(r))
    if warning:
        print(warning)
    if json_out:
        Path(json_out).write_text(json.dumps(r, indent=1) + '\n', encoding='utf-8')
    return 0 if r['identical'] else 1


def compare_main(argv=None, cfg=None):
    """c5h compare A B."""
    ap = argparse.ArgumentParser(prog='c5h compare', description='Compare two runs\' per-turn state digests '
                                 '(run folders or HeadlessDigest.log files). Exit code 0 when identical.')
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('--heal-turns', type=int, default=2, help='a difference that heals within this many turns is a transient')
    ap.add_argument('--json', help='write the full comparison here')
    a = ap.parse_args(argv)
    return cmd_compare(a.a, a.b, a.heal_turns, a.json)


def main(argv=None, cfg=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) >= 2 and argv[0] == 'verify':
        return cmd_verify(argv[1])
    if len(argv) >= 3 and argv[0] == 'compare':
        return compare_main(argv[1:])
    if len(argv) >= 2 and argv[0] == 'hash':
        for name in argv[1:]:
            print('%016x  %s' % (xxh64(Path(name).read_bytes()), name))
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
