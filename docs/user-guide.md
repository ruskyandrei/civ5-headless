# User guide

`civ5headless.exe` plays Civilization V (Brave New World) AI autoplay games with an unchanged game DLL and without the
game. `c5h` is the command line around it: configuration, databases, single games, batches, comparisons, and the
real-game automation the references are recorded with. With the same AI logging ([ai-logging.md](ai-logging.md)), a game from
a spec is the game the real game plays from that spec: with the Vox Populi DLL the per-turn state digests are identical (a
100-turn Continents reference, and a Standard game to victory with 300 of 302 turns identical and the same result, both
with AI logging on, which is not the `c5h` default). The host is several times faster than
the game, needs a fifth of its memory, and many games run side by side.

Design and status: [plan](plan.md), [ledger](ledger.md), [engine contract](engine-contract.md), [database
builder](database-builder.md), [DLL additions](dll-additions.md), [history](history).

## 1. Setup

Needs: Windows, Civilization V with Brave New World (Steam), started once to its main menu (it writes the caches the
host reads: `Localization-Merged.db`, `Civ5CoreDatabase.db`), and Python 3.11 or newer. The Python tools use the
standard library only. No compiler: `civ5headless.exe` needs only the Visual C++ 2008 runtime, which Civ V installs
(the game's own DLLs use it), and the Firaxis DLLs it loads from the game folder.

The release kit (`civ5-headless-<version>-<commit>.zip`, made by `tools/make_kit.py`) is the way to run it without
building: unpack it anywhere and run `c5h.cmd` there. The kit folder is the workspace (runs, caches, your
`civ5headless.toml`), and its `bin\civ5headless.exe`, `profiles\` and `probes\` are found by themselves (`kit.json`
marks the folder as a kit). A kit may bundle its own Python in `runtime\`; `c5h.cmd` uses it, else `C5H_PYTHON`,
Python on `PATH` or the `py` launcher.

From a checkout, `c5h.cmd` runs the tools in place (`python -m civ5headless` with `python\` on `PYTHONPATH`; set
`C5H_PYTHON` for another Python). `pip install -e .` installs a `c5h` command instead; it needs setuptools, which pip
fetches from the network.

```
c5h doctor                                               # what was found, what is missing
c5h run examples/specs/continents-tiny.json --turns 30   # one game; the run folder goes to runs/
c5h compare runs/A runs/B                                # per-turn state digests of two runs
```

Configuration: the game folder (Steam registry and library folders), `Documents\My Games\Sid Meier's Civilization 5`
(the Documents known folder), its MODS and cache folders, the activated mods (`Civ5ModsDatabase.db`), the game DLL
(the first active mod's, the Community Patch folder's, or the game's own) and the host (`out/latest.txt`) are
discovered. The host's UI emulation profiles (`profiles`) and the Lua probes (`probes`, with `fingerprint.lua`) are
the workspace's `profiles\` and `probes\` folders, else the repository's; an installed package has neither, so copy
them into the workspace or set the two keys. Anything else, or anything to override, goes in `civ5headless.toml`
(current folder, the workspace, or `C5H_CONFIG`; see [civ5headless.example.toml](../civ5headless.example.toml));
`c5h --config FILE` picks another. `c5h doctor` prints every value with where it came from, then checks three
groups: host games (exit code 1 when one fails), the real game, and building the host.

| Command | Does |
|---|---|
| `c5h doctor` | configuration and checks |
| `c5h run` | one host game from a spec |
| `c5h queue run\|status\|compare\|report` | many games in parallel |
| `c5h report RUN...` | rebuild runs' `report.html`, and a summary page over several runs |
| `c5h compare A B` | per-turn digests of two runs (folders or logs) |
| `c5h spec resolve\|lua` | resolve a spec, or print the front-end Lua that sets it up in the game |
| `c5h snapshot` | the DLC snapshot, built from the game's files |
| `c5h dbcache`, `c5h dbbuild`, `c5h dbdiff` | the game database for a mod set: cached, built, compared |
| `c5h modsdb` | the mods registry (`Civ5ModsDatabase.db`) for a mod set, from the `.modinfo` files |
| `c5h fingerprint compare\|show` | per-turn Lua fingerprints of two runs (any DLL) |
| `c5h hazards scan\|methods\|known` | UI Lua that changes game state, for deciding on UI emulation profiles |
| `c5h parity`, `c5h host-first`, `c5h dumps`, `c5h skew`, `c5h digest` | parity with real-game references |
| `c5h verify` | the verification suite |
| `python -m civ5headless.realgame.probe\|record\|dbcapture\|tuner` | the real game ([section 8](#8-the-real-game)) |

`c5h COMMAND -h` gives each command's options.

## 2. Building the host

The host must be built with the game DLL's compiler, MSVC 2008 SP1 (VC9), because C++ objects cross the engine
interfaces. `tools/get_toolchain.py` makes a portable copy from Microsoft's Windows SDK 7.0 ISO (the one Vox
Populi's CI uses) without installing anything:

```
python tools/get_toolchain.py                       # downloads GRMSDK_EN_DVD.iso (1.5 GB), makes toolchain\
python tools/get_toolchain.py --iso GRMSDK_EN_DVD.iso
python tools/build_host.py                          # -> out\Release\<timestamp>\civ5headless.exe, out\latest.txt
```

The ISO's size and SHA-256 are checked; its packages come out with 7-Zip when it is installed, else with a built-in
UDF reader; `msiexec /a` unpacks them (an administrative image: nothing is installed or registered). It takes about
20 s from a local ISO, and `toolchain\provenance.json` records the hashes. `tools/toolchain.py` also finds a
toolchain in `--toolchain DIR`, `C5H_TOOLCHAIN` or an installed Visual Studio 2008.

The host loads the Firaxis DLLs (database, localization, Lua) and the ICU data from the game folder; nothing of the
game is copied or committed.

## 3. Game database

The host needs the game database for the DLL and mods it runs. You rarely build it yourself: `c5h run` and the queue
use the cache (`c5h dbcache`), which builds once per input set in about 3 s and reuses the result
(`<workspace>\cache\db\<key>\built.db`). The key hashes the contents of the mods' `.modinfo/.xml/.sql/.lua` files,
the mods database, the DLL, the DLC snapshot and the host, so a mod edit gives a new database automatically.

- **DLC snapshot:** the database with the base game, the expansions and every installed DLC, before any mod. `c5h
  snapshot` builds it from the game's files (the game's cached `Civ5CoreDatabase.db` plus each DLC package, in about
  a second) and caches it; the database cache does that by itself when `snapshot` is not set. A snapshot captured
  from the game (`python -m civ5headless.realgame.dbcapture --run NAME --snapshot-only`, then `snapshot = ...` in
  `civ5headless.toml`) can be used instead; `c5h snapshot --oracle CAPTURED.db` compares the two.
- **Mods input:** a MODS folder and its `Civ5ModsDatabase.db` (the activated mods). The live ones work, but the live
  database lists no activated mods while the game sits in its mod browser; a pinned copy (a MODS folder copy and a
  copy of the database taken in a modded session) keeps an input stable. Both are only read.
- **Mod sets:** `--mod ID[:VER]` (repeatable; `c5h run`, `c5h dbcache`, the queue's `mods`) gives the activated mods
  instead of a mods database, so any mods in a MODS folder can be played without the game having activated them; see
  below. `--no-mods` plays without mods: the database is then the DLC snapshot post-processed by the DLL. With an
  explicit mod set the DLL is the set's (or, with `--no-mods`, the game's own) unless `--dll` or `civ5headless.toml`
  names one ([section 5](#the-dll)).
- `c5h dbbuild --mods-dir MODS --mods-db DB --out built.db [--oracle GAME.db]` builds one database directly;
  `c5h dbdiff A B` compares two. Details: [database builder](database-builder.md).

### The mods registry

The game keeps a registry of the installed mods in its cache (`Civ5ModsDatabase.db`): properties, files, actions,
entry points, relationships, and which mods are activated. The host reads the activated mods, their InGameUIAddin
entry points and their imported Lua files from it, and the database builder reads the activation order. `c5h modsdb`
writes that registry for a mod set from the `.modinfo` files:

```
c5h modsdb --mod d1b6328c-ff44-4b0d-aad7-c657f83610cd --mod 8411a7a8-dad3-4622-a18e-fcc18324c799:17 [--mods-dir MODS]
           [--out Civ5ModsDatabase.db] [--compare GAME_REGISTRY.db]
```

- A mod without a version is its highest version in the MODS folder. The activation order is the order given, except
  that a mod comes after the mods it depends on or references.
- A dependency outside the set, a dependency version out of range, or a mod blocking another in the set is an error,
  as in the game's mod browser.
- The rows are those the game writes (the registry generated for the five mods of the Vox Populi fork is identical to
  the game's, table by table). Only the given mods are listed, all activated.
- Without `--out` the registry is cached (`<db_cache>\modsdb\<key>\`); `c5h run --mod ...` and the queue use the same
  cache and pass the registry to the database build and to the host.
- `--compare` checks it against another registry (`--path-as` the folder the other one records, `--ignore md5` to
  leave out file hashes the game has not rescanned).

Games played from the generated registry and from the game's are identical (same database, same per-turn digests
and fingerprints, the same add-ins and profiles).

## 4. Game specs

A spec is JSON mirroring the single-player setup screens; omitted fields behave like an untouched screen
(`c5h spec -h`, plan section 4.4). Examples are in `examples/specs/`:

```json
{"map": {"script": "Assets/Maps/Continents.lua", "size": "WORLDSIZE_STANDARD"},
 "speed": "GAMESPEED_STANDARD", "era": "ERA_ANCIENT", "difficulty": "HANDICAP_EMPEROR",
 "seeds": {"map": 1001, "sync": 2002}}
```

- Other fields: `map.options`, `map.civ5map` (premade maps), `majors`, `cityStates`, `slots` (civ, team per
  slot), `gameOptions`, `victories`, `maxTurns`.
- `minorCivs` lists `MINOR_CIV_*` types (at most `cityStates`) for the host, which uses them where it chooses the
  city-states itself (DLLs that leave that to the game). The real game cannot be told which city-states to place (its
  pregame Lua has no call for it), so a comparison with the real game fills the field with the game's own choice.
- The two seeds fix the whole game (map, civs, city-states, every AI decision): the same spec, DLL and database give
  the same game on every run, and in the real game when the AI logging matches ([ai-logging.md](ai-logging.md)).
- A spec without seeds draws new ones on every run, recorded in the run's `spec-resolved.json`. Each example
  comes twice: `NAME.json` without seeds (a new game each run), `NAME-fixed-seed.json` with them (the same game
  each run).
- `c5h spec resolve SPEC.json --out RESOLVED.json` checks the names against the game database and writes the
  explicit form the host reads; `c5h run` resolves for you and also takes a resolved spec. `c5h spec lua SPEC.json`
  prints the front-end Lua that sets the same pregame state in the real game.

## 5. One game

```
c5h run examples/specs/continents-standard.json --turns 250 [--run NAME] [--seeds MAP SYNC] [--dll DLL]
```

The run folder is `<runs>\NAME` (default `<spec>-<date>-<time>`), or `--run-dir DIR`. Inputs left out come from the
configuration. Useful options:

| Option | Meaning |
|---|---|
| `--db DB` | use this database instead of the cached build |
| `--mods-dir MODS --mods-db DB` | another mods input |
| `--mod ID[:VER]`, `--no-mods` | a mod set from a generated registry ([section 3](#the-mods-registry)), or no mods; either also picks the DLL ([below](#the-dll)) |
| `--profiles DIR`, `--no-profiles` | the UI emulation profiles (default: the configured folder), or none |
| `--skip-addin NAME`, `--no-addins` | leave out one mod InGameUIAddin (its file name without extension, e.g. `SmallResourceIcons`; repeatable), or all |
| `--fingerprint` | the Lua fingerprint probe, every turn; `--fingerprint-every K`, `--fingerprint-turns 1,50,100` and `--fingerprint-split N` set it (each implies it) |
| `--lua-probe FILE` | run a Lua file in the game once it has started, before turn 0 (repeatable) |
| `--checkpoint-extra FILE`, `--final-extra FILE` | run a Lua file at every checkpoint, or once after the last turn, and keep what it returns as JSON in `extra/` (repeatable; [probes.md](probes.md)) |
| `--diagnostics N` | `Game.SetStackingDiagnosticsLevel(N)` before autoplay (Vox Populi fork DLLs) |
| `--improved-mask N`, `--ab-role baseline\|test` | A/B switches of the AI improvement program's DLLs |
| `--checkpoint-every N` | checkpoint every N turns (default 1) |
| `--maximum-seconds S`, `--stall-seconds S` | end the game after S seconds, or after S seconds without a new turn |
| `--max-memory-mb MB` | commit limit of the host process (job object; default 3500) |
| `--affinity MASK`, `--low-priority` | CPU pinning, below-normal priority |
| `--cpu-limit PERCENT`, `--efficiency-mode` | quieter background runs: a hard cap at PERCENT of one core (the time limits stretch to match), and Windows efficiency mode (EcoQoS, idle priority); the game is the same, only slower ([running.md](running.md#running-in-the-background)) |
| `--heap-perturb N`, `--frame-skew K` | the verification suite's address and frame-count checks |
| `--sba` | a real small-block allocator for the DLL (about 3% less CPU on a 150-turn game, none on a short one); for DLLs that pass `trajectory.heap-perturb`. `status.json` records it |
| `--ai-logging on\|off` | whether the DLL builds its AI logs; default `ai_logging` in `civ5headless.toml`, else off. Off is faster, and with Vox Populi the same seed plays a different game with it on ([ai-logging.md](ai-logging.md)). `status.json` records it (`aiLogging`, `gameLogFlags`) |
| `--detail summary\|standard\|full` | what the run folder keeps ([Run folders](#run-folders)); default `standard`. `--prune` is the old name of `standard` |
| `--no-report` | no `report.html` (`c5h report RUN` writes it later) |
| `--load SAVE` | continue a saved game (`.Civ5Save`, the real game's or the host's) instead of a spec; its mod set unless one is given ([saves.md](saves.md)). A `.Civ5Save` given as the spec does the same. With `--seeds`, it plays on with new rolls on the same map ([a save as a baseline](saves.md#a-save-as-a-baseline)) |
| `--autosave none\|game\|N`, `--save-at T`, `--no-save-final` | the saves the real game loads, in `<run>\saves`: the last turn by default, autosaves none by default ([saves.md](saves.md)) |
| `--host-arg=ARG` | pass an option to `civ5headless.exe` (run it without arguments for the list) |

From Python, `civ5headless.run.play(spec, turns, ...)` plays one game and returns its run folder, whether it
completed, its status and its run report (`Result.report`). Its keywords are the options above (`mods=[...]`,
`no_mods`, `profiles`, `no_profiles`, `skip_addins=[...]`, `no_addins`, `fingerprint=True` or `{"every": K, "turns":
[...], "split": N}`, `lua_probes=[...]`, `detail='summary'`, `ai_logging='on'`, `report=False`, `host_args=[...]`, `checkpoint_extras=[...]`, `final_extras=[...]`, ...).

### The DLL

The game DLL is, in this order:

1. `--dll` (a queue job's `dll`);
2. `dll` in `civ5headless.toml`;
3. with an explicit mod set: for `--no-mods` the game's own (`Assets\DLC\Expansion2\CvGameCore_Expansion2.dll`), for
   `--mod` the first mod of the set, in activation order, that ships `CvGameCore_Expansion2.dll`, else the game's own;
4. otherwise the discovered one: the first active mod's, else the Community Patch folder's, else the game's own.

`events.jsonl` (action `dll`), `status.json` (`dll`, `dllReason`) and the report's header say which and why. `c5h
dbcache`, the queue and the verification suite choose the same way. A `db` in `civ5headless.toml` is still used as is,
whatever the mod set: leave it out for games with other mods.

### UI add-ins, profiles and probes

The host has no UI screens. It runs every activated mod's InGameUIAddin in a stub UI context after the load screen,
and the UI emulation profiles do what UI code that changes game state does in the real game (popups, notifications,
per-frame updates); see [UI emulation profiles](profiles.md) and the [engine contract](engine-contract.md), section
10.6. `c5h run` passes the configured profiles folder (`--profiles`), so a run never depends on where the exe is.

Lua probes (`--lua-probe`, and the fingerprint) run in order in the game's `InGame` context after the turn-0
checkpoint and before turn 0 is processed. The real-game session runs them at the same point
(`start_game(lua_probes=..., fingerprint=...)`), so the same files print the same lines on both sides. In `c5h run`,
a generated `probe-settings.lua` runs before your probes and sets `C5H_RUN_DIR` and `C5H_EXTRA_DIR` (the run folder
and its `extra` folder, for files a probe writes).

Extras are Lua files whose return values c5h keeps ([probes.md](probes.md)). `--checkpoint-extra FILE` runs at the
end of c5h's checkpoint Lua (`hh-checkpoint.lua`: every `--checkpoint-every` turns from the first turn played, and
at the final checkpoint), `--final-extra FILE` at the end of its final Lua (`hh-final.lua`, after the last turn).
Each file is compiled with `loadstring` and called with a context table (`name`, `kind`, `turn`, `state` kept
between calls, `runDir`, `extraDir`) in a protected call; its result goes into the checkpoint as `data.extra.NAME`
(an error as `data.extraErrors.NAME`), and after the game into `extra/NAME.jsonl` (one row per checkpoint) and
`extra/NAME-final.json`. An error is logged and never fails the game.

The fingerprint (`probes/fingerprint.lua`) hashes the game state through plain getters every turn (or every K-th
turn) and prints it to `Logs\Lua.log`; it needs no DLL support, so it compares games of any DLL, including the stock
one: `c5h fingerprint compare RUN_A RUN_B` (run folders, Logs folders or Lua.log files; `show` prints what a log
holds). Its settings go into a generated `fingerprint-settings.lua` that runs just before it.

Is a mod set's UI safe in the host? Play a short game, then `c5h hazards scan --report RUN\host-report.json` lists
the calls in its UI files that change game state and whether a profile covers them; `c5h hazards known` records
files checked to need no profile ([profiles](profiles.md), "Does a mod's UI code need a profile?").

### Run folders

The files follow the real-game benchmark of the Vox Populi fork this came from, so its readers (`compare.py`,
`abcompare.py`, `samerun.py`) read host runs unchanged:

| File | Content |
|---|---|
| `events.jsonl` | launched, checkpoint (every 10 turns), victory / target_reached, extra (one per extra file), completed or failed |
| `setup.json` | the setup query (before arming), plus `backend`, `dllSHA256`, `specKey` and the A/B fields |
| `spec-resolved.json` | the resolved spec |
| `checkpoints/turn-NNNN.json` | players, cities and wars at every turn change; `turn-NNNN-final.json` after autoplay; the checkpoint extras' values in `data.extra` |
| `extra/` | the extras' results (`NAME.jsonl`, `NAME-final.json`) and the files probes write there ([probes.md](probes.md)); kept at every detail level |
| `turn-times.jsonl`, `turn-seen.json` | seconds since autoplay started, per turn |
| `replay-messages.json`, `replay-data.json` | replay messages (without type 2) and every major's replay series |
| `status.json` | `state`, `startTurn`/`endTurn`/`turns`, `wallSeconds`, `secondsPerTurn`, `process` (CPU seconds, peak working set, peak private bytes), `endReason` (target_turn, victory, killed), `winner`/`victory`, `save` (seed key), `background`, `aiLogging`, `gameLogFlags`, `extras` (each extra's rows, errors and first error) |
| `host-report.json` | the host's run report: status and error, exe, DLL and databases, options, activated mods, profiles selected, add-in contexts and their errors, unknown UI files, engine stubs the DLL called, Lua errors, timings and memory ([engine contract](engine-contract.md), section 14) |
| `hh-command.json` | the host command line, the inputs' hashes, the DLL and why, the mods registry, profiles, fingerprint settings, extras (with their SHA-256) and detail level |
| `final-state.json` | after the last turn: every player (names, colours, alive, score, cities, population, techs, policies, era, wonders, religion; a major's `master`: the player leading its master team, -1 for none, left out by a DLL without vassalage) and the final map (plot owners, cities) |
| `report.html` | the run report (below) |
| host outputs | `newgame-report.json`, `Logs/` (HeadlessDigest.log with the per-turn digests, Lua.log, the DLL's logs), `t000-map.json`, `t000-digest.json`; with `--detail full` also `dump-t000/`, `dump-tN/` and `db/` |

#### Detail levels

`--detail` (queue key `detail`) decides what the folder keeps. Every level plays the same game: the host is told not
to write what a level leaves out (`--no-dumps`, `--keep-log`; the DLL still logs, so its results do not change), and
the database copies it needs while it runs (`db/`) are deleted afterwards.

| Level | Keeps | Tiny, 30 turns: this fork's DLL / stock DLL, no mods |
|---|---|---:|
| `summary` | status, events, setup, spec, host report, turn times, replay data and messages, the final checkpoint, `final-state.json`, `t000-map.json`, `Logs/Lua.log` and `Logs/Headless.log`, `report.html` | 1.0 MB / 0.8 MB |
| `standard` (default) | also the per-turn checkpoints and the small logs: `HeadlessDigest.log` (digest DLLs), `Database.log`, `CustomMods.log`, `vfs-index.log`, ... | 1.4 MB / 1.1 MB |
| `full` | everything the host writes: the state dumps (`dump-t000/`, `dump-tN/`, about 85 MB each with a field-map DLL), the database copies (`db/`, about 60 MB) and every log of the DLL (its AI logs with `--ai-logging on`; the sizes here are with them) | 243 MB / 58 MB |

Logs asked for by an option (`Stacking-*.log` with `--diagnostics`, `RandCalls.csv` with `--host-arg=--rand-log`,
`Logs/HeadlessDumps` with `--host-arg=--dump-turns`) are kept at every level. What reads what: `c5h compare`, `queue
compare` and the verification suite need the digests (`standard`; the suite asks for it); `c5h fingerprint compare`
reads `Lua.log` (any level); `c5h dumps` needs the dumps (`full`; `c5h parity`, `host-first` and `skew` run the host
themselves and keep everything). With AI logging on, leaving out the DLL's AI logs saves disk rather than time: a Standard
game's first 100 turns took 66.6 s and left 4.5 MB at `standard`, and took 69.3 s and left 437 MB at `full`. The DLL does the
AI logging's work at every level; having it off, the default, is what saves time ([ai-logging.md](ai-logging.md)).

#### The run report

`report.html` is written at the end of every game (`--no-report`: not) and `c5h report RUN [RUN...]` rebuilds it from
the folder. It is one self-contained page (inline CSS and SVG; light and dark themes; a little optional JavaScript for
the theme button and the timeline filter):

- the setup (map, size, speed, difficulty, seeds, civilizations and city-states, the DLL with its SHA-256 and why it
  was chosen, mods, host commit, detail level) and the result (winner and victory, or the turn the game stopped at;
  autoplay seconds and seconds per turn, CPU, peak memory);
- the players: civilization, leader, team, alive, score, cities, population, techs, policies, era, wonders, religion;
  vassals indented under their master (`final-state.json`'s `master`, else the replay messages and the final wars);
- per-player charts over the turns (score, cities, population, techs, policies, culture, science, gold, military,
  land, happiness, tourism, faith, wonders) from the replay data, else the checkpoints, else the fingerprint metrics,
  each line labelled at its end; the host's seconds per turn;
- the event timeline (cities founded and captured, wars and peace, wonders, religions, eras, eliminations, ...),
  filterable by kind and player; the map at turn 0 and the final territories with the cities;
- health (Lua errors with file and line, stubbed calls not known to be harmless, UI profiles, add-ins, unknown UI
  files) and the folder's files with their sizes.

A run without some of it (a DLL without digests or replay data, the stock DLL, a game stopped early or crashed, an
older run folder) leaves those parts out with a note; a report that fails is logged in `events.jsonl` and never fails
the game. `c5h report A B ...` (or a folder of runs) also writes a summary page over them, `summary.html` in their
common folder (`--summary FILE`): a table of the games and their main series overlaid.

Where they differ from a real run, by design:

- **Checkpoints and times** are exact: taken when the game turn advances, not by a poll during a turn. They do not
  change the game. `wallSeconds` counts from arming autoplay to the last turn; start-up (database copy, map
  generation, about 3 s) is outside it.
- **save:** not a saved game (those are in `saves\`, listed in `saves` and `finalSave`); `save` holds a seed key
  `headless-<spec>-m<map>-s<sync>-<hash>` that groups runs of the same game.
- **background:** system busy cores, the real game's CPU and everything but the host during the run, so timings can
  be judged.
- **Victory** ends autoplay: `endReason` is `victory`, with `winner` and `victory`.

## 6. Batches

```
c5h queue run QUEUE.json [--workers N] [--affinity p-first|none] [--cpu-limit PERCENT] [--efficiency-mode]
              [--total-cpu-limit PERCENT] [--retry-failed]
c5h queue status QUEUE.json
c5h queue compare QUEUE.json [--vpbench DIR]
c5h queue report QUEUE.json
```

```json
{"out": "ab-stack", "workers": 8,
 "defaults": {"spec": "../specs/continents-standard.json", "turns": 250, "maxSeconds": 7200},
 "variants": {"A": {"dll": "baseline/CvGameCore_Expansion2.dll", "abRole": "baseline"},
              "B": {"dll": "test/CvGameCore_Expansion2.dll", "abRole": "test"}},
 "seeds": [[1001, 2002], [1003, 2004], [1005, 2006]]}
```

- Paths in a queue are relative to the queue file. Jobs are seeds x variants (named `<variant>-m<map>-s<sync>`)
  plus any explicit `jobs`; every job key (`spec`, `turns`, `dll`, `db`, `modsDir`, `modsDb`, `mods` (a mod set,
  `["ID[:VER]", ...]`), `noMods`, `profiles`, `noProfiles`, `skipAddins`, `noAddins`, `fingerprint` (`true` or
  `{"every": K, "turns": [...], "split": N}`), `diagnostics`, `improvedMask`, `abRole`, `checkpointEvery`,
  `maxSeconds`, `stallSeconds`, `maxMemoryMB`, `detail` (default `standard`; the old `prune: false` is `full`),
  `report` (default on), `hostArgs`, `luaProbes`, `checkpointExtras`, `finalExtras`, `lowPriority` (default on),
  `cpuLimit`, `efficiencyMode`, `smallBlockAllocator` (`--sba`), `aiLogging` (`"on"` or `"off"`), `load` (a save instead of `spec`; `seeds` are then new
  seeds for the game, [saves.md](saves.md#a-save-as-a-baseline)),
  `autosave`, `saveAt`, `saveFinal`) can be set in `defaults`, a variant or a job. Examples are in
  `examples/queues/`.
- Mod sets' registries and databases are built once per (mods input, DLL) before the workers start.
- Worker k is pinned to the k-th physical core: P-cores (both hyperthreads) first, then E-cores.
- `--total-cpu-limit PERCENT` (or a top-level `"totalCpuLimit"`) caps all running games together at that percent of
  one core (200: two cores' worth), however many workers; the games share it
  ([running.md](running.md#running-in-the-background)).
- State is in `QUEUE.state.json`. A restart skips completed runs, re-queues interrupted ones (the partial folder is
  kept as `<name>-rN`) and leaves failed ones failed unless `--retry-failed`. A stopped runner takes its games with
  it (job objects). `QUEUE.stop` pauses: no new games start, running ones finish.
- `status` prints the job table and totals (game turns per hour, background load) and writes `QUEUE.summary.json`.
  `queue run` ends by writing `<out>/summary.html`, a page over the queue's games with links to their reports;
  `queue report` rebuilds it.
  `compare` compares the digests of each seed's variants; with `--vpbench DIR` (the Vox Populi fork's benchmark
  tools) it also runs samerun per seed pair and abcompare over the batch.

Host games are deterministic: a repeated run is identical, so the A/A noise floor that real runs need two baselines
for is zero, one run per (seed, variant) is enough, and more seeds buy more signal.

## 7. Comparing runs

- `c5h compare A B`: the first differing digest point, the differing sections per turn, and transients (differences
  that heal within `--heal-turns` turns). `savedb` (UI state, see Limits) is reported apart and never counts.
- `c5h dumps REF HOST [--fields DIR]`: two state dumps section by section; with a field map (written by a field-map
  build of the DLL) each difference is attributed to the field that wrote it.
- `c5h fingerprint compare A B`: the same for the Lua fingerprints of runs played with `--fingerprint` (any DLL,
  also without the digest additions), with the per-player metrics of the first differing turn.
- `c5h digest verify DUMP`: recompute a dump's hashes.
- `python tools/phase_speed.py RUN...`: seconds per turn by game phase.

`c5h compare`, `c5h fingerprint compare` and `c5h queue compare` warn when the two games had different AI logging
([ai-logging.md](ai-logging.md)).

## 8. The real game

`civ5headless.realgame` drives the installed game through its FireTuner debug port, for references and verification.
It never edits the game's files; the only file it changes is `config.ini`, two seed values, which it puts back.

Needs: `EnableTuner = 1` and `LoggingEnabled = 1` in `config.ini` (`c5h doctor` checks, never edits), Steam running
and signed in, and the game not running. Optional: Core Temp running, for the CPU-temperature guard.

```python
from civ5headless import config
from civ5headless.realgame.session import RealGameSession

cfg = config.load()
with RealGameSession(cfg, cfg.runs / 'my-reference') as s:
    s.launch(1001, 2002)                       # the first game's seeds, pinned before the launch
    s.set_mods([m.id for m in cfg.active_mods])
    s.start_game('examples/specs/continents-tiny.json', 1001, 2002, fingerprint=True)
    result = s.autoplay(100)                   # startTurn, endTurn, endReason, turnSeconds, ...
    replay = s.replay_data()                   # as the host's replay-data.json
    s.collect_logs(cfg.runs / 'my-reference/logs')
    stats = s.process_stats()                  # peak working set, peak private bytes, CPU seconds
```

`load_game(save, fingerprint=True)` continues a saved game instead of `start_game`: it activates the `set_mods`
mods, else the save's (the save lists only the mods that affect saved games, so set the full set to keep the others),
and loads from the Mods menu as the game's Load screen does. `save_game(name)` saves as the in-game Save menu does and
moves the file into `<run>\saves\` ([saves.md](saves.md)).

- **Launch:** `CivilizationV_DX11.exe -DX11` directly, with `steam_appid.txt` in the game folder until the tuner port
  is open (a Steam SDK development launch; the file is removed again), then the main menu.
- **Mods:** `set_mods(ids)` makes the enabled mods exactly that set in the mod browser and activates them (`[]`:
  none). The enabled list stays as set, as when done by hand.
- **Seeds:** the game reads `MapRandSeed`/`SyncRandSeed` from `config.ini` when its DLL loads, and a game started from
  the menu then uses them for everything the seeds decide, random civs included, so the host replays it from the
  same seeds with any DLL. Seeds given to `launch()` are pinned before the launch; `start_game` with other seeds pins
  them and relaunches the game. `quit()` restores the original values, or the next session does after a crash
  (`c5h doctor` warns while seeds are pinned). With the DLL's optional digest additions, `start_game` checks the seeds
  the engine used and fails on a mismatch.
- **Order:** `start_game` sets up the pregame state (`c5h spec lua`), starts the game, waits for it to load, runs the
  probes in the game's context and leaves it paused at turn 0; `autoplay` arms autoplay (quick combat and movement,
  every player an AI) and only then unpauses. That is the host's order.
- **CPU guard:** with Core Temp running, the game is ended when the hottest core stays at 95 °C or more for three
  samples 15 s apart (`cpu-temperature.jsonl` in the run folder).
- The run folder logs everything: `session.jsonl`, `calls/` (each labelled Lua call and its result),
  `spec-resolved.json`, `config.ini.backup`.

Tools on the session (`-h` for options):

- `python -m civ5headless.realgame.probe --run NAME [--spec SPEC --turns N] --probe CONTEXT[@WHEN]=FILE.lua`: Lua
  probes at the front end, at turn 0 and after autoplay; with `--host-digest` it compares the live digests with a
  host run and stops after a divergence.
- `python -m civ5headless.realgame.record MATRIX.json`: a matrix of references, one probe run per case, resumable.
- `python -m civ5headless.realgame.dbcapture --run NAME [--snapshot-only]`: the game's databases (DLC snapshot,
  modded) and a check against what the game holds in memory.
- `python -m civ5headless.realgame.tuner serve|states|exec`: a persistent tuner connection for manual Lua calls.

**Not yet run against the real game.** The session is tested against a fake game behind a fake tuner (protocol,
order of calls, seeds, mods, relaunch, logs, quit). The code it is ported from ran live in the source repository, but
these parts are new and have not:

- the Python launcher (it replaces a PowerShell one: same `steam_appid.txt` handling and port-owner check) and the
  CPU guard's Core Temp reading (same layout as the PowerShell guard);
- `set_mods` disabling mods (`Modding.DisableMod`) and changing a non-empty enabled list (the old code only enabled
  mods into an empty list), and activating with no mods;
- waiting for ModsSinglePlayer to be shown (`ContextPtr:IsHidden()`), and `to_main_menu` followed by a new
  activation and game in the same session;
- `reseed='reload'` (pin the seeds and rely on the next mod activation reloading the DLL, instead of relaunching);
  the default relaunch uses the verified mechanism;
- `replay_data()` (the host's replay-data Lua, fetched one player per call because of the tuner's result limits);
- the session as a whole, and probe, record and dbcapture on it.

## 9. References and parity

Parity means the host plays exactly the real game's game. Check it after host changes, merges and DLL changes that
touch the engine boundary.

1. Optionally run the host on every case first (`c5h host-first MATRIX.json`): the recorder then compares the live
   digests and stops a couple of turns after a difference, which saves real-game time.
2. Record references in the real game with a DLL that has the digest additions: `python -m
   civ5headless.realgame.record MATRIX.json [--host-digest "first-{run}/Logs/HeadlessDigest.log"]` (the spec's seeds
   pinned, per-turn digest on, dumps at the load screen and after the last turn).
3. `c5h parity REF_RUN` (or `--matrix STATUS.json`) replays each reference in the host and compares the digests and
   dumps. A difference names a turn and section; dumps of that turn from a field-map DLL name the fields
   (`--host-arg=--dump-turns --host-arg=a:b`, `c5h dumps --fields`). Fix the host when it answers differently from
   the engine; make the DLL result-neutral when it depends on something the host cannot reproduce.
4. `c5h skew` plays cases with and without an extra engine update every K updates: a result that depends on the
   frame count is a divergence waiting to happen, since the real game's frame count is not the host's.

## 10. Speed, scaling and memory

Measured 2026-10-25 on an i9-13900K (8 P-cores with hyperthreading, 16 E-cores, 64 GB) with the Vox Populi fork's DLL,
Continents Standard Emperor, 8 majors, 16 city-states, the reference profile's DLL logging on. The machine was shared
(the real game recording references, DLL builds); the tables give that background load.

### One game against the real game

Host: one game pinned to one P-core (seeds 1001/2002), background 2.7 to 4.7 busy cores. Real game: three benchmark
runs with the same settings and other seeds, and a trace run of this spec.

| s/turn | host (1001/2002) | real A | real B | real C | real, this spec | speed-up |
|---|---:|---:|---:|---:|---:|---:|
| T0-25 | 0.59 | 3.87 | 3.88 | 3.86 | 3.76 | 6.5x |
| T25-50 | 0.67 | 3.87 | 3.86 | 3.88 | 4.04 | 5.9x |
| T50-75 | 0.86 | 4.27 | 5.30 | 4.47 | 4.48 | 5.3x |
| T75-100 | 1.17 | 5.32 | 5.92 | 5.31 | 5.55 | 4.7x |
| T100-125 | 1.46 | 5.91 | 6.95 | 6.12 | | 4.3x |
| T125-150 | 1.66 | 6.34 | 7.15 | 6.33 | | 4.0x |
| T150-175 | 2.56 | 7.55 | 8.17 | 6.95 | | 2.9x |
| T175-200 | 3.35 | 8.16 | 11.44 | 8.57 | | 2.8x |
| T200-225 | 3.20 | 9.39 | 11.84 | 10.81 | | 3.3x |
| T225-250 | 4.08 | 10.22 | 11.64 | 10.02 | | 2.6x |
| **T0-250** | **1.96** | 6.49 | 7.62 | 6.63 | | **3.5x** |

(speed-up: the mean of the real columns over the host; after T100 the real games are other games, so the late
phases compare like with like only on average.)

- **Whole games:** 250 turns in 490 s of autoplay against 1,597 to 1,904 s in the real game; 100 turns of the
  reference game in 81.5 to 87 s against 443 s for the same game in the real game, 5.2 to 5.4x.
- **Where the gain comes from:** early turns are bound by the real game's frame pacing (one player per update, about
  105 ms apart, so about 3.8 s per turn whatever the AI does); the host runs updates back to back. Late turns are AI
  work in both, and the host still saves the rendering, UI and waiting.
- **CPU:** 451 CPU-s for 250 turns against 3,985 to 4,900 CPU-s for the real game (it keeps about 2.5 cores busy).
- **DLL logging:** the figures here have it on. With it off (measured with `--no-game-logging`), 100 turns go from 81.5 s to
  55.6 s, but the logging changes AI results in this DLL (the game differs from turn 53), so such runs are not comparable
  with logged runs or a logging real game ([ai-logging.md](ai-logging.md)).

### Memory

| | peak working set | peak private bytes |
|---|---:|---:|
| host, T100 | 270 to 283 MB | about 280 MB |
| host, T250 | 327 MB | 326 MB (peak virtual 506 MB) |
| real game, T250 | 1,763 to 1,825 MB | 2,046 to 2,080 MB (peak virtual 2.9 GB) |

Memory never limits parallelism: 24 games need under 8 GB. The default commit limit of 3,500 MB per game only
catches runaway runs.

### Parallel scaling

W identical 100-turn games on W workers, pinned P-cores first. "Batch" is W x 100 turns over the batch time (the
slowest game); "steady" is W x 100 turns over the mean game time, the rate of a long queue that keeps every worker
busy.

| workers | batch s | turns/h (batch) | turns/h (steady) | game s on P-cores | game s on E-cores | other busy cores |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 90 | 3,980 | 4,000 | 87.0 | | 4.7 |
| 2 | 97 | 7,430 | 7,730 | 89.8 | | 5.2 |
| 4 | 97 | 14,830 | 15,710 | 88.3 | | 5.4 |
| 8 | 109 | 26,450 | 29,070 | 95.7 | | 6.3 |
| 12 | 147 | 29,450 | 36,890 | 101.6 | 135.1 | 6.2 |
| 16 | 163 | 35,360 | 45,190 | 105.3 | 140.2 | 7.3 |
| 24 | 267 | 32,360 | 55,010 | 113.0 | 170.3 | 9.9 |

- Up to 8 workers (one P-core each) scaling is almost linear: 8 games take 10% longer than one.
- An E-core game takes about 1.35x a P-core game. 16 workers gave the best batch rate here and 24 the best steady
  rate, at the cost of stretching every game.
- 8 workers (the default) while the machine is shared; 16 to 24 for a long queue on an idle machine. In real-game
  terms the machine plays roughly 25 times as many games per hour as the real game.

## 11. Limits

- **savedb:** the `savedb` digest section (the embedded `Civ5SavedGameDatabase.db` the real game's UI writes into
  saves) is not reproduced. It holds UI state, not game state, and comparisons leave it out.
- **UI-driven state:** the host runs the mods' InGameUIAddins and emulates the other UI Lua that changes game state
  with profiles. A UI mod whose stateful Lua is neither an add-in the host can run nor covered by a profile needs a
  new profile; `host-report.json` lists its files as unknown and `c5h hazards scan` finds the calls.
- **DLL logging changes AI results** in some DLLs: `c5h` games have AI logging off by default, which is faster, while a real
  game with `AILog = 1` has it on and may play a different game. `--ai-logging on` plays as that real game does, and
  `c5h verify` matches `config.ini` itself. Never mix the two in a comparison ([ai-logging.md](ai-logging.md)).
- **Saves:** a real-game save loaded in the host and in the real game can drift apart after the first turn (1 gold at
  the next turn in one test); the host's load sequence is reconstructed, not traced ([saves.md](saves.md) section 6).
- **Text:** the localization database is the game's `Localization-Merged.db`; mod text edits need a game launch to
  appear in city names and replay messages (rules are unaffected).
- **32-bit:** the host is a large-address-aware 32-bit process (4 GB address space), far above its needs.
