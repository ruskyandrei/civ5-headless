# Batch games: several games per host process, and other optional speed-ups (plan, 2026-10-29)

Status: proposed, nothing implemented. To be done later. Written from the tactical AI test suite
(`E:\Projects\civ5_tactical_test_suite`), whose games last 10 to 15 turns and spend most of their time starting up.

Every change here is **opt-in**. With no new command, option or queue key, the host and `c5h` behave exactly as they
do today (release 0.3.2), and section 8 is the gate that proves it before anything is merged.

## 1. Goal

Play many short games faster at the same CPU budget, without changing what a game is:

- a batched game gives the same result as the same game played alone, turn for turn (state digests or the Lua
  fingerprint), on every DLL the host supports, or the batch falls back to one game per process for that DLL;
- its run folder has the same files a single game's has, so every tool that reads run folders keeps working;
- a crash, stall or leak loses at most the game in progress;
- single games (`newgame`, `loadgame`), `builddb`, `c5h run`, `c5h queue` with default settings and `c5h verify` are
  unchanged.

Not goals: several games at once in one process (the DLL's globals allow one game at a time), batches of loaded
games (`loadgame`) in the first version, or any change to how the real game is driven.

## 2. Where the time goes

One tactical-suite game (VP DLL `Release-5.4.8-9-g6b97a2de`, Duel map rewritten by a Lua setup probe, 4 majors,
15 turns, detail `standard`), phases from `Logs\Headless.log`, seconds of one core:

| Phase | Seconds | Today | Batch, keep the DLL (tier B) | Batch, reload the DLL (tier A) |
|---|---|---|---|---|
| Process start, Firaxis DLLs, VFS | 0.16 | per game | once per process | once per process |
| Localization (`Localization::Initialize`; the 53 MB localization database is copied per run) | 1.3 | per game | once | once |
| Database copy, `Init`, post-processing, `CacheGameDatabaseData` | 2.9 | per game | once | per game |
| Pregame and game start (map, `Game.Init`, `Init2`, started steps) | 0.5 | per game | per game | per game |
| UI emulation, add-ins | 0.3 | per game | per game | per game |
| Lua probes (the scenario setup) | 0.3 | per game | per game | per game |
| Autoplay, 15 turns | 2.3 to 2.8 | per game | per game | per game |
| Other (Lua boot, reports, shutdown) | about 0.4 | per game | mostly once | mostly once |
| Outside the host (`run.py`: process, job object, hashes, files) | about 0.8 | per game | mostly once | mostly once |
| **Per game** | **about 9.2** | | **about 4.0 (2.3x)** | **about 7.0 (1.3x)** |

Measured suite times at 16 games at once, 60% of a core each, efficiency mode, E-cores: quick mode, 3 DLLs, 432 games
in 4.3 minutes; full mode, 5 DLLs, 2,160 games in 21 minutes. With tier B, roughly 2 and 9 minutes.

Long games gain little: a 150-turn Tiny game (0.43 s a turn with VP) spends about 6 seconds of about 70 starting up.

## 3. Ground rules

1. **Off by default.** Batch play is a new host command (`batch`) and a new queue key (`gamesPerProcess`, default 1).
   Nothing else changes behaviour unless asked.
2. **The single-game path is only refactored, never changed** (phase 1), and only merged when the regression gate
   (section 8) shows identical games, reports and file sets before and after. Where a refactor cannot be shown to be
   neutral, the batch path duplicates the code instead.
3. **Same game.** A batched game must equal the same request played alone. The host and the queue check this on
   their own (the canary, section 6.3); a DLL that fails falls back to fresh processes, with a warning.
4. **Same outputs.** Each batched game writes a normal run folder, plus a `batch` record (process, index in the
   process, boot run folder) in `host-report.json`, `hh-command.json` and `status.json`.
5. **Failure isolation.** The queue kills a batch process that crashes, stalls or outgrows its memory, marks the
   game in progress failed, and plays it again in a fresh single-game process.
6. **Compatibility.** The Python side asks the host whether it knows `batch` (`host_supports`, as for `--no-dumps`)
   and falls back to one game per process with older hosts; the host and Python exchange a protocol version first.
7. **Recorded.** Reports say which speed-up a game used, so comparisons and `c5h verify` can tell.

## 4. Phase 0: measure and decide (no host code)

1. **The real exe's second game.** Record what the real game does between two games in one session (autoplay game 1,
   exit to the main menu, start game 2 with the same mods), with the call recording used for
   [engine-contract.md](engine-contract.md). Questions:
   - Which calls end game 1 and start game 2: `setGameStarted(false)`, `resetPlayers`, `NetMessageStaticsReset`,
     `DestroyDeal`, `CvDllGame::Uninit`, `CvDllGameContext::Uninit` and `Init` again?
   - Does the exe reload the game DLL or the database when the mod set is unchanged?
   - Does it build a new InGame Lua state, or reuse the main state's?
   The DLL is written for this sequence, so batch mode's game boundary should copy it (section 5.1) instead of
   inventing one. If the exe reloads the DLL between games, tier B is a host-only shortcut, and the canary
   (section 6.3) is the only evidence it is sound.
2. **DLL state that outlives a game.** Audit the SDK sources and VP's (`CvGameCoreDLL_Expansion2`) for state that
   `CvGame::uninit` and `init` do not reset:
   - mutable globals and function-local `static` caches, above all caches keyed by turn or by ID, which a later game
     at the same turn would find "valid";
   - members of `CvGlobals` (`GC`) filled at `Init` and changed during play;
   - engine pointers the DLL keeps: `FILogFile*` from `GetLog`, the cache folder path, the script system, any
     `lua_State*` or Lua registry reference;
   - thread-local storage.
   Output: a table appended to this document (state, reset by, risk), and the list of DLLs to test (VP release, the
   stock DLL, the ai-improvements DLL).
3. **Lua.** Decide between keeping the main Lua state and scrubbing it per game (L1), or closing it and booting a new
   one per game (L2); see section 5.3.
4. **CRT `rand`.** Lua's `math.random` is the calling thread's CRT `rand()` in `lua51_Win32.dll`'s MSVCR90 (per-thread
   state, seeded 1; `ApplyStartOptions` in `HHMain.cpp`), and `Continental_Drift.lua` depends on it. Find whether
   boot consumes any of it before game start: a probe that draws `math.random()` at the start of a fresh game and
   compares it with the first draw after `math.randomseed(1)`.
5. **Timings.** Repeat the table of section 2 for the stock DLL and for a Tiny 150-turn game, with the phase timers
   that `Headless.log` already has.

Exit: findings appended here; the go or no-go for tier B; L1 or L2.

## 5. Host design

### 5.1 The command

```
civ5headless.exe batch --games -|FILE  --out BOOTDIR  [every process-level option of newgame]
```

- `--games -` reads one request per line on standard input (JSON), so the queue can feed a process one game at a
  time and keep its work-stealing order; `--games FILE` reads a JSON array, for manual use and tests.
- The host writes one JSON line per event on standard output: `hello` (protocol `c5h-batch/1`, host version, boot
  seconds), `started`, `finished` (exit code as `newgame` would return it, seconds, private bytes, health checks
  of section 5.4), `refused` (a request it cannot play). Everything else goes to the logs, not to standard output.
- `BOOTDIR` gets the boot's own files: the boot part of `Headless.log`, the database and localization copies,
  `host-report.json` for the boot (`dll`, `database`, stub calls during boot).
- End of input: normal shutdown, as `newgame` shuts down today.

A request carries only per-game options; the process-level ones are fixed at start. A request naming a process-level
option, or an unknown one, is refused, never guessed:

| Per game (in a request) | Per process (on the command line) |
|---|---|
| `spec`, `out`, `turns` | `--dll`, `--db`, `--mods-dir`, `--mods-db`, `--no-mods`, `--loc-db`, `--game-dir` |
| `luaProbes`, `luaBeforeArm`, `checkpointLua`, `checkpointEvery`, `luaBeforeDump` | `--profiles`, `--no-profiles`, `--no-addins`, `--skip-addin` |
| `exeSeed`, `crtSrand`, `fpuPrecision`, `pregameTurnTimer` | `--heap-perturb` (before `LoadLibrary`), `--crt-sse2` |
| `frameSkew`, `idleUpdates` | `--no-game-logging` and the logging flags (`SetLogging` at boot), `--no-lua-debug` |
| `autosave`, `saveAt`, `noSaveFinal`, the detail options (`noDumps`, `keepLog`) | `--strict`, `--verbose`, `--reset game\|dll` |

`--keep-dll-loaded` applies once, at the end of the process.

### 5.2 Boot once, play many (tier B, `--reset game`, the default of `batch`)

Phase 1 splits `Boot()` in `host/driver/HHMain.cpp` into three functions with today's calls, in today's order:

1. **Engine boot**: paths, crash-log marker, log open, Firaxis DLLs, VFS, profiles, script system, engine utility,
   user interface, `LoadLibrary` of the game DLL (heap perturbation), `DllGetGameContext`, `SetDLLIFace`,
   `SetEngineUserInterface`, logging flags, database, localization, `Init`, post-processing,
   `CacheGameDatabaseData`, `HideDebugLibrary`, counts, Lua registration and self-test.
2. **Play one game**: pregame, start (`HHStartNewGame`), `HHSetSaveGame`, UI emulation, t000, add-ins,
   `HHWriteT000`, probes, autoplay, final checkpoint and dump, final save, the game's reports.
3. **Engine shutdown**: Lua shutdown, `HHSetSaveGame(NULL)`, `HHEndNewGame`, `Destroy`, `Uninit`,
   `SetGameDatabase(NULL)`, `ShutdownLocalization`, optional `FreeLibrary`, close the databases, the reports.

`newgame` becomes 1, 2, 3. `batch` becomes 1, then for each request 2 and the game boundary below, then 3.

**Game boundary** (after game k, before game k+1). Use the real exe's sequence from phase 0; until then, the
shutdown sequence the engine contract records (section 8 there), without the parts that end the engine:

1. `setGameStarted(false)`, `resetPlayers()`, `NetMessageStaticsReset()`, `GetGameDeals` then `DestroyDeal`,
   `CvDllGame::Uninit()` (as `HHEndNewGame` does today, so `Uninit` does not trap).
2. Host state back to its state after boot (section 5.3).
3. Health checks (section 5.4); a failed check ends the process after reporting, and the next game starts fresh.

### 5.3 What the host resets per game

Every module with per-game state registers a reset function (`HHRegisterGameReset(name, fn)`), so the list is
explicit in the code and each reset is logged. What phase 1 must collect:

| State | Where | Reset |
|---|---|---|
| Run folder paths, current directory | `HHGetPaths()` (`run`, `logs`, `cache`), `SetCurrentDirectory` | per game; `db` stays the boot's |
| Host log | `HHLogOpen` (`Headless.log`) | close and open in the game's `Logs\`; copy the boot section in first, marked `boot (shared)`, so tools that read phases keep working |
| DLL log files | `HHLogFileMgr` (`m_logs`, folder, keep patterns) | keep every `HHLogFile` object (the DLL may hold the pointers) and retarget it: close the file, reopen in the new folder, truncated, on its next write; `Lua.log` likewise |
| Cache folder | `GetCacheFolderPath`; `HeadlessSeeds.txt` (read and deleted at `CvGame::init`); `Civ5SavedGameDatabase.db` (created by the DLL per new game) | per game, if phase 0 finds the DLL does not keep the path; otherwise one boot cache folder, emptied between games, with the files moved into the game's folder |
| Save data connection | `Modding.OpenSaveData` (one connection, opened on first use, `HHLuaEnv.cpp`) | close at game end |
| User data | `Modding.OpenUserData` (in-memory, empty at the start of every run) | empty |
| Lua: game contexts | InGame state and every UI context and probe thread (`CreateLuaThread`, `RegisterScriptLibraries`) | free them all |
| Lua: events | `GameEvents` listeners (`__HHGameEventListeners`, `recordListener` per context), `Events`, `LuaEvents`, `MapModData` | L1: clear, then restore `_G` and the prelude tables from a snapshot taken after boot (new keys removed, changed keys restored), release registry references; L2: close the state, boot a new one (globals, `ipairs`, game data registration; not the `VP_LUAAPI` self-test, which belongs to `CacheGameDatabaseData`) |
| Lua: counters | `s_iDefines` and other statics in `host/lua` | reset |
| Pregame | ~1,900 setters at boot and front end; the real exe keeps some values between its games (`--pregame-turn-timer` exists for Marathon) | restore the state right after boot: replay the boot's pregame sequence from `resetPlayers`, `ResetGameOptions`, `ResetMapOptions`, then the spec as today; check with a pregame digest (every value the Lua API reads, as `setup.pregame` does) |
| Engine UI state | selection list, head unit, notifications, popups, busy-unit updates, end-turn timer semaphore, active player, `SetGraphicsInitialized`, `UI.IsLoadedGame`, queued `ResponsePlayerOption` deliveries | back to boot values |
| `GetDone` | true from `Uninit` on (engine contract G15) | false again before game k+1 |
| Engine random objects | `CvDllRandom` objects 0 and 1 | re-seeded as in a fresh process |
| Save records | `HHSetSaveGame`, autosave list, `--save-at` turns | per game |
| Frame counter, frame skew, idle updates | autoplay loop | per game |
| CRT `rand` (Lua `math.random`) | MSVCR90 of `lua51_Win32.dll`, per thread | `--crt-srand` given: as today; otherwise restore the fresh-process state (phase 0, item 4: `math.randomseed(1)` if boot draws nothing) |
| x87/SSE control word | logged at start | restore the boot value; log the value each game started with |
| Error and call counters | `GetErrorCount`, `HHLogError` count, first-call records of engine methods | report per-game deltas; boot calls reported once, marked shared |
| Threads | any thread the host starts for a game | start per game, so thread-local state is fresh |

### 5.4 Health checks per game

After every game the host records, in `host-report.json` (`batch.health`) and on the `finished` line:

- private bytes, working set, handle count, and their growth since game 1;
- Lua memory after a full collection, the number of globals, listeners and registry entries, against the boot
  snapshot (L1);
- the game database: `sqlite3_total_changes` on every host connection to it, against the value after boot. A game
  that wrote to the database (a mod's Lua `DB.Query` update, say) is a valid game, but the next game would see the
  change: the process reports it and exits, and the queue continues in a new one. The localization database the same.
- the control word, and the pregame digest after the reset against the one after boot.

Limits: `--max-games N` (default 32) and `--max-private-mb M` (default 1,400: the host is 32-bit, see
[why-32-bit.md](why-32-bit.md), and fragmentation grows with each game). Reaching one ends the process cleanly after
the game; the queue starts a new one.

### 5.5 Tier A: reload the DLL per game (`--reset dll`)

For a DLL that fails the canary in tier B. Per game: the game boundary, then `Uninit`, `SetGameDatabase(NULL)`,
`FreeLibrary` (as shutdown does today unless `--keep-dll-loaded`), a fresh database copy (the DLL writes to it in post-processing),
`LoadLibrary`, `SetDLLIFace`, `Init`, post-processing, `CacheGameDatabaseData`. The DLL's statics start fresh;
the process heap does not, so a DLL whose results depend on addresses can still differ (section 7). Gain about 2.2 s
a game (localization, process start, outside overhead). Build it only if phase 3 finds a DLL that needs it.

## 6. Python design (`python/civ5headless`)

### 6.1 `run.py`

Split `newgame()` into the parts a batch needs:

- `prepare(a)`: resolve the DLL, mods, database and spec; write `spec-resolved.json`, `hh-before-arm.lua`,
  `hh-checkpoint.lua`, `hh-final.lua`; return the command line (single game) or the request (batch), the boot key
  and the `hh-command.json` content;
- `launch` and `monitor`, as today for a single game: one process in a `winproc.Job`, `turn-times.jsonl` for the
  maximum and stall seconds;
- `collect(run)`: extras, `status.json`, `finish()`.

The single-game path calls the same three in the same order, so its files stay byte-for-byte the same, apart from
timings.

Small change for both paths: cache `dbcache.sha256_file` of the host and the DLL by (path, size, mtime) for the
life of the Python process (today hashed again for every game).

### 6.2 `queue.py`

- New queue key `gamesPerProcess` and option `c5h queue run --games-per-process N`; 1 (the default) runs the
  current `worker()` unchanged.
- N above 1: each worker slot owns a `BatchHost` (one `civ5headless.exe batch` process in its own job object, with
  the slot's affinity, the job's CPU and memory limits nested in the queue's total CPU cap, as today).
- The worker takes the next job from the shared deque as today. If its boot key (host and DLL hashes, database,
  mods, localization database, game folder, profiles, add-in options, heap perturbation, logging, process-level
  `host_arg`s) differs from its process's, it ends that process and starts one for the new key.
- Per game it writes the request, watches that game's `turn-times.jsonl` for the time limits, and on `finished`
  collects the run folder exactly as for a single game.
- A crash, stall, time-limit kill or broken protocol: the job object kills the process; the game in progress is
  marked failed with the reason and played again once in a fresh single-game process (`retriedFresh: true`); the
  slot starts a new batch process for its next job.
- Queue state per job: `process` (batch process ID, index in the process, boot folder), `bootSeconds` shared.
- `report_page` and `summary`: a column for the process and index; boot time shown apart.

### 6.3 The canary

For every boot key, a queue session with `gamesPerProcess` above 1 plays its first `batchCanary` games (queue key,
default 2) twice: in the batch, and fresh in a single process. It compares them with `c5h compare` (digests, every
turn) or `c5h fingerprint compare` (DLLs without the digest additions).

- **Identical**: batch mode stays on for that key.
- **Different**: a warning in the queue log and report, batch mode off for that key for the rest of the session,
  and the games already batched under it played again fresh.

`batchCanary: 0` skips it, for a DLL already shown sound (phase 3) or when only statistical equivalence is wanted
(section 7). The canary costs two extra games per boot key and session; the tactical suite has one boot key per DLL.

### 6.4 `c5h verify`

Unchanged by default. Later, optional: `c5h verify --host-only --batch-check` adds a `trajectory.batch` reference (the
A/A game batched behind two other games, against the fresh one), info only. The ensembles keep using fresh
processes unless asked.

## 7. Heap layout

A process that has played games has a different heap from a fresh one. A DLL whose results depend on addresses
(containers keyed or sorted by pointer) then plays different games in a batch: still valid games, but not identical
to fresh ones. `c5h verify` already measures this (`trajectory.heap-perturb`; VP: 150 of 150 turns identical), so a
DLL that passes it should pass the canary as well. For a heap-sensitive DLL a fresh game is not reproducible either
(`--heap-perturb` changes it), and batch results are equivalent only in distribution. `batchIdentity: statistical` (a
queue key) accepts that knowingly; the default `exact` falls back to fresh processes.

## 8. The regression gate for single games (phase 1, and every later phase)

Before and after each change, on the same machine and build settings:

1. `c5h verify --host-only` with the VP DLL, and with `--no-mods` and the stock DLL: same verdicts, same digests.
2. The reference games (the specs in `examples\`, a tactical-suite scenario with a Lua setup probe, a game with
   city-states, barbarians and religion on): per-turn digests (`c5h compare`) and fingerprints identical;
   `host-report.json` `steps`, stub calls and DLL fields identical; `newgame-report.json` identical apart from
   timings; the same file list in every run folder.
3. `loadgame` from a final save of one of those games: an identical continuation.
4. Failure paths keep their exit codes and messages: a missing DLL, a bad spec, a Lua error in a probe, a
   `c5h run --maximum-seconds` kill, a stall.
5. `c5h queue run` with a small queue: same fields in the queue's `.state.json`, same report.
6. The Python unit tests (`tests\`) pass.
7. Speed: host seconds within noise (5 runs each).

Each phase is its own merge commit, so any of them can be reverted alone.

## 9. Phase 3: isolation tests for batch mode

A test command (`c5h batch-check`, kept in the repository, used before enabling batch for a new DLL) and fixed tests:

1. For each DLL of phase 0: the reference games played fresh, then in one batch in order, in reverse order, shuffled,
   and one spec three times in a row. Every batched game identical to its fresh game, every turn.
2. The same with `--heap-perturb` and with `frameSkew`: the batch reproduces what those options do fresh.
3. A game whose probe throws a Lua error and a game killed at its time limit, in the middle of a batch: the games
   after them identical to fresh.
4. A database-writing probe: the process reports it and exits; the next game is fresh.
5. Soak: 200 tactical games in one process (no `--max-games`): private bytes, handles, Lua memory and globals stay
   flat after the first few games; the run folders match the fresh ones.
6. Output shape: every run folder of a batched game has the files of a fresh one, and the JSON reports parse with
   the current readers (`c5h compare`, the run report, the queue report, the tactical suite's `tools/report.py`).

## 10. Phase 6 (independent, optional): localization for single games

About 1.3 s of every single game, and of every batch process once. Two steps, each its own option:

1. **No copy per run.** The host copies the 53 MB localization database into the run folder and opens the copy
   read-write (`HHDatabase.cpp`, `InitLocalization`). Find whether anything writes to it (`sqlite3_total_changes`
   after a game). If nothing does, `--loc-db-shared` opens a cached copy (`<cache>\loc\<sha256>.db`, made once,
   checked by hash) instead.
2. **One language.** `c5h dbbuild --slim-loc LANG` builds a localization database with only that language's rows,
   cached by the source hash and language; `--loc-db` points at it. Gate: digests identical, and a probe that looks
   up every text key used by the DLL and the mods gives the same strings both ways.

Default stays the full copy.

## 11. Not planned

- **Reseed on load** (one setup save, many seeds): saves only the map and pregame step (about 0.5 s) on top of
  batch mode, and makes every replicate share the map. Reconsider only if map generation becomes the bottleneck.
- **Two games at once in one process**: the DLL's globals (`GC`, the game and map singletons, the pathfinders) allow one game.

## 12. Phases, sizes, order

| Phase | What | Size | Depends on |
|---|---|---|---|
| 0 | Measure and decide (section 4) | small | |
| 1 | Refactor `Boot()` and `newgame()` into parts, reset registry (no behaviour change; gate of section 8) | medium | 0 |
| 2 | `batch` command, tier B, health checks, limits | large | 1 |
| 3 | Isolation tests, `c5h batch-check` | medium | 2 |
| 4 | Python batch worker, canary, fallback, `gamesPerProcess` | medium | 2 (3 before enabling) |
| 5 | Tier A, only if a DLL needs it | small | 3 |
| 6 | Localization options | small | none |

Acceptance for enabling batch mode for a DLL: phase 3 passes for it; the tactical suite in quick mode, the same
seeds, gives the same `run.json` games batched and fresh, at least 2x faster at the same CPU budget (16 games, 60%
of a core each).

Docs to update with the code: [user-guide.md](user-guide.md) (the `batch` command), [running.md](running.md) (queue
keys `gamesPerProcess`, `batchCanary`, `batchIdentity`), [engine-contract.md](engine-contract.md) (the game boundary
and what the real exe does between games), [ledger.md](ledger.md), `CHANGELOG.md`.

## 13. Risks

| Risk | Detected by | Mitigation |
|---|---|---|
| DLL state survives between games (static caches keyed by turn, `GC` members) | phase 0 audit, canary, phase 3 | fresh processes for that DLL, or tier A |
| Lua state leaks between games (listeners, globals, registry) | L1 snapshot diff, health checks | L2 (a new state per game) |
| Pregame values carried over (the real exe does, e.g. the turn timer) | pregame digest after the reset | replay the boot's pregame sequence |
| CRT `rand` or control word carried over | first `math.random` draw, control word in the log | restore per game |
| Heap layout differs from a fresh process | canary; `trajectory.heap-perturb` | fresh processes, or `batchIdentity: statistical` |
| 32-bit address space fills or fragments | private bytes per game | `--max-games`, `--max-private-mb` |
| A crash loses queued games | protocol, job object | only the game in progress; played again fresh |
| The DLL keeps engine pointers (log files, cache path) | phase 0 audit | keep the objects, retarget their files |
| The refactor changes single games | the gate of section 8 | not merged until identical; revert per phase |
| A tool misreads a batched run folder | phase 3, item 6 | same files; boot data marked shared |
| An old host or an old `c5h` | `hello` protocol line, `host_supports` | one game per process |

## 14. For the tactical test suite

Once phase 4 is in, the suite only adds `"gamesPerProcess": 16` (and leaves `batchCanary` at its default) to the queue
it writes in `tools/tts.py`. Its runs keep their folders, `run.json` and reports. Until then the suite works as it
does now.
