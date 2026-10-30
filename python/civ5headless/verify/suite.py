"""One run of the verification suite: plan, execute (host phases beside the real game), analyse, report.

The run folder (<runs>/verify-<mode>-<date>-<time>, or --out) holds:
  plan.json        settings, capabilities, inputs (DLL, mods, database, host exe), specs, phases and jobs
  specs/           the resolved specs the games use
  probes/          the Lua probes injected on both sides
  host/<job>/      one host run folder per job (detail standard: logs, turn times, replay data, digests)
  real/            the real-game session: real.json, one folder per game with its log segments and replay data
  loc/             with the real game: the localization database the host games read, copied before it starts
  execution.json   what ran: job states and timings, phases, the load timeline, the cost model
  verify.json      the result: checks, verdict, comparisons, statistics, performance
  report.html      the same as one self-contained page
  verify.log       the progress lines
"""
from __future__ import annotations
import copy
import hashlib
import json
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .. import dbcache, modsdb
from .. import run as host_run
from .. import spec as gamespec
from ..probes import fingerprint_options
from ..realgame import seeds as real_seeds
from . import capabilities, checks, exact, hostside, perf, pins, probes, realside, report, series

FORMAT = 'civ5headless-verify/1'
RESEED_A, RESEED_B = (81001, 82001), (81002, 82002)  # trajectory.reseed's seeds; a pair's other game takes one of B's


def freeze_mods(settings, cfg):
    """The configured active mods as an explicit mod set ("ID:VERSION"), or None when the run has its own set (--mod,
    --no-mods) or none is active. With an explicit set the host games use a registry generated from the .modinfo
    files: the game's own registry is rewritten while the real game launches and activates mods, and a host game
    started then would find no active mods."""
    if settings.no_mods or settings.mods or not cfg.active_mods:
        return None
    return [f'{m.id}:{m.version}' for m in cfg.active_mods]


class Suite:
    def __init__(self, cfg, settings, out_dir=None, caps_overrides=None, play=None, log=None):
        self.cfg, self.settings = cfg, settings
        self.t0 = time.monotonic()
        self.deadline = self.t0 + settings.budget_seconds
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.out = Path(out_dir or settings.out or (cfg.require('runs') / f'verify-{settings.mode}-{stamp}')).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.caps_overrides = caps_overrides or {}
        self.play = play
        self._log = log
        self.notes = []
        self.phases, self.real_games = [], []
        self.sources = {}           # spec file -> the spec it was resolved from (re-resolved with city-state pins)
        self.pinned = {}            # (spec file, city-states) -> pinned spec file
        self.pin_lock = threading.Lock()
        self.pins = None

    def log(self, message):
        line = f'[{time.monotonic() - self.t0:7.1f}s] {message}'
        with open(self.out / 'verify.log', 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        (self._log or print)(line)

    # ---- plan ----

    def prepare(self):
        s, cfg = self.settings, self.cfg
        self.exe = Path(self.caps_overrides.get('exe') or dbcache.find_exe(None, cfg))
        self.caps = capabilities.detect(self.exe, cfg, self.caps_overrides)
        self.log(f'verify {s.mode}: budget {s.budget_minutes:g} min, out {self.out}')
        for k, why in self.caps.notes.items():
            self.log(f'  not available: {k}: {why}')
        frozen = freeze_mods(s, cfg)
        if frozen is not None:
            s.mods = frozen
            self.log(f'mod set: the {len(frozen)} active mod(s), fixed for the whole run')
        # Likewise the game rebuilds Localization-Merged.db when it activates mods: with the real game, the host games
        # read a copy taken before it starts.
        self.loc_db = None
        if s.real and cfg.loc_db and Path(cfg.loc_db).is_file():
            self.loc_db = self.out / 'loc' / Path(cfg.loc_db).name
            self.loc_db.parent.mkdir(exist_ok=True)
            shutil.copy2(cfg.loc_db, self.loc_db)
            self.log(f'localization: {cfg.loc_db} copied before the real game starts')
        # The host games log as the real game does, whatever ai_logging says: with some DLLs AILog changes the game.
        self.host_log_args = []
        if s.real and cfg.config_ini and Path(cfg.config_ini).is_file():
            self.host_log_args = real_seeds.host_log_args(cfg.config_ini)
            self.log(f"logging: the host games log as config.ini says ({' '.join(self.host_log_args)})")
            if set(host_run.LOG_OPTIONS) & set(s.host_args):
                self.notes.append('host_args sets the logging, so the host games may not log as the real game does')
        # The DLL as run.play chooses it (modsdb.choose_dll): the host games get the same one from s.dll and the mods.
        self.dll, why = modsdb.choose_dll(cfg, s.dll, [] if s.no_mods else s.mods)
        self.log(f'DLL {self.dll} ({why})')
        db, info = dbcache.database(cfg, db=s.db, dll=self.dll, exe=self.exe, log=self.log,
                                    mods=[] if s.no_mods else s.mods)
        self.db = Path(db)
        specs = self.out / 'specs'
        specs.mkdir(exist_ok=True)
        self.trajectory = self.write_spec(specs / 'trajectory.json', s.trajectory_spec())
        seeds = self.trajectory['seeds']
        self.setups = []
        for k, sp in enumerate(s.setups):
            name = sp.get('name') or f'setup{k + 1}'
            self.setups.append((name, self.write_spec(specs / f'setup-{name}.json', sp, seeds)))
        self.standard = None
        if s.standard_turns:
            self.standard = self.write_spec(specs / 'standard.json', s.standard_spec, seeds)
        pdir = self.out / 'probes'
        pdir.mkdir(exist_ok=True)
        self.probes = [probes.write_pregame(pdir)] if self.caps.lua_probe else []
        # The fingerprint probe: run.play and RealGameSession.start_game run the same files at the same point.
        self.fingerprint = (fingerprint_options(s.fingerprint) if self.caps.lua_probe and self.caps.fingerprint_probe
                            and self.caps.fingerprint is not None else None)
        # City-state pins from the real game for the host games compared with it (a DLL that leaves the choice to
        # the exe gets them from the host otherwise, which cannot draw what the exe drew).
        self.pinning = bool(s.real and s.pin_minor_civs and self.fingerprint and self.caps.session_class is not None)
        self.build_phases()
        self.history_path = perf.history_path(cfg.require('runs'))
        self.history = perf.load_history(self.history_path)
        self.cost = hostside.CostModel(self._prior_curves())
        self.trim()
        self.real_games = self.build_real_games() if s.real else []
        self.write_plan()

    def write_spec(self, path, spec, seeds=None):
        """Resolve a spec (see resolve), write it to path and remember what it was resolved from."""
        source, resolved = self.resolve(spec, seeds)
        host_run.write(path, resolved)
        self.sources[str(path)] = source
        return resolved

    def _types(self, table, where=''):
        con = sqlite3.connect(self.db.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            return [t for (t,) in con.execute(f'select Type from {table} {where} order by ID')]
        except sqlite3.Error:
            return []
        finally:
            con.close()

    def resolve(self, spec, seeds=None):
        """(the spec as given, completed; the resolved spec). Seeds when the spec has none; with pin_civs, every
        random major civilization named (from settings.civs, then any other playable one): the stock DLL leaves them
        to the exe, which the host cannot reproduce. City-states: in host-only mode, with pin_minor_civs, the spec's
        city-states are named from settings.minor_civs; a real-game comparison pins the real game's instead
        (pin_spec), since the real game cannot be told which to use."""
        s = self.settings
        spec = copy.deepcopy(spec)
        spec.pop('name', None)
        if seeds and not spec.get('seeds'):
            spec['seeds'] = dict(seeds)
        r = gamespec.resolve(spec, self.db)
        spec['seeds'] = dict(r['seeds'])  # the seeds drawn for a spec without any stay
        changed = False
        active = [x for x in r['slots'] if x['status'] in ('taken', 'computer')]
        missing = [x['slot'] for x in active if x['civId'] < 0]
        if s.pin_civs and missing:
            playable = set(self._types('Civilizations', 'where Playable = 1'))
            taken = {x['civ'] for x in active if x['civId'] >= 0}
            choice = [c for c in s.civs if c in playable and c not in taken]
            choice += sorted(playable - taken - set(choice))
            given = {x['slot']: dict(x) for x in spec.get('slots') or []}
            for slot, civ in zip(missing, choice):
                given[slot] = dict(given.get(slot, {}), slot=slot, civ=civ)
            spec['slots'] = [given[k] for k in sorted(given)]
            changed = True
        if s.pin_minor_civs and not s.real and spec.get('minorCivs') is None and r.get('cityStates'):
            known = self._types('MinorCivilizations')
            choice = [c for c in s.minor_civs if c in known]
            choice += [c for c in known if c not in choice]
            spec['minorCivs'] = choice[:int(r['cityStates'])]
            changed = bool(spec['minorCivs']) or changed
        return spec, (gamespec.resolve(spec, self.db) if changed else r)

    def pin_spec(self, spec_file, minor_civs):
        """The spec file of spec_file's game with these city-states (types in slot order): written once."""
        key = (str(spec_file), tuple(minor_civs))
        with self.pin_lock:
            if key not in self.pinned:
                source = dict(self.sources[str(spec_file)], minorCivs=list(minor_civs))
                tag = hashlib.sha256('|'.join(minor_civs).encode()).hexdigest()[:6]
                path = Path(spec_file).with_name(f'{Path(spec_file).stem}-cs{tag}.json')
                host_run.write(path, gamespec.resolve(source, self.db))
                self.pinned[key] = path
            return self.pinned[key]

    def _job(self, name, kind, spec_file, turns, seeds, role, size, probes=True, pin=None, **kw):
        return hostside.Job(name=name, kind=kind, spec=spec_file, turns=turns, seeds=seeds, role=role, size=size,
                            probes=list(self.probes) if probes else [], fingerprint=self.fingerprint if probes else None,
                            pin=pin if self.pinning else None, **kw)

    def build_phases(self):
        s = self.settings
        specs = self.out / 'specs'
        tseeds = (self.trajectory['seeds']['map'], self.trajectory['seeds']['sync'])
        tsize = self.trajectory['worldSize']['type']
        traj = specs / 'trajectory.json'
        setup_jobs = []
        for name, r in self.setups:
            sd = (r['seeds']['map'], r['seeds']['sync'])
            for half in ('a', 'b'):
                setup_jobs.append(self._job(f'setup-{name}-{half}', 'setup', specs / f'setup-{name}.json', s.setup_turns, sd,
                                            f'setup-{name}-{half}', r['worldSize']['type'], essential=True,
                                            pin=f'setup-{name}'))
        t = dict(essential=True, pin='trajectory')
        base = self._job('trajectory', 'trajectory', traj, s.turns, tseeds, 'base', tsize, **t)
        refs = [self._job('trajectory-aa', 'reference', traj, s.turns, tseeds, 'aa', tsize, **t),
                self._job(f'trajectory-skew{s.frame_skew}', 'reference', traj, s.turns, tseeds, 'skew', tsize,
                          frame_skew=s.frame_skew, **t),
                self._job(f'trajectory-perturb{s.heap_perturb}', 'reference', traj, s.turns, tseeds, 'perturb', tsize,
                          heap_perturb=s.heap_perturb, **t)]
        if self.probes or self.fingerprint:
            refs.append(self._job('trajectory-noprobe', 'reference', traj, s.turns, tseeds, 'noprobe', tsize, probes=False, **t))
        self.vary = 'sync' if s.ensemble_vary == 'auto' else s.ensemble_vary
        self.vary_reason = 'configured' if s.ensemble_vary != 'auto' else 'default (no probe result)'
        ensemble = [self._job(f'ensemble-{k + 1:02d}', 'ensemble', traj, s.turns, seeds, 'ensemble', tsize)
                    for k, seeds in enumerate(s.ensemble_seeds(self.vary))]
        if s.ensemble_vary == 'auto' and ensemble:
            # Does the sync seed change this DLL's games? (Vox Populi draws most random numbers from game-state
            # hashes, so its games depend on the map seed only.) Two short games decide before the ensemble starts.
            n = min(s.turns, s.sync_probe_turns)
            other = s.ensemble_seeds('sync', 1)[0]
            sync_jobs = [self._job('syncprobe-a', 'probe', traj, n, tseeds, 'syncprobe-a', tsize, probes=False),
                         self._job('syncprobe-b', 'probe', traj, n, other, 'syncprobe-b', tsize, probes=False)]
        else:
            sync_jobs = []
        phases = []
        if s.single_phase:
            single = [hostside.Phase('single', 1, [base], throughput=True)]
        else:
            single = []
            refs.insert(0, base)
        width = min(s.reference_workers, s.workers)
        while len(refs) % width and ensemble:
            refs.append(ensemble.pop(0))  # full waves, so the phase's throughput is that many games at once
        references = hostside.Phase('references', min(width, len(refs)), refs, throughput=len(refs) % width == 0)
        ens = [hostside.Phase('ensemble', min(s.workers, len(ensemble)), ensemble, throughput=True)] if ensemble else []
        if self.pinning:
            # The pinned games wait for the real game to start theirs, so the ensemble plays first.
            if sync_jobs:
                phases.append(hostside.Phase('probe', len(sync_jobs), sync_jobs))
            phases += ens
            if setup_jobs:
                phases.append(hostside.Phase('setups', min(s.workers, len(setup_jobs)), setup_jobs))
            phases += single + [references]
        else:
            setup_jobs += sync_jobs
            if setup_jobs:
                phases.append(hostside.Phase('setups', min(s.workers, len(setup_jobs)), setup_jobs))
            phases += single + [references] + ens
        if s.reseed_turns and self.caps.has('--reseed'):
            # New seeds for a loaded game: a game saved at reseed_save_turn, then that save played on with two seed pairs
            # that differ in one seed each, the first of them twice.
            turn, end = s.reseed_save_turn, s.reseed_save_turn + s.reseed_turns
            phases.append(hostside.Phase('reseed-save', 1, [self._job('reseed-save', 'reseed', traj, turn + 1, tseeds, 'reseed-save', tsize,
                                                                      save_at=[turn])]))
            phases.append(hostside.Phase('reseed', min(s.workers, 4), [
                self._job(f'reseed-{role}', 'reseed', traj, end, seeds, f'reseed-{role}', tsize)
                for role, seeds in (('a', RESEED_A), ('a2', RESEED_A), ('map', (RESEED_B[0], RESEED_A[1])),
                                    ('sync', (RESEED_A[0], RESEED_B[1])))]))
        long_jobs = []
        if self.standard is not None:
            long_jobs.append(self._job('standard', 'standard', specs / 'standard.json', s.standard_turns,
                                       (self.standard['seeds']['map'], self.standard['seeds']['sync']), 'standard',
                                       self.standard['worldSize']['type'], essential=bool(s.real), pin='standard'))
        for k in range(s.victory_games):
            sync = s.ensemble_seed + 5000 + k
            long_jobs.append(self._job(f'victory-s{sync}', 'victory', traj, s.victory_max_turns, (tseeds[0], sync), 'victory', tsize))
        if long_jobs:
            phases.append(hostside.Phase('long', min(s.workers, len(long_jobs)), long_jobs))
        self.phases = phases

    def between(self, phase):
        """After each phase: how the probe-off reference runs (decide_noprobe); after the game that saves: the save the
        reseed games load (decide_reseed); after the phase with the sync-seed probe: what the ensemble varies
        (ensemble_vary auto)."""
        self.decide_noprobe(phase)
        self.decide_reseed(phase)
        if self.settings.ensemble_vary != 'auto' or not any(j.kind == 'probe' for j in phase.jobs):
            return
        jobs = {j.role: j for j in phase.jobs}
        a, b = jobs.get('syncprobe-a'), jobs.get('syncprobe-b')
        if not (a and b and a.state == 'completed' and b.state == 'completed'):
            self.vary_reason = 'the sync-seed probe did not complete; sync seeds by default'
            self.log(f'ensemble: {self.vary_reason}')
            return
        same, how = exact.same_game(a.run_dir, b.run_dir)
        if same:
            self.vary = 'map'
            self.vary_reason = (f'two games with different sync seeds were identical for {a.turns} turns ({how}): this DLL '
                                'does not draw on the sync seed, so the ensemble varies the map seed (other maps of the same '
                                'script and size)')
            for p in self.phases:
                for j in p.jobs:
                    if j.kind == 'ensemble' and j.state == 'pending':
                        k = int(j.name.rsplit('-', 1)[1]) - 1
                        j.seeds = self.settings.ensemble_seeds('map', k + 1)[k]
        else:
            self.vary_reason = (f'two games with different sync seeds differ ({how})' if same is False else
                                'the sync-seed probe could not compare the games; sync seeds by default')
        self.log(f'ensemble varies the {self.vary} seed: {self.vary_reason}')

    def decide_reseed(self, phase):
        """The reseed games load the --save-at save of the reseed-save game, or are skipped without it."""
        saver = next((j for j in phase.jobs if j.role == 'reseed-save'), None)
        if saver is None:
            return
        save = next((x['path'] for x in saver.status.get('saves') or [] if x.get('kind') == 'turn'), None)
        for p in self.phases:
            for j in p.jobs:
                if j.kind == 'reseed' and j is not saver:
                    if save:
                        j.spec = Path(save)
                    else:
                        j.state, j.error = 'skipped', f'the game that saves left no save ({saver.state})'

    def decide_noprobe(self, phase):
        """A DLL without state digests can only be compared through the fingerprint, so a game without any probe
        cannot be compared with the trajectory game: the probe-off reference then takes sparse fingerprints instead
        (every sparse_every turns), and dense against sparse tells whether the per-turn probe changes the game."""
        if self.fingerprint is None or getattr(self, 'noprobe_decided', False):
            return
        done = [j for j in phase.jobs if j.state == 'completed' and j.run_dir]
        if not done:
            return
        self.noprobe_decided = True
        if any(exact.digest_log(j.run_dir) for j in done):
            return
        every = max(2, int(self.settings.sparse_every))
        for p in self.phases:
            for j in p.jobs:
                if j.role == 'noprobe' and j.state == 'pending':
                    j.fingerprint = dict(every=every, turns=[], split=None)
                    self.log(f'{j.name}: no state digests from this DLL, so sparse fingerprints (every {every} turns) '
                             'against the dense ones')

    def _prior_curves(self):
        """The base game's curve from this machine's history (same setup), as the cost model's prior."""
        key = self.setup_key()
        for h in reversed(self.history):
            curve = (h.get('host') or {}).get('curve')
            if h.get('setupKey') == key and curve:
                return {self.trajectory['worldSize']['type']: curve}
        return {}

    def trim(self):
        """Fit the host plan into the budget: fewer ensemble games, then no single-worker phase, no victory games."""
        s = self.settings
        available = self.deadline - time.monotonic() - s.reserve_seconds
        est = hostside.plan_seconds(self.phases, self.cost)
        ens = next((p for p in self.phases if p.name == 'ensemble'), None)
        while est > available and ens and len(ens.jobs) > s.min_members:
            dropped = ens.jobs.pop()
            dropped.state, dropped.error = 'skipped', 'trimmed to fit the budget'
            ens.workers = min(ens.workers, len(ens.jobs))
            est = hostside.plan_seconds(self.phases, self.cost)
            self._trimmed = getattr(self, '_trimmed', []) + [dropped]
        long = next((p for p in self.phases if p.name == 'long'), None)
        while est > available and long and any(j.kind == 'victory' for j in long.jobs):
            v = [j for j in long.jobs if j.kind == 'victory'][-1]
            long.jobs.remove(v)
            v.state, v.error = 'skipped', 'trimmed to fit the budget'
            self._trimmed = getattr(self, '_trimmed', []) + [v]
            est = hostside.plan_seconds(self.phases, self.cost)
        single = next((p for p in self.phases if p.name == 'single'), None)
        if est > available and single:
            refs = next(p for p in self.phases if p.name == 'references')
            refs.jobs.insert(0, single.jobs[0])
            refs.workers = min(len(refs.jobs), s.workers)
            refs.throughput = True
            self.phases.remove(single)
            self.notes.append('the trajectory game ran beside the references, not alone (budget)')
            est = hostside.plan_seconds(self.phases, self.cost)
        if getattr(self, '_trimmed', None):
            n = sum(1 for j in self._trimmed if j.kind == 'ensemble')
            self.notes.append(f'trimmed to fit the budget: {n} ensemble game(s)' +
                              (f', {len(self._trimmed) - n} victory game(s)' if len(self._trimmed) > n else ''))
        self.estimate = est
        self.log(f'host plan: {sum(len(p.jobs) for p in self.phases)} games in {len(self.phases)} phases, about '
                 f'{est / 60:.1f} min ({available / 60:.1f} min available)')
        for p in self.phases:
            self.log(f'  {p.name}: {len(p.jobs)} game(s) on {p.workers} worker(s)')
        if est > available:
            self.notes.append(f'the host plan (about {est / 60:.1f} min) exceeds the budget; games that cannot finish are skipped')

    def build_real_games(self):
        s = self.settings
        games = [dict(name=f'setup-{name}', kind='setup', spec=r, turns=s.setup_turns) for name, r in self.setups]
        games.append(dict(name='trajectory', kind='trajectory', spec=self.trajectory, turns=s.turns))
        if s.real_aa:
            games.append(dict(name='trajectory-aa', kind='aa', spec=self.trajectory, turns=s.turns))
        if self.standard is not None:
            games.append(dict(name='standard', kind='standard', spec=self.standard, turns=s.standard_turns))
        # Every seed pair costs the real game a relaunch: the trajectory's seeds first, then each other pair together.
        order = {}
        for g in games:
            order.setdefault((g['spec']['seeds']['map'], g['spec']['seeds']['sync']), len(order))
        first = (self.trajectory['seeds']['map'], self.trajectory['seeds']['sync'])
        key = lambda g: (g['spec']['seeds']['map'], g['spec']['seeds']['sync'])
        return sorted(games, key=lambda g: (key(g) != first, order[key(g)]))

    def setup_key(self):
        blob = json.dumps(dict(spec=self.trajectory, turns=self.settings.turns), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12].upper()

    def inputs(self):
        s = self.settings
        if s.no_mods:
            mods = []
        elif s.mods:
            mods = [dict(id=m.split(':')[0], version=m.split(':')[1] if ':' in m else None, name=None) for m in s.mods]
        else:
            mods = [dict(id=m.id, version=m.version, name=m.name) for m in self.cfg.active_mods]
        mods_key = hashlib.sha256(json.dumps(sorted(f"{m['id']}:{m['version']}" for m in mods)).encode()).hexdigest()[:12].upper()
        return dict(dll=dict(path=str(self.dll), sha256=dbcache.sha256_file(self.dll) if self.dll.is_file() else None),
                    exe=dict(path=str(self.exe), sha256=dbcache.sha256_file(self.exe) if self.exe.is_file() else None),
                    database=str(self.db), mods=mods, modsKey=mods_key, setupKey=self.setup_key(),
                    setup=f"{Path(self.trajectory['map']['file']).name} {self.trajectory['worldSize']['type']} "
                          f"seeds {self.trajectory['seeds']['map']}/{self.trajectory['seeds']['sync']}, {self.settings.turns} turns")

    def write_plan(self):
        host_run.write(self.out / 'plan.json', dict(
            format=FORMAT, mode=self.settings.mode, settings=self.settings.to_dict(), capabilities=self.caps.to_dict(),
            inputs=self.inputs(), probes=[str(p) for p in self.probes], fingerprint=self.fingerprint,
            fingerprintProbe=str(self.caps.fingerprint_probe) if self.caps.fingerprint_probe else None,
            pinning=self.pinning, hostLogArgs=self.host_log_args, estimateSeconds=round(self.estimate, 1),
            notes=self.notes, phases=[dict(name=p.name, workers=p.workers, throughput=p.throughput, jobs=[j.to_dict() for j in p.jobs])
                                      for p in self.phases],
            trimmed=[j.to_dict() for j in getattr(self, '_trimmed', [])],
            realGames=[dict(name=g['name'], kind=g['kind'], turns=g['turns']) for g in self.real_games]))

    # ---- execute ----

    def execute(self):
        s = self.settings
        real_state = {'phase': None}
        self.pins = pins.Pins() if self.pinning else None
        host = hostside.HostSide(self.cfg, s, self.phases, self.out, self.deadline, self.t0, str(self.db), self.cost,
                                 play=self.play, log=self.log, between=self.between, pins=self.pins, pin_spec=self.pin_spec,
                                 loc_db=self.loc_db, log_args=self.host_log_args)
        timeline = perf.LoadTimeline(self.t0, hosts=host.sample, real_state=real_state).start()
        real = None
        try:
            host.start()
            if s.real:
                if self.caps.session_class is None:
                    self.log('real game: not available (' + self.caps.notes.get('realGameSession', 'no RealGameSession') + ')')
                    real = dict(games=[], errors=['RealGameSession is not available: ' + self.caps.notes.get('realGameSession', '')],
                                unavailable=True)
                    (self.out / 'real').mkdir(exist_ok=True)
                    host_run.write(self.out / 'real/real.json', real)
                else:
                    self.log(f'real game: {len(self.real_games)} game(s) in one session')
                    side = realside.RealSide(self.cfg, s, self.caps.session_class, self.out, self.real_games, self.probes,
                                             self.deadline, self.t0, log=self.log, state=real_state,
                                             fingerprint=self.fingerprint, pins=self.pins,
                                             fingerprint_module=self.caps.fingerprint)
                    real = side.run()
                    self.log('real game: done' + (f" ({'; '.join(real['errors'])})" if real.get('errors') else ''))
            while host.thread.is_alive():
                host.join(1.0)
        except KeyboardInterrupt:
            host.stop.set()
            if self.pins is not None:
                self.pins.close('interrupted')
            self.log('interrupted: the running host games are killed with their job objects')
            raise
        finally:
            if self.pins is not None:
                self.pins.close('the real game did not start it')
            timeline.stop()
            self.write_execution(host, timeline)
        return real

    def write_execution(self, host, timeline):
        host_run.write(self.out / 'execution.json', dict(
            format=FORMAT, seconds=round(time.monotonic() - self.t0, 1), hostError=host.error,
            ensembleVary=dict(vary=self.vary, reason=self.vary_reason),
            phases=[p.to_dict() for p in self.phases],
            jobs=[dict(j.to_dict(), status=j.status) for p in self.phases for j in p.jobs] + [j.to_dict() for j in getattr(self, '_trimmed', [])],
            loadSamples=timeline.samples, loadPeriod=timeline.period, costModel=self.cost.to_dict()))


# ---- analysis ----

def analyse(out_dir, cfg=None, settings=None, caps_overrides=None, history=True, log=print, self_test=False, foreign=(),
            shift=0.05):
    """Checks, verdict and report from a suite run folder. Writes verify.json and report.html; appends the history
    line when history is true (once per run folder). self_test adds the statistics self-test, with foreign run
    folders from another setup as negative controls. Returns the result dict."""
    out = Path(out_dir)
    plan = host_run.read(out / 'plan.json')
    execution = host_run.read(out / 'execution.json')
    if not plan or not execution:
        raise FileNotFoundError(f'{out} has no plan.json and execution.json (not a c5h verify run folder)')
    if settings is None:
        from . import settings as settings_mod
        settings = settings_mod.Settings(**plan['settings'])
    caps = capabilities.detect(None, cfg, dict(dict(host_options=plan['capabilities'].get('hostOptions', [])), **(caps_overrides or {})))
    jobs = {j['name']: j for j in execution['jobs']}
    real = realside.load(out) if settings.real else None
    ctx = checks.Context(out=out, settings=settings, caps=plan['capabilities'], jobs=jobs, real=real,
                         db=plan['inputs'].get('database'), fingerprint=caps.fingerprint, hazards=caps.hazards, cfg=cfg,
                         phases=execution['phases'])
    ctx.statistics['loadSamples'] = execution.get('loadSamples')
    ctx.statistics['ensembleVary'] = execution.get('ensembleVary') or {}
    result = []
    result += checks.setup_checks(ctx)
    result += checks.health_checks(ctx)
    v = checks.victory_check(ctx)
    if v:
        result.append(v)
    result += checks.reference_checks(ctx)
    result += checks.real_trajectory_checks(ctx)
    result += checks.statistics_checks(ctx)
    entry = history_entry(plan, execution, ctx)
    hpath = perf.history_path(cfg.require('runs')) if cfg is not None else None
    past = [h for h in (perf.load_history(hpath) if hpath else []) if h.get('run') != str(out)]
    result += checks.performance_checks(ctx, past, entry)
    essential = []
    base = ctx.job('base')
    if not base or base.get('state') not in ('completed', 'partial'):
        essential.append('the host trajectory game')
    if settings.real:
        g = ctx.real_game('trajectory')
        if not g or g.get('state') not in ('completed', 'partial'):
            essential.append('the real trajectory game')
    verdict, code = checks.verdict(result, essential, settings.strict)
    headline = make_headline(ctx, settings)
    selftest = None
    if self_test:
        from . import selftest as st
        others = [(Path(f).name, series.game_series(Path(f), ctx.fingerprint, settings.aggregates, settings.exclude_metrics))
                  for f in foreign]
        selftest = st.run(ctx.statistics.get('ensemble') or [], settings, foreign=others, shift=shift, log=log)
    doc = dict(format=FORMAT, utc=perf.utc(), run=str(out), mode=settings.mode, verdict=verdict, exitCode=code,
               headline=headline, essentialMissing=essential, seconds=execution.get('seconds'),
               budgetSeconds=settings.budget_seconds, machine=perf.machine(), inputs=plan['inputs'],
               capabilities=plan['capabilities'], settings=plan['settings'], notes=plan.get('notes', []),
               checks=result, comparisons=ctx.comparisons, statistics=_statistics_doc(ctx), selfTest=selftest,
               performance=_performance_doc(ctx), history=[h for h in past if h.get('setupKey') == entry['setupKey']][-30:] + [entry],
               phases=execution['phases'], jobs=[{k: v for k, v in j.items() if k != 'status'} for j in execution['jobs']],
               real=_real_doc(real), hostError=execution.get('hostError'))
    host_run.write(out / 'verify.json', doc)
    (out / 'report.html').write_text(report.html(doc), encoding='utf-8')
    if history and hpath and not execution.get('historyAppended'):
        perf.append_history(hpath, dict(entry, verdict=verdict))
        execution['historyAppended'] = True
        host_run.write(out / 'execution.json', execution)
    return doc


def make_headline(ctx, settings):
    c = ctx.comparisons
    h = settings.turns
    refs = []
    for key, what in (('aa', 'A/A'), ('skew', 'frame skew'), ('perturb', 'heap perturbation')):
        r = c.get(key)
        if r is not None:
            refs.append(f"{r['matchedTurns']} ({what})")
    ref_text = ', '.join(refs) if refs else 'unknown (no host references)'
    if settings.real:
        r = c.get('real')
        if r is None:
            return f'No real-against-host comparison; the DLL reproduces itself in the host for {ref_text} of {h} turns.'
        return (f"The host matches the real game for {r['matchedTurns']} of {r['compared']} turns; "
                f"the DLL reproduces itself for {ref_text}.")
    return f'Host only: the DLL reproduces itself in the host for {ref_text} of {h} turns.'


def history_entry(plan, execution, ctx):
    base = checks.host_timing_job(ctx)
    host = perf.host_figures(base) if base else None
    real = perf.real_figures(ctx.real, 'trajectory') if ctx.real_mode else None
    inputs = plan['inputs']
    rows = perf.throughput(execution['phases'])
    curve = (host or {}).get('curve') or []
    return dict(utc=perf.utc(), run=str(ctx.out), mode=ctx.settings.mode, setupKey=inputs.get('setupKey'), setup=inputs.get('setup'),
                dllSHA256=(inputs.get('dll') or {}).get('sha256'), exeSHA256=(inputs.get('exe') or {}).get('sha256'),
                modsKey=inputs.get('modsKey'), mods=[m.get('name') or m.get('id') for m in inputs.get('mods') or []],
                machine=perf.machine(),
                host=dict({k: v for k, v in (host or {}).items() if k != 'curve'}, curve=curve[::max(1, len(curve) // 30)] + curve[-1:]) if host else None,
                real={k: v for k, v in (real or {}).items() if k != 'curve'} or None,
                throughput={str(r['workers']): dict(gamesPerHour=r['gamesPerHour'], turnsPerHour=r['turnsPerHour']) for r in rows})


def _statistics_doc(ctx):
    env, b = ctx.statistics.get('envelope') or {}, ctx.statistics.get('bias') or {}
    ref, twin = ctx.statistics.get('reference') or {}, ctx.statistics.get('twin') or {}
    charts = []
    for name, v in sorted((env.get('series') or {}).items()):
        turns = sorted(v['bands'])
        charts.append(dict(series=name, label=series.label(name), coverage=v['coverage'], turns=turns,
                           lo=[v['bands'][t][0] for t in turns], median=[v['bands'][t][1] for t in turns],
                           hi=[v['bands'][t][2] for t in turns], reference=[ref.get(name, {}).get(t) for t in turns],
                           twin=[twin.get(name, {}).get(t) for t in turns] if twin else None,
                           bias=(b.get('series') or {}).get(name)))
    twin_env = ctx.statistics.get('envelopeTwin') or {}
    return dict(reference=ctx.statistics.get('referenceName'), members=len(ctx.statistics.get('ensemble') or []),
                vary=ctx.statistics.get('ensembleVary'),
                envelope={k: v for k, v in env.items() if k != 'series'}, twinCoverage=twin_env.get('coverage'),
                bias={k: v for k, v in b.items() if k != 'series'}, charts=charts)


def _performance_doc(ctx):
    p = ctx.statistics.get('performance') or {}
    return dict(host=p.get('host'), real=p.get('real'), throughput=perf.throughput(ctx.phases, ctx.statistics.get('loadSamples')),
                loadSamples=ctx.statistics.get('loadSamples'))


def _real_doc(real):
    if not real:
        return None
    return {k: v for k, v in real.items() if k != 'games'} | dict(games=[{k: v for k, v in g.items() if k != 'autoplay'} | (
        dict(endTurn=g['autoplay'].get('endTurn'), endReason=g['autoplay'].get('endReason')) if g.get('autoplay') else {})
        for g in real.get('games', [])])


def run(cfg, settings, out_dir=None, caps_overrides=None, play=None, log=None, self_test=False):
    """Plan, execute and analyse one suite run; returns the result dict (verify.json)."""
    suite = Suite(cfg, settings, out_dir, caps_overrides, play, log)
    suite.prepare()
    suite.execute()
    suite.log('analysing')
    doc = analyse(suite.out, cfg, settings, caps_overrides, history=True, log=suite.log, self_test=self_test)
    suite.log(f"verdict {doc['verdict']} (exit {doc['exitCode']}) in {(time.monotonic() - suite.t0) / 60:.1f} min: {doc['headline']}")
    suite.log(f"report: {suite.out / 'report.html'}")
    return doc
