# Running games

A hands-on guide to playing games with `civ5headless`: what to install, how to describe a game, every option of
`c5h run`, batches, comparisons, the verification suite and the configuration file, with examples. The
[user guide](user-guide.md) explains how things work underneath; this page is about using them.

Examples write `c5h`: that is `c5h.cmd` in the kit or checkout folder (`.\c5h.cmd` from PowerShell when the folder
is not on `PATH`). Every command prints its options with `-h`, e.g. `c5h run -h`.

## 1. What you need

- Windows, and Civilization V with Brave New World from Steam, started at least once to its main menu. The game
  writes the caches the host reads (`Civ5CoreDatabase.db`, `Localization-Merged.db`) at its first start.
- **Release kit** (recommended): unpack `civ5-headless-<version>-<commit>.zip` anywhere. It brings the host
  (`bin\civ5headless.exe`), its own Python (`runtime\`), the tools, profiles, probes, examples and these docs. Runs
  and caches go into the kit folder.
- **Checkout**: Python 3.11 or newer, and a host built with `python tools/build_host.py` (README, "Building").
- No compiler is needed to run anything: the host uses the Visual C++ 2008 runtime that Civ V installs.
- The real game is only needed for the verification suite's real-game checks (section 8).

## 2. First steps

```
c5h doctor
```

`doctor` shows every setting with where it came from (discovered, the config file, a default) and checks three
groups: what host games need, what real-game runs need, and the toolchain (only for building the host). Exit code 1
means a host-game check failed; fix that first. What it discovers:

| Setting | Found from |
|---|---|
| Game folder | Steam (registry, `libraryfolders.vdf`, the Civ V app manifest) |
| My Games folder | the Documents known folder (follows a OneDrive-redirected Documents) |
| MODS, cache, `config.ini` | inside My Games |
| Active mods | the game's mods registry (`cache\Civ5ModsDatabase.db`): the mods you last activated in the game |
| Game DLL | the first active mod's `CvGameCore_Expansion2.dll`, else the Community Patch folder's, else the game's own |
| Host | the kit's `bin\`, or the latest build of a checkout |

Then play a game:

```
c5h run examples\specs\continents-tiny.json --turns 50
```

This plays a Tiny Continents game with your active mods and DLL until turn 50 and prints progress as JSON lines.
The run folder is `runs\continents-tiny-<date>-<time>\`. Its `report.html` shows the game: setup, result, charts,
map, timeline and health (section 6). The first run with a mod set also builds and caches the game database, which
takes a few seconds; later runs reuse it.

A full game, on a new random map: Continental Drift at Standard size, Epic speed, Immortal. The game stops at the
first victory, or at turn 750, Epic's last. A Vox Populi test game ended in a cultural victory at turn 404 after 31
minutes.

```
c5h run examples\specs\continental-drift.json --turns 750
```

## 3. Describing a game: specs

A spec is a JSON file that mirrors the single-player setup screens. Anything left out behaves like an untouched
setup screen. Examples are in `examples\specs\`.

### A minimal spec

```json
{"map": {"script": "Assets/Maps/Continents.lua", "size": "WORLDSIZE_TINY"},
 "speed": "GAMESPEED_STANDARD", "era": "ERA_ANCIENT", "difficulty": "HANDICAP_EMPEROR",
 "seeds": {"map": 30017, "sync": 40017}}
```

### Every field

| Field | Meaning | Default |
|---|---|---|
| `map.script` | map script file as in the game's MapScripts table (`Assets/Maps/Pangaea.lua`, a mod's script such as `Communitu_79a.lua`), or `"random"` | Continents |
| `map.size` | `WORLDSIZE_DUEL`, `_TINY`, `_SMALL`, `_STANDARD`, `_LARGE`, `_HUGE`, or `"random"` | Standard |
| `map.options` | map script options by their `TXT_KEY_MAP_OPTION_*` name and value index | the script's defaults |
| `map.civ5map` / `map.type` | a premade map instead of a script: a file from Map_Sizes, or a Maps type resolved by size | none |
| `speed` | `GAMESPEED_QUICK`, `_STANDARD`, `_EPIC`, `_MARATHON` | Standard |
| `era` | start era, e.g. `ERA_ANCIENT`, `ERA_MEDIEVAL` | Ancient |
| `difficulty` | `HANDICAP_SETTLER` … `HANDICAP_DEITY` | Emperor |
| `majors` | number of major civs | the world size's default |
| `cityStates` | number of city-states | the world size's default |
| `slots` | per slot: `{"slot": N, "civ": "CIVILIZATION_X", "team": T}` (unlisted slots: random civ, own team) | random civs |
| `minorCivs` | city-state types (`MINOR_CIV_X`) in slot order, for DLLs that leave the choice to the game (section 3.4) | random |
| `gameOptions` | e.g. `{"GAMEOPTION_RAGING_BARBARIANS": true}` | none set |
| `victories` | the enabled victory types, e.g. `["VICTORY_DOMINATION", "VICTORY_TIME"]` | all |
| `maxTurns` | the game's turn limit (time victory) | the speed's |
| `seeds` | `{"map": M, "sync": S}` (section 3.2) | fresh random seeds |

### More examples

A Small Communitu map with options, six majors, raging barbarians, only domination and time victories, Greece in
slot 1:

```json
{"map": {"script": "Communitu_79a.lua", "size": "WORLDSIZE_SMALL",
         "options": {"TXT_KEY_MAP_OPTION_SEA_LEVEL": 3, "TXT_KEY_MAP_OPTION_WORLD_AGE": 4}},
 "majors": 6, "cityStates": 10,
 "gameOptions": {"GAMEOPTION_RAGING_BARBARIANS": true},
 "victories": ["VICTORY_DOMINATION", "VICTORY_TIME"], "maxTurns": 200,
 "slots": [{"slot": 1, "civ": "CIVILIZATION_GREECE", "team": 1}],
 "seeds": {"map": 7, "sync": 8}}
```

Two teams of two on a Small Pangaea at Deity, quick speed:

```json
{"map": {"script": "Assets/Maps/Pangaea.lua", "size": "WORLDSIZE_SMALL"},
 "speed": "GAMESPEED_QUICK", "difficulty": "HANDICAP_DEITY", "majors": 4,
 "slots": [{"slot": 0, "team": 0}, {"slot": 1, "team": 0}, {"slot": 2, "team": 2}, {"slot": 3, "team": 2}]}
```

A premade map (`examples\specs\earth-small.json`):

```json
{"map": {"civ5map": "Assets/Maps/Earth_Small.Civ5Map"}, "speed": "GAMESPEED_QUICK"}
```

Check a spec without playing it: `c5h spec resolve SPEC.json` validates every name against the game database and
prints the explicit form the host reads (IDs, the resolved random choices, the seeds). `c5h spec lua SPEC.json`
prints the Lua that sets up the same game in the real game's setup screen.

### 3.2 Seeds

The map seed and the sync seed fix the whole game: the map, the civilizations, the city-states and every AI decision.
The same spec, seeds, DLL, mods and database give the same game on every run, and the same game the real game plays.

- A spec without seeds gets fresh random ones. They are recorded in the run folder (`spec-resolved.json`), and
  `c5h run runs\NAME\spec-resolved.json --turns N` plays that game again.
- Each example comes twice: `examples\specs\NAME.json` has no seeds (a new map and game on every run), and
  `NAME-fixed-seed.json` pins them (the same game every run, for comparisons and A/B tests).
- `--seeds MAP SYNC` overrides the spec's seeds: `c5h run my.json --turns 100 --seeds 1001 2002`.
- To get different games from one spec, vary the seeds. Some DLLs (Vox Populi) draw almost everything from the map
  seed, so vary that one; the stock DLL uses both.

### 3.3 How long

`--turns N` plays until game turn N. A game also ends earlier on a victory, which the run records (winner, victory
type, turn). For a whole game, give the speed's last turn: Quick 330, Standard 500, Epic 750, Marathon 1500.

How long a game takes in the host, with Vox Populi:
- Tiny, 100 turns: well under a minute.
- Standard size and speed, to a cultural victory at turn 281: 9 minutes.
- Standard size, Epic speed, to turn 404: 31 minutes.

The stock DLL is several times faster.

### 3.4 DLL and mods

By default a game uses your active mods and the DLL that comes with them. To play something else:

| You want | Use |
|---|---|
| Another DLL build with your mods | `--dll path\to\CvGameCore_Expansion2.dll` |
| A different set of installed mods, without activating them in the game | `--mod <ModID>[:<version>]` per mod (repeatable) |
| A MODS folder elsewhere (a pinned copy, another mod pack) | `--mods-dir D:\packs\VP-5.4.8\MODS` with `--mod ...` |
| No mods at all | `--no-mods` |
| Another mods registry (e.g. a copy of the game's) | `--mods-db path\to\Civ5ModsDatabase.db` |

Mod IDs are the GUIDs in each mod's `.modinfo`; `c5h doctor` lists the active ones. A mod given without a version is
its highest version in the MODS folder. Dependencies are put first automatically; a missing dependency, a version out
of range or a mod that blocks another is an error, as in the game's mod browser.

Which DLL plays: `--dll` if given, else a `dll` set in `civ5headless.toml`, else the DLL that comes with the mods
being played. That is the first mod (in activation order) that ships a `CvGameCore_Expansion2.dll`, else the game's
own, so `--no-mods` plays the game's own DLL. The run's `events.jsonl` and report say which DLL was used and why.

```
:: the stock game, no mods
c5h run examples\specs\continents-tiny.json --turns 100 --no-mods

:: Community Patch only, from your MODS folder
c5h run examples\specs\continents-tiny.json --turns 100 --mod d1b6328c-ff44-4b0d-aad7-c657f83610cd

:: a test build of the DLL against your usual mods
c5h run examples\specs\continents-standard.json --turns 150 --dll C:\builds\test\CvGameCore_Expansion2.dll
```

Some DLLs, the stock one among them, leave the choice of random civilizations and city-states to the game's
executable. The host then chooses them itself, which cannot match what the real game would choose. Name every civ in
`slots` and the city-states in `minorCivs` when such a game is to be compared with the real game; for host-only work
it does not matter. The run's `host-report.json` says who chose (`dll.hostChoseCivs`, `minorCivs`).

### 3.5 The game database

The host plays on the same game database the game builds when it loads your mods. It is built automatically and
cached per mod set and DLL (`cache\db\`), starting from a snapshot of the base game and DLC that is itself built from
the game's files. You rarely need to touch this:

- `c5h dbcache` prints (and builds) the cached database for the current mods and DLL.
- `--db path\to\built.db` plays on a database you built or captured yourself.
- `c5h snapshot` builds the base-game snapshot on its own; `c5h dbbuild` builds a database step by step with a
  report.

### 3.6 Saved games

The host reads and writes the game's own `.Civ5Save` files: a save from the real game continues in the host, and
a save the host writes opens in the game's Load menu (with the same mods active). [saves.md](saves.md) has the
details.

Continue a saved game to turn 120: give the save where the spec goes (or as `--load SAVE`). The save brings its
mods: the host activates them for the run (the game need not have them active), and picks the DLL as for `--mod`
(section 3.4).

```
c5h run "%USERPROFILE%\Documents\My Games\Sid Meier's Civilization 5\ModdedSaves\single\Rome.Civ5Save" --turns 120
```

`--turns` is still the turn to play to, after the save's. `--seeds` does not apply: the save holds the random state.
`c5h save FILE...` shows what a save is: turn, civilization, map, DLC, mods and DLL (`--json` for all of it).

Every save a run writes stays in its folder, `<run>\saves\`, never in the game's save folders. By default a run
saves its last turn, so the game can be opened in the real game or continued with another run; other saves are off
unless asked for, because each costs time (about 0.1 s on a Tiny map):

| Option | Saves |
|---|---|
| (default) | the last turn, once autoplay has ended: `Final_<turn and date>.Civ5Save` |
| `--no-save-final` | not the last turn |
| `--autosave none` | no autosaves (the default) |
| `--autosave game` | the game's own setting (`UserSettings.ini`, `TurnsBetweenAutosave`, 10 unless changed) |
| `--autosave N` | every N turns |
| `--save-at T` | at turn T (repeatable): `Turn_<turn and date>.Civ5Save` |

Autosaves follow the real game: `saves\auto\AutoSave_Initial_0000 BC-4000.Civ5Save` and
`AutoSave_Post_0000 BC-4000.Civ5Save` at turn 0, then `AutoSave_Post_<turn and date>` every N turns (e.g.
`AutoSave_Post_0050 BC-2000.Civ5Save`); all are kept. To open a host save in the real game, copy it into the game's
`ModdedSaves\single` (or `Saves\single` for an unmodded game) and load it from the Mods menu with the same mods
active ([saves.md](saves.md) section 6).
`status.json` lists the saves (`saves`, `finalSave`), and the report shows them. Queue jobs take a save as `spec`
or `load`, and `autosave`, `saveAt` and `saveFinal` (section 7).

### 3.7 A Vox Populi DLC modpack

A modpack (`Assets\DLC\VP_MODPACK...`) is not a set of mods: the game builds the whole modded database into its
cache from the modpack's `Override\*.xml` files and plays it as DLC, with no mods active. The host's own database
build cannot repeat that (it would apply the DLC layer again on top of a core database that already holds the mods),
so play on the database the real game built:

1. Start the real game once with the modpack installed and no mods active, to its main menu.
2. Copy `cache\Civ5DebugDatabase.db` and `cache\Localization-Merged.db` somewhere they stay put.
3. Point the host at them and at the modpack's DLL, and play with `--no-mods` (queue: `"noMods": true`):

```toml
dll = "C:/Program Files (x86)/Steam/steamapps/common/Sid Meier's Civilization V/Assets/DLC/VP_MODPACK/Mods/(1) Community Patch/CvGameCore_Expansion2.dll"
db = "pinned/vp-modpack/Civ5DebugDatabase.db"
loc_db = "pinned/vp-modpack/Localization-Merged.db"
```

Copy them again after updating the modpack. The modpack's Lua comes from its package folders; inside one package the
file found last wins, as in the game (VP's copy over CP's, the package's `UI` folder over its `Mods`). Name map
scripts by their path, e.g. `Assets/Maps/Communitu_79a.lua`, when a copy in your `Maps` folder has the same name.

## 4. Mod UI: add-ins and profiles

The host has no user interface. Two things stand in for the parts of mod UI code that change the game:

- **UI add-ins** (`InGameUIAddin` entries of your mods) run in a stand-in UI context, so add-ins that register
  gameplay events work as in the game. `--skip-addin NAME` leaves one out (its file name, with or without `.lua`),
  `--no-addins` leaves them all out.
- **UI emulation profiles** (`profiles\`) reproduce what specific UI screens do to the game state (Vox Populi's
  "who is winning" popup, EUI's civilization ribbon, Community Patch's city events). They are chosen automatically
  from the mod files they match. `--profiles DIR` uses another folder, `--no-profiles` none.
  [profiles.md](profiles.md) says how to write one.

Which add-ins ran, which profiles were chosen and why, and which UI files the host does not know are in the run's
`host-report.json`. `c5h hazards scan --report runs\NAME\host-report.json` lists UI code that changes the game state
without a profile covering it: start there if a mod set's games look different from the real game.

## 5. Fingerprints and probes

`--fingerprint` makes the host write a compact per-turn summary of the game state into the run's `Lua.log`: hashes of
the players, cities, units, techs, map and diplomacy, and per-player metrics (score, gold, cities, population, techs,
policies, units, culture, faith, wonders, religions, wars). It works with any DLL and does not change the game.

| Option | Effect |
|---|---|
| `--fingerprint` | every turn |
| `--fingerprint-every 10` | every 10th turn |
| `--fingerprint-turns 1,50,100` | these turns as well |
| `--fingerprint-split N` | sections per log line (default 12; only matters for very long lines) |

Use it to compare two runs exactly (`c5h fingerprint compare runs\A runs\B`), to see where two games part ways, or as
the source of per-turn metrics. The real-game side of the verification suite injects the same probe.

Your own Lua gets anything else out of a game ([probes.md](probes.md) is the guide, with examples in
`examples\probes\`):

| Option | Runs | Output |
|---|---|---|
| `--checkpoint-extra FILE` | at every checkpoint (`--checkpoint-every`) and the final one | what the file returns, as JSON: `extra\NAME.jsonl`, one line per checkpoint |
| `--final-extra FILE` | once, after the last turn | what the file returns, as JSON: `extra\NAME-final.json` |
| `--lua-probe FILE` | once the game has started, before turn 0 is played; its `GameEvents` listeners for the rest of the game | what it prints to `Lua.log`, or files it writes into `C5H_EXTRA_DIR` |

All three are repeatable, and NAME is the file name without `.lua`. An error in an extra costs only that checkpoint's
value (the row says why); a probe that does not load fails the run. Use plain getters only: Lua that changes the game
changes the result.

```
c5h run examples\specs\continents-tiny.json --turns 150 --checkpoint-extra examples\probes\turn-stats.lua --final-extra examples\probes\game-summary.lua --lua-probe examples\probes\city-captures.lua
```

A probe on its own:

```lua
-- my-probes/gold.lua: every 10th turn, each major civ's gold and number of cities
GameEvents.PlayerDoTurn.Add(function(iPlayer)
    local turn = Game.GetGameTurn()
    local p = Players[iPlayer]
    if turn % 10 == 0 and p:IsAlive() and not p:IsMinorCiv() and not p:IsBarbarian() then
        print(string.format("GOLD|turn=%d|player=%d|gold=%d|cities=%d", turn, iPlayer, p:GetGold(), p:GetNumCities()))
    end
end)
```

```
c5h run examples\specs\continents-standard.json --turns 200 --lua-probe my-probes\gold.lua
findstr "GOLD|" runs\NAME\Logs\Lua.log
```

## 6. What a run leaves behind

Every game gets a run folder: `runs\NAME\`, with `--run NAME` choosing the name (default `<spec>-<date>-<time>`) and
`--run-dir DIR` any folder. Open `report.html` in it first.

### The report

`report.html` is one self-contained page, written when the game ends. It has light and dark themes, and opens from the
folder with no server and no internet connection.

- **Setup and result:**
  - the map, size, speed, difficulty, seeds and civilizations;
  - the DLL and why it was chosen, the mods, the host version and the detail level;
  - the winner and victory type, or the turn the game stopped at;
  - autoplay time, seconds per turn, CPU and peak memory.
- **Players:** civilization, leader, team, alive, score, cities, population, techs, policies, era, wonders and
  religion, plus the city-states and which of them survived.
- **Over the game:** a chart per measure for every civilization, from the game's replay data: score, cities,
  population, techs, policies, culture, science, gold, military, land, happiness, tourism, faith and wonders.
- **Map:** the map at turn 0 with the starting positions, and the final territories with the cities.
- **Timeline:** cities founded and captured, wars and peace, pacts, wonders, religions, eras, golden ages,
  eliminations and the victory. It has a strip over the turns and a table you can filter by kind and player.
- **Performance:** the host's seconds per turn.
- **Health:** Lua errors with file and line, engine calls the host only stubs, UI profiles and add-ins, and unknown UI
  files.
- **Files:** what the folder holds, with sizes and links.

When a part has no data, the report says so instead of showing it: the stock DLL has no digests, and a crashed game
has no final state.

```
c5h report runs\NAME                         # rebuild a run's report.html (after an upgrade, or after --no-report)
c5h report runs\A runs\B runs\C              # their reports, plus summary.html: the games side by side
c5h report runs\my-queue --summary ab.html   # every run in a folder; the summary page where you want it
c5h queue report my-queue.json               # a queue's summary page (queue run also writes it at the end)
```

### Detail levels

`--detail` (queue key `detail`) decides what the run folder keeps. Every level plays the same game; the host just
doesn't write what the level leaves out.

| Level | Keeps | Tiny, 30 turns | Standard, 100 turns |
|---|---|---:|---:|
| `summary` | results, setup, timings, replay, the final checkpoint and state, `Lua.log`, the report | about 1 MB | |
| `standard` (default) | also a checkpoint every turn and the small logs, including `HeadlessDigest.log` | about 1.4 MB | 4.5 MB |
| `full` | everything: state dumps (`dump-t000\`, `dump-tN\`), the game database copy (`db\`), all of the DLL's logs (including its AI logs) | 60 to 250 MB | 437 MB |

- `standard` is enough for the report, `c5h compare`, `c5h queue compare` and the verification suite, which need the
  digests. `c5h fingerprint compare` works at any level.
- Use `full` when you need the DLL's AI logs, or the state dumps for `c5h dumps`.
- The levels save disk, not time. The Standard game above took 66.6 s at `standard` and 69.3 s at `full`.
- Logs you ask for are kept at every level: `Stacking-*.log` with `--diagnostics`, and `RandCalls.csv` with
  `--host-arg=--rand-log`.
- `--prune` is the old name of `--detail standard`.

### The files

| File | Contents |
|---|---|
| `report.html` | the report above (none with `--no-report`) |
| `status.json` | the outcome (completed, stopped or failed), turns played, wall time, CPU, peak memory, winner, and your extras' rows and errors |
| `events.jsonl` | what the runner did, one JSON line per event (DLL choice, database build, launch, turns, end) |
| `setup.json`, `spec-resolved.json` | the game as set up: map, civs and seeds, enough to play it again |
| `hh-command.json` | the host command line, the inputs' hashes, the DLL and why it was chosen, the detail level |
| `turn-times.jsonl` | seconds per turn |
| `checkpoints\turn-NNNN.json` | the players, cities and wars at each turn (only the last turn at `summary`), and your checkpoint extras' values (`data.extra`) |
| `extra\` | your extras' results: `NAME.jsonl` (one line per checkpoint) and `NAME-final.json`, and files your probes write there ([probes.md](probes.md)); kept at every detail level |
| `final-state.json` | every player at the end, and the owners and cities on the final map |
| `replay-data.json`, `replay-messages.json` | the game's replay: per-player series and the event log |
| `t000-map.json` | the map at turn 0: terrain, features, starting positions |
| `host-report.json` | the host's view: DLL, mods, add-ins, profiles, Lua errors, engine calls it only stubs |
| `host-stdout.txt` | the host's console output |
| `saves\` | the run's saves: the last turn unless `--no-save-final`, and those asked for (section 3.6); kept at every detail level |
| `save-header.json` | with `--load`: the header of the save the game continued |
| `Logs\` | `Lua.log` (with fingerprints), `Headless.log`, and `HeadlessDigest.log` for DLLs with the digest additions; the AI logs at `full` |

## 7. Many games: queues

A queue file describes many games; `c5h queue run` plays them in parallel, one per CPU core by default.

```json
{"comment": "four seeds of a Standard game, 150 turns, with the current mods and DLL",
 "workers": 8,
 "defaults": {"spec": "../specs/continents-standard.json", "turns": 150, "fingerprint": true},
 "variants": {"base": {}},
 "seeds": [[1001, 2002], [3101, 3202], [4101, 4202], [5101, 5202]]}
```

```
c5h queue run my-queue.json
c5h queue status my-queue.json
```

- Jobs are every seed with every variant, named `<variant>-m<map>-s<sync>`, plus any explicit `"jobs"`.
- Every `c5h run` option can be set in `defaults`, a variant or a job, under its JSON name: `spec`, `turns`, `dll`,
  `db`, `modsDir`, `modsDb`, `mods` (`["ID[:VER]", ...]`), `noMods`, `profiles`, `noProfiles`, `skipAddins`,
  `noAddins`, `fingerprint` (`true` or `{"every": K, "turns": [...], "split": N}`), `luaProbes`,
  `checkpointExtras`, `finalExtras`, `seeds`,
  `checkpointEvery`, `maxSeconds`, `stallSeconds`, `maxMemoryMB`, `detail`, `report`, `lowPriority`, `cpuLimit`,
  `efficiencyMode`, `hostArgs`,
  `load` (a save instead of `spec`; a `spec` that is a `.Civ5Save` is loaded too), `autosave`, `saveAt`,
  `saveFinal` (default `true`).
- Paths in the file are relative to the file. Run folders go to `"out"` (default `runs\<queue file name>`).
- The queue is resumable: `QUEUE.state.json` remembers what is done. Stop it with Ctrl-C (running games are killed and
  re-queued at the next start) or by creating `QUEUE.stop` (running games finish, no new ones start). Rerun
  `queue run` to continue; `--retry-failed` retries failed games.
- `--workers N` sets the number of games at once. Workers are pinned to physical cores (`--affinity none` turns that
  off) and run below normal priority, so the machine stays usable. `--cpu-limit PERCENT` and `--efficiency-mode`
  make every game quieter, and `--total-cpu-limit PERCENT` caps the whole queue
  ([Running in the background](#running-in-the-background)).

An A/B test of two DLL builds over four seeds (`examples\queues\example-ab.json`):

```json
{"workers": 8,
 "defaults": {"spec": "../specs/continents-standard.json", "turns": 150, "maxSeconds": 3600},
 "variants": {"A": {"dll": "path/to/baseline/CvGameCore_Expansion2.dll"},
              "B": {"dll": "path/to/test/CvGameCore_Expansion2.dll"}},
 "seeds": [[1001, 2002], [3101, 3202], [4101, 4202], [5101, 5202]]}
```

Host games are deterministic, so one game per seed and variant is enough: a difference between A and B on the same
seed is caused by the change, not by chance. More seeds give more confidence in a trend.
`c5h queue compare my-queue.json` compares each seed's variants turn by turn.

## 8. Comparing runs

| Command | Compares |
|---|---|
| `c5h compare A B` | per-turn state digests (DLLs with the digest additions): first differing turn, sections, transients |
| `c5h fingerprint compare A B` | per-turn fingerprints (any DLL, runs played with `--fingerprint`), with the metrics of the first differing turn |
| `c5h fingerprint show A --metrics` | what one run's fingerprint log holds |
| `c5h queue compare Q` | every seed's variants of a queue |
| `c5h dbdiff A.db B.db` | two game databases, table by table |

Exit code 0 means identical. A difference that heals within two turns (`--heal-turns`) is reported as a transient.

## 9. The verification suite

`c5h verify` answers: for this DLL, these mods and these maps, how closely does a headless game represent a real Civ
V autoplay game, and how much faster and leaner is it? [verification.md](verification.md) explains the checks and
how to read the report.

| Mode | Needs the game | Time | Plays |
|---|---|---|---|
| `c5h verify --host-only` | no | about 5 min | host references (repeat, extra frames, shifted memory), probe check, a small ensemble, throughput |
| `c5h verify` (`--quick`) | yes | about 10 min | the real game: 4 setups to turn 0 and a Tiny game of 150 turns; beside it the host references and a 32-game ensemble |
| `c5h verify --full` | yes | about 20–25 min | adds a real repeat game, a Standard game on both sides, a larger ensemble, host games to victory |

```
:: before anything else, and in CI
c5h verify --host-only

:: the stock game
c5h verify --host-only --no-mods

:: a different map and size for the trajectory and ensemble games
c5h verify --map Assets/Maps/Pangaea.lua --size WORLDSIZE_SMALL --turns 120

:: a fixed time budget, warnings as failures
c5h verify --budget 20 --strict
```

The result is `report.html` and `verify.json` in `runs\verify-<mode>-<date>-<time>\`. The exit code is 0 for pass
(warnings allowed), 1 for fail (or a warning with `--strict`), 2 for a usage or configuration error, 3 for an
incomplete run. Real-game modes need FireTuner on (`EnableTuner = 1` in the game's `config.ini`; `c5h doctor` checks)
and leave `config.ini` as they found it.

Other options: `--spec FILE` (the trajectory and ensemble game), `--seeds MAP SYNC`, `--players N`,
`--city-states N`, `--ensemble N`, `--workers N`, `--dll`, `--db`, `--mod`, `--setups FILE` or `--no-setups`,
`--out DIR`, and `--analyse DIR` to rebuild the checks and report of a finished run.

## 10. Configuration file

Discovery covers the usual setup. Anything to pin or override goes in `civ5headless.toml`, in the kit or checkout
folder (or the folder you run `c5h` from, or the file named by `C5H_CONFIG`, or `c5h --config FILE`). Every key is
optional; relative paths are relative to the file. Use forward slashes or single quotes for Windows paths.

```toml
# play against a pinned copy of the mods instead of the live MODS folder
mods_dir = "D:/civ5/pinned-vp-5.4.8/MODS"
mods_db  = "D:/civ5/pinned-vp-5.4.8/Civ5ModsDatabase.db"

# a DLL under development
dll = "C:/dev/vp/build/CvGameCore_Expansion2.dll"

# run folders and caches somewhere with space
runs     = "E:/c5h-runs"
db_cache = "E:/c5h-cache/db"

[verify]
turns = 120
ensemble = 48
```

All keys: `workspace`, `game_dir`, `user_dir`, `mods_dir`, `cache_dir`, `mods_db`, `loc_db`, `config_ini`, `dll`,
`snapshot`, `db`, `db_cache`, `exe`, `profiles`, `probes`, `toolchain`, `runs`; `[verify]` holds the verification
suite's settings (verification.md). `civ5headless.example.toml` describes each one. `c5h doctor` shows the result.

## 11. Limits and resources

| Option | Effect | Default |
|---|---|---|
| `--maximum-seconds S` | stop the game after S seconds | 6 hours |
| `--stall-seconds S` | stop it when no turn completes for S seconds | 900 |
| `--max-memory-mb MB` | memory limit of the host process (0: none) | 3500 |
| `--affinity MASK` | CPU affinity of the host, e.g. `0x3` | none |
| `--low-priority` | run below normal priority | off for `run`, on in queues |
| `--cpu-limit PERCENT` | cap the host at this percent of one core (1-100) | none |
| `--efficiency-mode` | run in Windows efficiency mode (EcoQoS, idle priority) | off |
| `--checkpoint-every N` | checkpoint every N turns | 1 |

A game stopped by a limit keeps its run folder with status `stopped` or `failed` and the reason. A host crash writes
`HHCRASH` lines (exception, module and offset, registers, stack) to the host's output, `host-stdout.txt`.

### Running in the background

A game keeps one core busy, which keeps the fans going. Two options make a game quieter, together or apart:

```
c5h run examples\specs\continental-drift.json --turns 750 --cpu-limit 50
c5h run examples\specs\continental-drift.json --turns 750 --cpu-limit 50 --efficiency-mode
c5h queue run my-queue.json --workers 4 --cpu-limit 50
c5h queue run my-queue.json --workers 8 --total-cpu-limit 200
```

- `--cpu-limit 50` holds the host to half of one core: Windows stops scheduling it once it has used its share
  (a hard cap on the game's job object). It averages 50% of one core; single seconds reach about 60%, as Windows
  enforces the cap over its own short intervals. The run takes about twice as long. `--maximum-seconds` and
  `--stall-seconds` stretch by the same factor (100/PERCENT), so a capped game is not stopped for being slow.
- `--efficiency-mode` is Task Manager's efficiency mode: Windows runs the host at lower clock speeds, on the
  efficient cores of a hybrid CPU, and at idle priority. It sets no limit: an idle machine still gives the host most
  of a core, only a slower and cooler one, and anything else that wants the CPU goes first. A Tiny game took 1.6
  times as long in a test.
- Neither changes the game: the host reads the clock only for its timings, so a capped game plays the same turns as
  an uncapped one. Its timings (`secondsPerTurn`, the report's speed) are not the host's speed, though.
  `status.json` records `cpuLimit`, `totalCpuLimit` and `efficiencyMode`, and the report's CPU card shows them.
- In a queue, `--cpu-limit` is per game: 4 workers at `--cpu-limit 50` use about 2 cores in all. `--cpu-limit` and
  `--efficiency-mode` on `queue run` apply to every game of that session; `cpuLimit` and `efficiencyMode` set them
  per job in the queue file.
- `--total-cpu-limit PERCENT` on `queue run` (or `"totalCpuLimit"` at the top of the queue file) caps all the
  queue's running games together, however many workers there are. It is a percent of one core too, so above 100 is
  more than one core: 200 is two cores' worth, and the most is the whole machine (100 times its logical
  processors). The games share it about equally: 8 workers at `--total-cpu-limit 200` get about a quarter of a core
  each. The time limits stretch by that expected share. With both, a game gets the smaller of its own cap and its
  share.
- The one-time database build for a new mod set is not capped.

## 12. Development options

For DLL and host developers; regular runs do not need them.

| Option | Effect |
|---|---|
| `--frame-skew K` | an extra engine update before every K-th one: a game that changes is frame-rate dependent |
| `--heap-perturb SEED` | moves the DLL and the heap: a game that changes depends on memory addresses |
| `--host-arg ARG` | pass an option straight to `civ5headless.exe` (repeatable), e.g. `--host-arg=--no-game-logging` |
| `--exe`, `--game-dir`, `--loc-db` | another host build, game folder or localization database |
| `--diagnostics`, `--improved-mask`, `--ab-role` | A/B switches of the Vox Populi fork's stacking AI (DLLs with those switches) |

`c5h compare` with `--frame-skew` and `--heap-perturb` runs is how the verification suite checks a DLL's
reproducibility (`c5h verify --host-only` does it for you).

## 13. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `doctor`: no `Civ5CoreDatabase.db` or `Localization-Merged.db` | start the game once to its main menu |
| `doctor`: game folder not found | Steam library elsewhere: set `game_dir` in `civ5headless.toml` |
| a mod set fails to build | `c5h dbbuild` shows the step and the mod file that failed |
| Lua errors in `host-report.json` | a mod's UI add-in failed in the stand-in UI; `--skip-addin NAME` if it is UI-only |
| games differ from the real game | `c5h hazards scan --report RUN\host-report.json`; then `c5h verify` |
| the run stops with `stalled` | the DLL is stuck in a turn; `host-stdout.txt` and `Logs\` show where |
| Python not found (checkout) | install Python 3.11+, or set `C5H_PYTHON` to a `python.exe` |
