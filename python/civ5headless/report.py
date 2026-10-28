"""Run reports: report.html in a run folder, and a summary page over several runs.

  c5h report RUN [RUN...] [--summary FILE] [--no-runs]

RUN is a run folder, a run name under the runs folder, or a folder of run folders (a queue's out folder). Each run's
report.html is rebuilt from what its folder holds (--no-runs: not); with more than one run, a summary page compares
them (default <their common folder>/summary.html). run.play writes report.html at the end of every game (c5h run
--no-report: not) and c5h queue run writes the queue's summary page.

The page is self-contained (inline CSS and SVG, a little optional JavaScript for the timeline filter and the theme
button; it reads the same without), in light and dark themes:
- header: the spec (map, size, speed, difficulty, seeds, civilizations and city-states), the DLL (file, SHA-256 and
  why it was chosen), the mods, the host (exe hash, commit) and the detail level;
- result: the winner and victory, or the turn the game stopped at; autoplay time, seconds per turn, CPU, peak memory;
- players: civilization, leader, team, alive, score, cities, population, techs, policies, era, wonders, religion;
  vassals under their master (final-state.json's master, else the replay messages and the final wars);
- per-player charts over the turns (replay data, else the checkpoints, else the fingerprint metrics), each line labelled
  at its end; the host's seconds per turn; the event timeline (replay messages, plus era changes and eliminations from
  the checkpoints), filterable by kind and player; the map at turn 0 and the final territories with the cities;
- health (host-report.json: Lua errors, stubbed calls not known to be harmless, profiles, add-ins, unknown UI files)
  and the folder's files.
Whatever a run lacks (no digests, no replay data, a stock DLL, a game stopped early, a crash) leaves its section out
with a note. collect(run) reads a folder into a model, render(model) makes the page; run.py collects before it tidies
the folder for its detail level and writes after.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys

from . import __version__
from .html import CSS, fmt, h, series_svg, short, size, ticks

REPORT = 'report.html'
SUMMARY = 'summary.html'
MAX_POINTS = 400  # per chart line; longer series are thinned evenly (the last point kept)

# Per-player charts: (key, title, sources in order of preference). Sources: replay data sets, checkpoint fields,
# fingerprint metrics (C5HM).
CHARTS = [
    ('score', 'Score', [('replay', 'REPLAYDATASET_SCORE'), ('checkpoint', 'score'), ('fingerprint', 'score')]),
    ('cities', 'Cities', [('replay', 'REPLAYDATASET_CITYCOUNT'), ('checkpoint', 'cityCount'), ('fingerprint', 'cities')]),
    ('population', 'Population', [('replay', 'REPLAYDATASET_POPULATION'), ('checkpoint', 'population'),
                                  ('fingerprint', 'population')]),
    ('techs', 'Techs known', [('replay', 'REPLAYDATASET_TECHSKNOWN'), ('checkpoint', 'techs'), ('fingerprint', 'techs')]),
    ('policies', 'Policies', [('replay', 'REPLAYDATASET_NUMBEROFPOLICIES'), ('checkpoint', 'policies'),
                              ('fingerprint', 'policies')]),
    ('culture', 'Culture per turn', [('replay', 'REPLAYDATASET_CULTUREPERTURN'), ('checkpoint', 'culture')]),
    ('science', 'Science per turn', [('replay', 'REPLAYDATASET_SCIENCEPERTURN'), ('checkpoint', 'science')]),
    ('gold', 'Gold per turn', [('replay', 'REPLAYDATASET_GOLDPERTURN'), ('checkpoint', 'goldRate')]),
    ('treasury', 'Gold in the treasury', [('checkpoint', 'gold'), ('fingerprint', 'gold')]),
    ('might', 'Military might', [('replay', 'REPLAYDATASET_MILITARYMIGHT'), ('checkpoint', 'might')]),
    ('military', 'Military units', [('checkpoint', 'military'), ('fingerprint', 'military')]),
    ('land', 'Land (plots)', [('replay', 'REPLAYDATASET_TOTALLAND'), ('checkpoint', 'land')]),
    ('happiness', 'Excess happiness', [('replay', 'REPLAYDATASET_EXCESSHAPINESS'), ('checkpoint', 'happiness')]),
    ('tourism', 'Tourism per turn', [('replay', 'REPLAYDATASET_TOURISMPERTURN'), ('checkpoint', 'tourism')]),
    ('faith', 'Faith per turn', [('checkpoint', 'faith')]),
    ('wonders', 'World wonders', [('checkpoint', 'wonders'), ('fingerprint', 'wonders')]),
]
CHECKPOINT_FIELDS = ('team', 'civilization', 'minor', 'barbarian', 'score', 'cityCount', 'unitCount', 'military', 'techs',
                     'era', 'policies', 'population', 'land', 'might', 'gold', 'goldRate', 'science', 'culture', 'faith',
                     'tourism', 'happiness', 'wonders')
# Fallback colours (players without the game's colours, runs on the summary page).
PALETTE = ('#3b78c4', '#d1495b', '#3a9d5d', '#e0a030', '#8e5cc4', '#2bb3b3', '#c9622f', '#7a8b2b', '#c45c9d', '#5c6fc4',
           '#9c6b3c', '#4c9c8c', '#b03a3a', '#6a6a6a', '#2f7f2f', '#b0902f')
ERAS = ('Ancient', 'Classical', 'Medieval', 'Renaissance', 'Industrial', 'Modern', 'Atomic', 'Information')
ERA_TYPES = {'ERA_POSTMODERN': 'Atomic', 'ERA_FUTURE': 'Information'}
# Replay message kinds: by type (the DLL's ReplayMessageTypes), else by the (English) text of major events.
TYPE_KINDS = {1: 'city', 3: 'capture', 4: 'raze', 5: 'religion', 6: 'pantheon'}
TEXT_KINDS = [('war', re.compile(r'declares? war on', re.I)), ('peace', re.compile(r'made peace with', re.I)),
              ('elimination', re.compile(r'has been conquered|has been defeated', re.I)),
              ('pact', re.compile(r'defensive pact|permanent alliance|vassal|research agreement', re.I)),
              ('wonder', re.compile(r'\bcompletes\b|been built in a far away land', re.I)),
              ('capture', re.compile(r'was captured by|lost their original capital', re.I)),
              ('raze', re.compile(r'set ablaze|was destroyed', re.I)), ('victory', re.compile(r'has won a', re.I)),
              ('tech', re.compile(r'first to (?:discover|complete)|proven that the world is round', re.I)),
              ('golden', re.compile(r'golden age', re.I)), ('person', re.compile(r'has created', re.I))]
# Vassalage in the replay messages (English, like TEXT_KINDS): "<leader> has become the vassal of <leader>!" and
# "<leader> is no longer the vassal of <leader>!"; a liberated civilization brought back as its liberator's vassal (the
# message's player): "<civ> has conquered <city> and restored <adjective> sovereignty. ... is now the vassal of <civ>.";
# and an elimination, "The <adjective> Civilization has been conquered!!!" (the message's player is the one eliminated).
VASSAL_BECAME = re.compile(r'^(.+) has become the vassal of (.+)!$')
VASSAL_FREED = re.compile(r'^(.+) is no longer the vassal of (.+)!$')
VASSAL_RESTORED = re.compile(r'restored (.+?) sovereignty\..* is now the vassal of ')
ELIMINATED = re.compile(r'^The (.+) Civilization has been (?:conquered|defeated)')
KINDS = {'victory': 'Victory', 'elimination': 'Eliminations', 'war': 'Wars', 'peace': 'Peace', 'pact': 'Pacts',
         'capture': 'Cities captured', 'raze': 'Cities razed', 'city': 'Cities founded', 'wonder': 'Wonders',
         'religion': 'Religions', 'pantheon': 'Pantheons', 'era': 'Eras', 'golden': 'Golden ages', 'tech': 'First to a tech',
         'person': 'Great people', 'other': 'Other'}
# Terrain colours of the turn-0 map (fixed in both themes); plot types: 0 mountain, 1 hills, 2 flat land, 3 water.
TERRAIN = {'TERRAIN_OCEAN': '#2c5a85', 'TERRAIN_COAST': '#4a83b3', 'TERRAIN_GRASS': '#6b9a3c', 'TERRAIN_PLAINS': '#a8a14c',
           'TERRAIN_DESERT': '#d8c27c', 'TERRAIN_TUNDRA': '#8d9886', 'TERRAIN_SNOW': '#e9edf0'}
FEATURES = {'FEATURE_ICE': '#dce8f1', 'FEATURE_FOREST': '#3e6a2e', 'FEATURE_JUNGLE': '#2d6b45', 'FEATURE_MARSH': '#5e7e6c',
            'FEATURE_OASIS': '#4fae8a', 'FEATURE_FLOOD_PLAINS': '#9db24c', 'FEATURE_ATOLL': '#6fb3c9', 'FEATURE_FALLOUT': '#8a7a4a'}
MOUNTAIN, WONDER = '#7b6e64', '#d4a017'
HEX_W, HEX_ROW, HEX_R = 10.0, 8.66, 5.8  # hex width, row step, hex radius (a little over 5.77, so hexes close up)

# What a run folder holds (the folder list in the report).
FILES = {
    REPORT: 'this page', 'status.json': 'state, end turn and reason, timings, CPU and memory, winner, DLL and detail level',
    'events.jsonl': 'what happened during the run: launched, DLL chosen, checkpoints, victory, completed or failed',
    'setup.json': 'the setup the game reports (players, map, speed) and the A/B fields',
    'spec.json': 'the spec as given', 'spec-resolved.json': 'the resolved spec the host played',
    'spec.resolved.json': "the host's copy of the resolved spec",
    'save-header.json': 'the header of the save the game was loaded from (c5h run --load)',
    'saves': 'the saves the game wrote: the last turn (Final_), --save-at (Turn_), --autosave (auto\\)',
    'hh-command.json': 'the host command line, the inputs and their hashes, the DLL and why, the detail level',
    'host-report.json': "the host's run report: inputs, mods, profiles, add-ins, stubbed calls, Lua errors, timings, memory",
    'newgame-report.json': "the host's new-game report", 'host-stdout.txt': "the host's console output",
    'turn-times.jsonl': 'seconds since autoplay started, per turn', 'turn-seen.json': 'the same, as one object',
    'checkpoints': 'players, cities and wars at every turn (summary detail: the final one only)',
    'replay-data.json': "every major civilization's replay series (score, cities, techs, ...)",
    'replay-messages.json': 'the replay messages (cities, wars, wonders, religions, ...)',
    'final-state.json': 'players (names, colours, standing) and the final map (plot owners, cities)',
    't000-map.json': 'the map at turn 0: terrain, features, resources, starting positions',
    't000-digest.json': 'the state digest at turn 0 (DLLs with the digest additions)',
    'lua-before-arm.json': 'the setup Lua result before autoplay', 'lua-before-dump.json': 'the end-of-game Lua result',
    'hh-before-arm.lua': 'Lua the host ran before autoplay', 'hh-checkpoint.lua': 'Lua the host ran every turn',
    'hh-final.lua': 'Lua the host ran after autoplay', 'fingerprint-settings.lua': 'the fingerprint probe settings',
    'Logs': "the logs: Lua.log, Headless.log (the host), HeadlessDigest.log (per-turn digests) and the DLL's",
    'db': 'the database copies the game ran on (full detail)', 'cache': "the game's cache folder for this run",
    'crashlogs': 'crash markers and minidumps', 'CvAssert.log': "the DLL's assertions", 'argv.json': 'the host arguments',
}


# ---- reading ----

def _json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        return default


def _jsonl(path):
    out = []
    try:
        for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _checkpoints(run):
    """({turn: {owner: {field: value}}} of the per-turn checkpoints, the final checkpoint's data or None)."""
    turns, final = {}, None
    folder = Path(run) / 'checkpoints'
    for p in sorted(folder.glob('turn-*.json')) if folder.is_dir() else []:
        d = (_json(p) or {}).get('data')
        if not isinstance(d, dict):
            continue
        if p.stem.endswith('-final'):
            final = d
            continue
        turn = d.get('turn')
        if isinstance(turn, int):
            turns[turn] = {pl['owner']: {k: pl.get(k) for k in CHECKPOINT_FIELDS if pl.get(k) is not None}
                           for pl in d.get('players') or [] if isinstance(pl, dict) and 'owner' in pl}
    if final and isinstance(final.get('turn'), int) and final['turn'] not in turns:
        turns[final['turn']] = {pl['owner']: {k: pl.get(k) for k in CHECKPOINT_FIELDS if pl.get(k) is not None}
                                for pl in final.get('players') or [] if isinstance(pl, dict) and 'owner' in pl}
    return turns, final


def _map0(run):
    """The turn-0 map, compact: width, height and per plot (plot type, terrain, feature, natural wonder)."""
    d = _json(Path(run) / 't000-map.json')
    if not isinstance(d, dict) or not d.get('plots'):
        return None
    plots = [(p.get('plotType'), p.get('terrain'), p.get('feature'), bool(p.get('naturalWonder'))) for p in d['plots']]
    return dict(width=d.get('width'), height=d.get('height'), plots=plots, players=d.get('players') or [],
                cityStates=d.get('cityStates') or [])


def _fingerprint_metrics(run):
    try:
        from . import fingerprint
        return fingerprint.read(run).metrics or None
    except Exception:  # noqa: BLE001 (no Lua.log, no fingerprint lines)
        return None


def collect(run, light=False):
    """A run folder read into the report's model (missing files give empty parts). light: what the summary page needs
    (no checkpoints, map or fingerprint)."""
    run = Path(run)
    m = dict(name=run.name, folder=run)
    m['status'] = _json(run / 'status.json', {}) or {}
    m['setup'] = _json(run / 'setup.json', {}) or {}
    m['spec'] = _json(run / 'spec-resolved.json', {}) or _save_spec(_json(run / 'save-header.json', {}) or {})
    m['command'] = _json(run / 'hh-command.json', {}) or {}
    m['host'] = _json(run / 'host-report.json') or _json(run / 'newgame-report.json', {}) or {}
    m['events'] = _jsonl(run / 'events.jsonl')
    m['turnTimes'] = sorted((r['turn'], r['seconds']) for r in _jsonl(run / 'turn-times.jsonl')
                            if isinstance(r.get('turn'), int) and isinstance(r.get('seconds'), (int, float)))
    m['replay'] = _json(run / 'replay-data.json', {}) or {}
    m['messages'] = _json(run / 'replay-messages.json', []) or []
    m['final'] = _json(run / 'final-state.json', {}) or {}
    m['checkpoints'], m['finalCheckpoint'] = ({}, None) if light else _checkpoints(run)
    if light:
        m['finalCheckpoint'] = _checkpoints_final(run)
    m['map0'] = None if light else _map0(run)
    m['fingerprint'] = None if light else _fingerprint_metrics(run)
    return m


def _checkpoints_final(run):
    finals = sorted((Path(run) / 'checkpoints').glob('turn-*-final.json')) if (Path(run) / 'checkpoints').is_dir() else []
    return (_json(finals[-1]) or {}).get('data') if finals else None


# ---- the model's players and series ----

def pretty(type_name, prefix=''):
    """CIVILIZATION_THE_NETHERLANDS -> The Netherlands."""
    if not type_name:
        return ''
    s = str(type_name)
    for p in (prefix, 'CIVILIZATION_', 'LEADER_', 'MINOR_CIV_', 'VICTORY_', 'ERA_', 'WORLDSIZE_', 'GAMESPEED_', 'HANDICAP_',
              'TXT_KEY_RELIGION_', 'RELIGION_', 'TXT_KEY_'):
        if p and s.startswith(p):
            s = s[len(p):]
            break
    return s.replace('_', ' ').title()


def religion(name):
    """A religion's name; a text key or type the game did not translate is made readable."""
    name = str(name or '')
    return pretty(name) if re.fullmatch(r'[A-Z0-9_]+', name) else name


def era_name(era):
    if isinstance(era, str):
        return ERA_TYPES.get(era) or pretty(era, 'ERA_')
    if isinstance(era, int) and 0 <= era < len(ERAS):
        return ERAS[era]
    return '' if era is None else f'era {era}'


def _luminance(colour):
    try:
        r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (TypeError, ValueError, IndexError):
        return None
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _mix(colour, other, t):
    a = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(a, b))


def _chroma(colour):
    try:
        rgb = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    except (TypeError, ValueError, IndexError):
        return -1.0
    return max(rgb) - min(rgb)


def player_colour(colours):
    """The colour that stands for a player, from its (primary, secondary) colours: the secondary (the banner and
    territory colour of the major civilizations, the colour of the city-states, whose primary is black) unless it is
    nearly grey and the primary is not (the generic player colours). '' when there is none."""
    valid = [c if isinstance(c, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', c) else '' for c in list(colours)[:2]] + ['', '']
    primary, secondary = valid[0], valid[1]
    if secondary and (_chroma(secondary) >= 0.15 or _chroma(primary) <= _chroma(secondary)):
        return secondary
    return primary or secondary


def theme_colours(colour):
    """(light theme, dark theme) variants of a player colour, readable on either background."""
    lum = _luminance(colour)
    if lum is None:
        return colour, colour
    light = _mix(colour, '#000000', 0.45) if lum > 0.45 else colour
    dark = _mix(colour, '#ffffff', 0.5) if lum < 0.08 else colour
    return light, dark


def players(m):
    """Every player of the game ({id: dict}): majors and city-states with names, colours and final standing, merged from
    final-state.json, the final checkpoint, the turn-0 map and setup.json; minor and barbarian flags."""
    out = {}

    def entry(pid):
        return out.setdefault(int(pid), dict(id=int(pid)))

    for p in m['setup'].get('players') or []:
        if isinstance(p, dict) and 'owner' in p:
            e = entry(p['owner'])
            e.setdefault('civilization', p.get('civilization'))
            e.setdefault('minor', p.get('minor'))
            e.setdefault('handicap', p.get('handicap'))
            e.setdefault('human', p.get('human'))
    for p in (m.get('map0') or {}).get('players') or []:
        if isinstance(p, dict) and 'slot' in p:
            e = entry(p['slot'])
            for k in ('civilization', 'leader', 'team', 'minor', 'barbarian', 'minorCiv'):
                if p.get(k) is not None:
                    e[k] = p[k]
    fc = m.get('finalCheckpoint') or {}
    for p in fc.get('players') or []:
        if isinstance(p, dict) and 'owner' in p:
            e = entry(p['owner'])
            e['alive'] = True
            for k in CHECKPOINT_FIELDS:
                if p.get(k) is not None:
                    e[k] = p[k]
            e['cities'] = p.get('cityCount', e.get('cities'))
    for p in m['final'].get('players') or []:
        if isinstance(p, dict) and 'owner' in p:
            e = entry(p['owner'])
            for k, v in p.items():
                if v is not None and k != 'owner':
                    e[k] = v
    for pid in m['replay']:
        try:
            entry(int(pid)).setdefault('minor', False)
        except ValueError:
            pass
    if fc.get('players') and not m['final'].get('players'):
        alive = {p.get('owner') for p in fc['players'] if isinstance(p, dict)}
        for e in out.values():
            e.setdefault('alive', e['id'] in alive)
    winner = m['status'].get('winner')
    k, taken = 0, []
    for pid in sorted(out):
        e = out[pid]
        e['barbarian'] = bool(e.get('barbarian')) or pid == 63 or e.get('civilization') == 'CIVILIZATION_BARBARIAN'
        e['minor'] = bool(e.get('minor'))
        e.setdefault('team', pid)
        if not e.get('name'):
            e['name'] = (pretty(e.get('minorCiv'), 'MINOR_CIV_') if e['minor'] and e.get('minorCiv') else
                         pretty(e.get('civilization'), 'CIVILIZATION_')) or f'player {pid}'
        if not e.get('leaderName') and e.get('leader') and not e['minor']:
            e['leaderName'] = pretty(e['leader'], 'LEADER_')
        if e['minor'] or e['barbarian']:
            e['colour'] = player_colour(e.get('colors') or []) or PALETTE[k % len(PALETTE)]
        else:
            e['colour'] = distinct_colour(e.get('colors') or [], taken) or PALETTE[k % len(PALETTE)]
            taken.append(e['colour'])
            k += 1
        e['winner'] = winner is not None and winner >= 0 and e.get('team') == winner and not e['minor']
    return out


def _distance(a, b):
    """Perceptual-ish distance of two colours (the "redmean" weighting)."""
    (r1, g1, b1), (r2, g2, b2) = ([int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in (a, b))
    rm = (r1 + r2) / 2
    return ((2 + rm / 256) * (r1 - r2) ** 2 + 4 * (g1 - g2) ** 2 + (2 + (255 - rm) / 256) * (b1 - b2) ** 2) ** 0.5


def distinct_colour(colours, taken):
    """A major civilization's colour: of its two (banner and icon) colours that are not nearly grey, the one farthest
    from the colours already taken (the more vivid one when both are far enough), so lines and territories differ."""
    valid = [c for c in list(colours)[:2] if isinstance(c, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', c)]
    vivid = [c for c in valid if _chroma(c) >= 0.12] or valid
    if not vivid:
        return ''
    if not taken:
        return player_colour(colours) if player_colour(colours) in vivid else vivid[0]
    near = lambda c: min(_distance(c, t) for t in taken)
    far = [c for c in vivid if near(c) >= 150]
    if len(far) > 1:
        return player_colour(colours) if player_colour(colours) in far else max(far, key=_chroma)
    return max(vivid, key=near)


def label(name, width=14):
    """A short chart label: no leading "The", at most width characters."""
    name = re.sub(r'^The ', '', str(name or ''))
    return name if len(name) <= width else name[:width - 1] + '.'


def majors(ps):
    return [p for p in ps.values() if not p['minor'] and not p['barbarian']]


def _thin(points, limit=MAX_POINTS):
    if len(points) <= limit:
        return points
    step = len(points) / (limit - 1)
    out = [points[int(i * step)] for i in range(limit - 1)]
    return out + [points[-1]]


def series(m, sources, ids):
    """(source label, {player: [(turn, value)]}) from the first source with data over more than one turn, for the
    players ids; ('last turn only', {}) when the sources only have the last turn (the final checkpoint at summary)."""
    last_only = False
    for kind, key in sources:
        data = {}
        if kind == 'replay':
            for pid, sets in m['replay'].items():
                try:
                    pid = int(pid)
                except ValueError:
                    continue
                pts = sorted((int(t), v) for t, v in (sets or {}).get(key) or [] if isinstance(v, (int, float)))
                if pid in ids and pts:
                    data[pid] = pts
        elif kind == 'checkpoint':
            for turn, row in sorted(m['checkpoints'].items()):
                for pid, values in row.items():
                    if pid in ids and isinstance(values.get(key), (int, float)):
                        data.setdefault(pid, []).append((turn, values[key]))
        elif kind == 'fingerprint' and m.get('fingerprint'):
            for turn, row in sorted(m['fingerprint'].items()):
                for pid, values in row.items():
                    if int(pid) in ids and isinstance(values.get(key), (int, float)):
                        data.setdefault(int(pid), []).append((int(turn), values[key]))
        if any(len(v) > 1 for v in data.values()):
            label = {'replay': 'replay data', 'checkpoint': 'checkpoints', 'fingerprint': 'fingerprint'}[kind]
            return f'{label}: {key}', data
        last_only = last_only or bool(data)
    return ('last turn only' if last_only else None), {}


def turn_seconds(m):
    """[(turn, seconds)] the host spent on each turn: host-report.json, else the differences of turn-times.jsonl."""
    per = ((m['host'].get('turns') or {}).get('secondsPerTurn') or {})
    if per:
        out = sorted((int(t), float(s)) for t, s in per.items() if isinstance(s, (int, float)))
        if out and len(out) > 1 and out[-1][1] < 0.1 * max(s for _, s in out):
            out = out[:-1]  # the last entry is the idle tail after autoplay ended
        return out
    times = [(0, 0.0)] + list(m['turnTimes'])
    return [(t0, round(s1 - s0, 3)) for (t0, s0), (t1, s1) in zip(times, times[1:])]


def clean(text):
    """Replay message text without the game's markup ([COLOR_...], [ICON_...], [NEWLINE])."""
    text = re.sub(r'\[NEWLINE\]', ' ', str(text or ''))
    text = re.sub(r'\[[A-Z0-9_]+\]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def kind_of(message):
    t = message.get('Type')
    if t in TYPE_KINDS:
        return TYPE_KINDS[t]
    text = message.get('Text') or ''
    for kind, rx in TEXT_KINDS:
        if rx.search(text):
            return kind
    return 'other'


def events(m, ps):
    """[dict(turn, kind, player, text)]: replay messages, plus era changes and eliminations of the major civilizations
    from the per-turn checkpoints (else from final-state.json and the replay data), by turn."""
    out, bare = [], []
    for msg in m['messages']:
        if isinstance(msg, dict) and isinstance(msg.get('Turn'), int):
            e = dict(turn=msg['Turn'], kind=kind_of(msg), player=msg.get('Player'), text=clean(msg.get('Text')))
            (out if e['text'] else bare).append(e)
    for e in bare:  # messages without text (city captures in some DLLs): kept unless a text message says the same
        if not any(o['turn'] == e['turn'] and o['kind'] == e['kind'] for o in out):
            who = ps.get(e['player'], {}).get('name') or f"player {e['player']}"
            e['text'] = f'{KINDS[e["kind"]]}: {who}'
            out.append(e)
    ids = {p['id'] for p in majors(ps)}
    told = {e['player'] for e in out if e['kind'] == 'elimination'}  # the game's own "has been conquered"
    turns = sorted(m['checkpoints']) if len(m['checkpoints']) > 1 else []
    era, seen, gone = {}, set(), set()
    for turn in turns:
        row = m['checkpoints'][turn]
        for pid in ids:
            v = row.get(pid)
            if v is None:
                if pid in seen and pid not in gone:
                    gone.add(pid)
                    if pid not in told:
                        out.append(dict(turn=turn, kind='elimination', player=pid, text=f"{ps[pid]['name']} is eliminated"))
                continue
            seen.add(pid)
            e = v.get('era')
            if isinstance(e, int):
                if pid in era and e > era[pid]:
                    out.append(dict(turn=turn, kind='era', player=pid, text=f"{ps[pid]['name']} enters the {era_name(e)} era"))
                era[pid] = e
    if not turns:
        for p in majors(ps):
            if p.get('alive') is False and p['id'] not in told:
                last = max((t for s in (m['replay'].get(str(p['id'])) or {}).values() for t, _ in s or []), default=None)
                out.append(dict(turn=(last + 1) if last is not None else m['status'].get('endTurn'), kind='elimination',
                                player=p['id'], text=f"{p['name']} is eliminated"))
    out.sort(key=lambda e: (e['turn'] if e['turn'] is not None else 10 ** 6))
    return out


def vassal_masters(m, ps):
    """({vassal: master} of the major civilizations at the end, both alive; where it came from: 'final-state.json',
    'the replay messages' or '' when nothing says). final-state.json's master (each major's master team's leader, -1 for
    none; newer runs), else the replay messages in order, each once (a message can be there twice): a vassal's last
    became, restored or no-longer message counts, and an elimination ends the vassalage of the civilization eliminated
    and of its vassals. A restoration names the civilization by its adjective, which the message of its elimination
    (always earlier: only a civilization eliminated can be restored) gave too. The final checkpoint's wars then drop
    the vassalages the game ended without a message (most of them)."""
    ids = {p['id'] for p in majors(ps)}
    alive = {pid for pid in ids if ps[pid].get('alive') is not False}
    found = {pid: ps[pid]['master'] for pid in ids if type(ps[pid].get('master')) is int}
    source = 'final-state.json' if found else ''
    if not found:
        leader = {ps[pid].get('leaderName'): pid for pid in ids if ps[pid].get('leaderName')}
        adjective, seen = {}, set()
        messages = [x for x in m['messages'] if isinstance(x, dict) and isinstance(x.get('Turn'), int)]
        for msg in sorted(messages, key=lambda x: (x['Turn'], x['index'] if isinstance(x.get('index'), int) else 0)):
            text, who = clean(msg.get('Text')), msg.get('Player')
            if (msg['Turn'], text) in seen:
                continue
            seen.add((msg['Turn'], text))
            gone = ELIMINATED.match(text)
            if gone and who in ids:
                adjective[gone.group(1)] = who
                found.update({v: None for v, k in found.items() if v == who or k == who})
                continue
            pair = VASSAL_BECAME.match(text) or VASSAL_FREED.match(text)
            if pair and pair.group(1) in leader and pair.group(2) in leader:
                vassal, master = leader[pair.group(1)], leader[pair.group(2)]
                if pair.re is VASSAL_BECAME:
                    found[vassal] = master
                elif found.get(vassal) == master:
                    found[vassal] = None
                source = 'the replay messages'
                continue
            restored = VASSAL_RESTORED.search(text)
            if restored and adjective.get(restored.group(1)) is not None and who in ids:
                found[adjective[restored.group(1)]] = who
                source = 'the replay messages'
        # The game ends some vassalages without a message. A vassal is at war with whoever its master is at war with,
        # so one whose wars in the final checkpoint differ from its master's is not a vassal any more.
        wars = (m.get('finalCheckpoint') or {}).get('wars')
        if isinstance(wars, list):
            enemies = {}
            for pair in wars:
                if isinstance(pair, list) and len(pair) == 2:
                    enemies.setdefault(pair[0], set()).add(pair[1])
                    enemies.setdefault(pair[1], set()).add(pair[0])
            found = {v: k for v, k in found.items()
                     if k is None or enemies.get(v, set()) - {k} == enemies.get(k, set()) - {v}}
    return {v: k for v, k in found.items() if k is not None and k != v and v in alive and k in alive}, source


# ---- page parts ----

PAGE_CSS = '''
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
.toc{font-size:13px;margin:10px 0 0}.toc a{margin-right:12px;color:var(--info);text-decoration:none}
a{color:var(--info)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(165px,100%),1fr));gap:10px;margin:14px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.card .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.card .v{font-size:20px;font-weight:650;line-height:1.3}.card .s{font-size:12px;color:var(--muted)}
.card.win{border-color:var(--warn)}
.charts{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(380px,100%),1fr));gap:10px}
.maps{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(440px,100%),1fr));gap:10px}
.maps svg{display:block;width:100%;height:auto}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:-1px;border:1px solid var(--line)}
tr.winner td{font-weight:650}tr.dead td{color:var(--muted)}
tr.vassal td:first-child{position:relative;padding-left:calc(9px + 25px * var(--d,1))}
tr.vassal td:first-child::before,tr.vmore td:first-child::after{content:'';position:absolute;left:calc(25px * var(--d,1) - 8px);
border-left:1.5px solid var(--muted);opacity:.5}
tr.vassal td:first-child::before{top:-1px;height:17px;width:10px;border-bottom:1.5px solid var(--muted);border-bottom-left-radius:5px}
tr.vmore td:first-child::after{top:16px;bottom:-1px}
.vtag{font-size:11px;font-weight:400;color:var(--muted)}
.pill{display:inline-block;padding:0 7px;border-radius:9px;font-size:11px;font-weight:600;color:#fff;background:var(--warn)}
.tlbox{max-height:460px;overflow:auto;border:1px solid var(--line);border-radius:6px}
.tlbox table{border:none;border-radius:0}.tlbox td:nth-child(-n+3){white-space:nowrap}
.filters{display:flex;gap:10px;margin:6px 0;font-size:13px}.filters select{font:inherit;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:4px}
.theme{font:inherit;font-size:12px;background:var(--panel);color:var(--muted);border:1px solid var(--line);border-radius:12px;padding:2px 10px;cursor:pointer}
.note{color:var(--muted);font-size:13px;margin:6px 0}
.kv>div{min-width:0;overflow-wrap:anywhere}
.state{display:inline-block;padding:1px 9px;border-radius:10px;font-size:12px;font-weight:600;color:#fff;background:var(--na);text-transform:uppercase}
.state.completed{background:var(--ok)}.state.failed{background:var(--bad)}
'''

SCRIPT = '''<script>
(function(){var d=document,r=d.documentElement,b=d.getElementById('theme');
if(b){b.hidden=false;b.onclick=function(){var t=r.getAttribute('data-theme');
var n=t==='dark'?'light':t==='light'?null:'dark';if(n)r.setAttribute('data-theme',n);else r.removeAttribute('data-theme');
b.textContent='theme: '+(n||'auto')}}
var f=d.getElementById('tlf');if(!f)return;f.hidden=false;var k=d.getElementById('tlk'),p=d.getElementById('tlp');
function go(){var rows=d.querySelectorAll('#tl tbody tr');for(var i=0;i<rows.length;i++){var x=rows[i];
x.style.display=((!k.value||x.getAttribute('data-k')===k.value)&&(!p.value||x.getAttribute('data-p')===p.value))?'':'none'}}
k.onchange=go;p.onchange=go})();
</script>'''


def _player_vars(ps):
    """CSS custom properties --pN (player colours) for the light and the dark theme."""
    light, dark = [], []
    for pid, p in sorted(ps.items()):
        a, b = theme_colours(p['colour'])
        light.append(f'--p{pid}:{a}')
        dark.append(f'--p{pid}:{b}')
    base = '--water:#a8c6e0;--land:#dcd8cc;--edge:#ffffff'
    dbase = '--water:#1f3447;--land:#4a4e54;--edge:#16181b'
    return (f':root{{{base};{";".join(light)}}}'
            f'@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{{dbase};{";".join(dark)}}}}}'
            f':root[data-theme="dark"]{{{dbase};{";".join(dark)}}}')


def _kv(rows):
    return '<div class="kv">' + ''.join(f'<div class="muted">{h(k)}</div><div>{v}</div>' for k, v in rows if v not in (None, '')) + '</div>'


def _card(key, value, sub='', cls=''):
    return (f'<div class="card {cls}"><div class="k">{h(key)}</div><div class="v">{value}</div>'
            + (f'<div class="s">{sub}</div>' if sub else '') + '</div>')


def _save_spec(header):
    """For a game loaded from a save (no spec): the spec fields the report shows, from the save's header."""
    if not header:
        return {}
    return dict(map=dict(script=header.get('mapScript')), worldSize=header.get('worldSize'), speed=header.get('speed'),
                difficulty=header.get('handicap'), era=header.get('startEra'))


def _saves_text(st):
    """The Save row: the save a game was loaded from and the saves it wrote."""
    parts = []
    if st.get('loadedFrom'):
        parts.append(f'Loaded from <span class="mono">{h(Path(st["loadedFrom"]).name)}</span> (turn {h(st.get("startTurn"))})'
                     + ''.join(f'<div class="muted">{h(w)}</div>' for w in st.get('loadWarnings') or []))
    saves = st.get('saves') or []
    if saves:
        kinds = {}
        for x in saves:
            kinds[x.get('kind')] = kinds.get(x.get('kind'), 0) + 1
        parts.append(f'{len(saves)} written (' + ', '.join(f'{n} {h(k)}' for k, n in kinds.items()) + ')'
                     + (f' &middot; final <span class="mono">{h(Path(st["finalSave"]).name)}</span>' if st.get('finalSave') else ''))
    return '<br>'.join(parts) or None


def _mb(n):
    return f'{n / 2 ** 20:,.0f} MB' if isinstance(n, (int, float)) and n else None


def _setup_rows(m, ps):
    s, spec, st, cmd, host = m['setup'], m['spec'], m['status'], m['command'], m['host']
    mp = spec.get('map') or {}
    script = mp.get('file') or mp.get('script') or s.get('map') or ''
    world = (spec.get('worldSize') or {}).get('type') if isinstance(spec.get('worldSize'), dict) else spec.get('worldSize')
    world = world or s.get('world')
    speed = (spec.get('speed') or {}).get('type') if isinstance(spec.get('speed'), dict) else spec.get('speed') or s.get('speed')
    diff = (spec.get('difficulty') or {}).get('type') if isinstance(spec.get('difficulty'), dict) else spec.get('difficulty')
    era = (spec.get('era') or {}).get('type') if isinstance(spec.get('era'), dict) else spec.get('era')
    seeds = spec.get('seeds') or {}
    n_major = len(majors(ps)) or spec.get('majors')
    n_minor = len([p for p in ps.values() if p['minor']]) or spec.get('cityStates')
    dll = st.get('dll') or cmd.get('dll') or (host.get('dll') or {}).get('path') or ''
    sha = st.get('dllSHA256') or cmd.get('dllSHA256') or (host.get('dll') or {}).get('sha256') or ''
    why = st.get('dllReason') or cmd.get('dllReason')
    dll_text = (f'<span class="mono">{h(Path(dll).name or dll)}</span> <span class="mono muted" title="SHA-256">{h(sha)}</span>'
                + (f'<div class="muted">{h(why)}</div>' if why else '')
                + ('<div class="muted">digest additions</div>' if (host.get('dll') or {}).get('digestAdditions') else ''))
    mods = host.get('mods')
    if mods is None and cmd.get('noMods'):
        mods = []
    mods_text = (', '.join(h(x.get('name') or x.get('id')) + f' <span class="muted">v{h(x.get("version"))}</span>' for x in mods)
                 if mods else ('none' if mods is not None else '-'))
    hh = host.get('host') or {}
    commit = hh.get('gitHead')
    host_text = (f'<span class="mono">{h(Path(hh.get("exe") or cmd.get("exe") or "").name)}</span> '
                 f'<span class="mono muted" title="SHA-256">{h((hh.get("sha256") or cmd.get("exeSHA256") or "")[:16])}</span>'
                 + (f' &middot; commit <span class="mono">{h(commit[:10])}</span>' + (' (modified)' if hh.get('gitDirty') else '') if commit else '')
                 + f' &middot; c5h {h(__version__)}')
    options = []
    if cmd.get('fingerprint'):
        options.append('fingerprint')
    host_opts = host.get('options') or {}
    if host_opts.get('noProfiles'):
        options.append('no profiles')
    elif (host.get('profiles') or {}).get('selected'):
        options.append(f"{len(host['profiles']['selected'])} UI profiles")
    if host_opts.get('noAddins'):
        options.append('no add-ins')
    if host_opts.get('gameLogging') is False:
        options.append('no game logging')
    for k in ('heapPerturb', 'frameSkew'):
        if host_opts.get(k):
            options.append(f'{k} {host_opts[k]}')
    if isinstance(s.get('diagnosticsLevel'), int) and s['diagnosticsLevel'] > 0:
        options.append(f"diagnostics {s['diagnosticsLevel']}")
    if s.get('abRole'):
        options.append(f"A/B {s['abRole']}")
    # older runs record no detail level; one with its database copies kept everything
    detail = st.get('detail') or cmd.get('detail') or ('full' if (Path(m['folder']) / 'db').is_dir() else None)
    return [('Map', f'{h(Path(str(script).replace(chr(92), "/")).stem)} &middot; {h(pretty(world, "WORLDSIZE_"))}'
             + (f' &middot; {h(m["map0"]["width"])}x{h(m["map0"]["height"])}' if m.get('map0') else '')),
            ('Game', ' &middot; '.join(x for x in (h(pretty(speed, 'GAMESPEED_')), h(pretty(diff, 'HANDICAP_')),
                                                   h(era_name(era)) + ' start' if era else '') if x)),
            ('Seeds', f"map {h(seeds.get('map'))}, sync {h(seeds.get('sync'))}" if seeds else None),
            ('Players', f'{h(n_major)} civilizations, {h(n_minor)} city-states'),
            ('DLL', dll_text), ('Mods', mods_text), ('Host', host_text),
            ('Run', ' &middot; '.join(x for x in (f"detail {h(detail)}" if detail else '', h(', '.join(options))) if x)),
            ('Spec', f'<span class="mono">{h(Path(st.get("spec") or "").name)}</span>' if st.get('spec') else None),
            ('Saves', _saves_text(st))]


def _result(m, ps):
    st = m['status']
    state = st.get('state') or 'unknown'
    win = [p for p in majors(ps) if p.get('winner')]
    cards = []
    if st.get('endReason') == 'victory' or win:
        names = ', '.join(p['name'] for p in win) or f"team {st.get('winner')}"
        cards.append(_card('Winner', h(names), f"{h(pretty(st.get('victory'), 'VICTORY_'))} victory, turn {h(st.get('endTurn'))}", 'win'))
    elif state == 'completed':
        cards.append(_card('Result', f"turn {h(st.get('endTurn'))}", 'played to the target turn, no winner'))
    else:
        cards.append(_card('Result', f"stopped at turn {h(st.get('endTurn', '?'))}", h(st.get('endReason') or state)))
    cards.append(_card('Autoplay', f"{fmt(st.get('wallSeconds'), 1)} s",
                       f"{fmt(st.get('secondsPerTurn'), 3)} s/turn &middot; host {fmt(st.get('hostSeconds'), 1)} s"))
    proc = st.get('process') or {}
    cpu = proc.get('CPUSeconds')
    limits = ([f"capped at {h(st['cpuLimit'])}% of a core"] if st.get('cpuLimit') else []) \
        + ([f"queue capped at {h(st['totalCpuLimit'])}% of a core"] if st.get('totalCpuLimit') else []) \
        + (['efficiency mode'] if st.get('efficiencyMode') else [])
    cards.append(_card('CPU', f'{fmt(cpu, 1)} s' if cpu is not None else '-',
                       ' &middot; '.join(([f"{fmt(cpu / st['hostSeconds'], 2)} cores busy"] if cpu and st.get('hostSeconds') else [])
                                         + limits)))
    cards.append(_card('Peak memory', h(_mb(proc.get('PeakWorkingSet')) or '-'), 'working set'
                       + (f" &middot; private {h(_mb(proc.get('PeakPrivateBytes')))}" if proc.get('PeakPrivateBytes') else '')))
    bg = st.get('background') or {}
    if bg.get('otherBusyCores') is not None:
        cards.append(_card('Machine', f"{fmt(bg.get('otherBusyCores'), 1)} cores", f"busy with other work, of {h(bg.get('logicalCPUs'))}"))
    out = '<div class="cards">' + ''.join(cards) + '</div>'
    if st.get('error'):
        out += f'<p class="note">Error: {h(st["error"])}</p>'
    return out


def _vassal_rows(rows, masters):
    """[(player, depth, more)]: rows in their order, each master followed by its vassals (depth one more; more: a
    sibling vassal follows), the vassals in the same order. A vassal whose master is not in rows stays at the top."""
    shown = {p['id'] for p in rows}
    under = {}
    for p in rows:
        if masters.get(p['id']) in shown:
            under.setdefault(masters[p['id']], []).append(p)
    out, placed = [], set()

    def place(p, depth, more):
        placed.add(p['id'])
        out.append((p, depth, more))
        vassals = [q for q in under.get(p['id'], []) if q['id'] not in placed]
        for i, q in enumerate(vassals):
            place(q, depth + 1, i < len(vassals) - 1)

    for p in rows:
        if p['id'] not in placed and masters.get(p['id']) not in shown:
            place(p, 0, False)
    for p in rows:  # vassals of each other (not seen in a game): at the top
        if p['id'] not in placed:
            place(p, 0, False)
    return out


def _players_table(m, ps):
    rows = sorted(majors(ps), key=lambda p: (not p.get('winner'), -(p.get('score') or 0), p['id']))
    if not rows:
        return '<p class="note">No players: the run has no setup, checkpoints or final state.</p>'
    masters, source = vassal_masters(m, ps)
    head = ('<table><thead><tr><th>civilization</th><th>leader</th><th class="num">team</th><th>alive</th>'
            '<th class="num">score</th><th class="num">cities</th><th class="num">pop.</th><th class="num">techs</th>'
            '<th class="num">policies</th><th>era</th><th class="num">wonders</th><th>religion</th></tr></thead><tbody>')
    body = []
    for p, depth, more in _vassal_rows(rows, masters):
        cls = 'winner' if p.get('winner') else ('dead' if p.get('alive') is False else '')
        tag = ''
        if depth:
            cls = (cls + ' vassal' + (' vmore' if more else '')).strip()
            says = f"{p['name']} is {ps[masters[p['id']]]['name']}'s vassal"
            tag = f' <span class="vtag" title="{h(says)}">vassal</span>'
        era = p.get('eraType') or p.get('era')
        body.append(f'<tr class="{cls}"' + (f' style="--d:{depth}"' if depth > 1 else '') + '><td>'
                    f'<span class="dot" style="background:var(--p{p["id"]})"></span>{h(p["name"])}{tag}'
                    + (' <span class="pill">winner</span>' if p.get('winner') else '')
                    + (' <span class="muted">(human slot)</span>' if p.get('human') else '') + '</td>'
                    f'<td>{h(p.get("leaderName"))}</td><td class="num">{h(p.get("team"))}</td>'
                    f'<td>{"yes" if p.get("alive") else "no" if p.get("alive") is False else "-"}</td>'
                    f'<td class="num">{fmt(p.get("score"))}</td><td class="num">{fmt(p.get("cities"))}</td>'
                    f'<td class="num">{fmt(p.get("population"))}</td><td class="num">{fmt(p.get("techs"))}</td>'
                    f'<td class="num">{fmt(p.get("policies"))}</td><td>{h(era_name(era))}</td>'
                    f'<td class="num">{fmt(p.get("wonders"))}</td><td>{h(religion(p.get("religion")))}</td></tr>')
    minors = sorted((p for p in ps.values() if p['minor']), key=lambda p: p['name'])
    tail = ''
    if masters:
        tail += ('<p class="note">Vassals are listed under their master, as at the end of the game: '
                 + ('from final-state.json.' if source == 'final-state.json' else
                    'from the replay messages (became and no longer a vassal, restored as one, eliminated) and the '
                    "wars at the end (a vassal has its master's wars). The game does not announce every end of a "
                    'vassalage, so now and then a former vassal still shows here.') + '</p>')
    if minors:
        alive = sum(1 for p in minors if p.get('alive') is not False)
        tail += (f'<p class="note">City-states ({alive} of {len(minors)} alive): '
                + ', '.join(f'<span class="dot" style="background:var(--p{p["id"]})"></span>{h(p["name"])}'
                            + (' (gone)' if p.get('alive') is False else '') for p in minors) + '</p>')
    return '<div class="scroll">' + head + ''.join(body) + '</tbody></table></div>' + tail


def _charts(m, ps):
    ids = {p['id'] for p in majors(ps)}
    if not ids:
        return '<p class="note">No per-player data.</p>'
    out, missing, zero, last = [], [], [], []
    for key, title, sources in CHARTS:
        source, data = series(m, sources, ids)
        if not data:
            (last if source else missing).append(title)
            continue
        if not any(v for pts in data.values() for _, v in pts):
            zero.append(title)
            continue
        lines = [(label(ps[pid]['name']), f'var(--p{pid})', _thin(pts), ps[pid].get('alive') is False)
                 for pid, pts in sorted(data.items())]
        out.append(f'<div class="chart"><div class="t"><b>{h(title)}</b><span class="muted">{h(source)}</span></div>'
                   f'{series_svg(lines, label_width=116, title=title)}</div>')
    note = ('<p class="note">Lines end at their labels (dashed: eliminated). Sources in order: replay data, the per-turn '
            'checkpoints, the fingerprint metrics.' + (f' No data for: {h(", ".join(missing))}.' if missing else '')
            + (f' Zero all game: {h(", ".join(zero))}.' if zero else '')
            + (f' Only the last turn kept (detail summary): {h(", ".join(last))}.' if last else '') + '</p>')
    return note + '<div class="charts">' + ''.join(out) + '</div>' if out else '<p class="note">No per-player series: no replay data, checkpoints or fingerprint metrics.</p>'


def _hex_svg(width, height, groups, extra='', title=''):
    """groups: [(fill, [(x, y) plot coordinates])]; the map with north up and odd rows shifted half a hex."""
    W, H = width * HEX_W + HEX_W / 2, height * HEX_ROW + 2 * HEX_R - HEX_ROW + 2
    r, half = HEX_R, HEX_W / 2 + 0.05
    out = [f'<svg viewBox="-{HEX_W / 2 + 1:.0f} -{r + 1:.0f} {W + 2:.0f} {H:.0f}" role="img" aria-label="{h(title)}">'
           f'<defs><path id="hx" d="M0,-{r}L{half},-{r / 2:.2f}V{r / 2:.2f}L0,{r}L-{half},{r / 2:.2f}V-{r / 2:.2f}Z"/></defs>']
    for fill, cells in groups:
        if cells:
            out.append(f'<g fill="{fill}">' + ''.join(
                f'<use href="#hx" x="{x * HEX_W + (HEX_W / 2 if y % 2 else 0):g}" y="{(height - 1 - y) * HEX_ROW:.1f}"/>'
                for x, y in cells) + '</g>')
    out.append(extra + '</svg>')
    return ''.join(out)


def _plot_xy(m_width, i):
    return i % m_width, i // m_width


def _terrain_colour(plot):
    ptype, terrain, feature, wonder = plot
    if wonder:
        return WONDER
    if ptype == 0:
        return MOUNTAIN
    colour = FEATURES.get(feature) or TERRAIN.get(terrain) or ('#2c5a85' if ptype == 3 else '#8a8a7a')
    if ptype == 1:
        colour = _mix(colour, '#000000', 0.18)
    return colour


def _maps(m, ps):
    map0, final = m.get('map0'), (m['final'] or {}).get('map') or {}
    width = (map0 or {}).get('width') or final.get('width')
    height = (map0 or {}).get('height') or final.get('height')
    if not width or not height:
        return '<p class="note">No map: t000-map.json and final-state.json are missing.</p>'
    parts = []
    starts = ''
    if map0:
        groups = {}
        for i, plot in enumerate(map0['plots']):
            if plot[0] == 3 and plot[1] == 'TERRAIN_OCEAN' and not plot[2]:
                continue  # the background
            groups.setdefault(_terrain_colour(plot), []).append(_plot_xy(width, i))
        for p in map0.get('players') or []:
            if isinstance(p, dict) and p.get('startX') is not None and not p.get('barbarian'):
                x, y = p['startX'], p['startY']
                cx, cy = x * HEX_W + (HEX_W / 2 if y % 2 else 0), (height - 1 - y) * HEX_ROW
                rr = 3.6 if not p.get('minor') else 2.4
                starts += (f'<circle cx="{cx:g}" cy="{cy:.1f}" r="{rr}" fill="var(--p{p.get("slot")})" stroke="#fff" stroke-width="1">'
                           f'<title>{h(ps.get(p.get("slot"), {}).get("name"))} start</title></circle>')
        bg = f'<rect x="-6" y="-7" width="{width * HEX_W + 12}" height="{height * HEX_ROW + 12}" fill="#2c5a85"/>'
        svg = _hex_svg(width, height, sorted(groups.items()), starts, 'map at turn 0')
        svg = svg.replace('</defs>', '</defs>' + bg, 1)
        parts.append(f'<div class="chart"><div class="t"><b>Turn 0</b><span class="muted">terrain, starting positions</span></div>{svg}</div>')
    owners = final.get('owners')
    if owners:
        try:
            owner_list = [int(x) for x in str(owners).split(',') if x != '']
        except ValueError:
            owner_list = []
        water = {i for i, plot in enumerate((map0 or {}).get('plots') or []) if plot[0] == 3}
        groups = {}
        for i, o in enumerate(owner_list):
            if o >= 0:
                groups.setdefault(f'var(--p{o})', []).append(_plot_xy(width, i))
            elif i not in water and map0:
                groups.setdefault('var(--land)', []).append(_plot_xy(width, i))
        cities = ''
        for c in final.get('cities') or []:
            if not isinstance(c, dict) or not isinstance(c.get('plot'), int):
                continue
            x, y = _plot_xy(width, c['plot'])
            cx, cy = x * HEX_W + (HEX_W / 2 if y % 2 else 0), (height - 1 - y) * HEX_ROW
            owner = ps.get(c.get('owner'), {})
            cap = c.get('capital') and not owner.get('minor')
            cities += (f'<circle cx="{cx:g}" cy="{cy:.1f}" r="{3.4 if cap else 2.2}" fill="var(--edge)" stroke="var(--p{c.get("owner")})"'
                       f' stroke-width="{1.6 if cap else 1.2}"><title>{h(c.get("name"))} ({h(owner.get("name"))}, population '
                       f'{h(c.get("population"))})</title></circle>')
            if cap:
                cities += (f'<text x="{cx + 4.5:g}" y="{cy + 3:.1f}" font-size="9" font-weight="600" fill="var(--fg)" '
                           f'stroke="var(--edge)" stroke-width="2.2" paint-order="stroke">{h(owner.get("name"))}</text>')
        bg = (f'<rect x="-6" y="-7" width="{width * HEX_W + 12}" height="{height * HEX_ROW + 12}" '
              f'fill="{"var(--water)" if map0 else "var(--land)"}"/>')
        order = sorted(groups.items(), key=lambda g: g[0] != 'var(--land)')
        svg = _hex_svg(width, height, order, cities, 'territory at the end').replace('</defs>', '</defs>' + bg, 1)
        parts.append(f'<div class="chart"><div class="t"><b>Turn {h(m["final"].get("turn"))}</b><span class="muted">territory, '
                     f'cities (capitals named)</span></div>{svg}</div>')
    elif map0:
        parts.append('<div class="chart"><p class="note">No final territory: the run has no final-state.json (an older run, '
                     'or the game did not end normally).</p></div>')
    return '<div class="maps">' + ''.join(parts) + '</div>'


def _timeline(m, ps):
    evs = events(m, ps)
    if not evs:
        return '<p class="note">No events: the run has no replay messages or checkpoints.</p>'
    kinds = [k for k in KINDS if any(e['kind'] == k for e in evs)]
    turns = [e['turn'] for e in evs if isinstance(e['turn'], int)]
    t0, t1 = 0, max(turns + [m['status'].get('endTurn') or 0, 1])
    width, left, row = 1000, 128, 16
    strip_kinds = [k for k in kinds if k != 'city' or len(kinds) < 3]
    height = row * len(strip_kinds) + 22
    X = lambda t: left + (t - t0) * (width - left - 10) / max(t1 - t0, 1)
    svg = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="event timeline">']
    for k, kind in enumerate(strip_kinds):
        y = 8 + k * row
        svg.append(f'<text x="{left - 8}" y="{y + 4}" text-anchor="end" font-size="11" fill="var(--fg)">{h(KINDS[kind])}</text>'
                   f'<line x1="{left}" x2="{width - 10}" y1="{y}" y2="{y}" stroke="var(--line)" stroke-width="0.6"/>')
        for e in evs:
            if e['kind'] == kind and isinstance(e['turn'], int):
                fill = f'var(--p{e["player"]})' if e['player'] in ps else 'var(--muted)'
                svg.append(f'<circle cx="{X(e["turn"]):.1f}" cy="{y}" r="3.2" fill="{fill}" stroke="var(--panel)" stroke-width="0.6">'
                           f'<title>T{e["turn"]}: {h(e["text"][:120])}</title></circle>')
    for t in ticks(t0, t1, 8, integer=True):
        svg.append(f'<text x="{X(t):.1f}" y="{height - 4}" text-anchor="middle" font-size="10" fill="var(--muted)">{t}</text>')
    svg.append('</svg>')
    players_in = sorted({e['player'] for e in evs if e['player'] in ps}, key=lambda pid: (ps[pid]['minor'], ps[pid]['name']))
    filters = ('<div class="filters" id="tlf" hidden><label>kind <select id="tlk"><option value="">all</option>'
               + ''.join(f'<option value="{k}">{h(KINDS[k])}</option>' for k in kinds) + '</select></label>'
               '<label>player <select id="tlp"><option value="">all</option>'
               + ''.join(f'<option value="{pid}">{h(ps[pid]["name"])}</option>' for pid in players_in) + '</select></label></div>')
    rows = ''.join(f'<tr data-k="{e["kind"]}" data-p="{h(e["player"])}"><td class="num">{h(e["turn"])}</td><td>{h(KINDS[e["kind"]])}</td>'
                   f'<td>' + (f'<span class="dot" style="background:var(--p{e["player"]})"></span>{h(ps[e["player"]]["name"])}'
                              if e['player'] in ps else '') + f'</td><td>{h(e["text"])}</td></tr>' for e in evs)
    return (f'<div class="chart scroll">{"".join(svg)}</div>{filters}<div class="tlbox"><table id="tl"><thead><tr><th class="num">turn</th>'
            f'<th>kind</th><th>player</th><th>event</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<p class="note">{len(evs)} events: the replay messages (founded cities only in the table'
            + (' and here' if 'city' in strip_kinds else '') + '), era changes and eliminations from the checkpoints.</p>')


def _performance(m):
    per = turn_seconds(m)
    out = []
    if per:
        k = 10
        avg = [(per[i][0], sum(s for _, s in per[max(0, i - k + 1):i + 1]) / len(per[max(0, i - k + 1):i + 1])) for i in range(len(per))]
        out.append('<div class="chart"><div class="t"><b>Host seconds per turn</b><span class="muted">dashed: 10-turn mean</span></div>'
                   + series_svg([('s/turn', 'var(--bar)', _thin(per), False), ('mean', 'var(--ref)', _thin(avg), True)],
                                width=1000, height=200, title='host seconds per turn') + '</div>')
    timings = m['host'].get('timings') or {}
    if timings:
        keep = [(k, v) for k, v in timings.items() if isinstance(v, (int, float)) and k != 'total' and v >= 0.05]
        out.append('<p class="note">Host phases: ' + ', '.join(f'{h(k.replace("_", " "))} {fmt(float(v), 1)} s' for k, v in keep)
                   + (f'; total {fmt(float(timings["total"]), 1)} s' if isinstance(timings.get('total'), (int, float)) else '') + '.</p>')
    mem = m['host'].get('memory') or {}
    if mem:
        out.append(f"<p class=\"note\">Host memory: peak working set {fmt(mem.get('peakWorkingSetMB'), 0)} MB, peak private "
                   f"{fmt(mem.get('peakPrivateMB'), 0)} MB, {fmt(mem.get('cpuSeconds'), 1)} CPU seconds.</p>")
    return ''.join(out) or '<p class="note">No turn times.</p>'


def _health(m):
    host = m['host']
    if not host:
        return '<p class="note">No host report (the host did not start, or it is an older host).</p>'
    rows = []
    st = host.get('status')
    rows.append(('Host status', f'{h(st)}' + (f' &middot; {h(host.get("error"))}' if host.get('error') else '')))
    lua = host.get('luaErrors') or {}
    errs = lua.get('list') or []
    lua_text = f"{fmt(lua.get('failing', 0))} counted, {fmt(lua.get('total', len(errs)))} in all"
    if errs:
        lua_text += '<ul>' + ''.join(f'<li><span class="mono">{h(Path(str(e.get("file") or "?")).name)}:{h(e.get("line"))}</span> '
                                     f'{h(e.get("message"))} <span class="muted">({h(e.get("context"))}, {h(e.get("phase"))}'
                                     + (', not counted' if e.get('counted') is False else '') + ')</span></li>'
                                     for e in errs[:40]) + '</ul>'
    rows.append(('Lua errors', lua_text))
    stubs = [c for c in (host.get('stubCalls') or {}).get('calls') or [] if not c.get('knownHarmless')]
    rows.append(('Stubbed calls', 'none outside the harmless list' if not stubs else '<ul>' + ''.join(
        f'<li><span class="mono">{h(c.get("method"))}</span> x{fmt(c.get("count"))} (first in {h(c.get("firstPhase"))})</li>'
        for c in stubs[:40]) + '</ul>'))
    prof = host.get('profiles') or {}
    sel = prof.get('selected') or []
    rows.append(('UI profiles', (', '.join(f'<span title="{h(p.get("reason"))}">{h(p.get("name"))}</span> <span class="muted">'
                                           f'({h(p.get("hook"))})</span>' for p in sel) or 'none selected')
                 + (f'<div class="muted">load errors: {h("; ".join(prof["loadErrors"]))}</div>' if prof.get('loadErrors') else '')))
    add = host.get('addins') or {}
    if add:
        failed = [c for c in add.get('contexts') or [] if c.get('status') not in ('run', 'skipped')]
        rows.append(('UI add-ins', f"{fmt(add.get('run', 0))} run, {fmt(add.get('failed', 0))} failed, {fmt(add.get('skipped', 0))} skipped"
                     + ('<ul>' + ''.join(f'<li>{h(c.get("context"))} <span class="muted">({h(c.get("mod"))})</span>: {h(c.get("error"))}</li>'
                                         for c in failed[:20]) + '</ul>' if failed else '')))
    ui = host.get('uiFiles') or {}
    if ui:
        unknown = ui.get('unknown') or []
        rows.append(('Unknown UI files', 'none' if not unknown else ', '.join(
            h(u.get('name') if isinstance(u, dict) else u) for u in unknown[:40])))
    minors = host.get('minorCivs') or {}
    if minors.get('pinned'):
        rows.append(('City-state pins', f"{minors.get('matching')} of {minors.get('pinned')} as the spec asked"))
    return _kv(rows)


def _files(run):
    run = Path(run)
    if not run.is_dir():
        return ''
    rows = []
    entries = sorted(run.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    for p in entries:
        if p.is_dir():
            files = [f for f in p.rglob('*') if f.is_file()]
            n, total = len(files), sum(f.stat().st_size for f in files)
            what = FILES.get(p.name) or ('state dump (full detail)' if p.name.startswith('dump-t') else '')
            extra = ''
            if p.name == 'Logs' and files:
                shown = sorted(files, key=lambda f: -f.stat().st_size)[:12]
                extra = '<div class="muted">' + ', '.join(f'<a href="{h(f.relative_to(run).as_posix())}">{h(f.name)}</a> {size(f.stat().st_size)}'
                                                          for f in shown) + (f', and {n - len(shown)} more' if n > len(shown) else '') + '</div>'
            rows.append(f'<tr><td><a href="{h(p.name)}/">{h(p.name)}/</a></td><td class="num">{size(total)}</td>'
                        f'<td class="num">{n}</td><td>{h(what)}{extra}</td></tr>')
        else:
            rows.append(f'<tr><td><a href="{h(p.name)}">{h(p.name)}</a></td><td class="num">{size(p.stat().st_size)}</td>'
                        f'<td class="num"></td><td>{h(FILES.get(p.name, ""))}</td></tr>')
    total = sum(f.stat().st_size for f in run.rglob('*') if f.is_file())
    return ('<div class="scroll"><table><thead><tr><th>file</th><th class="num">size</th><th class="num">files</th><th>what</th></tr></thead><tbody>'
            + ''.join(rows) + f'</tbody></table></div><p class="note">{size(total)} in all (this page included, as written).</p>')


def _section(sid, title, body):
    return f'<h2 id="{sid}">{h(title)}</h2>{body}'


def _guard(fn, *args):
    """A section, or a note when building it failed (the rest of the page still renders)."""
    try:
        return fn(*args)
    except Exception as error:  # noqa: BLE001
        return f'<p class="note">This section could not be built: {h(type(error).__name__)}: {h(error)}</p>'


def render(m, files=True):
    """The run report page for a model from collect()."""
    ps = _guard(players, m)
    if isinstance(ps, str):
        ps = {}
    st = m['status']
    state = st.get('state') or 'unknown'
    started = (st.get('startedUTC') or '')[:16].replace('T', ' ')
    sections = [('result', 'Result', _result), ('players', 'Players', _players_table), ('charts', 'Over the game', _charts),
                ('map', 'Map', _maps), ('timeline', 'Timeline', _timeline)]
    parts = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             f'<title>{h(m["name"])}: c5h run</title><style>{CSS}{PAGE_CSS}{_player_vars(ps)}</style></head><body><main>',
             f'<div class="top"><div><h1>{h(m["name"])}</h1><div class="muted"><span class="state {h(state)}">{h(state)}</span> '
             f'&middot; {h(started)} UTC &middot; civ5-headless run</div></div>'
             f'<button class="theme" id="theme" hidden>theme: auto</button></div>',
             '<div class="toc">' + ''.join(f'<a href="#{sid}">{h(t)}</a>' for sid, t, _ in sections)
             + '<a href="#performance">Performance</a><a href="#health">Health</a><a href="#files">Files</a></div>',
             '<h2 id="setup">Setup</h2>' + _guard(lambda: _kv(_setup_rows(m, ps)))]
    for sid, title, fn in sections:
        parts.append(_section(sid, title, _guard(fn, m, ps)))
    parts.append(_section('performance', 'Performance', _guard(_performance, m)))
    parts.append(_section('health', 'Health', _guard(_health, m)))
    if files:
        parts.append(_section('files', 'Files', _guard(_files, m['folder'])))
    parts.append(f'<p class="note">Written by c5h {h(__version__)} on {datetime.now():%Y-%m-%d %H:%M}; rebuild with '
                 f'<span class="mono">c5h report {h(m["name"])}</span>.</p></main>{SCRIPT}</body></html>')
    return ''.join(parts)


def write(run, model=None):
    """Write <run>/report.html (from model, else from the folder); returns its path."""
    run = Path(run)
    model = model or collect(run)
    path = run / REPORT
    path.write_text(render(model), encoding='utf-8')
    return path


# ---- several runs ----

AGGREGATES = [('Score of the leader', 'REPLAYDATASET_SCORE', 'score', max),
              ('Cities, all civilizations', 'REPLAYDATASET_CITYCOUNT', 'cityCount', sum),
              ('Population, all civilizations', 'REPLAYDATASET_POPULATION', 'population', sum),
              ('Techs of the leader', 'REPLAYDATASET_TECHSKNOWN', 'techs', max)]


def _aggregate(m, dataset, field, fn):
    """[(turn, fn over the major civilizations)] from the replay data."""
    by_turn = {}
    for sets in m['replay'].values():
        for t, v in (sets or {}).get(dataset) or []:
            if isinstance(v, (int, float)):
                by_turn.setdefault(int(t), []).append(v)
    turns = sorted(by_turn)
    while len(turns) > 1 and len(by_turn[turns[-1]]) < len(by_turn[turns[-2]]):
        turns.pop()  # the last turn, which only the players whose turn had started recorded
    return [(t, fn(by_turn[t])) for t in turns]


def render_summary(models, out_dir, title='c5h runs'):
    """The summary page over several runs (models from collect(light=True)); links are relative to out_dir."""
    rows, charts = [], []
    colours = {m['name']: PALETTE[k % len(PALETTE)] for k, m in enumerate(models)}
    for m in models:
        ps = players(m)
        st, spec = m['status'], m['spec']
        win = [p['name'] for p in majors(ps) if p.get('winner')]
        link = os.path.relpath(Path(m['folder']) / REPORT, out_dir).replace('\\', '/')
        has = (Path(m['folder']) / REPORT).is_file()
        mp = spec.get('map') or {}
        world = (spec.get('worldSize') or {}).get('type') if isinstance(spec.get('worldSize'), dict) else m['setup'].get('world')
        seeds = spec.get('seeds') or {}
        dll = st.get('dll') or m['command'].get('dll') or ''
        mods = m['host'].get('mods')
        proc = st.get('process') or {}
        rows.append(f'<tr><td><span class="dot" style="background:{colours[m["name"]]}"></span>'
                    + (f'<a href="{h(link)}">{h(m["name"])}</a>' if has else h(m['name'])) + '</td>'
                    f'<td><span class="state {h(st.get("state"))}">{h(st.get("state") or "?")}</span></td>'
                    f'<td>{h(Path(str(mp.get("file") or mp.get("script") or "").replace(chr(92), "/")).stem)} {h(pretty(world, "WORLDSIZE_"))}</td>'
                    f'<td class="mono">{h(seeds.get("map"))}/{h(seeds.get("sync"))}</td>'
                    f'<td><span class="mono">{h(Path(dll).name)}</span> <span class="mono muted">{h((st.get("dllSHA256") or "")[:8])}</span></td>'
                    f'<td class="num">{h(len(mods) if isinstance(mods, list) else "-")}</td>'
                    f'<td>{h(", ".join(win)) or "-"}</td><td>{h(pretty(st.get("victory"), "VICTORY_")) if st.get("victory") else ""}</td>'
                    f'<td class="num">{fmt(st.get("endTurn"))}</td><td class="num">{fmt(st.get("wallSeconds"), 1)}</td>'
                    f'<td class="num">{fmt(st.get("secondsPerTurn"), 3)}</td><td class="num">{h(_mb(proc.get("PeakWorkingSet")) or "-")}</td></tr>')
    label = len(models) <= 14
    for title_, dataset, field, fn in AGGREGATES:
        lines = [(m['name'][:14], colours[m['name']], _thin(_aggregate(m, dataset, field, fn)), False) for m in models]
        if any(l[2] for l in lines):
            charts.append(f'<div class="chart"><div class="t"><b>{h(title_)}</b><span class="muted">replay data</span></div>'
                          f'{series_svg(lines, label_width=130 if label else 0, title=title_)}</div>')
    per = [(m['name'][:14], colours[m['name']], _thin(turn_seconds(m)), False) for m in models]
    if any(p[2] for p in per):
        charts.append('<div class="chart"><div class="t"><b>Host seconds per turn</b><span class="muted">turn times</span></div>'
                      f'{series_svg(per, label_width=130 if label else 0, title="host seconds per turn")}</div>')
    legend = '' if label else ('<div class="legend">' + ''.join(f'<span><i class="sw" style="background:{c}"></i>{h(n)}</span>'
                                                               for n, c in colours.items()) + '</div>')
    done = [m for m in models if m['status'].get('state') == 'completed']
    turns = sum(m['status'].get('endTurn') or 0 for m in done)
    secs = sum(m['status'].get('wallSeconds') or 0 for m in done)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{h(title)}</title><style>{CSS}{PAGE_CSS}</style></head><body><main>'
            f'<div class="top"><div><h1>{h(title)}</h1><div class="muted">{len(models)} runs, {len(done)} completed, {fmt(turns)} turns '
            f'in {fmt(secs, 0)} s of autoplay</div></div><button class="theme" id="theme" hidden>theme: auto</button></div>'
            '<h2>Games</h2><div class="scroll"><table><thead><tr><th>run</th><th>state</th><th>map</th><th>seeds</th><th>DLL</th>'
            '<th class="num">mods</th><th>winner</th><th>victory</th><th class="num">turn</th><th class="num">autoplay s</th>'
            '<th class="num">s/turn</th><th class="num">peak</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
            + ('<h2>Over the game</h2>' + legend + '<div class="charts">' + ''.join(charts) + '</div>' if charts else '')
            + f'<p class="note">Written by c5h {h(__version__)} on {datetime.now():%Y-%m-%d %H:%M}. Each run has its own report.html.</p>'
            f'</main>{SCRIPT}</body></html>')


def write_summary(folders, out, title='c5h runs'):
    """Write the summary page over the run folders to out; returns its path."""
    out = Path(out)
    models = [collect(f, light=True) for f in folders]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_summary(models, out.parent, title), encoding='utf-8')
    return out


# ---- command line ----

def run_folders(args, cfg=None):
    """Run folders from arguments: run folders, names under the runs folder, or folders of run folders."""
    out = []
    for a in args:
        p = Path(a)
        if not p.exists() and cfg is not None and getattr(cfg, 'runs', None):
            p = Path(cfg.runs) / a
        if (p / 'status.json').is_file():
            out.append(p.resolve())
        elif p.is_dir():
            subs = sorted(d.resolve() for d in p.iterdir() if (d / 'status.json').is_file())
            if not subs:
                raise SystemExit(f'c5h report: {a}: no status.json in it or in its folders')
            out += subs
        else:
            raise SystemExit(f'c5h report: no run folder {a}')
    return out


def main(argv=None, cfg=None):
    p = argparse.ArgumentParser(prog='c5h report', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('runs', nargs='+', help='run folders, run names, or folders of runs')
    p.add_argument('--summary', help='the summary page for several runs (default: <common folder>/summary.html)')
    p.add_argument('--no-runs', action='store_true', help="do not rebuild each run's report.html")
    a = p.parse_args(argv)
    folders = run_folders(a.runs, cfg)
    code = 0
    if not a.no_runs:
        for f in folders:
            try:
                print(write(f))
            except Exception as error:  # noqa: BLE001
                print(f'{f}: {type(error).__name__}: {error}', file=sys.stderr)
                code = 1
    if len(folders) > 1 or a.summary:
        out = Path(a.summary) if a.summary else Path(os.path.commonpath([str(f.parent) for f in folders])) / SUMMARY
        print(write_summary(folders, out))
    return code


if __name__ == '__main__':
    sys.exit(main())
