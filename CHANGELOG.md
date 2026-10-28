# Changelog

Every release of civ5-headless. Each GitHub release has the kit (`civ5-headless-<version>-<commit>.zip`) attached.

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
