"""The host side of the suite: host games in phases, each with its own number of workers, inside a time budget.

Phases run one after another; inside a phase, its workers take the jobs in order. The phases are what the
throughput figures come from (plan 5.4): a phase with one worker and one game, one with four games at once, and the
ensemble with eight or more. Before each job starts, the cost model estimates how long it takes; a job that cannot
finish before the deadline is skipped (the ensemble is trimmed from its end), except essential jobs, which start
with a time limit that ends at the deadline and may give a partial game.

The cost model starts from a prior (the machine's history file, else a built-in Tiny curve scaled by map size) and
learns from every finished game: per world size, the cumulative autoplay seconds per turn.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import json
import threading
import time
from pathlib import Path

from .. import run as host_run
from .. import winproc

# One Tiny game pinned to a free core: startup + a * turns + b * turns^2 seconds (measured with Vox Populi on a
# 13th-generation Core: 150 turns in about 80 s). The machine's history file replaces it after the first run.
PRIOR = dict(startup=3.5, a=0.16, b=0.0024)
SIZE_FACTOR = {'WORLDSIZE_DUEL': 0.7, 'WORLDSIZE_TINY': 1.0, 'WORLDSIZE_SMALL': 1.8, 'WORLDSIZE_STANDARD': 4.0,
               'WORLDSIZE_LARGE': 7.0, 'WORLDSIZE_HUGE': 11.0}


@dataclass
class Job:
    name: str
    kind: str                       # setup, single, reference, ensemble, victory, standard
    spec: Path                      # resolved spec file
    turns: int
    seeds: tuple | None = None      # (map, sync)
    probes: list = field(default_factory=list)
    fingerprint: dict | None = None # run.play's fingerprint options (None: no fingerprint probe)
    pin: str | None = None          # the real game whose city-states this game takes (pins.py)
    frame_skew: int | None = None
    heap_perturb: int | None = None
    save_at: list = field(default_factory=list)  # turns to save at (trajectory.reseed: the save the others load)
    essential: bool = False
    size: str = 'WORLDSIZE_TINY'
    role: str = ''                  # base, aa, skew, perturb, noprobe, setup-<name>-a/-b, ...
    state: str = 'pending'          # pending, running, completed, partial, failed, skipped
    run_dir: Path | None = None
    started: float | None = None    # seconds since the suite started
    finished: float | None = None
    estimate: float | None = None
    end_turn: int | None = None
    error: str | None = None
    status: dict = field(default_factory=dict)
    concurrency: list = field(default_factory=list)  # running host games, sampled while this job ran
    pinned: list | None = None      # the city-states it was given
    pin_note: str | None = None     # why it was not pinned
    pin_wait: float | None = None   # seconds it waited for the real game's pins

    def to_dict(self):
        return dict(name=self.name, kind=self.kind, role=self.role, turns=self.turns,
                    seeds=list(self.seeds) if self.seeds else None, frameSkew=self.frame_skew,
                    heapPerturb=self.heap_perturb, probes=[str(p) for p in self.probes], size=self.size,
                    fingerprint=self.fingerprint, pin=self.pin, pinned=self.pinned, pinNote=self.pin_note,
                    pinWait=self.pin_wait,
                    essential=self.essential, state=self.state, runDir=str(self.run_dir) if self.run_dir else None,
                    started=self.started, finished=self.finished, estimate=self.estimate, endTurn=self.end_turn,
                    error=self.error, meanConcurrency=round(sum(self.concurrency) / len(self.concurrency), 2) if self.concurrency else None)


@dataclass
class Phase:
    name: str
    workers: int
    jobs: list
    throughput: bool = False        # a throughput measurement (uniform games, all at once)
    started: float | None = None
    finished: float | None = None

    def to_dict(self):
        done = [j for j in self.jobs if j.state == 'completed']
        seconds = (self.finished - self.started) if self.started is not None and self.finished is not None else None
        out = dict(name=self.name, workers=self.workers, throughput=self.throughput, jobs=[j.name for j in self.jobs],
                   started=self.started, finished=self.finished, seconds=seconds, completed=len(done),
                   turns=sum(j.end_turn or 0 for j in done))
        if seconds and done:
            out.update(gamesPerHour=len(done) * 3600 / seconds, turnsPerHour=out['turns'] * 3600 / seconds,
                       meanGameSeconds=sum(j.status.get('hostSeconds') or 0 for j in done) / len(done))
        return out


class CostModel:
    """Estimated wall seconds of a host game: cumulative autoplay seconds per turn, per world size."""

    def __init__(self, prior_curves=None):
        self.curves = {}   # size -> [(turn, seconds)] of the longest observed game
        self.startup = {}  # size -> seconds before autoplay (host seconds - autoplay seconds)
        self.lock = threading.Lock()
        for size, curve in (prior_curves or {}).items():
            if curve:
                self.curves[size] = sorted((int(t), float(s)) for t, s in curve)

    def observe(self, size, turn_times, host_seconds=None, wall_seconds=None):
        """turn_times: [(turn, seconds since autoplay started)] of a finished game."""
        curve = sorted((int(t), float(s)) for t, s in turn_times)
        with self.lock:
            old = self.curves.get(size)
            if len(curve) >= 10 and (not old or curve[-1][0] >= old[-1][0]):  # short games say little about later turns
                self.curves[size] = curve
            if host_seconds and wall_seconds is not None:
                self.startup[size] = max(1.0, host_seconds - wall_seconds)

    def estimate(self, size, turns, concurrency=1):
        with self.lock:
            curve = self.curves.get(size)
            startup = self.startup.get(size, PRIOR['startup'] * max(1.0, SIZE_FACTOR.get(size, 4.0) ** 0.5))
        f = SIZE_FACTOR.get(size, 4.0)
        prior = lambda n: f * (PRIOR['a'] * n + PRIOR['b'] * n * n)
        if curve and turns <= curve[-1][0]:
            autoplay = _interpolate(curve, turns)
        elif curve:
            # beyond the observed turns: the prior's shape, scaled to where the observation ends
            t_end, s_end = curve[-1]
            autoplay = prior(turns) * min(4.0, max(0.25, s_end / max(prior(t_end), 1e-6)))
        else:
            autoplay = prior(turns)
        contention = 1.0 if concurrency <= 4 else 1.1
        return startup + autoplay * contention

    def to_dict(self):
        with self.lock:
            return dict(curves={s: c[::max(1, len(c) // 30)] + [c[-1]] for s, c in self.curves.items()},
                        startup=dict(self.startup))


def _interpolate(curve, turns):
    """Cumulative seconds at turns (within the curve) from [(turn, seconds)]."""
    if turns <= curve[0][0]:
        return curve[0][1] * turns / max(curve[0][0], 1)
    for (t0, s0), (t1, s1) in zip(curve, curve[1:]):
        if t0 <= turns <= t1:
            return s0 + (s1 - s0) * (turns - t0) / max(t1 - t0, 1)
    return curve[-1][1]


def turn_times(folder):
    out = []
    p = Path(folder) / 'turn-times.jsonl'
    try:
        for line in p.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
                out.append((int(r['turn']), float(r['seconds'])))
            except (ValueError, KeyError):
                pass
    except OSError:
        pass
    return out


class HostSide:
    """Runs the phases on threads; thread-safe counters for the load timeline."""

    def __init__(self, cfg, settings, phases, out_dir, deadline, t0, db, cost=None, play=None, log=print, between=None,
                 pins=None, pin_spec=None, loc_db=None, log_args=()):
        """between(phase) is called after each phase (the suite adjusts later jobs there); a job with a pin waits for
        that real game's city-states in pins (pins.Pins) and plays pin_spec(spec, minor civ types) instead. loc_db:
        the localization database for every game (None: the configured one); log_args: the host options that log as
        the real game does, before the host_args setting."""
        self.cfg, self.settings, self.phases = cfg, settings, phases
        self.loc_db = str(loc_db) if loc_db else None
        self.log_args = list(log_args)
        self.between = between
        self.pins, self.pin_spec = pins, pin_spec
        self.out_dir = Path(out_dir)
        self.deadline, self.t0 = deadline, t0   # time.monotonic() values
        self.db = db
        self.cost = cost or CostModel()
        self.play = play or host_run.play
        self.log = log
        self.lock = threading.Lock()
        self.running = 0
        self.stop = threading.Event()
        self.thread = None
        self.error = None

    def now(self):
        return time.monotonic() - self.t0

    def remaining(self):
        return self.deadline - time.monotonic()

    def start(self):
        self.thread = threading.Thread(target=self._run_all, name='host-side', daemon=True)
        self.thread.start()
        return self

    def join(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)

    def _run_all(self):
        try:
            for phase in self.phases:
                if self.stop.is_set():
                    for j in phase.jobs:
                        if j.state == 'pending':
                            j.state, j.error = 'skipped', 'stopped'
                    continue
                self.run_phase(phase)
                if self.between:
                    self.between(phase)
        except BaseException as error:  # noqa: BLE001 (reported in the result, never lost on a thread)
            self.error = f'{type(error).__name__}: {error}'
            self.log(f'host side stopped: {self.error}')

    def run_phase(self, phase):
        masks = winproc.worker_affinities(phase.workers, self.settings.affinity) if self.settings.affinity != 'none' else [None] * phase.workers
        queue = list(phase.jobs)
        qlock = threading.Lock()
        phase.started = self.now()
        self.log(f'host phase {phase.name}: {len(phase.jobs)} game(s) on {phase.workers} worker(s)')

        def worker(slot):
            while not self.stop.is_set():
                with qlock:
                    if not queue:
                        return
                    job = queue.pop(0)
                self.run_job(job, masks[slot] if slot < len(masks) else None, phase.workers)

        threads = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(min(phase.workers, len(queue)) or 0)]
        for th in threads:
            th.start()
            time.sleep(0.2)  # stagger the database copies and map generation a little
        for th in threads:
            th.join()
        phase.finished = self.now()

    def run_job(self, job, affinity, concurrency):
        if job.state == 'skipped':  # the suite decided so after an earlier phase
            return
        job.estimate = round(self.cost.estimate(job.size, job.turns, concurrency), 1)
        spec = job.spec
        if job.pin and self.pins is not None:
            spec = self.pinned_spec(job)
        left = self.remaining() - self.settings.reserve_seconds
        if left <= 5 or (not job.essential and job.estimate > left):
            job.state, job.error = 'skipped', f'budget: needs about {job.estimate:.0f} s, {max(left, 0):.0f} s left'
            self.log(f'  skip {job.name}: {job.error}')
            return
        job.run_dir = self.out_dir / 'host' / job.name
        job.started = self.now()
        job.state = 'running'
        with self.lock:
            self.running += 1
        try:
            s = self.settings
            r = self.play(spec, job.turns, run_dir=job.run_dir, cfg=self.cfg, seeds=dict(map=job.seeds[0], sync=job.seeds[1]) if job.seeds else None,
                          dll=s.dll, db=self.db, mods=None if s.no_mods else s.mods, no_mods=bool(s.no_mods), loc_db=self.loc_db,
                          fingerprint=job.fingerprint or False, lua_probes=[str(p) for p in job.probes],
                          heap_perturb=job.heap_perturb, frame_skew=job.frame_skew, save_at=job.save_at, max_seconds=int(max(left, 10)),
                          stall_seconds=300, max_memory_mb=self.settings.max_memory_mb, affinity=affinity,
                          low_priority=self.settings.low_priority, host_args=self.log_args + list(self.settings.host_args), quiet=True,
                          # the checks read digests, Lua.log, per-turn checkpoints, replay data, turn times and the
                          # host report: standard keeps them all; no per-game report.html (verify writes its own)
                          detail='standard', report=False)
            job.status = r.status or {}
            job.end_turn = job.status.get('endTurn')
            if r.ok:
                job.state = 'completed'
            else:
                job.error = job.status.get('error') or 'failed'
                job.state = 'partial' if job.status.get('endReason') == 'killed' and job.end_turn else 'failed'
        except Exception as error:  # noqa: BLE001
            job.state, job.error = 'failed', f'{type(error).__name__}: {error}'
        finally:
            with self.lock:
                self.running -= 1
            job.finished = self.now()
        if job.state == 'completed':
            self.cost.observe(job.size, turn_times(job.run_dir), job.status.get('hostSeconds'), job.status.get('wallSeconds'))
        self.log(f"  {job.state} {job.name}: turn {job.end_turn}, {job.finished - job.started:.0f} s (estimate {job.estimate:.0f} s)"
                 + (f', {job.error}' if job.error else ''))

    def pinned_spec(self, job):
        """Wait for the city-states of the real game job.pin (no longer than the budget allows) and return the spec
        to play: pinned to them, or job.spec with the reason in job.pin_note."""
        t = time.monotonic()
        room = self.remaining() - self.settings.reserve_seconds - (10 if job.essential else job.estimate)
        if self.pins.get(job.pin) is None:
            self.log(f'  {job.name} waits for the city-states of the real game {job.pin}')
        got = self.pins.wait(job.pin, room)
        job.pin_wait = round(time.monotonic() - t, 1)
        if not got.get('minorCivs'):
            job.pin_note = got.get('reason') or 'no city-states'
            return job.spec
        try:
            spec = self.pin_spec(job.spec, got['minorCivs'])
        except Exception as error:  # noqa: BLE001 (played unpinned; setup.civ-choice reports it)
            job.pin_note = f'cannot pin {got["minorCivs"]}: {type(error).__name__}: {error}'
            self.log(f'  {job.name}: {job.pin_note}')
            return job.spec
        job.pinned = list(got['minorCivs'])
        return spec

    def sample(self):
        """Called by the load timeline: records the running count on the running jobs."""
        with self.lock:
            n = self.running
        for phase in self.phases:
            for j in phase.jobs:
                if j.state == 'running':
                    j.concurrency.append(n)
        return n


def plan_seconds(phases, cost):
    """Estimated wall seconds of the phases, run one after another (each in waves of its workers)."""
    total = 0.0
    for p in phases:
        waves = [p.jobs[i:i + p.workers] for i in range(0, len(p.jobs), p.workers)]
        total += sum(max(cost.estimate(j.size, j.turns, p.workers) for j in w) for w in waves if w)
    return total
