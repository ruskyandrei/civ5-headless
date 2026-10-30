# Verification suite: `c5h verify`

`c5h verify` tells you, for your DLL, mods and maps, how accurately a headless game represents a real Civilization V
autoplay game, and how fast and lean the host is against the real game. It answers three questions:

1. **Is the setup exact?** The same database, the same pregame state and the same game at turn 0, on both sides.
2. **Does the game follow the same path?** Turn by turn, how long the host and the real game stay identical, measured
   against how long the DLL reproduces itself.
3. **Does it behave the same, statistically?** Whether the real game's per-turn metrics sit inside the spread of many
   host games, without a metric drifting to one side.

It also measures speed and memory on both sides and keeps a history per machine.

## Running it

```
c5h verify                 # quick mode (the default), about 10 minutes
c5h verify --full          # about 20-25 minutes
c5h verify --host-only     # about 5 minutes, no real game: for CI
c5h verify --analyse runs/verify-quick-20261025-201500    # checks and report again from a finished run
c5h verify --host-only --no-mods --dll "<game>/Assets/DLC/Expansion2/CvGameCore_Expansion2.dll"   # the stock DLL
```

With `--no-mods` (or `--mod ID[:VER]`, repeatable) the host games, the database (built from the game's files when
`db` is not configured) and the real game use that mod set instead of the configured one.

The result is a run folder, `<runs>/verify-<mode>-<date>-<time>/`, with `report.html` (one self-contained page:
open it in a browser) and `verify.json` (the same content for scripts). `c5h verify -h` lists every option.

| Mode | Real game | Host, at the same time | Time |
|---|---|---|---|
| quick | one launch; 4 setups to turn 0; one seeded Tiny game of 150 turns | the same games; A/A, frame-skew, heap-perturbation and probe-off references; an ensemble of 32 Tiny games | about 10 min |
| full | adds a real A/A of the Tiny game and a Standard game of 60 turns | adds the Standard game, a 64-game ensemble and 4 Tiny games to victory | about 20-25 min |
| host-only | none | 4 setups twice; the references; an ensemble of 8 | about 5 min |

The real game needs the real-game session (`civ5headless.realgame.session`, FireTuner enabled; `c5h doctor`). The
suite never changes your game files: it reads the game's database (an export through the session, else the game's
cache) and its logs, and seeds games through the session.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | passed: no check failed (warnings allowed) |
| 1 | a check failed, or, with `--strict`, a check warned |
| 2 | usage or configuration error |
| 3 | incomplete: the host trajectory game or the real trajectory game did not run, so there is no verdict |

## How a run works

The host side plays its games in **phases**, one after another, while the real game plays its own games in one
session:

1. **setups**: each turn-0 setup twice (A/A), and two short games that tell whether the sync seed changes this DLL's
   games (see the ensemble below);
2. **single**: the trajectory game alone on one worker: clean timings and the one-worker throughput;
3. **references**: A/A, frame skew, heap perturbation and probe off, four at a time;
4. **ensemble**: the ensemble, eight at a time;
5. **long** (full mode): the Standard game and the games to victory.

With the real game (quick and full), the host games compared with it wait for its city-states (below), so the order
is: the two sync-seed games (**probe**), the **ensemble**, then **setups**, **single**, **references** and **long**:
by the time the ensemble is done, the real game has started its setups and its trajectory game.

Both sides run the same Lua probes at the same point (the game started, before turn 0 is processed): the fingerprint
probe (`probes/fingerprint.lua`, through `run.play(fingerprint=...)` and `RealGameSession.start_game(fingerprint=...)`,
every turn by default; `fingerprint = {every = 5}` and the like set its `every`, `turns` and `split`), then the
suite's PreGame probe.

The real game reads its seeds when it loads the DLL, so each distinct pair of seeds costs a relaunch. The setups
therefore take the trajectory game's seeds (other map scripts, sizes and speeds, same seeds), and the real games are
played grouped by seed pair, the trajectory game's pair first: quick mode needs a single launch.

**Civilizations and city-states.** Some DLLs (Vox Populi) make the random choices of civilizations and city-states
themselves, which the host reproduces; the stock DLL leaves them to the game's exe, so the host has to choose, and its
draw cannot be the exe's. So:

- every spec names the civilization of each major slot: the suite fills the slots a spec leaves random from `civs`
  (base-game civilizations, in order, skipping any the spec already uses or the database lacks); `pin_civs = false`
  turns this off;
- the real game's front end cannot be told which city-states to use, so the suite reads them from each real game's
  turn-0 fingerprint (the probe's `player` lines, `minor=MINOR_CIV_X` per slot) as soon as that game has started,
  and gives them to the host games compared with it (its setup's A/A pair; the trajectory game and its references;
  the Standard game) as the spec's `minorCivs`. Such a host game waits for them, at most as long as the budget
  allows; without them it plays unpinned and `setup.civ-choice` says why. A DLL that chooses by itself keeps its
  choice, and the host report then says whether it matches the real game's;
- in host-only mode every spec names its city-states from `minor_civs` (base-game city-states, in order, those the
  database has), so a stock-like DLL's host-only games are fully named too;
- the ensemble is not pinned in real mode: its games are not compared one by one;
- `pin_minor_civs = false` turns the city-state pins off.

**Budget.** Before the run, a cost model estimates every game: this machine's history file (the trajectory game's
seconds per turn from the last run of the same setup), else a built-in Tiny curve scaled by map size. If the plan
does not fit the budget, the ensemble is trimmed (not below `min_members`), then the games to victory, then the
single phase. During the run, a game that cannot finish before the deadline is skipped; essential games (the
trajectory game and its references, the setups) start anyway and stop at the deadline, which leaves a partial game.
The real game's autoplay also stops at the deadline. `--budget MINUTES` changes the budget.

**Load.** Host games run below normal priority while the real game runs. A sampler records, every 5 seconds, the
machine's busy cores, the real game's own busy cores and the number of host games running; the report puts these
beside every timing, so contention is visible. The real game uses about three cores; on a 32-thread machine eight
host games leave room for it, but the real game's timings are never measured on an idle machine.

## The checks

Every check is **pass**, **warn** or **fail**; **info** (a measurement), **n/a** (not possible here: a missing piece,
or host-only mode; the reason is given) or **skipped** (no data: the budget or an earlier failure).

### Exact setup (must pass)

| Check | What | Pass |
|---|---|---|
| `setup.turn0` | the game state at turn 0 for each seeded setup, real against host (state digests, or the Lua fingerprint), and the host against itself | every setup identical |
| `setup.pregame` | every `PreGame` value the stock Lua API can read, printed by a probe on both sides once the game has started | every value both sides report equal (the nickname is ignored); a getter only one side answers is a difference of the Lua API, not of the game, and only warns |
| `setup.database` | the host's game database against the game's own after the mods loaded, table by table, rows as multisets; when the session exports the database (`CREATE TABLE AS` copies, which carry no schema, indexes or triggers), the data only | identical |
| `setup.civ-choice` | from `host-report.json` (`dll.hostChoseCivs`, `minorCivs`) of the compared host games (host-only: every host game): who chose the civilizations and city-states (the DLL, the spec's names, or a draw by the host), and whether they match the real game's | pass when nothing was drawn by the host and, in real mode, a compared game's city-states match the real game's (`minorCivs.allMatch`); fail otherwise (host-only: warn on a draw) |

These need the real game; in host-only mode `setup.turn0` checks only that the host reproduces its own turn-0
state, which catches map scripts or setup code that are not seeded.

### Health

| Check | What | Result |
|---|---|---|
| `health.lua-errors` | Lua errors in the host's runs that the real game's logs do not have | fail on any host-only error; host-only mode: warn on any error |
| `health.stubs` | engine methods the host only stubs that were called, from `host-report.json` | fail on any method not on the known-harmless list |
| `health.ui-hazards` | the UI hazard scan (state-changing DLL calls in UI Lua that the host's UI emulation does not cover) and UI files no profile knows | warn on any uncovered finding a game can reach; findings reached only through player input are listed apart |
| `health.victory-games` | full mode: host Tiny games played to a victory | pass when all end in a victory |

The hazard scan (`civ5headless.hazards.scan_report` over the UI files of the host's trajectory game) gives each
finding its reach: `input` when only player input runs it (mouse callbacks, buttons, input handlers, tooltips),
which an autoplayed game, with no human player, never sends; `game` or `load` otherwise. Covered findings (a
profile, a curated review, the DLL itself, an add-in the host runs) do not count. Input-only findings are counted
apart and do not warn; a game- or load-reachable finding warns (a warning, not a failure: it may never run in
practice, and the bias check is the net for one that does).

### Trajectory

All references use the trajectory game's spec and seeds, played to the horizon (150 turns):

| Check | Host game | Tells |
|---|---|---|
| `trajectory.aa` | the same game again | how long the DLL (with the host) reproduces itself: M |
| `trajectory.frame-skew` | `--frame-skew 7`: an extra engine update before every 7th | whether results depend on the number of engine frames (the real game runs other frame counts) |
| `trajectory.heap-perturb` | `--heap-perturb SEED`: the DLL rebased, random heap spacers | whether results depend on memory addresses |
| `trajectory.probe-neutral` | without the Lua probes; for a DLL without state digests, with sparse fingerprints (every `sparse_every` = 25 turns) | that the probes the comparison relies on change nothing (no digests: dense and sparse fingerprints agree at the sampled turns) |
| `trajectory.reseed` | a save of turn `reseed_save_turn` (20) played on for `reseed_turns` (10) with new seeds: the same seeds twice, then one seed changed each | that the same seeds repeat, and which seed changes this DLL's games from a save ([saves.md](saves.md#a-save-as-a-baseline)). Warns when the same seeds differ or neither seed matters. Vox Populi: the map seed; the stock DLL: the sync seed. Needs a host with `--reseed` |
| `trajectory.real` | the real game against the host's trajectory game | how long the host matches the real game: N |
| `trajectory.real-aa`, `trajectory.standard` | full mode: real A/A; the Standard game | the real game's own reproducibility; scale |

The headline reads "the host matches the real game for N of 150 turns; the DLL reproduces itself for M".
`trajectory.real` passes when N reaches the shortest of the references (or the whole horizon): the host cannot be
expected to match the real game longer than the DLL matches itself. A/A, skew and perturbation **warn** when they
differ (a property of the DLL, which limits the exact checks); the probe-off check **fails** when the probes change
the game, because the comparison would then be invalid.

Differences that heal within two turns (`heal_turns`) are **transients**: reported, not failed. Differences only in
sections that hold UI-only state (`savedb` of the digest, or `ui_sections`) are listed apart.

Comparison methods, best first: the DLL's per-turn state digests (DLLs with the optional additions,
[dll-additions.md](dll-additions.md)); the Lua fingerprint (any SDK-derived DLL; needs a host with `--lua-probe`);
for host-only references, the host's per-turn checkpoints (cities, scores, wars; coarse). Counts are in turns: a
difference first seen at the start of turn t leaves t turns matched, and a game of 150 turns has at most 150 (the
fingerprint also has the state after the last turn, the digests do not).

### Statistical fidelity

**The ensemble.** Host games with the trajectory game's setup and other seeds. By default it keeps the map seed and
varies the sync seed: other games on the same map. Some DLLs do not draw on the sync seed at all: Vox Populi takes
most random numbers from hashes of the game state, so two games with different sync seeds are identical. The setups
phase plays two short games to find out; if they are identical, the ensemble varies the map seed instead (other maps
of the same script and size), and the report says so. `ensemble_vary = "sync"` or `"map"` sets it.

**Series.** Per turn, for each replay dataset (score, land, population, gold, culture, ...) and each fingerprint
metric (cities, population, techs, policies, units, wars, ...), the mean, the maximum and the sum over the living
major civs (the civs differ between games, the aggregates do not; the sum also sees how many civs there are and
live). Turns before `stats_from_turn` are left out.

**Envelope test** (`statistics.envelope`). For each series and turn, the 2.5-97.5% band of the ensemble; coverage is
the fraction of the real game's points inside. A finite ensemble's band holds fewer than 95% of a new game's points
(about 90% with 32 games, about 75% with 8), and how many fewer depends on ties and on how persistent games are, so
the suite measures it: each ensemble game in turn is left out and scored against the others. The real game passes
when its coverage reaches 95% or the 5% quantile of those leave-one-out coverages, whichever is lower; within 2
points below, warn; further, fail.

**Bias check** (`statistics.bias`). For each series, the real game's mean deviation from the ensemble median over
the turns, in units of the ensemble's spread at each turn (turns where the ensemble has no spread are skipped; a
turn counts at most 5 spreads). A single game sits on one side of the median for long stretches by chance, so this
is compared with the same statistic of every ensemble game against the others, and a series is flagged only when it
is further out than the 95% quantile of the ensemble games' most extreme series (a max-T threshold: about a 5% chance
of any false flag, whatever the correlation between metrics), and on one side of the median on at least 90% of
turns. It catches a systematic shift, such as a missing UI emulation that changes gold, even when the DLL is not
deterministic. A flag is a warning. With fewer than 16 ensemble games the result is info only.

For each series the report gives the **detectable shift**: the relative shift of that metric the check would flag
with this ensemble. One real game can only reveal shifts that are large against the spread between games; a metric
whose games differ by 20% cannot show a 3% bias.

**The host twin.** The real game is also compared with its twin, the host's trajectory game (same seeds). A seed
can give an atypical game, such as an early conquest, and then both sides sit outside the band in the same way. So
the envelope test passes when the real game's coverage is within the margin of the twin's, and a bias flag that the
twin shares (same series, same side) is reported but not counted. Only differences between host and real game fail.

In host-only mode the statistics score the host's own trajectory game against the ensemble: a self-test of the
machinery and of the ensemble, not a statement about the real game. The checks then report **info** with the
status they would have had.

### Performance (info)

| Check | What |
|---|---|
| `performance.speed` | seconds per turn early, mid and late (thirds of the horizon), launch to first turn, peak working set, peak private bytes, CPU seconds: real game against host, with the machine load during the real game |
| `performance.throughput` | the host phases with one, four and eight workers: games and turns per hour, busy cores |
| `performance.history` | the host's seconds per turn against the median of its last five runs of the same setup and mode on this machine; warns above +15% and names what changed (DLL, mods, host) |
| `performance.standard` | full mode: the Standard game on both sides (scale and memory) |

The history is `<runs>/verify-history/<machine>.jsonl`, one line per run; the report draws the trend.

## Reading the report

- The banner: the verdict, the headline (N of M turns), the time used against the budget.
- **Checks**, by section; "details" opens the data behind each line.
- **Trajectory**: one bar per comparison: green while identical, amber for a transient, red from the lasting
  difference; the table names the differing sections.
- **Statistical fidelity**: one chart per series, worst first (flagged series at the top): the ensemble's band and
  median, the reference game, and its points outside the band. The title gives the coverage and, from the bias
  check, on which side of the median the game sits and how often.
- **Performance**: the speed table (real, host, ratio), the cumulative autoplay time per turn, the throughput table,
  the load over the run and the history.
- **Runs**: every host game and real game, with states, times, estimates and errors.

## What the checks can and cannot tell

- Exact equality (turn 0, trajectory) is the strongest evidence, but only for the setups and seeds played. A DLL
  that reproduces itself for the whole horizon and matches the real game for all of it is very likely to match on
  other seeds of the same kind of game; a different map script or game option can still find a difference.
- A difference in the trajectory says when, and in which state sections, not why. The run folders keep the digests
  and logs for a closer look (`c5h compare`, `c5h dumps`).
- When the DLL does not reproduce itself (A/A warns), the exact checks stop at that turn and the statistics carry
  the verdict.
- The statistics compare one real game with a host ensemble. They catch large or systematic differences, not small
  ones (see the detectable shift), and about one run in twenty may warn by chance. When the ensemble varies the map,
  the envelope also covers map differences and is wider: with Vox Populi on Tiny Continents (32 games, other map
  seeds) the bias check detects shifts of about 25% in the steadiest series and much more in most others (see the
  self-test below). The statistics are a net for gross errors; the exact checks carry the fine-grained evidence.
- `setup.pregame` and the Lua fingerprint read only what the stock Lua API exposes; state the API does not show is
  covered by the digests (DLL additions) or not at all.
- `health.stubs` and `health.ui-hazards` list what the host does not emulate; a clean result does not prove that
  every UI script behaves as in the game. The bias check is the safety net for UI effects nobody listed.
- Performance figures come from one machine under the recorded load; compare them through the history, not across
  machines.

## Calibration

The defaults below were set without the real game, for the first real-game runs (phase E) to set:

- the envelope (`envelope_target`, `envelope_alpha`, `envelope_margin`) and bias thresholds (`bias_alpha`,
  `bias_one_sided`, `bias_min_members`, `bias_z_cap`), from real games scored against their ensembles;
- the ensemble sizes and the budgets of the modes, and the real game's cost per turn in the planner (the cost model
  knows the host only);
- full mode with a DLL that ignores the sync seed (Vox Populi): the ensemble keeps varying the map seed, with the
  larger ensemble (64); whether longer horizons are needed for useful statistical power is for calibration to decide;
- `heal_turns`, and whether `ui_sections` and `pregame_ignore` need more entries once real PreGame dumps exist;
- the default setups, civilizations and city-states: every one must start in the real game with the shared seeds.

The first real-game runs (2026-10-26; "Runs on record") kept them:
- every statistical check passed with a wide margin: envelope 95–97% against thresholds of 85–89%, no bias flagged,
  global p 0.67–0.79;
- the 4 default setups started in the real game with the shared seeds and matched at turn 0 for all three DLLs;
- quick and full mode took less than half their budgets (25 and 55 minutes), so the budgets were left as they are;
- the real game costs about 13x the host per turn with Vox Populi and 22x with the stock DLL;
- `pregame_ignore` needed no entries: a getter only one side answers now warns, and the values both sides report
  were all equal.

What one run per DLL cannot settle: the bias thresholds' false-alarm rate over many runs, and whether the
divergences that the upstream determinism fixes remove show up in longer or larger games.

## Configuration

Every option has a key in a `[verify]` table of `civ5headless.toml`; `[verify.quick]`, `[verify.full]` and
`[verify.host-only]` apply to one mode. The command line wins over the file.

```toml
[verify]
turns = 150                 # trajectory and ensemble horizon
workers = 8                 # host games at once in the ensemble phase
spec = "specs/my-game.json" # or a table; --map, --size, --players, --city-states, --seeds override parts of it
ensemble_vary = "auto"      # auto, sync or map
host_args = ["--sba"]

[verify.quick]
ensemble = 24
```

| Key | Default | Meaning |
|---|---|---|
| `budget_minutes` | 25 / 55 / 5 | time budget of the mode |
| `spec`, `map`, `size`, `players`, `city_states`, `seeds` | Tiny Continents, seeds 30017/40017 | the trajectory and ensemble game |
| `turns` | 150 | horizon |
| `ensemble`, `ensemble_seed`, `ensemble_vary` | 32 / 64 / 8, 70001, auto | ensemble size, first varied seed, what varies |
| `workers`, `reference_workers`, `single_phase`, `affinity`, `low_priority` | 8, 4, true, p-first, with the real game | host scheduling |
| `frame_skew`, `heap_perturb`, `heal_turns` | 7, 4242, 2 | references and transients |
| `reseed_save_turn`, `reseed_turns` | 20, 10 | `trajectory.reseed`: the save's turn, the loaded games' length (0: no such reference) |
| `setups`, `setup_turns` | 4 base-game setups, 1 | turn-0 setups: a list of specs (each may have a `name`) |
| `standard_spec`, `standard_turns`, `victory_games`, `victory_max_turns` | Standard Continents, 0 / 60, 0 / 4, 500 | full mode |
| `envelope_band`, `envelope_target`, `envelope_alpha`, `envelope_margin` | [0.025, 0.975], 0.95, 0.05, 0.02 | envelope test |
| `bias_alpha`, `bias_one_sided`, `bias_z_cap`, `bias_min_members` | 0.05, 0.9, 5, 16 | bias check |
| `min_members`, `stats_from_turn`, `aggregates`, `exclude_metrics` | 8, 2, [mean, max, sum], [] | series |
| `ui_sections`, `pregame_ignore` | [savedb], [GetNickName, GetActivePlayer] | listed apart / ignored |
| `pin_civs`, `civs`, `pin_minor_civs`, `minor_civs` | true, base-game civilizations, true, base-game city-states | naming random civilizations and city-states |
| `fingerprint`, `sparse_every` | true, 25 | the fingerprint probe: true, false, or `{every, turns, split}`; the probe-off reference of a DLL without digests |
| `mods`, `no_mods` (`--mod ID[:VER]`, `--no-mods`) | the configuration's mods, false | the mod set of host games, database and real game |
| `host_args`, `max_memory_mb`, `reserve_seconds`, `regression_threshold`, `strict` | [], 3500, 15, 0.15, false | other |

**AI logging** ([ai-logging.md](ai-logging.md)): with the real game, the host games log as `config.ini` says
(`LoggingEnabled`, `AILog`, `AIPerfLog`, `BuilderAILog`, `PlayerAndCityAILogSplit`; `hostLogArgs` in `plan.json`), because with
some DLLs `AILog` changes the game. `ai_logging` in `civ5headless.toml` does not apply to them. It does apply to `--host-only`,
where there is no real game to match, so those games have AI logging off unless it says on. A logging option in `host_args`
wins in every mode, and with the real game the run notes that the two sides may then differ.

## Statistics self-test

```
c5h verify --analyse RUN --self-test [--foreign OTHER_RUN]... [--shift 0.05]
```

re-scores a finished run's ensemble against itself: each of the first 12 ensemble games is held out and scored as if
it were the real game (the envelope should pass and the bias check stay quiet about as often as alpha says); each
metric of a held-out game is shifted by plus and minus the shift (does the bias check flag it?); and every
`--foreign` run folder (a game from another setup: another map script, size or DLL) is scored against the whole
ensemble (both checks should object). The result goes into the report.

What it showed for Vox Populi, Tiny Continents, 150 turns, an ensemble of 32 games on other map seeds:

| Test | Result |
|---|---|
| hold-outs (12) | envelope passed 12 of 12 (mean coverage 93%); bias quiet in 11 of 12 |
| one metric shifted by 5% | flagged in none of 29 metrics (detectable shifts run from about 23% to several hundred %) |
| shifted by 30% / 50% | flagged in about 10 / 21 of 29 metrics |
| foreign: Tiny Pangaea (3 games) | 2 of 3 caught |
| foreign: Small Continents (3 games) | 0 of 3 caught with means and maxima only; 3 of 3 once the world sums were added (now the default) |

So the statistical tier tells a game of another kind, or a large shift in one metric, from the ensemble; it does not
see a few percent. A same-map ensemble (a DLL that draws on the sync seed) is tighter.

## Runs on record

Host-only mode, Tiny Continents, 150 turns, 8 host processes at most, on the development machine (2026-10-25):

| DLL | Time | Verdict | Notes |
|---|---|---|---|
| Vox Populi (digest additions), its mods | 4.0 min | pass | A/A, frame skew, heap perturbation: 150 of 150 turns (digests); no UI hazard a game can reach (259 input-only); the DLL chose the city-states; the ensemble varies the map seed; 0.43 s per turn, 450 MB |
| stock BNW DLL, no mods | 0.8 min | warn | the fingerprint compares (no digests): 150 of 150 turns; dense and sparse fingerprints agree; the specs named the city-states and the host applied them; the sync seed changes this DLL's games, so the ensemble keeps the map; 37 game-reachable UI hazards (EUI_core_library and EUI_tooltip_library, EUI being installed as DLC: 20; LeaderHeadRoot, TradeLogic, TopPanel: 17) and 25 EUI files no profile knows: the warning; 0.08 s per turn, 121 MB |

With the real game, on the development machine (2026-10-26; one real game beside 8 host processes):

| DLL and mods | Mode | Time | Verdict | Real game against the host |
|---|---|---|---|---|
| Vox Populi fork with the digest additions, its mods | quick | 11.1 min | warn (PreGame API) | 148 of 148 turns identical (digests); 0.36 against 2.52 s per turn |
| stock BNW DLL, no mods | quick | 5.2 min | warn (PreGame API, UI hazards) | 150 of 150 turns identical (fingerprints); 0.06 against 1.38 s per turn |
| Vox Populi 5.4.8 release, its 5 mods | quick | 10.1 min | fail (1 ensemble game: localization rebuilt by the real game, since fixed) | 150 of 150 turns identical (fingerprints); 0.18 against 2.29 s per turn |
| Vox Populi 5.4.8 release, its 5 mods | full | 22.5 min | warn (PreGame API, 3 UI hazards) | 150 of 150 Tiny turns and 60 of 60 Standard turns identical; the real game repeats itself (150 of 150); 64 of 64 ensemble games; 4 of 4 victory games; 0.18 against 2.37 s per turn (Standard: 0.30 against 3.84), 288 MB against 1570 MB |
| Civ5StackMod 0.3.3-1 (VC9 build, stacking AI), its 5 mods (run 2026-10-30) | quick | 11.1 min | warn (PreGame API, 3 UI hazards, host time against the history, which is plain VP's: this DLL takes about twice the CPU) | 150 of 150 turns identical (fingerprints); 0.35 against 2.35 s per turn, 382 MB against 1789 MB |

## Output folder

| File | Content |
|---|---|
| `plan.json` | settings, capabilities, inputs (DLL, mods, database, host, with hashes), phases and jobs, estimate |
| `specs/`, `probes/` | the resolved specs and the Lua probes both sides used |
| `host/<game>/` | host run folders (detail standard: small logs, digests, turn times, checkpoints, replay data) |
| `real/` | the real-game session: `real.json`, one folder per game with its log segments and replay data |
| `execution.json` | job states and timings, phases, the load samples, the cost model |
| `verify.json`, `report.html`, `verify.log` | the result, the report, the progress lines |

## Pieces the suite relies on

The suite detects each piece when it starts and turns a missing one into "n/a" checks with the reason:

- `civ5headless.exe` options, from its usage text: `--lua-probe` (fingerprint and pregame probes), `--frame-skew`,
  `--heap-perturb`; and `host-report.json` in each run folder: `dll.hostChoseCivs`, `minorCivs` (`chosenBy`,
  `allMatch`, `slots`), `stubCalls.calls` (`method`, `count`, `knownHarmless`), `luaErrors.list`, `uiFiles.unknown`.
- `civ5headless.run.play(spec, turns, ..., mods, no_mods, fingerprint, lua_probes, ...)` for every host game;
  `civ5headless.probes.fingerprint_lua(cfg)` (the configured `probes` folder) for the probe.
- `civ5headless.fingerprint.read(path)`: `.sections` (turn, section, hash), `.metrics` (turn, player, metric,
  value), `.meta` and `.players` (the city-states) from the `C5HFP`/`C5HM` lines.
- `civ5headless.spec`: `minorCivs` (city-state types in slot order) in a spec.
- `civ5headless.realgame.session.RealGameSession`: `launch(map_seed, sync_seed)`, `set_mods(ids)`,
  `start_game(spec, map_seed, sync_seed, lua_probes, fingerprint)` (a dict; `relaunched` when new seeds needed a new
  process), `autoplay(turns, max_seconds)` (a dict: `turnSeconds`, cumulative per turn, `endReason`, `victory`),
  `to_main_menu()`, `collect_logs(dest)`, `process_stats()` (`peakWorkingSet`, `peakPrivateBytes`, `cpuSeconds`),
  `replay_data()`, `quit()`, and optionally `game_database(dest)`.
- `civ5headless.hazards.scan_report(report_path)`: the hazard findings with `reach` and `covered`.
