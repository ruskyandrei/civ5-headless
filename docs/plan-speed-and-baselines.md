# Small speed-ups, and saved games as baselines (plan, 2026-10-30)

Status: both phases implemented on 2026-10-30 (see "What the implementation changed", below). The numbers in sections 2,
3 and 8 were measured on a throwaway host build; its changes are in [plan-speed-trial.patch](plan-speed-trial.patch), which
is evidence and not production code. Section 7 has the numbers of the implementation.

Two phases, independent of each other:

- **Phase 1:** load a save and play it on with new seeds.
- **Phase 2:** small host changes that make games a little faster.

The larger speed-ups that were measured are left out on purpose; section 8 records them.

**What the implementation changed from this plan:**

- **The stock DLL was the mirror image of VP.** It responds to the sync seed only: a new map seed alone gives an identical
  game. So the warning is for any game with only one new seed, not only the sync seed. [saves.md](saves.md) has the table.
- **The stock DLL's final saves did not play on.** The DLL ends an autoplayed game on its last turn, because the active
  player is dead, and the host stopped when a loaded game's state was not "on". Autoplay now stops when the state changes,
  and such a load warns. `--save-at` saves are not affected.
- **`--save-at T` needs a game that goes past T,** so the examples are `--turns 101 --save-at 100`.
- **The allocator's gain on a long game was smaller than in the trial:** CPU about -3%, not -7%.
- **Loading called a stub that `health.stubs` did not know,** `getAssignedNetworkID`. The DLL only stores the value in the
  pregame, so it went on the harmless list.

## 1. Goals

1. Load one save and play it on with new seeds, so that a saved game can be the baseline of an ensemble or an A/B test.
2. Take the cheap and safe speed-ups, without changing what a game is.

Not goals: reusing the boot between games, changing what the DLL logs, or any change to how the real game is driven.

## 2. Where the time goes

One tactical-suite game: VP DLL (build AFL, `Release-5.4.8-9-g6b97a2de`), Duel map rewritten by a Lua setup probe, 4
majors, 15 turns, detail `summary`. The P-core column is one game uncapped. The suite column is the median of the 432
games of the suite's `quick1` run (60% of a core, efficiency mode, E-cores), wall seconds.

| Phase | P-core | Suite |
|---|---:|---:|
| Process start, VFS, game DLL | 0.15 | 0.16 |
| Localization (copy 0.015 s, `Localization::Initialize` the rest) | 0.50 | 1.29 |
| `CacheGameDatabaseData` and post-processing | 1.06 | 2.90 |
| Pregame and game start (mostly the map) | 0.23 | 0.60 |
| UI emulation, add-ins | 0.19 | 0.50 |
| Lua probes (the scenario setup) | 0.13 | 0.36 |
| Autoplay, 15 turns | 1.19 | 2.57 |
| Reports, shutdown | 0.20 | 0.24 |
| **Host** | **about 3.5** | **8.7** |
| Python around the host (before launch 0.05, after exit 0.05) | not measured | 0.10 |

A sampling profile of the host's main thread (about 600 samples a second; the Firaxis DLLs and Windows have no public
symbols, so these are shares by module):

- **Cache and localization:** about 45% in the Firaxis database DLL (which holds SQLite), about 40% in `ntdll` (mostly
  the heap, by the look of it), about 10% in the C runtime. The game DLL itself is 2% of the cache step.
- **Autoplay:** game DLL 35%, `ntdll` 29%, C and C++ runtimes 26% (string formatting), Lua 6%.
- **SHA-256 of the exe, the DLL and the UI files** in the host's reports: about 2% of a short game.

## 3. What the trials showed

Same game means Lua fingerprints identical on every turn against the unchanged host (`c5h fingerprint compare`).

| Change | Short game (15 turns) | Long game | Same game? | Decision |
|---|---|---|---|---|
| Engine call counters without a map lookup per call | CPU -4 to -5% | not measured alone | yes | phase 2 |
| Small-block allocator (`FSBAAllocate`) | no change | CPU -7%, wall -3% (Tiny, 150 turns) | yes, 151 of 151 turns | phase 2, opt-in |
| Indexes on every `*Type` column of the game database | cache 1.06 to 1.03 s | | yes | dropped |
| No localization copy per run | 0.015 s | | | dropped |
| Long texts cut to 40 characters | localization -0.07 s | | yes | dropped |
| Half the localization rows removed | localization -0.22 s | | the game fails | dropped: time follows row count, and rows are needed |
| `--no-ui-frame` | no change | | yes | dropped |
| `--no-addins` | -0.15 s | | yes in this game | stays as it is, opt-in: add-ins can hook gameplay |
| `checkpointEvery: 100` | -0.13 s | | yes | stays as it is: the suite wants its per-turn data |

## 4. Ground rules

1. **Off by default, except what cannot change a game.** Phase 2's counters and hash cache are always on, once the gate
   shows identical games. Everything else is an option or a queue key.
2. **The gate:** section 8 of [plan-batch-games.md](plan-batch-games.md) before each merge: verify, reference games,
   `loadgame`, failure paths, queue state, unit tests, speed.
3. **Recorded:** `status.json` and `host-report.json` say which options a game used, so that tools can refuse to mix them.
4. **Fallback:** a host that cannot do something says so clearly, never silently.

## 5. Phase 1: new seeds for a loaded game

Today `c5h run --load` refuses `--seeds`. A queue job with `load` and `seeds` **ignores the seeds silently**, so its games
are copies of one another under different names. This phase fixes both.

### 5.1 What has to change in the game

Both seeds matter, and which one depends on the DLL:

- **Map seed:** VP takes most of its randomness from `CvGame::randCore`, a hash of `CvPreGame::mapRandomSeed()`, the turn
  and a seed per call site (about 250 uses in the DLL, against about 20 of the game's generator). This hash needs no
  generator state.
- **Sync seed and the game's generator (`m_jonRand`):** the stock DLL draws on these (ledger; not re-run here).

Both can be reached through the official interfaces, with no change to any DLL: `ICvPreGame1::setMapRandomSeed`,
`setSyncRandomSeed`, and `ICvGame1::GetRandomNumberGenerator()` then `ICvRandom1::Reset`.

Measured on the VP DLL, from a save of turn 30, played to turn 45:

| Compared | Result |
|---|---|
| The save loaded twice, no new seeds | identical on all 16 turns |
| New seeds (111, 222) twice | identical on all 16 turns |
| No new seeds against new seeds | differ from turn 31, the first turn after the save |
| Seeds (111, 222) against (333, 444), and (333, 444) against (555, 666) | differ from turn 31 |
| New map seed only, against no new seeds | differ from turn 31 |
| **New sync seed only, against no new seeds** | **identical on all 16 turns** |
| A final save (turn 31) with and without new seeds | differ from turn 32 |

So a reseed of the generator alone, the obvious way to do this, would give VP ensembles of identical games.

A map seed changed on a loaded game does not change the map, which is already in the save. So this also gives VP what a
new game cannot: other rolls on the same map. A new VP game with another map seed gets another map, and another sync seed
gets the same game ([verification.md](verification.md), "The ensemble").

### 5.2 Host

`loadgame --reseed MAP:SYNC` (either side may be empty, which keeps the save's). In `HHLoadSavedGame`, after the body has
been read and checked, before `HHGameStartedSteps`:

1. `setMapRandomSeed(MAP)`.
2. `setSyncRandomSeed(SYNC)`, then `GetRandomNumberGenerator()`, `Reset(SYNC % 52319761)` (the reduction `CvGame::init`
   applies), `Destroy()`. The generator's value before and after go to the log.
3. The steps go to `newgame.steps`, and `load.reseed` to the report.

Notes:

- The map generator (`m_mapRand`) cannot be reached and keeps the save's state. After the map is made, the DLL uses it
  only in `CvPlot::getInlandCorner` and in the simultaneous-turn shuffle.
- `GAMEOPTION_NEW_RANDOM_SEED` reseeds from the clock inside `Game::Read`. A reseed after it makes such a save repeatable.
- The new seeds are in the pregame, so the run's own later saves carry them.

### 5.3 Python

- `c5h run SAVE --turns T --seeds MAP SYNC` and `run.play(SAVE, T, seeds=...)`: the refusal goes. A host without
  `--reseed` (`host_supports`) gives a clear error.
- Queue jobs: `load` with `seeds` passes them on. Then `seeds` x `variants` over a save works:

  ```json
  {"defaults": {"load": "base.Civ5Save", "turns": 130},
   "variants": {"A": {"dll": "a.dll", "abRole": "baseline"}, "B": {"dll": "b.dll", "abRole": "test"}},
   "seeds": [[1001, 2002], [1003, 2004]]}
  ```

- `status.save` becomes `loaded-<save name>-t<turn>-<file hash>-m<map>-s<sync>` for a reseeded game, so that
  `abcompare.py` pairs the same seeds. `loadedFrom` still names the save. `status.json` and `setup.json` get `reseed`.
- The run report and `c5h queue report` show the seeds of a reseeded game.

### 5.4 Using a save as a baseline

```
c5h run my-spec.json --turns 101 --save-at 100 --run base
c5h run "runs/base/saves/Turn_0100 <date>.Civ5Save" --turns 130 --seeds 1001 2002 --run base-s1
```

What the documentation must say ([saves.md](saves.md) sections 4, 6 and 7 hold the details):

- **Compare loaded games with loaded games.** A loaded game does not reproduce the game that wrote the save: VP's
  `CvPlayer::Read` reads the espionage AI's lists wrongly, and an autosave or `--save-at` save replays part of its turn.
  All arms from one save share these effects, so an A/B from one save is sound.
- **A/B across builds:** VP builds share a GUID, so a save loads into another build. The host fails the load if the DLL
  does not use up the body exactly, but compatible-looking saved data remains the user's responsibility.
- **One seed alone can do nothing:** the sync seed on VP, the map seed on the stock DLL. `c5h run` and the queue warn when
  only one is given.

A scenario can be a baseline too (a probe sets it up and calls `UI.SaveGame`). For the tactical suite that would replace
the map and setup step, about 0.3 s of 3.5 on a P-core (a load takes 0.06 s), and every seed would start from the same
position. It would also change what the suite measures, so it is the suite's decision and not part of this plan.

### 5.5 Tests

- Unit tests with the fake host: arguments, refusal gone, status fields, queue seeds on a load.
- `c5h verify`: a new reference `trajectory.reseed`, as the table in 5.1 (same seeds identical, other seeds differ), on
  each DLL it verifies. It reports which seed the DLL responds to. **The stock DLL has not been run yet; this is the
  first thing to do.**
- Later, optional: `ensemble_vary = "reseed"`, an ensemble on one map from a turn-0 save.

## 6. Phase 2: small host changes

1. **Call counters.** `HHImplHit` does a `std::map<std::string, ...>` lookup and takes a lock on every engine call, 575,000
   of them in a short game (`IsGameCoreThread` and `IsGameCoreExecuting` are 427,000). Change: a static pointer to the
   counter per call site. The counts, first phases and reports stay the same. `ExportHit` becomes a plain increment, as
   the host has one thread. Stub calls keep their path, for `--strict`.
2. **Hash once.** The host hashes the exe and the DLL per run, and Python has already done so. Change: `--known-sha256
   PATH=HEX` from Python, and a cache by path in the host. Same report fields. A host run by hand hashes as today.
3. **Small-block allocator, opt-in (`--sba`, queue key `smallBlockAllocator`).** The DLL and the Firaxis DLLs call
   `FSBAAllocate` on every `new` (1.45 million in a short game) and the host answers "use `malloc`". The real exe has a
   real allocator there. Trial: size classes in 16-byte steps to 512 bytes, 64 KB pages from one reserved range, LIFO free
   lists, about 60 lines. It moves every small block, so it needs `trajectory.heap-perturb` to pass for the DLL. Default
   stays off until `c5h verify` has run with it on the stock DLL and the VP release.

## 7. Expected results

Measured on the implementation against the 0.3.2 host, one game at a time on one P-core, VP DLL:

| | Short game (Duel, 15 turns, 9 runs each, medians) | Tiny, 150 turns (2 runs each) |
|---|---|---|
| Call counters and hash once | CPU -4.1% (2.297 to 2.203 s), wall -5.4% (2.606 to 2.466 s) | not measured alone |
| Small-block allocator, opt-in | no change in the trial | CPU about -3% (24.8 to 24.05 s), wall about -3.5% (23.35 to 22.5 s) |

For the tactical suite that is about 5%: quick mode from 4.3 to about 4.1 minutes, full mode from 21 to about 20. Phase 2
is small on purpose. Most of a short game is the boot and most of a long game is AI work, and neither is touched here.

## 8. Left out

Measured, then left out of this plan on 2026-10-30. Recorded so that the work need not be repeated.

**Turning the AI log off.** It needed no host change: `--game-log-flags 0`. `c5h` has since gained a switch for it
(`--ai-logging`, queue key `aiLogging`, `ai_logging` in `civ5headless.toml`), and off is now its default.

- This now has its own page, [ai-logging.md](ai-logging.md).
- Gain: CPU -18% on the short game, wall -30% on Tiny at 150 turns, -40% on Duel at 100 turns.
- **It plays a different game from the logged one with the same seed.** Over 32 seeds of Tiny at 150 turns, all 32 pairs
  diverged, at turn 46 on the median (earliest 32, latest 132). Code under `GC.getAILogging()` changes game state (two
  places, [ai-logging.md](ai-logging.md)). The first visible difference was small in the two games inspected (one unit, 10
  culture).
- The same seed with the log off repeats exactly: 151 of 151 turns, also with `--heap-perturb`.
- On average the games are alike: over those 32 seeds, no difference at turns 50, 100 or 150 in score, cities, population,
  techs, policies, units, military, gold, culture, faith or wonders was statistically distinguishable, apart from one
  borderline result (cities at turn 50, +0.04). At turn 150, score differs by -0.1 (95% range -16 to +18, of 849).
- So it suits ensembles and A/B runs where every arm has it off, and not seed-by-seed comparison with logged games or the
  real game. `status.json` records the setting (`aiLogging`, `gameLogFlags`), and the comparison tools warn about a mix.
- Fixing the DLL would make log-off games identical, and would allow playing thousands without logs and replaying one with
  full logs. That would be work in the VP fork.

**One clone of the booted process per game.** `RtlCloneUserProcess` (`ntdll`, undocumented) worked here for the 32-bit host
on Windows 11, once every handle was made inheritable and read-only mapped views were copied to private memory.

- Gain: boot once (1.68 s), then 1.98 s a game against 3.5 s fresh: about 1.6x in the suite's quick mode, 1.7x in full mode.
  About 6% on a 150-turn game.
- Same game on the VP DLL: 15 turns (with and without a save), 100 turns, a loaded save with new seeds, and 48 clone games
  in 16 processes, 8 at a time.
- Not tested: the stock DLL, a crash or kill in a clone, job limits and efficiency mode on a clone, other Windows versions,
  separate run folders. The trial code is in `plan-speed-trial.patch` (`--fork-games`).

**Batch mode ([plan-batch-games.md](plan-batch-games.md)).** It saves the same boot, with reset logic that clones do not
need, so clones would be the better way to get that saving if it is ever wanted.

**Other notes.** The database log shows `MinorCivilizations` taking 45% of the cache step in one run, against 19% for
`Buildings`. Why was not investigated. The `ntdll` share of the boot was not reached by the allocator, so it probably comes
from direct `malloc` in the Firaxis DLL. SQLite pragmas were not tried.

## 9. Phases, sizes, order

| Phase | What | Size | Depends on |
|---|---|---|---|
| 1 | New seeds for a loaded game | small | none |
| 2 | Call counters, hash once, allocator (opt-in) | small | none |

Either can go first. Each is its own merge commit.

Acceptance:

- **Phase 1:** the table of 5.1 passes on the VP DLL and on the stock DLL (with the seed each responds to).
- **Phase 2:** the gate passes; CPU of a short game down by at least 3% with the counters and hash cache; the allocator
  passes `trajectory.heap-perturb` and plays the same game on the DLLs it is used with.

Docs to update with the code: [saves.md](saves.md) (the seeds, baselines), [running.md](running.md) and
[user-guide.md](user-guide.md) (options, keys), [verification.md](verification.md) (the new reference, the ensemble),
[engine-contract.md](engine-contract.md) (`--reseed`), [ledger.md](ledger.md), `CHANGELOG.md`.

`plan-speed-trial.patch` holds both phases' trial code and the clone trial. Keep it until both phases are merged, and
longer if clones may come back.

## 10. Risks

| Risk | Detected by | Mitigation |
|---|---|---|
| Reseeded games look different but the DLL ignores a seed | `trajectory.reseed`; the warning on the sync seed alone | both seeds are always set when given; the report says which matter |
| A save does not fit another DLL build | the body check at load | the load fails; compatibility stays the user's call |
| A/B from one save inherits an odd saved state | none general | documented; use several baselines |
| The allocator exposes an address-dependent DLL | `trajectory.heap-perturb`, reference games | opt-in; off for that DLL |
| The small changes alter single games | the gate | not merged until identical; revert per phase |
