# Ledger: standalone repository, any DLL or mod, verification suite

Resume point for the work in [plan.md](plan.md). Newest state first in "Resume here"; the log below is append-only.
The host's own history (milestones M0–M6, parity work against the real game) is in [history/](history).

## Resume here

- **State (2026-10-26):**
  - The run-outputs work is merged into main: detail levels, the per-run report, summary pages, and the DLL choice
    for explicit mod sets.
  - The kit with bundled Python is remade and checked.
  - Phase E has started with a short real-game pass (the user asked to keep it short).
  - See the log entry "phase E, first real-game runs" for the outcome.
- **The real game's DLL is now `D4390E6C`** (2026-10-27; `E8E58D95` before it), the Civ5StackMod AI build `v0.3.3-1-g8fbd4fb9f`
  (VC9, no digest additions), so the real game is not plain VP.
  Another session deployed `E8E58D95` on 2026-10-26 at 16:20, with its mod files at 16:21, over plain VP 5.4.8 (`262AEE88`),
  which the user had asked for to play test games. Leave the installed files alone; the source repository's ledger
  says where the installs are archived.
- **Saved games (2026-10-26, release 0.2.0):** the host reads and writes `.Civ5Save` files. See the log entry
  "saved games" and [saves.md](saves.md).
- **Phase E is done (2026-10-26).** The full verify on plain VP 5.4.8 passed with warnings only. The thresholds are
  kept and the durations documented (docs/verification.md, "Calibration" and "Runs on record").
- **Released:** 0.2.0 (saved games), after 0.1.0, from the repository `ruskyandrei/civ5-headless` (private), each
  with its kit attached.
- **Open:**
  1. The PreGame API gaps. The host has no `GetQuickCombat`, `GetQuickMovement` or `GetOverrideScenarioHandicap`
     (the real game answers `false` for all three). The real game answers nothing for `GetClimate`, `GetSeaLevel`,
     `IsHuman`, `IsMinorCiv` or `GetMinorCivType` after the start.
  2. A GitHub Actions release workflow.
  3. A `--turns` default that plays to the speed's last turn.
  4. Longer or larger real games, to see whether the upstream determinism fixes matter there.
  5. A real-game save loaded in the host and in the real game drifts by 1 gold at the next turn (saves.md section
     6). Finding it needs a trace of the real game's load, or a field-map build of the installed DLL.
- **Waiting on the user, not on the game:** making the repository public; opening the upstream VP pull requests (branches
  `vp-det-*` in the source repository, texts in its `docs/headless-upstream-prs.md`).
- **Open, known limits:** UI popups reach add-in listeners only through profiles (generic routing not evaluated); the
  order of `<LuaContext>` children before the parent's script is assumed (engine-contract open question 11);
  `CalculateGoldRate` counts as fixed only for DLLs with the digest additions; Squads reads options from user mod data
  (empty in the host); the hazard scan does not follow `include()`; statistical power is about a 25% shift when the
  DLL ignores the sync seed (VP), so the ensemble varies the map; installed-package data for `probes/`.

## Log

### 2026-10-25: phase A

- History: the 139 commits of the source repository's `headless-host` branch that touch the host paths, exported with
  `git fast-export` (path-limited) and renamed with `git filter-repo` (`Civ5Headless/` to `host/`, `work/headless/`
  and the generic `tools/headless/` scripts to `python/civ5headless/`, `docs/headless-*.md` to `docs/`). Every file at
  the tip is byte-identical to the source branch at `df9d682d4`. (A direct `git filter-repo` of a clone failed on
  objects reachable only from old VP release tags; the path-limited export avoids them.)
- `sdk/`: 49 headers (exactly what `/showIncludes` reports for the host sources) and 3 import libraries, copied
  unchanged; provenance and licence in `sdk/README.md`. The licence is GPL v3 (`License.rtf` of the source repository
  is the Community Patch Project's GPL v3), so `LICENSE` is the GPL v3 text.
- Build: `tools/build_host.py` no longer borrows the DLL build; `tools/toolchain.py` finds VC9 through `--toolchain`,
  `C5H_TOOLCHAIN`, `toolchain/` or an installed Visual Studio 2008 (registry). `tools/gen_interfaces.py` reads
  `sdk/` and writes `host/gen/`; regenerating changes only the banner line.
- Replay check: the new exe replays the M5 final game (Standard Continents, seeds 1001/2002, field-map DLL CB6B0BED)
  for 120 turns with digests identical to the reference host run in every section except `game`, which holds the
  remaining-autoplay count (the reference ran 302 turns). The previous exe, built from the same host source, run for
  the same 120 turns gives identical digest lines; the logs differ only in the OS thread id of the `HHENV` lines.
- Known: `host/setup/HHNewGame.cpp(823)` warning C4129 (an escape in embedded Lua; harmless, predates the split).

### 2026-10-25: platform milestone 1 merged

- Python modules moved with `git mv` into the plan's layout (`spec`, `dbbuild`, `run`, `queue`, `compare/`,
  `realgame/`), package-relative imports, `config.py` (civ5headless.toml plus discovery of Steam library, My Games,
  mods, cache, active mods, host exe), `c5h` (`python -m civ5headless`) with doctor, run, queue, compare and the
  former scripts as subcommands. 50 unit tests (2 integration tests need `C5H_INTEGRATION=1`).
- Equivalence: a Tiny 30-turn game through `c5h run` and through the old `hhbench.py` gives identical digests,
  checkpoints, replay data and final dumps.
- Behaviour changes: queue paths resolve against the queue file; a difference only in `savedb` no longer counts in
  `compare` and `skew` (as in `parity`). Test packages must not use standard-library names (`tests/platform_tests`).

### 2026-10-25: platform milestone 2 and anydll merged

- Platform milestone 2 (fcda9f6): `realgame/` (FireTuner client and service, launcher with optional CPU guard, mod
  activation, config.ini seeds with restore, `RealGameSession`, probe/record/dbcapture on it), tested against a fake
  game only; `c5h snapshot` builds the DLC snapshot from the game's files (modded databases built from it equal those
  from a captured snapshot); full `doctor`; `tools/get_toolchain.py` (from the SDK 7.0 ISO, `--iso`: identical files,
  host builds and plays); `c5h.cmd`; user guide and database-builder docs on `c5h`.
- Anydll (973c324): every InGameUIAddin runs in a stub UI context (no allowlist); the five UI emulations are profiles
  (`profiles/`, docs/profiles.md); `host-report.json`; `probes/fingerprint.lua` with `--lua-probe` and
  `fingerprint.py`; UI hazard scan `hazards.py`; MAX_MAJOR_CIVS from the game; game folder from the Steam registry;
  crash logging; stock BNW DLL support (the host picks random civilizations, which cannot match the real game: specs
  for real-game comparisons must name them).
- Checks: this fork's results unchanged after every host step (anydll: 8 cases, 7 Tiny x 100 turns and Standard x 120;
  independently, the 120-turn M5 replay with anydll's exe has identical digests). Probe on/off identical with the
  digest DLL; dense against sparse fingerprints identical on the stock DLL (3 x 150 turns) and on plain VP (upstream
  master + determinism fixes, 3 x 100 turns). On main: 104 tests pass; `c5h snapshot --dll <stock DLL>` runs the
  post-processing and equals the captured snapshot.

### 2026-10-25: verify merged

- `c5h verify` (22d209f): modes quick, full and host-only; checks setup (database, pregame, turn 0, civ choice,
  health: Lua errors, stubs, UI hazards), trajectory (real against host, A/A, frame skew, heap perturbation, probe
  neutrality), statistics (ensemble envelope and bias sign test over replay datasets and fingerprint metrics, with
  mean, max and sum aggregates), performance (per-turn time, memory, CPU, throughput at 1/4/8 workers, history
  file); HTML and JSON report; exit codes 0 pass, 1 fail, 2 usage, 3 incomplete. docs/verification.md.
- Host-only on the field-map DLL and this fork's mods (the agent's run): 3.5 min; A/A, skew 7 and heap-perturb all
  150/150 turns; probes neutral; 4/4 setups reproduce; the one warning is UI hazards (58 game-reachable state-changing
  calls without a profile). Throughput 56 / 231 / 394 games per hour at 1 / 4 / 8 workers.
- Statistics self-test (32-game ensemble): 12 held-out host games pass the envelope (mean coverage 93.1%), bias quiet
  in 11/12; a 5% shift of one metric is not detectable (about 25% is); foreign setups are caught once world sums are
  aggregated. VP ignores the sync seed for these games, so ensembles vary the map seed.
- Runs only against fakes: the whole real side. Calibration (phase E) must set the envelope and bias thresholds,
  ensemble sizes and budgets per mode, the real/host time ratio of the planner, `heal_turns`, the default setups and
  civilization list, and pregame/UI ignore lists from real dumps.

### 2026-10-25: platform milestone 3 merged

- 26e4a4e: `c5h modsdb` builds the mods registry from `.modinfo` files (game schema; activation order respects
  dependencies). For this fork's 5 mods it equals the game's live registry in every row; the database built from it
  and two Tiny 30-turn games (digests, fingerprints, host report) are identical to those from the game's registry.
  `c5h run --mod/--no-mods`, `dbcache`, queue keys use it. Profiles and fingerprint wired into `run`, `queue` and
  `RealGameSession.start_game` (same files, same injection point as the host); `c5h fingerprint`, `c5h hazards`;
  config keys `profiles`, `probes`; spec `minorCivs`; `RealGameSession.replay_data()` (one player per tuner call:
  the tuner caps a result at 10,000 items and 256 KiB). 174 tests.
- Host-only `c5h verify` on main (before this merge): 3.6 min, verdict warn (UI hazards only), as in the agent's run.

### 2026-10-25: anydll milestone 2 merged

- 9671c70: the host honours spec `minorCivs` when it chooses civs itself (stock-like DLLs; VP chooses in the DLL and
  is left alone, with a match report), `host-report.json` `minorCivs` section, random city-states only from IDs the
  database has; `--skip-addin` accepts names with an extension; `hazards.scan_report` and a per-hit `reach`
  (load/game/input) with better call resolution, curated reviews and host/DLL rules: on the M5 reference run 0
  uncovered game-reachable UI hazards (was 60), plain VP 5, stock DLL 37 (base-game UI); `fingerprint.probe_path()`.
  Invariant (8 cases) identical. 181 tests.
- Open: `CalculateGoldRate` counts as fixed only for DLLs with the digest additions (no better marker yet); Squads
  reads its options from user mod data, which the host keeps empty; the hazard scan does not follow `include()`;
  installed-package data for `probes/` (only `data/*.json` ships).
- Independent check after the merge: the 120-turn M5 replay with main's exe (out 20261025-211224) has digests identical
  to the phase A run.

### 2026-10-25: verify follow-up merged; stop before the real game

- b19eb10: verify uses the merged interfaces (fingerprint through `run.play`/`start_game`, `replay_data()`,
  `hazards.scan_report` reach, `--mod`/`--no-mods`); compared host games get the real game's city-states (read from
  its turn-0 fingerprint) when the DLL leaves the choice to the exe; DLLs without digests get a sparse-fingerprint
  probe-off reference; turn counts capped at the horizon. 188 tests.
- Host-only runs (agent): this fork's DLL and mods pass in 4.0 min (0 uncovered game-reachable UI hazards; A/A, skew
  and perturbation 150/150; 0.43 s/turn, 450 MB; 53 / 201 / 328 games/h at 1 / 4 / 8 workers). Stock DLL with no
  mods: warn in 0.8 min, only the expected 37 UI hazards of the base-game and EUI UI; fingerprint A/A, skew and
  perturbation 150/150; the stock DLL draws on the sync seed, so its ensemble keeps the map (0.08 s/turn, 121 MB).

### 2026-10-26: release kit

- Running needs no VC9: `civ5headless.exe` imports only Windows DLLs, MSVCR90/MSVCP90 (the Visual C++ 2008 runtime,
  which Civ V ships in `VCRedist\vcredist_x86.exe` and installs; the game's own DLLs import it too) and the Firaxis
  DLLs it loads from the game folder. VC9 is only for building.
- Decision (user, 2026-10-26: "whatever makes most sense"): binaries ship as a kit attached to GitHub releases, not in
  the repository. `tools/make_kit.py` makes `dist/civ5-headless-<version>-<commit>.zip` (about 1.5 MB) from `git
  archive HEAD` plus a host built at that commit: `bin\` (exe, pdb, build-info.json), the `c5h` package, profiles,
  probes, examples, user docs, LICENSE, SOURCE.txt (GPL source pointer), the SDK notice. `kit.json` makes config treat
  the folder as a kit (workspace, exe, profiles, probes); `c5h.cmd` uses a bundled `runtime\python.exe`
  (`--python-embed`), else `C5H_PYTHON`, Python on PATH or the py launcher. The host links with `/PDBALTPATH`, so
  the exe carries no build-folder path.
- Checked: `--smoke` (doctor from an unpacked kit finds the kit's exe, profiles, probes); from an unpacked kit outside
  the repository, a 20-turn Tiny game with the active mods (snapshot and database built from the game's files in
  under 4 s, six profiles from the kit, fingerprint on) and a 30-turn stock-DLL game with no mods, both fine.
- Open: bundle the embeddable Python in release kits or not; a GitHub Actions release workflow once the repository
  is on GitHub.

### 2026-10-26: bundled Python, running guide

- Decision (user, 2026-10-26): kits bundle Python. `make_kit.py` adds python.org's embeddable Python 3.12.10
  (amd64), which is pinned by SHA-256 and has its Authenticode signers checked (PSF and Microsoft), in `runtime\`.
  `python312._pth` is patched to add `..\python`, because the embeddable Python ignores PYTHONPATH. The kit is about
  12.6 MB.
- Checked: the smoke test confirms that the bundled Python ran. With no system Python on PATH, from an unpacked kit
  outside the repository:
  - run, fingerprint compare, hazards and modsdb all work;
  - `c5h verify --host-only` passes in 3.5 min (150/150 turns for A/A, frame skew and heap perturbation).
- An exe built at an earlier commit is accepted when the host sources have not changed since that commit.
- `docs/running.md`: how to run, with every option and examples. It is in the kit and linked from the README and the
  kit's QUICKSTART. Its section 6 waits on the run-outputs work.
- Found while writing it: `--no-mods` and `--mod` still played the active mods' DLL. The fix is part of the
  run-outputs work on `c5h-platform`, paused (see "Resume here").

### 2026-10-26: run outputs merged; phase E, first real-game runs

- Run outputs (`c5h-platform`, resumed and finished here):
  - detail levels, a report per run, summary pages, `final-state.json`, and the host's `--no-dumps`/`--keep-log`;
  - the DLL choice for `--no-mods`/`--mod`;
  - the report test for the game's own conquest messages;
  - the user guide's timing claim corrected (the levels save disk, not time);
  - single-turn series listed in a note instead of charted.

  Merged into main with 200 tests passing. `docs/running.md` section 6 covers the report, the levels and the files.
  `c5h run` now prints its every-10-turns progress on the console; the user asked, because a run printed nothing
  between `launched` and the end.
- Kit `0.1.0-20e984722`, with bundled Python. The smoke test passes, and games run from an unpacked kit, with the
  report. `--no-mods` plays the game's own DLL ("no mods: the game's own DLL").
- Real game, this fork's field-map DLL `CB6B0BED` with the VP mods; the user asked to keep it short:
  - **First live run of `RealGameSession`.** Mod activation shows `ModsMenu`; `ModsSinglePlayer` exists all along,
    hidden, and the setup runs in it as it is. The session waited for `ModsSinglePlayer` to be shown and timed out;
    now it waits for either. The fake game models it.
  - The game's console output ("number - table" per tuner call) went into the caller's streams; it now goes to null.
  - Smoke: launch 31 s, mods 16 s, start 7 s, 5 turns 10.6 s. Seeds verified, fingerprint probe ran, logs and
    replay data collected, quit 2 s, `config.ini` restored.
  - The session now asks DLLs with the digest additions for per-turn digests (`HeadlessSeeds.txt` `digest=1`
    `normalise=1` before the start, removed once loaded). `game_probe` writes its own file and opts out.
  - **`verify --quick` #1: fail, but no game difference.** The trajectory matched for 148 of 148 turns by digest.
    - `health.stubs`: VP calls `sendMoveGreatWorks`, which also carries its own net messages (CvDllNetMessageExt),
      and `sendDoTask`. The host only stubbed them, dropping game actions. Now both are delivered back
      (`ResponseMoveGreatWorks`, `ResponseCityDoTask`), as the other net messages are. Replaying the ensemble game
      that called them: identical to turn 130, then different (the move is applied).
    - `setup.database`: the local `civ5headless.toml` pinned an older reference database. The builder's database
      from the current mod files is identical to the game's export (data only), so the `db` line is gone.
    - `setup.pregame`: all 651 common values equal in 5 games. Getters only one side answers now warn instead of
      failing (see "Resume here" item 4).
  - **`verify --quick` #2 (`runs/verify-quick-20261026-114314`): warn, exit 0, in 11.1 min.**
    - Turn 0: 4 of 4 setups identical.
    - Real trajectory: identical for 148 of 148 turns, by digest. A/A, frame skew, heap perturbation and probe off
      give 150 of 150.
    - Database identical; no Lua errors; no unknown stubs; the civ choice matches.
    - Envelope 97.2%; no bias flagged.
    - Host 0.36 s/turn against the real game's 2.52 (6.9x), peak working set 350 MB against 1759 MB.
    - One setup (Archipelago Small) was compared by fingerprint rather than digest: look at why.
- The real game was left with the field-map DLL and the VP mods enabled, for the user's own test games.
- **Stock DLL, no mods (`runs/verify-quick-20261026-120737`): warn, exit 0, 5.2 min.**
  - Config: an empty config (everything discovered), `--no-mods --dll <game>/Assets/DLC/Expansion2/CvGameCore_Expansion2.dll`.
  - The real game activated the empty mod set (0.5 s).
  - Turn 0: 4 of 4 setups identical. Real trajectory: 150 of 150 turns identical, by fingerprint (no digests in this
    DLL). A/A, frame skew and heap perturbation give 150 of 150; probe-neutral agrees at 7 sparse turns.
  - Database identical; no Lua errors; no unknown stubs; the 13 compared games were pinned to the real game's
    city-states.
  - Envelope 96.2%; no bias flagged.
  - Host 0.06 s/turn against the real game's 1.38 (22.3x), 115 MB against 1184 MB.
  - Warnings: the PreGame API getters, and the UI hazards: 37 reachable state-changing UI calls without a profile in
    the stock UI, as in the host-only runs.
  - The 1-turn setup games end in GAMESTATE_OVER with no winner, in both the real game and the DLL:
    `CvGame::update` ends the game once autoplay is off and the active player is dead, and the stock DLL's autoplay
    kills the human slot. The 150-turn game keeps autoplay on and plays through.
  - Afterwards the VP mod set was enabled and activated again (the same 5 mods; the session leaves the mods it set).

### 2026-10-26: plain VP 5.4.8 against the real game; example specs

- **Plain VP 5.4.8:** upstream `Release-5.4.8` (`7307aefdf`), its packaged DLL `262AEE88` and its mod and UI files.
  - Deployed from a worktree of the source repository at the tag (`work/wt/vp548`), with adapted copies of its
    staging and deploy scripts: packaged DLL, no PDB.
  - The field-map install was archived by the deploy. The 5 mods were enabled again (the deploy clears the list).
  - The user asked for it to stay installed for their test games.
- **Plain VP `verify --quick` #1: fail.** 24 of 32 ensemble games stopped with "no activated mods". With nothing
  pinned in the configuration, host games read the game's live `Civ5ModsDatabase.db`, which the real game rewrites
  while it launches and activates mods. Fix: the suite turns the active mods into an explicit mod set at the start,
  so host games use a registry generated from the `.modinfo` files.
- **#2 (`runs/verify-quick-20261026-123158`): fail, on 1 ensemble game only.** It hit "localization initialisation
  failed": the game rebuilds `Localization-Merged.db` when it activates mods. Fix: with the real game, the host games
  read a copy taken before it starts (`<run>/loc/`). Tested with the fake game; not rerun live, to keep it short.
- **Everything else passed in both plain-VP runs:**
  - Turn 0: 4 of 4 setups identical.
  - Real trajectory: 150 of 150 turns identical by fingerprint (the release DLL has no digests). A/A, frame skew and
    heap perturbation give 150 of 150.
  - Database identical; no Lua errors; no unknown stubs; the city-states match.
  - Envelope 95.3%; no bias flagged.
  - Host 0.18 s/turn against the real game's 2.29 (13x), 289 MB against 1559 MB.
  - Warnings: the PreGame API getters, and 3 reachable UI hazards.
  - So the determinism fixes in the upstream PR drafts are not needed for these 150 Tiny turns. Where they matter is
    still open: longer or larger games.
- **Example specs:**
  - Every `examples/specs/NAME.json` is now `NAME-fixed-seed.json`. A new `NAME.json` without seeds draws new ones
    each run. The test checks the twins match; the A/A integration tests use the fixed-seed ones.
  - Quick start (kit), README and `running.md`: a full Continental Drift game (`--turns 750`, Epic's last turn), the
    last turn of each speed, game lengths, replaying a seedless game from `spec-resolved.json`.
- **Kit `0.1.0-619656f23`:** smoke test passes. From the unpacked kit, the seedless spec drew new seeds on each run,
  playing plain VP's DLL.

### 2026-10-26: full verify, release 0.1.0

- **`verify --full` on plain VP 5.4.8 (`runs/verify-full-20261026-130319`): warn, exit 0, 22.5 min.**
  - Tiny: 150 of 150 turns identical to the real game. Standard size: 60 of 60. The real game repeats itself (150 of
    150).
  - Turn 0: 4 of 4 setups identical. Database identical; no unknown stubs; the city-states match.
  - 64 of 64 ensemble games played, so both live-cache fixes hold with the real game. 4 of 4 victory games.
  - Envelope 96.2%; no bias flagged (global p 0.72).
  - Host 0.18 s/turn against 2.37 (12.8x), 288 MB against 1570 MB.
  - Warnings: the PreGame API getters, and 3 reachable UI hazards.
- **Calibration:** the statistical thresholds, ensemble sizes and budgets were kept: every statistical check passed
  with a wide margin, and both modes took less than half their budget. The documented durations are now quick about
  10 min and full about 20-25 min.
- **Release:**
  - `CHANGELOG.md` is added, the README status updated, and the changelog is in the kit.
  - The repository `ruskyandrei/civ5-headless` was created private, as approved, and `main` pushed; the user asked
    for the push and the release on 2026-10-26.
  - Tag `v0.1.0`; the kit from the tagged commit is attached to the release.


### 2026-10-26: saved games

- **What it does:** the host reads and writes the real game's `.Civ5Save` files ([saves.md](saves.md)).
  - `c5h run SAVE.Civ5Save --turns T` (or `--load SAVE`) continues a save with the save's mod set.
  - Every run saves its last turn into `<run>\saves\` (`--no-save-final`: not).
  - Autosaves are off by default: `--autosave none|game|N`. `--save-at T` saves at turn T.
  - Every save stays in the run folder.
  - `c5h save FILE` reads a header. The host's `UI.SaveGame` saves too.
  - The real-game session has `load_game` and `save_game`; the latter moves the game's file into the run.
- **Checks:**
  - Saving does not change the game (digests, 12 of 12 turns).
  - A host save loads in the real game and plays on.
  - The same host save, loaded in both and played 3 turns: fingerprints identical on 4 of 4 turns. The end saves
    differ in 67 bytes of 13 MB.
  - A real save taken after the real game's autoplay ended: 1 gold apart at the next turn (open item 5).
  - Long game: a Tiny game played in the host to turn 330 (`runs/tiny-330`, 9.4 min). Its final save loaded in the
    real game at turn 330, with player 0 human (`runs/tiny-330-real`). The game was left open for the user to look at.
  - `verify --host-only` with the installed DLL (`runs/verify-host-only-20261026-213525`): warn, 3.3 min, the 3 known
    reachable UI hazards only; A/A, frame skew and heap perturbation 150 of 150 turns.
- **Release 0.2.0:** the user asked for the push and a new kit and release with saves (2026-10-26).
- **Found on the way:**
  - The real game writes only mods with `AffectsSavedGames` into a save. A save that lists other mods makes it
    re-activate mods during the load. With VP's front end that rebuild fails ("database table is locked") and the game
    crashes, so the host writes the same list.
  - Loading from the vanilla main menu crashes VP's DLL in `CvGame::Read`; the game's own path is the Mods menu.
  - VP bug: `CvPlayer::Read` → `reset()` fills the espionage AI's per-civ lists (22 entries), and `operator>>` then
    appends 22 more. Saved values are lost, and the lists grow on every load, in the real game too.
  - The real game with DLL `E8E58D95` crashed once on `ExitToMainMenu` (in the CvGameDatabase DLL).
  - The host's first-chance exception at shutdown (`HHCRASH` at `CvGameCore+0x15D6E`, phase shutdown) predates the
    save code. It happens with VP builds `F7CB6626` and `E8E58D95`.
  - Test saves the real-game sessions wrote into the game's `ModdedSaves\single` were moved into their run folders
    (`runs/rt5-real`, `rt7-real-a`, `rt7-real-b`, `rt8-real`, under `game-folder\`). The real game's turn-0
    autosaves from `rt5-real` had overwritten earlier verify autosaves of the same names.

### 2026-10-27: quieter background runs

- **What it does:** `--cpu-limit PERCENT` caps the host at that percent of one core, and `--efficiency-mode` runs it in
  Windows efficiency mode ([running.md](running.md#running-in-the-background)). Both work for `c5h run` and queues
  (`cpuLimit`, `efficiencyMode`, or `queue run --cpu-limit/--efficiency-mode` for every game).
  - The cap is a hard CPU-rate cap on the game's job object (`JobObjectCpuRateControlInformation`). Its rate is a
    share of the whole machine, so 50% of one core is 156/10,000 on 32 logical processors. Pinning the host to one
    core does not change that.
  - Efficiency mode is EcoQoS (`ProcessPowerThrottling`, execution speed) plus idle priority, as Task Manager's.
  - The time limits stretch by 100/PERCENT. `status.json` records `cpuLimit` and `efficiencyMode`; the report's CPU
    card shows them.
- **Checks** (Tiny, fixed seed, 120 turns, the Stacking DLL `CB6B0BED`):
  - Uncapped: 31.2 s, 0.98 cores.
  - `--cpu-limit 50`: 60.1 s, 0.50 cores on average. One-second samples: median 0.50, maximum 0.62.
  - `--efficiency-mode`: 50.0 s, 0.92 cores at lower clocks.
  - Both games identical to the uncapped one on 120 of 120 digest points.
  - A 2-game queue with `--cpu-limit 50 --efficiency-mode`, pinned p-first: 0.50 and 0.51 cores, both in efficiency
    mode.
- **Queue-wide cap:** `queue run --total-cpu-limit PERCENT` (or `"totalCpuLimit"`) caps all of a queue's running
  games together. The queue holds one capped job, and each game's job nests in it (the host is assigned to the
  queue's job first).
  - A nested job's CpuRate counts against its parent's cap, not the machine. The first try, with machine-relative
    rates, gave 4 busy loops capped at 50% each under a 1-core total only 0.09 cores together. `winproc.cpu_rate`
    now takes the parent's cap (`within`). Busy loops then: total 100 with per-game 50 gave 0.25 each (1.06 in
    all); total 300 with per-game 50 gave 0.52 each; total 300 with per-game 25 gave 0.26 each.
  - The time limits stretch by the expected share, total / min(workers, games).
  - A real queue: 4 workers, Tiny, 60 turns, `--total-cpu-limit 100`, game d also at `cpuLimit` 10. a, b and c got
    0.30 cores each and d 0.10, 1.0 in all. One-second samples of the 4 together: median 1.03, maximum 1.28.
  - All 4 games are identical to an uncapped 60-turn `c5h run` of the same spec (60 of 60 digest points), as are the
    earlier queue games. Games of different lengths differ from turn 0 in the `game` section (the
    remaining-autoplay count), so the checks compare games of the same length.
- **Found on the way:** this machine's game cache folder (`My Games\...\cache`) is missing, so a new database cannot
  be built until the game is started once; the test runs used a cached build (`--db`).
- **Merged** into `main` with the run outputs work (`--checkpoint-extra`, `--final-extra`): the conflicts were the lists
  both extended (changelog, queue job keys, guides, option checks). 220 tests pass; a 30-turn game with
  `--cpu-limit 50 --final-extra examples/probes/game-summary.lua` held to 0.49 cores and wrote its extra.

### 2026-10-27: release 0.3.0

- **What it holds:** your own data from a game (Lua extras, `docs/probes.md`) and quieter background runs
  (`--cpu-limit`, `--efficiency-mode`, `queue run --total-cpu-limit`). The host is unchanged since 0.2.0: the kit
  carries the 0.2.0 build (`out/Release/20261026-213903`).
- **`verify --host-only`** (`runs/verify-host-only-20261027-123612`, the configured Stacking DLL `CB6B0BED`): pass,
  3.7 min, no warnings. 150 of 150 turns for A/A, frame skew and heap perturbation.
  - It ran with `snapshot` set to the cached `cache/db/snapshot/A214C431ADB9A501`, as the game's cache folder was
    missing. The game was then started to its main menu (35 s) and quit cleanly, with the user's go-ahead, which
    rebuilt the cache folder; `c5h doctor` finds it again.
- **Release:** the user asked for the push, the worktree's removal and a new kit and release (2026-10-27).

### 2026-10-28: release 0.3.1

- **What it holds:** two fixes from outside pull requests, reviewed and merged with the user's go-ahead:
  - [PR 1](https://github.com/ruskyandrei/civ5-headless/pull/1): `winproc.replace` retries `os.replace` on
    `PermissionError` (5 tries, 0.75 s), used by the queue state save and `modsdb.write`, so a reader or Defender
    holding `QUEUE.state.json` for a moment no longer ends `queue run`.
  - [PR 2](https://github.com/ruskyandrei/civ5-headless/pull/2): `HHVfs` gives each DLC package a group; inside one
    package the file scanned last wins (VP DLC modpacks ship CP's and VP's copies in one package). Between packages,
    and for base and mods, the order is unchanged. The in-package rule is inferred from the modpack's behaviour in the
    real game, not probed; no modpack is installed here.
- **Checked:** no stock package below `Assets\DLC` has two Lua files with one name (13 active packages). Hosts built
  from `main` before and after PR 2 write identical `vfs-index.log` files and resolve every include the same way
  (the configured 5 mods, and `--no-mods`); a 2-turn VP game is identical but for thread ids.
- **Host:** a new build, `out/Release/20261028-094452` (`89C95256`), from the merged `main`, with the VC9 toolchain
  in `Civ5StackMod\work\toolchain\sdk` (`C5H_TOOLCHAIN`). No compiler warnings.
- **`verify --host-only`** (`runs/verify-host-only-20261028-121300`, the configured Stacking DLL `CB6B0BED`): pass,
  3.4 min, no warnings. 150 of 150 turns for A/A, frame skew and heap perturbation.
- **The game's cache folder** is back (rebuilt for 0.3.0), so databases build again without `--db`.
- **Release:** the user asked for the review, the merges, the changelog and the 0.3.1 release (2026-10-28).

### 2026-10-28: release 0.3.2

- **What it holds:**
  - Vassals in the run report: the Players table lists each vassal indented under its master, from the new `master`
    field in `final-state.json`. It falls back to the replay messages and the final wars for older runs.
  - Queue job seeds as `[map, sync]`.
  - The host is unchanged since 0.3.1: the kit carries `out/Release/20261028-094452` (`89C95256`).
- **Vassal checks:**
  - On 276 finished games of the user's Vox Populi and StackMod batches, the replay-message reading matches the stats
    project's reference parser, apart from the war-check drops.
  - Against the 36 games whose probe logged vassalage live, it gives the exact end state in 34.
  - Loading continents-g0087's final save with the StackMod 0.3.5 DLL: the new field matched a probe reading the
    loaded save, for all 8 civilizations.
- **`verify --host-only`** (`runs/verify-host-only-20261028-163322`, the configured Stacking DLL `CB6B0BED`): pass,
  3.4 min, no warnings. 150 of 150 turns for A/A, frame skew and heap perturbation. Its games write `master` -1
  for every civilization.
- **Release:** the user asked for the merge and a patch release (2026-10-28).

### 2026-10-30: saves as baselines, small speed-ups, AI logging page

- **What it holds** ([plan-speed-and-baselines.md](plan-speed-and-baselines.md)):
  - `c5h run SAVE --seeds MAP SYNC` and queue jobs with `load` and `seeds`; the host's `loadgame --reseed MAP:SYNC`; the
    reference `trajectory.reseed` in `c5h verify`; seeds in `status.json`, `setup.json`, `status.save` and the reports.
  - Engine call counters per call site, one hash per file (`--known-sha256` from `c5h`), `--sba` (off by default).
  - [ai-logging.md](ai-logging.md).
- **Seeds, both DLLs, from a save of turn 30 to turn 45 (fingerprints):** same seeds identical on 16 of 16 turns. Vox Populi
  (AFL, `Release-5.4.8-9-g6b97a2de`) responds to the map seed only, the stock DLL to the sync seed only.
- **Found on the way:**
  - The stock DLL ends an autoplayed game on its last turn (`CvGame::update`: active player dead, not an observer, autoplay
    over), and `Read` makes `GAMESTATE_OVER` extended play. So its final saves stopped right after a load. Autoplay now stops
    when the state changes, and such a load warns.
  - `--save-at T` writes nothing when T is the last turn.
  - Loading calls the stub `getAssignedNetworkID`, unknown to `health.stubs` because `c5h verify` had never loaded a game. The
    DLL only stores the value in the pregame: on the harmless list.
  - `CvPlayer::GetBestSettlePlot` and `CvTacticalAI::PlotPillageMoves` are still logging-dependent in VP 5.4.8.
  - `c5h run`s started together on a cold database cache collided (`PermissionError` on
    `snapshot.db.build\text-schema.db`). Queues build once before their workers start and were not affected. Fixed below.
- **Gate, the 0.3.2 host (`89C95256`) against the new build (plan-batch-games.md section 8):**
  - Fingerprints identical: tactical scenario with its probes and extras (16 of 16), VP Tiny (81 of 81), stock Tiny (61 of 61).
    Same file lists. Engine call and export counts identical. `newgame-report.json` and `host-report.json` differ only in
    timings, save sizes (which also vary between two runs of one host), the host's own identity, and the new fields.
  - `loadgame`: VP from a final save 16 of 16, stock from a `--save-at` save 16 of 16.
  - Missing DLL, bad spec, Lua error, `--maximum-seconds`, stall: same exit codes and messages.
  - A small queue: same state and summary fields.
  - Unit tests: 233 run, 8 skipped, none failed.
- **Speed:** short game (Duel, 15 turns, one P-core, 9 runs of each): CPU -4.1% (2.297 to 2.203 s), wall -5.4% (2.606 to
  2.466 s). `--sba` on Tiny, 150 turns: CPU about -3%, wall about -3.5%, identical 151 of 151 turns. The trial's -7% CPU did not
  repeat. `--sba` is also identical on 100-turn games, VP and stock, 101 of 101 turns.
- **`verify --host-only` on the final build (`E921DD05`, verdict warn, exit 0):** AFL with 5 mods, and `--no-mods` with the stock
  DLL: 150 of 150 turns for A/A, frame skew and heap perturbation, `trajectory.reseed` passes. The one warning is
  `health.ui-hazards`, from the UI file lists, which the gate shows the same on both hosts. With `--host-arg=--sba` on the build
  before the stub rule: the same trajectory results, and `health.stubs` failing on that stub.
- **AI logging measured:** Tiny, 150 turns: VP CPU 24.3 to 17.0 s (-30%), stock 9.65 to 8.75 s (-9%). Stock with flags 15, 14, 1
  and 0: the same game, 151 of 151 turns, on one seed. VP: flags 15 and 1 the same game, 14 and 0 the same game, and the two
  pairs differ from turn 73 on that seed.
- **AI logging switch (`--ai-logging`, `aiLogging`, `ai_logging`), and off as `c5h`'s default.** The host's own default stays
  on, so no host change. On the host `E921DD05` with VP on Tiny, 6 turns each: on, off, the config file's off, a run's on over
  the file's off, `--host-arg` flags over the file, and `--no-game-logging` over a run's on. In all six `status.json` agreed
  with `host-report.json` and the report's Run row (`--no-game-logging` is `gameLogFlags` 0 in the status and `gameLogging`
  false in the host's report). `c5h run` and a queue with one job on and one off also recorded the right setting.
  - **The new default, Tiny, 150 turns, by fingerprints:** VP, a game that does not choose against `--ai-logging off`: identical,
    151 of 151 turns; against `on`: first difference at turn 73, as measured earlier on this seed. Stock: all three identical,
    151 of 151 turns.
  - **`verify --host-only` on the new default (verdict warn, exit 0):** AFL with 5 mods, and `--no-mods` with the stock DLL: 150
    of 150 turns for A/A, frame skew and heap perturbation. All 28 host games of each ran with `gameLogFlags` 0. The one
    warning is `health.ui-hazards`, as before.
  - `c5h verify` with the real game now reads the logging from `config.ini` (this machine's gives `--game-log-flags 15`, as
    before); run below.
  - The comparison tools warn about a mixed pair. Real runs: `fingerprint compare` of VP on against off warned, and off against
    off did not. `c5h compare` and `queue compare` tested with fake digests and the fake host only, as this AFL DLL writes no
    digests.
- **Parallel runs on an empty database cache (`winproc.file_lock`, `build.lock` in each cache entry):**
  - Eight `c5h run` processes at once on an empty cache, 4 VP and 4 stock, 30 turns. With the lock patched out, as the code was:
    7 of 8 failed (`PermissionError` WinError 32, one `disk I/O error`). With it, on two fresh caches: 8 of 8, one snapshot and
    two databases built, the games of each DLL identical to one another, 31 of 31 turns.
  - Recovery: a lost `built.db`, a partial one, and builds killed with their children at 0.3, 0.8 and 1.5 s were built again, with
    the same bytes. A snapshot build killed at 0.3 and 0.8 s was built again. Not tried: killing only the Python process, which
    leaves the host child running.
  - The cached database against the explicit one, VP, 150 turns: identical, 151 of 151.
- **Seen on the way, not changed:** with VP, the last turn of a game differs in the fingerprint from the same turn of a longer game
  with the same seed (30 against 40 turns: only turn 30 differs, `p00`, `players`). The released host (`89C95256`) does the same,
  and stock does not. So compare games of the same length up to the last turn.
- **`c5h verify --quick` with the real game (`runs/verify-quick-20261030-150414`; host `E921DD05`, the installed DLL
  `D4390E6C`, which is Civ5StackMod 0.3.3-1 built with VC9, not plain Vox Populi; its 5 mods, nothing pinned): warn, exit 0,
  11.1 min.**
  - 150 of 150 turns identical to the real game (fingerprints: this DLL writes no digests). A/A, frame skew and heap
    perturbation 150 of 150. Database identical. `trajectory.reseed` passes: this DLL's games respond to the map seed only,
    and the same seeds repeat (11 of 11 turns). Envelope 97.7%; no bias flagged (global p 0.15).
  - All 52 host games played with `gameLogFlags` 15, as `config.ini` says. Only `AILog = 1` was tried: a real game with
    `AILog = 0` has still not been compared.
  - Warnings: the PreGame getters only one side answers, and 3 reachable UI hazards, as before; and `performance.history`, 0.353
    s/turn against 0.183 (+93%).
  - That last one is the DLL, not the host. The 0.183 is plain VP's release, `262AEE88`. Alone on one P-core with AI logging on,
    the same DLL `D4390E6C`, two runs each: released host (`89C95256`) CPU 53.7 s, wall 58.4 s; new host CPU 51.6 s (-4%), wall
    55.6 s (-5%); the same game, 151 of 151 turns, and the same as the verify run's.
  - Afterwards `config.ini` had its seeds back at 0, with no marker left and no game or host process running.
- **Why that DLL takes twice the CPU of AFL.** AFL is VP master with two crash fixes, a clang build from the Civ5VP conquest
  experiment (`work/test-runs/conquest/LEDGER.txt`). Tiny, 150 turns, alone on one P-core, AI logging on, CPU:

  | DLL | Compiler | CPU |
  |---|---|---:|
  | Plain VP 5.4.8, the official release (`262AEE88`) | clang | 25.2 s |
  | Plain VP 5.4.8, built here (`9E7D9825`) | clang | 25.4 s |
  | AFL (`E3C3AF91`), one run, detail `standard` | clang | 25.4 s |
  | Plain VP 5.4.8, built here (`AABE1AE5`) | VC9 | 35.2 s |
  | StackMod 0.3.3-1, installed (`D4390E6C`), detail `standard` | VC9 | 51.6 s |

  - So the gap is the compiler (1.39x) times StackMod's own AI work (1.47x), which agrees with StackMod's
    `docs/headless-turn-time-gap-20261027.md`.
  - The compiler changes the game: plain VP's VC9 and clang builds differ from turn 37 on this seed. The clang build and the
    official release are identical, 151 of 151 turns.
  - In use and not clang: the installed DLL, and `dll` in this checkout's local `civ5headless.toml`, the VC9 digest build
    `CB6B0BED` (digest builds exist only for VC9). Everything else in use is clang already. StackMod has no clang build of
    0.3.3-1: its clang recipe came with 0.3.5.
- **Release dry run on `e245c94`, nothing published:**
  - Host `344BF2F3` built from clean sources. 250 unit tests, 8 skipped, OK. `make_kit --smoke`: `c5h doctor` exit 0 in the
    unpacked kit, 12 MB.
  - `verify --host-only`, Tiny, 150 turns, all 28 games at flags 0: AFL (`E3C3AF91`, 1.0 min) and the stock DLL (`C5483B58`, 0.6
    min): warn, exit 0, 150 of 150 for A/A, frame skew and heap perturbation, `trajectory.reseed` passes, the one warning
    `health.ui-hazards`. AFL needs the mods of its own checkout: with the 5.4.8 mods it stops on `no such table:
    UnitPromotions_TerrainHeal`.
- **The official VP 5.4.8 DLL (`262AEE88`) does not always repeat. Not a regression; the cause is not found.**
  - `verify --host-only` on it failed in 3 runs of 7 and gave warn in 4. In each failure the job `trajectory` played another game
    than `trajectory-aa`, `-skew7` and `-perturb4242`, which agreed, so A/A stops at 118 of 150 and `trajectory.probe-neutral`
    fails (it compares with the same game).
  - Only two games ever appear, in every case below: the other one differs from turn 118 on, 33 of 151 turns. At turn 118 the
    barbarians have 15 units against 9, and player 1 has one unit fewer, with less culture, faith and gold.
  - The verify commands differ only in the run folder's path. Replaying the command, Tiny, 150 turns, the same seed
    (30017, 40017), logging off unless named:

    | Case | Host's path length | Other game |
    |---|---:|---:|
    | verify's `trajectory` job | 181 | 3 of 7 |
    | the stamps of failing and passing runs, three times each | 181 | 2 of 15, and a stamp gives either game |
    | random stamps | 181 | 1 of 16 |
    | another job name of the same length | 181 | 0 of 2 |
    | as verify ran it | 181 | 5 of 32 |
    | without `pregame.lua` | 181 | 2 of 32 |
    | without `probe-settings.lua` | 181 | 5 of 32 |
    | without either | 181 | 5 of 32 |
    | AI logging on | 181 | 5 of 32 |
    | the released host (`89C95256`), random stamps | 181 | 4 of 16 |
    | **all of the above** | 181 | **32 of 216** |
    | other job names | 178 to 185 | 1 of 14 |
    | verify's `-aa`, `-skew7`, `-perturb4242` | longer | 0 of 21 |
    | `c5h run`, both hosts, logging on and off | shorter | 0 of 64 |
    | **all at other lengths** | | **1 of 99** |
    | AFL, as verify ran it | 181 | 0 of 32 |
    | AFL, `c5h run`, logging on and off | shorter | 0 of 32 |

  - So it differs from process to process with one command, and neither the probes, nor AI logging, nor this branch's host causes
    it. Which fits a read of uninitialised or address-dependent memory, as a path's length moves heap blocks, but that is not
    shown, nor is the host ruled out. AFL's 0 of 64 is one seed, so it does not show that a later commit fixed it.
  - Earlier passes on this DLL (2026-10-26) ran in run folders of other lengths.
- **Left for a release:** merge to `main`, version, changelog heading, README status, the kit from the release commit, tag, push,
  GitHub release. `c5h` from this checkout already defaults to logging off: the Civ5VP conquest experiment runs from it
  (`work/headless/c5h.py`), and its logged baselines need `ai_logging = "on"` in `work/headless/civ5headless.toml` to stay
  comparable.

### 2026-10-30: release 0.4.0

- **What it holds:** AI logging off by default with a switch, and a warning when a comparison mixes the two settings; a save as a
  baseline with new seeds; a little more host speed; games started together on an empty database cache no longer fail. The
  kit carries `out/Release/20261030-154805` (`344BF2F3`), built at `e245c94`; only docs and the version changed since.
- **Why a minor version:** a fixed seed now plays another game with Vox Populi unless `--ai-logging on` asks for the old one.
- **`verify --host-only`:** the dry run above. AFL and the stock DLL: warn, exit 0, 150 of 150 turns. The official VP 5.4.8 DLL
  fails in 3 runs of 7, which is the known issue above and not new.
- **From a kit of the merge commit `cb0bdf4`, unpacked, with its own Python, an empty database cache and 5.4.8's mods:**
  - `c5h doctor` exit 0, showing AI logging off as the default.
  - A queue of 4 games, logging off and on: all completed on the empty cache, at flags 0 and 15.
  - A default game, flags 0, with `--save-at`; its save played on with `--seeds 111 222`, twice: `status.reseed` recorded, the same
    game both times.
  - `--ai-logging on` against the default game: flags 15, and `c5h fingerprint compare` warns.
  - The stock DLL with `--no-mods`: completed, flags 0, with its report.
- **Not run for this release:** the real game. The last real-game verify is the one above, on StackMod 0.3.3-1, and another
  session was replacing that install while this one released.
- **Release:** the user asked for 0.4.0 after the dry run (2026-10-30).
