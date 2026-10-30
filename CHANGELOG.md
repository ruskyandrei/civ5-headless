# Changelog

Every release of civ5-headless. Each GitHub release has the kit (`civ5-headless-<version>-<commit>.zip`) attached.

## Unreleased

- **Behaviour change: AI logging is now off by default, and has a switch.** `c5h run --ai-logging on|off`,
  `run.play(ai_logging=...)`, queue key `aiLogging`, and `ai_logging` in `civ5headless.toml` for games that do not choose
  ([docs/ai-logging.md](docs/ai-logging.md)).
  - **This changes which game a seed plays with Vox Populi.** A fixed seed no longer replays a game from an earlier version,
    or a real game with `AILog = 1`. `--ai-logging on` plays those games, as `c5h` did before. The stock DLL played the same
    game either way on the one seed tried.
  - `--detail full` keeps AI logs only with `--ai-logging on`, and warns when it has none to keep.
  - A logging option in `--host-arg` still wins, and `civ5headless.exe` run by itself, `c5h parity`, `host-first` and `skew`
    stay on.
  - `status.json` records what a game played with (`aiLogging`, `gameLogFlags`), the run report's Run row shows it, and
    `c5h doctor` shows the setting.
  - `c5h compare`, `c5h fingerprint compare` and `c5h queue compare` warn when the two games had different AI logging, such as
    a run from before this change against a new one. `--json` and the queue's `.compare.json` record both (`aiLogging`).
- **A save as a baseline** ([docs/saves.md](docs/saves.md#a-save-as-a-baseline)): `c5h run SAVE --turns T --seeds MAP SYNC`
  plays a save on with new rolls on the same map, so one save can start an ensemble or an A/B test. In a queue, `seeds` x
  `variants` over a `load` works as it does over a spec.
  - A queue job with `load` and `seeds` used to ignore the seeds silently, which made its games copies of one another.
  - Give both seeds: Vox Populi rolls from the map seed and the stock DLL from the sync seed, and the other one alone
    leaves the game as it was. A game with only one new seed gets a warning.
  - `status.json` and `setup.json` have `reseed`, `status.save` gets `-m<map>-s<sync>` so `abcompare.py` pairs the same
    seeds, and the run report and the queue's summary page show them.
  - In the host: `loadgame --reseed MAP:SYNC`.
- **`c5h verify`: new reference `trajectory.reseed`.** It checks that the same save and seeds repeat, and reports which
  seed the DLL's games respond to.
- **Stock DLL: an autoplayed game's final save now plays on.** The stock DLL ends the game on the last turn of autoplay,
  so that save holds an ended game, and the host stopped straight after loading it ("autoplay stopped at turn N"). It now
  plays on in extended play, with a load warning, as no victory can end such a game. A `--save-at` save has none of this.
- **A little faster, the same games:**
  - The host counts engine calls without a map lookup and a lock per call, and hashes each file once (`--known-sha256`
    from `c5h`, so the exe and the DLL not at all). A short Vox Populi game: CPU -4.1%, wall -5.4%.
  - `--sba` (queue key `smallBlockAllocator`), off by default: a real small-block allocator for the DLL, as the real exe
    has. A 150-turn Vox Populi game: CPU about -3%, wall about -3.5%; none on a short game. Identical games in the
    trials. It moves every small block, so use it with a DLL that passes `trajectory.heap-perturb`. `status.json` records
    it (`smallBlockAllocator`).
- **[docs/ai-logging.md](docs/ai-logging.md)**: what AI logging costs, how a game with it off differs from the same seed
  with it on, what the logs hold, and how to set it.
- **`c5h run`s started together on an empty database cache no longer fail.** They used to build the same snapshot or database
  over one another (`PermissionError` on `text-schema.db`). Now the first builds and the others wait for it, also for the
  mods registry. A process that dies while building lets the next one take over.
- **`c5h verify` with the real game: the host games log as `config.ini` says.** They used to play with all flags on whatever
  the file said, so a real game with `AILog = 0` was compared with a different host game on DLLs where `AILog` changes the
  game. `c5h verify --host-only` follows the new default.
- **Known issue, not new in this release: the official Vox Populi 5.4.8 DLL does not always repeat a game.** On the one seed
  tried, the same command played one of two games, which part at turn 118, on this release's host and on 0.3.2's, with AI
  logging on or off. How often depends on the length of the run folder's path, so `c5h verify --host-only` can fail on this
  DLL in one folder and pass in another ([docs/running.md](docs/running.md#32-seeds)). The cause is not known.

## 0.3.2 (2026-10-28)

- **Queue job seeds:** a job's `seeds` takes `[map, sync]` as well as `{"map": M, "sync": S}`, as the queue's own
  `seeds` list does. Anything else stops the queue before a game starts, naming the job; it used to fail that job's
  game with `TypeError: dict() argument after ** must be a mapping`.
- **Vassals in the run report:** the Players table lists each vassal indented under its master, tagged "vassal", with
  a note saying where that came from. `final-state.json` now records each major's `master` (the player leading its
  master team, -1 for none). For runs without that field, `c5h report` reads vassalage from the replay messages: became
  and no longer a vassal, restored as one, eliminated. It drops a vassal whose wars at the end differ from its master's,
  since the game ends some vassalages without a message.

## 0.3.1 (2026-10-28)

- **Queues survive a busy state file** ([PR 1](https://github.com/ruskyandrei/civ5-headless/pull/1)): saving
  `QUEUE.state.json` failed with `PermissionError: [WinError 5] Access is denied` when another process held the file
  for a moment (`c5h queue status` from another terminal, Defender scanning it), which ended `queue run` and left its
  games interrupted. The save now retries for up to 0.75 s before it fails. Writing a mod set's generated mods
  registry (`Civ5ModsDatabase.db`) retries the same way.
- **Vox Populi DLC modpacks** ([PR 2](https://github.com/ruskyandrei/civ5-headless/pull/2),
  [docs/running.md](docs/running.md#37-a-vox-populi-dlc-modpack)):
  - When one DLC package holds two Lua files with the same name, the host now plays the one found last, as the real
    game does. A modpack ships CP's and VP's copies of files such as `AssignStartingPlots.lua` in one package; the
    host took CP's, so Communitu_79a failed on the missing `RegionDivision` table.
  - Stock DLC and mods-folder games resolve every file as before: no stock package has two Lua files with one name.
  - running.md section 3.7: how to play a modpack (the database the real game built, the modpack's DLL, `--no-mods`).
  - In the host: the kit has a new `civ5headless.exe` build. The 0.3.0 host still takes the first file.

## 0.3.0 (2026-10-27)

- **Your own data from a game** ([docs/probes.md](docs/probes.md), a guide to writing probes and extras, with
  examples in `examples\probes\`):
  - `c5h run --checkpoint-extra FILE`: a Lua file run at every checkpoint. What it returns goes into the checkpoint
    (`data.extra.NAME`) and, after the game, into `extra\NAME.jsonl`, one JSON line per checkpoint.
  - `c5h run --final-extra FILE`: a Lua file run once after the last turn. What it returns goes into
    `extra\NAME-final.json`.
  - An extra gets a context (`turn`, `state` kept between calls, the run and extra folders). An error costs only that
    checkpoint's value; it is logged in the row, `Lua.log`, `events.jsonl` and `status.json` (`extras`).
  - `--lua-probe` files get `C5H_RUN_DIR` and `C5H_EXTRA_DIR` (from a generated `probe-settings.lua`), for files a
    probe writes itself.
  - Queue jobs take `checkpointExtras` and `finalExtras`; `run.play` takes `checkpoint_extras` and `final_extras`.
  - Works with the 0.2.0 host: no host rebuild.
- **Quieter background runs** ([docs/running.md](docs/running.md#running-in-the-background)):
  - `--cpu-limit PERCENT` caps the host at that percent of one core, a hard cap on its job object. The game is the
    same, only slower; `--maximum-seconds` and `--stall-seconds` stretch to match.
  - `--efficiency-mode` runs the host in Windows efficiency mode (EcoQoS: lower clocks, efficient cores; idle
    priority), as Task Manager's.
  - Queues: `cpuLimit` and `efficiencyMode` job keys, and `--cpu-limit` and `--efficiency-mode` on `queue run` for
    every game of the session.
  - `queue run --total-cpu-limit PERCENT` (or `"totalCpuLimit"` in the queue file) caps all of a queue's running games
    together, however many workers (200: two cores' worth); the games share it.
  - `status.json` records `cpuLimit`, `totalCpuLimit` and `efficiencyMode`; the report's CPU card shows them.

## 0.2.0 (2026-10-26)

- **Saved games** ([docs/saves.md](docs/saves.md)): the host reads and writes the real game's `.Civ5Save` files.
  - `c5h run SAVE.Civ5Save --turns T` (or `--load SAVE`) continues a save, the real game's or the host's, with the
    save's mod set.
  - Every run saves its last turn, `saves\Final_<turn and date>.Civ5Save` (`--no-save-final`: not). All saves stay in
    the run folder.
  - `--autosave none|game|N` (default none) and `--save-at T` write more saves, which the real game loads too.
    Autosaves are named and timed as the real game's (`AutoSave_Initial_`, `AutoSave_Post_`).
  - `c5h save FILE` shows a save's header: turn, civilization, map, mods, DLL.
  - Queue jobs take `load` (or a save as `spec`), `autosave`, `saveAt` and `saveFinal`.
  - The real-game session has `load_game` (from the Mods menu) and `save_game` (moved into the run folder).
  - The host's `UI.SaveGame(name)` saves into the run folder.
  - `Modding.OpenSaveData()` is the game's save database (TableSaverLoader works); `UI.IsLoadedGame()` is true in a
    loaded game.
  - Known limit: a save the real game wrote, loaded in both the host and the real game, can differ slightly after the
    first turn (1 gold in one test; saves.md section 6).

## 0.1.0 (2026-10-26)

First release.

- **`civ5headless.exe`** plays Civilization V: Brave New World AI autoplay games without the game. It uses any game
  DLL built from the Civ V SDK (the stock DLL, Community Patch, Vox Populi or your own) with your mods, map scripts
  and their options, premade maps and setup options.
  - Several games run at once.
  - The same spec, seeds, DLL, mods and database give the same game every time.
- **`c5h` tools:**
  - `doctor`: what was found (game, My Games, mods, DLL, host) and what is missing.
  - `run`: one game from a JSON spec, with progress every 10 turns.
    - `--detail summary|standard|full` sets what the run folder keeps.
    - Every run gets a `report.html`: setup, result, players, charts, maps, timeline, health.
  - `queue`: many games in parallel, resumable, with variants for A/B tests and a summary page.
  - `compare`, `fingerprint`, `dbdiff`: games and databases side by side.
  - `report`: rebuild run reports, and a summary page over several runs.
  - `dbcache`, `dbbuild`, `modsdb`: the game database and mods registry, built from the game's files and the mods
    without running the game.
  - `hazards`: UI code that changes the game state.
  - `verify`: the verification suite. The host against itself (A/A, frame skew, heap perturbation, probes), and
    against the real game: the same setups and a seeded game turn by turn, the database, and an ensemble for the
    statistics. Modes: `--host-only` (about 5 minutes), quick (about 10), `--full` (about 22).
- **UI emulation profiles** stand in for the game's UI where it changes the game state (popups, choices). Mod UI
  add-ins run in the host.
- **Example specs** for 8 setups (Continents, Continental Drift, Pangaea, Communitu with options, Earth), each with
  and without fixed seeds, and example queues.
- **The release kit** has the prebuilt exe, the tools with a bundled Python 3.12 (nothing to install), the profiles,
  probes, examples and docs. Running needs no compiler.
- **Checked against the real game** with `c5h verify` (Tiny maps, 150 turns of AI autoplay), with turn-by-turn
  identical games:

  | DLL and mods | Turns identical | Host speed |
  |---|---|---|
  | Vox Populi 5.4.8 (release); full mode also a Standard map, 60 turns | 150 of 150; 60 of 60 | 13x the real game |
  | The stock DLL, no mods | 150 of 150 | 22x |
  | A Vox Populi fork with the host's optional state digests | 148 of 148 (full state digests) | 7x |
