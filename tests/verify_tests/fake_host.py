"""A stand-in for civ5headless.exe (run through fake_host.cmd) for the verification suite's tests.

--help prints a usage text (with --lua-probe when C5H_FAKE_LUA_PROBE=1, with --reseed when C5H_FAKE_RESEED=1). newgame
plays fakegame.Game for the spec's seeds and writes what run.py and the suite read: turn-times.jsonl,
Logs/HeadlessDigest.log, Logs/Lua.log (the probes' lines), checkpoints, newgame-report.json, lua-before-arm.json,
lua-before-dump.json (replay data, victory) and, with C5H_FAKE_HOST_REPORT=1, host-report.json. --save-at T writes a
Turn_T save (a real header, the spec next to it); loadgame --load SAVE [--reseed M:S] plays that spec on with the new
seeds, from the save's turn.

Environment: C5H_FAKE_SYNC_MATTERS=1 (the sync seed changes the game), C5H_FAKE_SKEW_DIVERGE=T and
C5H_FAKE_PERTURB_DIVERGE=T (--frame-skew / --heap-perturb change the game from turn T), C5H_FAKE_PROBE_DIVERGE=T (the
Lua probes change it), C5H_FAKE_NONDETERMINISTIC=T (every run differs from turn T), C5H_FAKE_VICTORY=T (victory at
turn T), C5H_FAKE_LUA_ERROR=1 (a Lua error line), C5H_FAKE_HOST_CHOSE_CIVS=1 (the DLL leaves city-states to the exe, so
the host chooses them: the spec's minorCivs, else its own draw; host-report.json says so), C5H_FAKE_NO_DIGEST=1 (a DLL
without state digests: the fingerprint only; a sparse fingerprint, C5HFP_EVERY, prints every k-th turn).
"""
import json
import os
import re
from pathlib import Path
import struct
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'python'))
import fakegame  # noqa: E402
from civ5headless import savegame  # noqa: E402

USAGE = '''usage: civ5headless.exe newgame --spec <resolved.json> --out <run dir> [options]
  --dll <path> --db <path> --game-dir <path> --mods-dir <path> --mods-db <path> --loc-db <path>
  --turns <n> --frame-skew <k> --heap-perturb <seed> --checkpoint-lua <file> --checkpoint-every <n>
  --lua-before-arm <file> --lua-before-dump <file> --exe-seed <n> --no-game-logging
'''


def env_int(name):
    v = os.environ.get(name)
    return int(v) if v else None


def write_save(path, turn, spec):
    """A .Civ5Save header as savegame.read_header reads it, and the game's spec next to it."""
    def text(s):
        return struct.pack('<I', len(s)) + s.encode()
    data = b'CIV5' + struct.pack('<I', 8) + text('1.0.3.279 (403694)') + text('403694') + struct.pack('<IB', turn, 0)
    for s in ('CIVILIZATION_GREECE', 'HANDICAP_EMPEROR', 'ERA_ANCIENT', 'ERA_ANCIENT', 'GAMESPEED_STANDARD', 'WORLDSIZE_TINY',
              'Assets\\Maps\\Continents.lua'):
        data += text(s)
    data += struct.pack('<II', 0, 0) + text('') + text('') + text('PLAYERCOLOR_GREECE')
    data += uuid.uuid4().bytes_le + text('fake') + uuid.UUID(savegame.TAIL_GUID).bytes_le + struct.pack('<I', 0)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data + b'rest')
    Path(str(path) + '.spec.json').write_text(json.dumps(spec), encoding='utf-8')


def main(argv):
    if len(argv) < 2 or argv[1] not in ('newgame', 'loadgame'):
        text = USAGE + ('  --lua-probe <file>   run a Lua file in the InGame context\n' if os.environ.get('C5H_FAKE_LUA_PROBE') == '1' else '')
        text += '  --reseed <map:sync>  new seeds after the load\n' if os.environ.get('C5H_FAKE_RESEED') == '1' else ''
        print(f'unknown command {argv[1] if len(argv) > 1 else ""}\n' + text, file=sys.stderr)
        return 2
    opts, flags, i = {}, set(), 2
    while i < len(argv):
        if argv[i].startswith('--') and i + 1 < len(argv) and not argv[i + 1].startswith('--'):
            opts.setdefault(argv[i], []).append(argv[i + 1])
            i += 2
        else:
            flags.add(argv[i])
            i += 1
    out = Path(opts['--out'][0])
    start = 0
    if argv[1] == 'loadgame':
        spec = fakegame.read_spec(opts['--load'][0] + '.spec.json')
        start = savegame.read_header(opts['--load'][0]).turn
        new = dict(zip(('map', 'sync'), opts.get('--reseed', [':'])[0].split(':')))
        spec['seeds'] = dict(spec['seeds'], **{k: int(v) for k, v in new.items() if v})
    else:
        spec = fakegame.read_spec(opts['--spec'][0])
    turns = start + int(opts['--turns'][0])
    probes = [Path(p).read_text(encoding='utf-8') for p in opts.get('--lua-probe', [])]
    changes = []  # (turn, tag): every cause of a different game, the earliest turn wins, the tags add up
    for option, var in (('--frame-skew', 'C5H_FAKE_SKEW_DIVERGE'), ('--heap-perturb', 'C5H_FAKE_PERTURB_DIVERGE')):
        if option in opts and env_int(var) is not None:
            changes.append((env_int(var), option))
    sparse = re.search(r'C5HFP_EVERY\s*=\s*(\d+)', ''.join(probes))
    every = int(sparse.group(1)) if sparse else 1
    if probes and every == 1 and env_int('C5H_FAKE_PROBE_DIVERGE') is not None:  # a sparse probe changes nothing
        changes.append((env_int('C5H_FAKE_PROBE_DIVERGE'), 'probe'))
    if env_int('C5H_FAKE_NONDETERMINISTIC') is not None:
        changes.append((env_int('C5H_FAKE_NONDETERMINISTIC'), uuid.uuid4().hex))
    diverge = min(t for t, _ in changes) if changes else None
    tag = '+'.join(sorted(x for _, x in changes))
    game = fakegame.Game(spec, sync_matters=os.environ.get('C5H_FAKE_SYNC_MATTERS') == '1', diverge_at=diverge, tag=tag,
                         victory_turn=env_int('C5H_FAKE_VICTORY'),
                         chooser='host' if os.environ.get('C5H_FAKE_HOST_CHOSE_CIVS') == '1' else 'dll')
    end = game.end_turn(turns)
    (out / 'Logs').mkdir(parents=True, exist_ok=True)
    (out / 'checkpoints').mkdir(exist_ok=True)
    with open(out / 'turn-times.jsonl', 'w', encoding='utf-8') as f:
        for t in range(start + 1, end + 1):
            f.write(json.dumps(dict(turn=t, seconds=game.turn_seconds(t))) + '\n')
    no_digest = os.environ.get('C5H_FAKE_NO_DIGEST') == '1'
    if not no_digest:
        (out / 'Logs/HeadlessDigest.log').write_text(''.join(game.digest_line(t) + '\n' for t in range(start, end)), encoding='utf-8')
    saves = []
    for t in opts.get('--save-at', []):
        path = out / f'saves/Turn_{int(t):04d}.Civ5Save'
        write_save(path, int(t), spec)
        saves.append(dict(kind='turn', turn=int(t), path=str(path), bytes=path.stat().st_size, error=''))
    lua = ['[0.1] Map Script: Generating Map']
    if any('C5HPG' in p for p in probes):
        lua += ['[0.2] ' + line for line in game.pregame_lines()]
    if any('C5HFP' in p for p in probes):
        lua += ['[0.3] ' + line for line in game.meta_lines()]
        for t in range(start, end + 1):  # the probe also prints the state after the last turn
            if t % every == 0:
                lua += ['[%d.0] ' % (t + 1) + line for line in game.fingerprint_lines(t)]
    if os.environ.get('C5H_FAKE_LUA_ERROR') == '1':
        lua.append(r'[3.5] Runtime Error: MODS\Some Mod\UI\Thing.lua:12: attempt to index a nil value')
    (out / 'Logs/Lua.log').write_text('\n'.join(lua) + '\n', encoding='utf-8')
    for t in range(start, end + 1):
        (out / f'checkpoints/turn-{t:04d}.json').write_text(json.dumps(dict(utc='x', seconds=0, data=game.checkpoint(t))), encoding='utf-8')
    (out / f'checkpoints/turn-{end:04d}-final.json').write_text(json.dumps(dict(data=dict(turn=end))), encoding='utf-8')
    (out / 'lua-before-arm.json').write_text(json.dumps(dict(setup=dict(turn=0), improvedMask=-2, diagnosticsLevel=-1)), encoding='utf-8')
    victory = dict(winner=1, victory='VICTORY_DOMINATION', turn=end) if game.victory_turn and end < turns else dict(winner=-1)
    (out / 'lua-before-dump.json').write_text(json.dumps(dict(victory=victory, messages=[], replayData=game.replay_data(turns))),
                                              encoding='utf-8')
    total = 3.0 + game.turn_seconds(end)
    (out / 'newgame-report.json').write_text(json.dumps(dict(
        status='passed', timings_seconds=dict(total=total, autoplay=game.turn_seconds(end), shutdown=0.1),
        engine_calls=[dict(method='ICvEngineUtility1::Thing', stub=True, count=3, first_phase='start')],
        newgame=dict(lua_errors=0, autoplay=dict(updates=end * 13), saves=saves))), encoding='utf-8')
    if os.environ.get('C5H_FAKE_HOST_REPORT') == '1':
        minors = game.minor_report()
        (out / 'host-report.json').write_text(json.dumps(dict(
            format='civ5headless-host-report/1', status='passed',
            dll=dict(digestAdditions=not no_digest, hostChoseCivs=any(x['chosenBy'] == 'host' for x in minors['slots'])),
            minorCivs=minors,
            stubCalls=dict(unknownCount=1, calls=[dict(method='ICvEngineUtility1::Harmless', count=5, knownHarmless=True),
                                                  dict(method='ICvEngineUtility1::Unknown', count=2, knownHarmless=False)]),
            luaErrors=dict(failing=0, total=0, list=[]), uiFiles=dict(unknownCount=0, unknown=[]),
            timings=dict(total=total, autoplay=game.turn_seconds(end), shutdown=0.1))), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
