"""Settings of c5h verify: mode defaults, then civ5headless.toml's [verify] and [verify.<mode>] tables, then the
command line (later wins).

  [verify]                       # any key of Settings, for every mode
  turns = 150
  workers = 8
  [verify.quick]                 # only for --quick (also [verify.full], [verify.host-only])
  ensemble = 24

Spec-shaped values (spec, setups, standard_spec) are game specs as in spec.py; setups is a list of specs, each with an
optional "name".
"""
from __future__ import annotations
from dataclasses import dataclass, field, fields
import copy
import json
from pathlib import Path

MODES = ('quick', 'full', 'host-only')

# The trajectory and ensemble game: Tiny Continents, standard speed (plan 5.2, 5.3).
DEFAULT_SPEC = {'map': {'script': 'Assets/Maps/Continents.lua', 'size': 'WORLDSIZE_TINY'},
                'speed': 'GAMESPEED_STANDARD', 'era': 'ERA_ANCIENT', 'difficulty': 'HANDICAP_EMPEROR',
                'seeds': {'map': 30017, 'sync': 40017}}

# Turn-0 setups (plan 5.1): base-game map scripts that every mod set has, a premade map, two speeds and sizes. A
# setup without seeds takes the trajectory game's: the real game reads its seeds when it loads the DLL, so every
# other seed pair costs a relaunch.
DEFAULT_SETUPS = [
    {'name': 'continents-tiny', 'map': {'script': 'Assets/Maps/Continents.lua', 'size': 'WORLDSIZE_TINY'}},
    {'name': 'pangaea-tiny-quick', 'map': {'script': 'Assets/Maps/Pangaea.lua', 'size': 'WORLDSIZE_TINY'},
     'speed': 'GAMESPEED_QUICK'},
    {'name': 'archipelago-small', 'map': {'script': 'Assets/Maps/Archipelago.lua', 'size': 'WORLDSIZE_SMALL'}},
    {'name': 'earth-small', 'map': {'civ5map': 'Assets/Maps/Earth_Small.Civ5Map'}},
]

# Full mode's scale game (plan 5.5): Standard Continents, 60 turns (the trajectory game's seeds).
DEFAULT_STANDARD_SPEC = {'map': {'script': 'Assets/Maps/Continents.lua', 'size': 'WORLDSIZE_STANDARD'},
                         'speed': 'GAMESPEED_STANDARD', 'era': 'ERA_ANCIENT', 'difficulty': 'HANDICAP_EMPEROR'}

# Civilizations given to the major slots a spec leaves random, in this order (those the database has): the stock DLL
# leaves random civilizations to the exe, which the host cannot reproduce, so verify names them. Base-game civs.
DEFAULT_CIVS = ['CIVILIZATION_GREECE', 'CIVILIZATION_EGYPT', 'CIVILIZATION_JAPAN', 'CIVILIZATION_SWEDEN',
                'CIVILIZATION_CHINA', 'CIVILIZATION_ENGLAND', 'CIVILIZATION_FRANCE', 'CIVILIZATION_GERMANY',
                'CIVILIZATION_ROME', 'CIVILIZATION_RUSSIA', 'CIVILIZATION_AMERICA', 'CIVILIZATION_ARABIA',
                'CIVILIZATION_AZTEC', 'CIVILIZATION_INDIA', 'CIVILIZATION_IROQUOIS', 'CIVILIZATION_OTTOMAN',
                'CIVILIZATION_PERSIA', 'CIVILIZATION_SIAM', 'CIVILIZATION_SONGHAI', 'CIVILIZATION_CARTHAGE',
                'CIVILIZATION_BYZANTIUM', 'CIVILIZATION_CELTS']

# City-states given to the host-only games' specs, in this order (those the database has), so that a DLL that leaves
# the choice to the exe gets named city-states rather than the host's draw. Base-game city-states. A comparison with
# the real game pins the real game's city-states instead (pins.py).
DEFAULT_MINOR_CIVS = ['MINOR_CIV_GENEVA', 'MINOR_CIV_BUDAPEST', 'MINOR_CIV_SYDNEY', 'MINOR_CIV_SINGAPORE',
                      'MINOR_CIV_BRUSSELS', 'MINOR_CIV_MONACO', 'MINOR_CIV_KATHMANDU', 'MINOR_CIV_HANOI',
                      'MINOR_CIV_LHASA', 'MINOR_CIV_ALMATY', 'MINOR_CIV_KUALA_LUMPUR', 'MINOR_CIV_CAPE_TOWN',
                      'MINOR_CIV_SIDON', 'MINOR_CIV_TYRE', 'MINOR_CIV_RAGUSA', 'MINOR_CIV_MILAN', 'MINOR_CIV_VALLETTA',
                      'MINOR_CIV_WITTENBERG', 'MINOR_CIV_ZANZIBAR', 'MINOR_CIV_VANCOUVER', 'MINOR_CIV_MOMBASA',
                      'MINOR_CIV_SAMARKAND', 'MINOR_CIV_VILNIUS', 'MINOR_CIV_KABUL']

MODE_DEFAULTS = {
    'quick': dict(budget_minutes=25.0, real=True, real_aa=False, standard_turns=0, ensemble=32, victory_games=0),
    'full': dict(budget_minutes=55.0, real=True, real_aa=True, standard_turns=60, ensemble=64, victory_games=4),
    'host-only': dict(budget_minutes=5.0, real=False, real_aa=False, standard_turns=0, ensemble=8, victory_games=0),
}


@dataclass
class Settings:
    mode: str = 'quick'
    budget_minutes: float = 25.0
    real: bool = True                   # play the real game (quick, full)
    real_aa: bool = False               # full: a second real Tiny game with the same seeds
    spec: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_SPEC))
    turns: int = 150                    # trajectory and ensemble horizon
    map: str | None = None              # overrides of spec: map script, world size, majors, city-states
    size: str | None = None
    players: int | None = None
    city_states: int | None = None
    seeds: list | None = None           # [map, sync] of the trajectory game (default: spec's)
    ensemble: int = 32                  # host ensemble games
    ensemble_seed: int = 70001          # the seeds the ensemble varies: ensemble_seed, ensemble_seed + 1, ...
    ensemble_vary: str = 'auto'         # sync (same map, other sync seeds), map (other map seeds), or auto: sync
                                        # unless a short probe shows the sync seed does not change this DLL's games
    sync_probe_turns: int = 20          # auto: the probe's length
    workers: int = 8                    # host processes at once (the ensemble phase)
    single_phase: bool = True           # the trajectory game alone first (one-worker throughput, clean timings)
    reference_workers: int = 4          # the host references (A/A, skew, perturbation, probe off) side by side
    frame_skew: int = 7                 # host --frame-skew K reference
    heap_perturb: int = 4242            # host --heap-perturb SEED reference
    reseed_save_turn: int = 20          # trajectory.reseed: the save it loads is of this turn of the trajectory game
    reseed_turns: int = 10              # trajectory.reseed: the loaded games' length (0: no such reference)
    heal_turns: int = 2                 # differences that heal within this many turns are transients
    setups: list = field(default_factory=lambda: copy.deepcopy(DEFAULT_SETUPS))
    setup_turns: int = 1                # turn-0 setups play this many turns (the fingerprint prints at turn 0)
    pin_civs: bool = True               # name the civilizations of major slots a spec leaves random
    civs: list = field(default_factory=lambda: list(DEFAULT_CIVS))
    pin_minor_civs: bool = True         # city-states: the real game's for the host games compared with it (from
                                        # its turn-0 fingerprint); host-only, minor_civs in every spec
    minor_civs: list = field(default_factory=lambda: list(DEFAULT_MINOR_CIVS))
    fingerprint: object = True          # the Lua fingerprint probe on both sides: true, or {every, turns, split}
    sparse_every: int = 25              # DLLs without digests: the probe-off reference fingerprints every k turns
    mods: list | None = None            # the mod set as "ID[:VERSION]" strings (default: the configuration's)
    no_mods: bool = False               # no mods: host games, database and real game
    standard_spec: dict = field(default_factory=lambda: copy.deepcopy(DEFAULT_STANDARD_SPEC))
    standard_turns: int = 0             # full: real and host Standard game of this many turns (0: none)
    victory_games: int = 0              # full: host Tiny games to victory
    victory_max_turns: int = 500
    envelope_band: list = field(default_factory=lambda: [0.025, 0.975])
    envelope_target: float = 0.95       # pass at this coverage (or the members' alpha quantile, if lower)
    envelope_alpha: float = 0.05
    envelope_margin: float = 0.02       # warn this far below the threshold, fail further down
    bias_alpha: float = 0.05            # max-T false-flag rate
    bias_one_sided: float = 0.9         # a flagged series sits on one side of the median this often
    bias_z_cap: float = 5.0             # a turn counts at most this many spreads from the median
    bias_min_members: int = 16          # fewer ensemble games: the bias check reports (info) but gives no verdict
    min_members: int = 8                # ensemble games needed at a turn for it to count
    stats_from_turn: int = 2            # statistics ignore earlier turns (every game is the same there)
    aggregates: list = field(default_factory=lambda: ['mean', 'max', 'sum'])
    exclude_metrics: list = field(default_factory=list)   # e.g. ["GOLDENAGETURNS"]
    ui_sections: list = field(default_factory=lambda: ['savedb'])
    pregame_ignore: list = field(default_factory=lambda: ['GetNickName', 'GetActivePlayer'])
    host_args: list = field(default_factory=list)
    affinity: str = 'p-first'           # host worker pinning: p-first or none
    low_priority: bool | None = None    # host processes below normal priority (default: when the real game runs)
    max_memory_mb: int = 3500
    reserve_seconds: float = 15.0       # kept free at the end of the budget for analysis and the report
    regression_threshold: float = 0.15  # history: warn when the host is this much slower than its recent runs
    strict: bool = False                # warnings count as failures for the exit code
    dll: str | None = None
    db: str | None = None
    out: str | None = None

    @property
    def budget_seconds(self):
        return self.budget_minutes * 60.0

    def trajectory_spec(self):
        """The trajectory/ensemble spec with the overrides applied and the seeds set."""
        spec = copy.deepcopy(self.spec)
        m = dict(spec.get('map') or {})
        if self.map:
            m.pop('civ5map', None); m.pop('type', None)
            m['script'] = self.map
        if self.size:
            m['size'] = self.size
        spec['map'] = m
        if self.players:
            spec['majors'] = int(self.players)
        if self.city_states is not None:
            spec['cityStates'] = int(self.city_states)
        if self.seeds:
            spec['seeds'] = {'map': int(self.seeds[0]), 'sync': int(self.seeds[1])}
        spec.setdefault('seeds', {'map': 30017, 'sync': 40017})
        return spec

    def ensemble_seeds(self, vary='sync', n=None):
        """[(map, sync)] of the ensemble. vary sync: the trajectory's map seed with other sync seeds; vary map: other
        map seeds with the trajectory's sync seed. The trajectory's own seed is skipped."""
        base = self.trajectory_spec()['seeds']
        key = 'map' if vary == 'map' else 'sync'
        out, s = [], int(self.ensemble_seed)
        while len(out) < (self.ensemble if n is None else n):
            if s != int(base[key]):
                out.append((s, int(base['sync'])) if key == 'map' else (int(base['map']), s))
            s += 1
        return out

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


class SettingsError(ValueError):
    pass


def _apply(s, table, where):
    names = {f.name for f in fields(Settings)}
    for key, value in (table or {}).items():
        k = key.replace('-', '_')
        if isinstance(value, dict) and key in MODES:
            continue  # a mode table
        if k not in names or k == 'mode':
            raise SettingsError(f'unknown setting {key!r} in {where}')
        setattr(s, k, copy.deepcopy(value))


def build(mode='quick', table=None, overrides=None):
    """Settings for mode from a [verify] table (with optional mode sub-tables) and command-line overrides
    ({name: value}, None values ignored)."""
    if mode not in MODES:
        raise SettingsError(f'unknown mode {mode!r}')
    s = Settings(mode=mode)
    for k, v in MODE_DEFAULTS[mode].items():
        setattr(s, k, v)
    table = table or {}
    _apply(s, table, '[verify]')
    _apply(s, table.get(mode), f'[verify.{mode}]')
    for k, v in (overrides or {}).items():
        if v is not None:
            setattr(s, k, v)
    for key in ('spec', 'standard_spec'):
        v = getattr(s, key)
        if isinstance(v, str):  # a spec file
            setattr(s, key, json.loads(Path(v).read_text(encoding='utf-8-sig')))
    if isinstance(s.setups, str):
        s.setups = json.loads(Path(s.setups).read_text(encoding='utf-8-sig'))
    s.setups = [json.loads(Path(x).read_text(encoding='utf-8-sig')) if isinstance(x, str) else x for x in s.setups]
    if s.low_priority is None:
        s.low_priority = bool(s.real)
    if s.ensemble_vary not in ('auto', 'sync', 'map'):
        raise SettingsError(f'ensemble_vary must be auto, sync or map, not {s.ensemble_vary!r}')
    if s.workers < 1 or s.turns < 1 or s.ensemble < 0 or s.reference_workers < 1:
        raise SettingsError('workers, reference_workers and turns must be positive, ensemble not negative')
    if s.mods and s.no_mods:
        raise SettingsError('mods and no_mods exclude each other')
    if isinstance(s.fingerprint, dict) and set(s.fingerprint) - {'every', 'turns', 'split'}:
        raise SettingsError('fingerprint is true, false or a table with every, turns and split')
    return s
