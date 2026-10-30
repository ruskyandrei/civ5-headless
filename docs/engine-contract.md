# Headless host: engine contract (M0)

What `CivilizationV_DX11.exe` does at the boundary with `CvGameCore_Expansion2.dll`, recorded with the trace
build (D1), and what `civ5headless.exe` must do to give the same game. Plan: [headless-host-plan.md](headless-host-plan.md)
(sections 2–4, M0–M3); DLL additions: [headless-dll-additions.md](headless-dll-additions.md).

Labels: **[M]** measured in a trace or probe, **[S]** read in the DLL source, **[I]** inferred (not proven).

## 0. Sources and limits

| Run | Content | Trace |
|---|---|---|
| S1 `work/test-runs/hh-m0-s1-continents` | mods → new game from `continents-standard.json` (Continents, Standard, 8 majors, 16 CS, seeds 1001/2002) → arm autoplay **after** "continue" → 5 turns → final save → quit | `HeadlessTrace-20261025-002901-p32304.log`, complete |
| S3 `work/test-runs/hh-m0-s3s4-earth` | same flow, `Earth_Small.Civ5Map` (6 majors, 12 CS, seeds 3003/4004), probes, 2 turns, no save | `HeadlessTrace-20261025-003110-p27816.log`, complete |
| S4 `work/test-runs/hh-m0-s4-hooks` | same spec as S3, hook probe at turn 0, quit | `HeadlessTrace-20261025-003245-p34196.log`, complete |
| AA100 `work/test-runs/hh-m0-aa100-a` | A/A run 1: S1 spec, autoplay armed **before** "continue", 100 turns | `HeadlessTrace-20261025-003403-p34972.log`, complete (used for breadth: net messages, autosaves, hooks) |
| Host boot | `work/headless-runs/boot-trace5-unload` (trace DLL under `civ5headless.exe`, M1) | `HeadlessTrace-20261025-002415-p17136.log` |

Driver actions are placed on the trace clock with `trace_phases.py --run` (anchor: the start-game call ends just
before the first new-game call; about ±0.1 s). Commands to regenerate everything are in section 13.

Limits of the traces (read before trusting a "never seen"):

- **Per-method cap of 50 lines per game turn.** The game turn is 0 from DLL load to the first `doTurn`, so the
  boot, front end, new game and the turn-0 loop share one budget. Calls after the 50th of a method in that span
  are only counted (SUM lines). In particular the front end's late `CvDllPreGame::set*` calls (the spec's slot
  setup, the exe's leader choice) and turn-0 `CallHook`s (e.g. `PlayerCityFounded`) are invisible in S1–S4.
  Section 4.2 reconstructs them from the counts.
- **Lua → DLL calls are not boundary calls.** UI and tuner Lua call the DLL's Lua libraries (`Game.*`,
  `Players[]`, …) directly; only the OUT calls they cause are visible (for example `Game.SetAIAutoPlay` shows as
  `PublishActivePlayer(8,0)`). `PreGame.*` is the exe's library and appears as inbound `CvDllPreGame` calls.
- **Out parameters** are recorded only where the proxy formats them (`stripSpecialCharacters`); the buffer of
  `GetEvaluatedMapScriptPath` and the streams of `Write`/`Read` are not.
- `seq`/`t_us` are taken at call start; a nested call appears with depth + 1.

## 1. Findings that matter most for parity

1. **Random civs and city-states are chosen with the exe's clock seed, before seed pinning** [M][S]. Starting
   a game calls `closeInactiveSlots` → `setGameStarted(true)` → VP's `CvPreGame::onGameStarted`, which picks every
   `civ = -1` major and every city-state with `CvGame::urandLimitExclusive`, i.e. from
   `CvPreGame::mapRandomSeed()`. At that point the seed is still the value `loadFromIni` set at DLL load:
   `timeGetTime()` because `config.ini` has `MapRandSeed = 0` (`exeMap` = `exeSync` = 139543027, 139671867,
   139766712, 139844323 in the four runs, i.e. ms since Windows boot). `HeadlessSeeds.txt` pins only later, in
   `CvGame::init`. Proof: S3 and S4 (same spec, same pinned seeds) got different civs (Rome/Huns/Inca/… vs
   Greece/Japan/Inca/…). **Consequence:** reference runs with random civs are not reproducible and the host
   cannot match them from the spec seeds. Fix options: (a) the spec resolver writes explicit civs and
   city-states (simplest, no DLL change); (b) pin the pregame seeds in `onGameStarted` too (D3 extension), or set
   `CONFIG MapRandSeed/SyncRandSeed` in `config.ini` before launch (read by `loadFromIni` at DLL load; nonzero
   values replace `timeGetTime()`). The host must in any case call `setMapRandomSeed/setSyncRandomSeed` with the
   same values the reference had at `closeInactiveSlots` (its `HHSEEDS exeMap/exeSync`), then let `CvGame::init`
   pin the spec seeds.
   **Resolved (255e26ba2, option b):** `setGameStarted(true)` now applies `HeadlessSeeds.txt` (without consuming it)
   just before `onGameStarted`, logging `HHSEEDS|point=gameStarted` with the engine's own seeds. The civs and
   city-states then follow the spec's map seed in the real game and in the host. References recorded before it
   (S1, S3, the first AA100) still have clock-seed civs; the `hh-m0-*b`, `hh-m0-communitu` and `hh-m0-cdrift` runs
   have the fix.
2. **The real game runs the new game on the UI thread and the turn loop on a separate game-core thread** [M].
   `IsGameCoreThread` answers false during boot and new game (map generation, `Init2`, `setInitialItems`) and
   true inside `Update`; the DLL selects the pathfinder (`GC.GetPathFinder()`: core vs interface instance) and the
   trade-path caches by this answer [S]. The host answers true always today. Emulate the role, not the thread:
   false outside `CvDllGame::Update`, true inside it.
3. **`HasGameCoreLock` is true on the game-core thread** (297 of 300 logged, all core-thread calls) [M]; the host
   answers false. The DLL uses it to enable the stacking preview caches (`StackPreview*Supported` return false
   without the lock), to yield and invalidate caches in the tactical search, and to release the lock around Lua
   hooks [S]. With false the host takes different (cache-less) code paths: slower, and a divergence if any cache
   is not exactly result-neutral. Emulate a recursive lock-depth counter: `GetGameCoreLock` +1,
   `ReleaseGameCoreLock` −1, `HasGameCoreLock` = depth > 0. The exe holds the lock while it holds any
   `CvDllGame`/`CvDllPlayer` wrapper (their constructors call `GetGameCoreLock`) [S], so it is held throughout
   `Update`.
4. **Player options travel through the net loopback and are serialized** [M][S]. `SendPlayerOptions(true)` at
   game start (and the active-player switches of autoplay) call `getPlayerOption` for the 12 `PlayerOptions` rows
   and `sendPlayerOption`; the engine then calls `ResponsePlayerOption(activePlayer, hash, value)` on the UI
   thread right after (same frame). Real answers: `PLAYEROPTION_WAIT_END_TURN` (hash −246858542) = true and the
   sixth option sent (hash −304873808, `PLAYEROPTION_SAFE_AUTOMATION` by order [I]) = true, the other ten false.
   These are the user's profile settings, not database defaults. The host answers false and drops the send
   today → `CvPlayer` options differ → player sections of the digest differ.
5. **AI actions use the net loopback too** [M]. AA100 has `sendMinorPledgeProtection` (19) and
   `sendMinorBullyGold` (3) from the game-core thread inside `Update`; the engine delivers
   `ResponsePledgeMinorProtection` / `ResponseMinorCivBullyGold` with the same arguments on the UI thread
   ~25–100 ms later, between two Updates. These change game state. The host must queue every `send*` that has a
   `Response*` and deliver it after the current `Update` returns and before the next one (section 6.4).
6. **One `Update` per player** [M]. In turns 1–99, `Update` is called exactly once per alive non-observer player
   (25 in S1/AA100 = 8 majors + 16 CS + barbarians; 19 in S3 = 6 + 12 + 1); the `Update` that finds no active
   player calls `CanAdvanceTurn` → `doTurn` and processes the next turn's first player. The count follows from
   the DLL, not from timing, so a host loop "call `Update` until the turn ends" reproduces it. The real game waits
   ~105 ms between Updates (median gap in every turn) [M]: 2.6 s of a ~3 s early turn is idle pacing.
7. **Lua hook semantics are not what the host does today** (all four dispatchers return false without running
   listeners). Engine semantics measured in S4 (section 10.1). Hooks with listeners in AA100:
   `PlayerCityFounded` and `TeamTechResearched` (the two gameplay add-ins of plan M3.2), and `PlayerDoTurn`,
   `SetPopulation`, `UnitPromoted`, `CityConstructed`, `CityBoughtPlot` (EUI UI scripts; section 10.2). No test or
   accumulator event had a listener.
8. **Autosaves write the game only on turn 0 and every 10th turn** [M]: `AutoSave` is called once per turn
   (`CvPlayer::setTurnActive(false)` for the first alive player) and twice on turn 0 (initial + post-turn), but
   `CvDllGame::Write` & co. run only for the turn-0 calls and on turns 10, 20, … 90 (AA100), i.e.
   `UserSettings.ini [AutoSave] TurnsBetweenAutosave = 10`. The engine writes synchronously inside `AutoSave`,
   on the game-core thread. `WriteReplay` never appears. Side effects of `Write` are listed in
   headless-dll-additions.md ("Exe-side consequences"); all are normalised in the digest, so the host may skip
   autosaves.
9. **Arming autoplay after "continue" (S1) differs at the boundary from arming before it (AA100)** [M]. In S1 the
   human player 0 is active when autoplay is armed; at the end of turn 0 the exe serializes the whole game on the
   UI thread (`SetTimeStr`, `PreGame::write`, `Game::Write`, `Map::Write`, teams, players,
   `WriteSupportingClassData`), copies the sync RNG (`CvDllRandom::CopyFrom/Write/Read`), calls
   `RandomNumberGeneratorSyncCheck(8, rng, true)` and clears the network deltas, then the core thread advances the
   turn. AA100 (vpbench calls setup-info → arm-autoplay → continue) has none of this. Use the AA100 order for
   reference runs and in the host.
10. **The LoadScreen pauses the game** [S]: `LoadScreen.lua` calls `Game.SetPausePlayer(activePlayer)` when the
    game is initialised and `Game.SetPausePlayer(-1)` on "continue". The first Updates (4 in AA100, 5 in S1) run
    while paused and do nothing; the initial autosave (`AutoSave(true,false)`, `CvGame::update` at turn slice 0)
    happens in the first unpaused Update. These are Lua calls, invisible in the trace; the host must replicate
    them (or start unpaused and accept that `m_iTurnSlice` differs, which is normalised).
11. **Some UI calls write serialized state** [S]: `SelectSettler` at game start increments the settler's
    `m_iEverSelectedCount`; the player options of item 4 are applied by the loopback; UI Lua's by-name
    `GetGameOption` stores defaults in pregame (normalised). The host must make the first two calls.

## 2. Threads and locks

| Thread (S1 / AA100) | Role | Calls [M] |
|---|---|---|
| 24012 / 17152 | main/UI thread | everything in boot, front end, new game, shutdown; in the turn loop: rendering getters, `UpdateTestEndTurn` (human turn only), `UnitIsMoving`, `Map::UpdateLayout`, Lua object pushes, net-message delivery (`Response*`), the S1 end-of-turn-0 save, tuner/UI Lua |
| 29152 / 16156 | game core | only `CvDllGame::Update` at depth 0 (plus wrapper `Destroy`); everything else it does is nested: AI, autosave `Write`s, hooks, `send*` |
| others (AA100: 7 threads, 120–377 lines each) | engine render workers | only terrain/feature/plot getters (`GetEffectTypeTag`, `GetFeatureType`, …) |

- `Update` runs on the core thread **while the UI thread keeps calling the DLL** [M]: AA100/S1 show UI-thread
  inbound calls that start inside an `Update` (section 5 of the phase report): accessors (`GetDefineSTRING`,
  unit art, `GetActiveTeam`, `IsFinalInitialized`, …; 52,415 logged lines in AA100) and `PushCvUnitInstance` from
  UI Lua. No state-changing inbound call overlapped an `Update` in the logged lines; `UnitIsMoving`,
  `UpdateLayout` and net responses run between Updates.
- Lock calls [M]: core thread `GetGameCoreLock`/`ReleaseGameCoreLock` balanced; `HasGameCoreLock` true on the
  core thread every time; on the UI thread true 50×, false 3× (S1) / 13× (AA100), the false ones when UI Lua
  triggers a `GameEvents` test (`PlayerCanFoundPantheon` via `CallTestAll`) outside any wrapper.
- `IsGameCoreThread` [M]: UI thread false, core thread true. `IsGameCoreExecuting` is only reached from
  `CvRandom::get` on a non-core thread [S] and was false every time it was logged (map generation on the UI
  thread; twice in AA100 after autoplay ended).
- UI-thread inbound calls with side effects that the host can drop in autoplay [S]:
  `UnitIsMoving` (sets a static turn slice used only by turn timers and human auto-end-turn),
  `Map::UpdateLayout` / `Plot::UpdateLayout` / `Map::UpdateDeferredFog` (visual flags, not serialized),
  `SetBusyUnitUpdatesRemaining(600)` (human end-turn), `EndTurnTimer*` (turn timer), `PushCv*Instance` (Lua
  wrappers), `GetGameOption(name)` from UI Lua (stores the database default in pregame's option list; normalised
  in the digest), `CvDllRandom::Get` on the exe's own RNG objects (not a game RNG).
- **UI Lua can still change state through Lua calls the trace cannot see** (for example a UI script calling a
  state-changing `Game`/`Player` method). The hook listeners seen are UI-only (section 10.2); a digest A/A with
  and without the EUI UI scripts would prove it.

## 3. Phase A: process start and DLL load (boot)

In the real game the vanilla `CvGameCore_Expansion2.dll` serves the main menu; activating the mods unloads it and
loads VP's DLL from the mod folder [I], so the trace (and this phase) starts inside the "activate mods" call
(which began at t ≈ −3.6 s on the trace clock), with the mods' database already built by the exe [M][I].

Inbound order (UI thread) [M]:

| t (s) | Call | Notes |
|---:|---|---|
| 0.030 | `DllGetGameContext()` | |
| 0.031 | `SetDLLIFace(engine)` → true | the trace build wraps the interface here |
| 0.031 | `SetGameDatabase(db)` | |
| 0.031 | `Init()` | OUT `InitGlobals`, `GetCacheFolderPath` |
| 0.050 | `GetPreGame` → `setVersionString("403694 FINAL_RELEASE")`, `GetDLLGUID`, `GetDLLVersion` ×2, `versionString` | |
| 0.050 | `SetLogging(true)`, `SetAILogging(true)`, `SetAIPerfLogging(true)`, `SetBuilderAILogging(true)`, `SetPlayerAndCityAILogSplit(true)`, `SetAllowRClickMovementWhileScrolling(false)`, `SetOutOfSyncDebuggingEnabled(false)` | logging flags come from `config.ini`; they change only logs |
| 0.050 | `GetGame` → `GetGameDeals` → `CreateDeal()` → 0 | the exe's scratch deal (engine-side) |
| 0.050 | `QueryInterface(ICvGameContext2)`, `SetEngineUserInterface(ui)` | |
| 0.050 | pregame reset: `setGameMode(-1)`, `setGameType(0,0)`, `resetSlots`, `resetGame`, `resetPlayers`, `loadFromIni(ini)` | **`loadFromIni` sets both seeds to `timeGetTime()`** (config seeds are 0) [S] |
| 0.051 | `setDLCAllowed(guid,true)` ×12, then the exe re-applies the last session's setup: `setGameMode(-1)`, `setGameType(0,0)`, `setActivePlayer(-1)`, `setGameSpeed(1)`, `setEra(0)`, `setWorldSize(4,true)`, `setMaxTurns(0)`, `setMapScriptName(<MODS>\(2) Vox Populi\Mapscripts/Continental_Drift.lua)`, `SetLoadFileName("",0)`, `setInternetGame(false)`, `setNumMinorCivs(-1)`, `setTurnTimer(1)`, `SetGameOption(QUICK_COMBAT,1)`, `SetGameOption(QUICK_MOVEMENT,1)`, `setPitBossTurnTime(0)`, 64 × `setTurnNotify*`, and per slot 0–63 the block `SetDLCAvailable, setLeaderName, setCivilizationDescription/ShortDescription/Adjective/Password, setEmailAddress, setNickname, setWhiteFlag, setHandicap, setCivilization, setTeamType(p,p), setLeaderHead(p,−1), setPlayerColor(p,−1), setSlotClaim, setSlotStatus, setPlayable, setMinorCiv, setNetID, setReady` (slot 0: handicap 5, civ 20 = Mongolia with key and package id; slots 1–5 as logged: handicap 8, civ −1) | the user's last setup [I]; overwritten or reset by the setup below |
| 0.062 | `GetDatabaseLoadUtility` → `FlushGameDatabaseData()` → true, `PerformDatabasePostProcessing()` → true | |
| 0.083 | `CacheGameDatabaseData()` → true (4.48 s) | OUT `ShouldValidateGameDatabase` → **true**; `GetAudioTagIndex(tag,5)` ×13 (soundscape ids: 5,4,8,3,1,6,0,−1); `GetScriptSystem`, `CreateLuaThread("VP_LUAAPI")` → nested IN `RegisterScriptLibraries(L)` → `FreeLuaThread` |

The host (M1) does `SetDLLIFace`, `SetEngineUserInterface`, `SetLogging(false)`, `SetGameDatabase`, `Init`,
`PerformDatabasePostProcessing`, `CacheGameDatabaseData` (0.98 s, `ShouldValidateGameDatabase` → false),
`RegisterScriptLibraries`. Differences that matter: none for state [I] (the validation pass only logs; audio ids
are stored in info objects and read only by UI getters [S]). For exactness: call `FlushGameDatabaseData` before
post-processing, answer `ShouldValidateGameDatabase` true, and reproduce the pregame reset + `loadFromIni` (or
set the seeds explicitly, finding 1).

## 4. Phase B: front end and mod activation

### 4.1 Measured [M]

After the DLL load the exe rebuilds the UI contexts (6.3 s without DLL calls), then (t 10.87):
`clearDLCAllowed()`, `setDLCAllowed(guid,true)` ×11; the setup screen reads pregame and calls
`GetWorldBuilderMapLoader` → `GetMapPreview(path)` ×6 (map previews), `loadWBScenario` ×3.

vpbench's pregame Lua (`work/headless/gamespec.py`; `PreGame.*` in the `ModsSinglePlayer` context) then calls,
in this order (S1, t 12.212): `setRandomWorldSize(false)`, `setWorldSize(3,true)`, `setNumMinorCivs(16)`,
`setLoadWBScenario(false)`, `setRandomMapScript(false)`, `setMapScriptName("Assets\\Maps\\Continents.lua")`,
`setGameSpeed(2)`, `setEra(0)`, `setVictory(0..4,true)`, `setMaxTurns(0)`, `ResetGameOptions()`,
`ResetMapOptions()`. S3/S4 the same with `setWorldSize(2,true)`, `setNumMinorCivs(12)`,
`setMapScriptName("Assets\\Maps\\Earth_Small.Civ5Map")`, `setGameSpeed(3)`.

The front-end Lua context has `PreGame` but no `Game`, `Map` or `Players` (S3 probe:
`{"Game":"nil","Map":"nil","Players":"nil","PreGame":"table"}`), so the DLL's Lua libraries are not
registered there and `Game.SetHeadlessPendingSeeds` cannot be used before the start [M].

### 4.2 Reconstructed from the SUM counts (calls beyond the cap) [M][I]

| Method | Total in turn 0 | Logged in boot | Late calls | Who |
|---|---:|---:|---:|---|
| `setCivilization` | 86 | 64 | 22 | spec slots 0–21 (`civ = -1` in S1) |
| `setTeamType` | 86 | 64 | 22 | spec slots 0–21 (`team = slot`) |
| `setSlotStatus` | 87 | 64 | 23 | spec slots 0–21 (3 = taken for 0, 1 = computer, 2 = closed) + 1 later |
| `setHandicap` | 65 | 64 | 1 | spec difficulty for slot 0 (5) |
| `setLeaderHead` | 72 | 64 | 8 | the exe's leader choice for the 8 majors (below) |
| `setSlotClaim`, `setLeaderName`, `setCivilizationDescription/ShortDescription/Adjective` | 65 | 64 | 1 | slot 0 |
| `setReady` | 193 | 192 | 1 | slot 0 |

The exact values are in `calls/00003-pregame-setup.json` and in the `HHPREGAME` snapshot at `gameInit`
(`HeadlessDigest.log`), which is what the host must reproduce in pregame.

## 5. Phase C: new game

### 5.1 Inbound order (UI thread), Lua map script (S1) [M]

| t (s) | Call (args) | What it does / OUT calls inside |
|---:|---|---|
| 12.263 | `setPersistSettings(false)` | start request processed on a later frame than `Events.SerialEventStartGame` |
| 12.272 | `GetGame` (lock), `IsGameMultiPlayer` → false, `GetPersistSettings`, `randomMapScript`, `gameType`, `slotStatus(0)` → 3 | |
| 12.273 | `VerifyHandicap(0)`, `setActivePlayer(0)` | |
| 12.273 | `GetDLCAllowed` (iterate 11) | |
| 12.273 | `CreateNetInitInfo()` → 0, `NetInitInfo::Write(stream)` → true, `GetDebugString`, `DestroyNetInitInfo(0)` | snapshot of pregame incl. both seeds |
| 12.273 | `CreateNetInitInfo()` → 1, `NetInitInfo::Read(stream)` → true, `Commit()` → true | `Commit` re-applies pregame: OUT `GetEvaluatedMapScriptPath("Assets\\Maps\\Continents.lua", buf, 1040)` → true, then `setMapScriptName(buf)`, `setRandomMapScript`, `setTransferredMap(false)`, `setLoadFileName`, `setMapNoPlayers`, `setWorldSize(size,false)`, climate, sea level, era, calendar, game turn, `setGameStarted(false)`, speed, turn timer, game name, **both seeds**, victories, … [S]. The pregame snapshot shows the script name unchanged, so the engine copied the relative path [M][I]. |
| 12.273 | `canReadyLocalPlayer`, `loadFileName`, `GetWorldInfo` → `GetDefaultPlayers` → 8, per-slot reads of slot 0, `DestroyNetInitInfo(1)` | |
| 12.273 | `setWorldSize(3,false)`, `setClimate(0)`, `setSeaLevel(1)`, `setEra(0)` | the exe resolves random world settings |
| 12.274 | `GetWorldBuilderMapLoader`, `CacheGameDatabaseData()` → true (no-op, 0 µs) | |
| 12.274 | `CreateRandomNumberGenerator()` → 0, `Random::Init(syncRandomSeed()=139543027)`, reads `randomWorldSize/worldSize/climate/seaLevel/era`, `DestroyRandomNumberGenerator(0)` | exe RNG for "random" world options; no draws when nothing is random |
| 12.274 | `isWBMapScript` → false, **`closeInactiveSlots()`** | `setGameStarted(true)` → `onGameStarted` picks random civs and city-states from `mapRandomSeed()` (still the clock seed) [S]; open slots → closed; OUT `BeginSendBundle`, `sendPlayerInfo(0..63)` (each reads the slot back), `EndSendBundle` |
| 12.275 | `GetGameAsynch` → `TunerEverConnected` (OUT `TunerEverConnected` → true) | |
| 12.275 | `GetGame` (lock) → **`Init(5)`** (arg = handicap of the active player) | `CvGame::init`: seed pinning and the `HHSEEDS`/`HHPREGAME`/`HHRNG gameInit` lines (OUT `GetCacheFolderPath` ×2), reset, RNG init (7 ms) |
| 12.282 | `isWBMapScript`, `CacheGameDatabaseData()` → true | |
| 12.282 | `CreateRandomNumberGenerator()` → 1, `Random::Init(syncRandomSeed() = 2002)` | **leader choice by the exe**: `GetNumPlayableCivilizationInfos` 43, `GetNumAIPlayableCivilizationInfos` 43, `GetNumCivilizationInfos` 45, `IsPlayable/IsAIPlayable` for all 45, then for each of the 8 majors `GetNumLeaderHeadInfos` + `IsLeaders(0..43)` + `Random::Get(10000,"Choosing Leader")` (78, 6570, …, 7251) and a capped `setLeaderHead` [M][I]. Every VP civ has one leader, so any formula gives the same leader; the draws use the exe's own RNG object, not a game RNG |
| 12.283 | `GetNumMinorCivInfos` 120, `GetMinorCivInfo` (all), `numMinorCivs`, `minorCivType(22..37)` → (57, …), `DestroyRandomNumberGenerator(1)` | reads the city-states `onGameStarted` chose |
| 12.283 | `GetGame` (lock) → **`Init2()`** → true (2.19 s) | see 5.2 |
| 14.472 | `LaterInit()` for players 0–63 (OUT `IsLoadedGame` → false for each alive player) | `CvPlayer::gameStartInit` |
| 14.477 | `GetMap` → `NumPlots` 4160 → `InitTacticalAnalysisMap(4160)` | no-op in VP [S] |
| 14.714 | `RegisterScriptLibraries(L_InGame)` | the in-game Lua state gets `Game`, `Players`, `Map`, … |
| 14.772 | `SetGameOption("GAMEOPTION_DYNAMIC_TURNS",0)`, `("…SIMULTANEOUS_TURNS",0)`, `("…PITBOSS",0)` | adds explicit entries to pregame's option list (same value as default) |
| 17.068–17.17 | UI Lua: `HasReceivedTurnComplete(p)` ×8, `IsPlayerConnected(0)` → true, `GetTutorialLevel` → −1, `ProposedDealExists`, … | read-only |
| 17.761, 17.879 | `EndTurnTimerSemaphoreDecrement()` ×2, ×7 | turn-timer counter clamps at 0 |
| 17.856 | **`SetGraphicsInitialized(true)`** | from here on plot/unit/city graphics setup runs inside the DLL (OUT `Gameplay*`) |
| 17.857 | `SetupGraphical()` for players 0–63 (nested `Map::UpdateDeferredFog`), `Plot::UpdateLayout(false)` ×29 | visual; layout flags are not serialized [S] |
| 18.311 | **`DoGameStarted()`** | map validation, feature "clearable" flags, building caches [S] (state-relevant) |
| 18.311 | `NetMessageStaticsReset()`, **`SendPlayerOptions(true)`** | OUT `BeginSendBundle`, 12 × (`getPlayerOption(hash)`, `sendPlayerOption(hash,v)`), `EndSendBundle` |
| 18.311 | `NetworkSyncronization::Clear{City,Random,Player,Plot,Unit}Deltas()` | MP sync bookkeeping |
| 18.311 | `NetMessageHandler::ResponsePlayerOption(0, hash, v)` ×12 | loopback delivery, same frame |
| 18.315 | **`SelectSettler()`** | OUT `clearSelectedCities`, `ClearSelectionList`, `InsertIntoSelectionList(unit,…)`, `GameplayMinimapUnitSelect`, …; also increments the settler's serialized `m_iEverSelectedCount` [S] |
| 18.342 | `RebroadcastNotifications()` | |
| 19.238 | first **`Update()`** on the core thread | phase D |

`.Civ5Map` (S3) differs only here [M]: after the exe's random-world RNG: `WorldBuilderMapLoader::Preload(L"Assets\\Maps\\Earth_Small.Civ5Map", false)` → true,
`setMapNoPlayers(true)`, `setWorldSize(2,false)`, then `closeInactiveSlots` as above; inside `Init2` the map comes
from the loader (OUT `DoMapSetup(2772)`, `CreateLuaThread("WorldBuilderMapLoader")`,
`LoadFile(L,"WorldBuilderRandomItems.lua")`, `CallFunction(L,0,0)` ×2, `FreeLuaThread`; the DLL calls
`lua_cpcall` itself for `AddRandomItems`, `LoadModData`, `RunPostProcessScript` [S]). No map-script `LoadFile`.

### 5.2 Inside `Init2` (Lua map script) [M]

`InitPlayers` (turns every closed major slot into an `SS_OBSERVER` slot with an unused civ and its first leader
[S]) → OUT `GetLengthSelectionList` → 0, `setCycleSelectionCounter(1)`, `PublishActivePlayerTurnStart`,
`openSlot(p)` for 25 slots → `InitMap`: `CreateLuaThread("Map Script")`,
`LoadFile(L, "Assets\\Maps\\Continents.lua")` → true, `CallCFunction(L, …)` → true (`GetMapInitData` [S][I]),
`DoMapSetup(4160)` (from `CvMap::init` [I]), `CallCFunction(L, …)` (`GenerateRandomMap` [I]) → nested
`CallFunction(L,0,0)` (the script's `GenerateMap`, 2 s; inside it the map RNG via `Map.Rand`:
`IsGameCoreThread` → false, `IsGameCoreExecuting` → false per draw; `HasGameCoreLock` → true;
`GameplayYieldMightHaveChanged`, `GameplayFeatureChanged`), `CallCFunction(L, …)` (`GetGameInitialItemsOverrides` [I]),
`FreeLuaThread` → `initDiplomacy` (OUT `GameplayMetTeam`, `CallHook("TeamMeet",[t,t])`, barbarians
`CallHook("DeclareWar",[63,t])`, `GameplayWarStateChanged`) → `setInitialItems` (`GameplayTechAcquired`,
units: `SetDontShowPopups(true/false)`, `IsUnitSelected` → false, `CallTestAll("PlayerCanTrain"/"CanHavePromotion")`
→ false/false, …) → `setFinalInitialized(true)`.

The `CallCFunction` function pointers are DLL addresses: the host just runs them on the given thread (protected
call) and returns true. `LoadFile` resolves the relative path through the VFS (`include` precedence, section 10.3).

### 5.3 OUT answers the host must give in this phase

| Method | Answer |
|---|---|
| `GetEvaluatedMapScriptPath(in, out, 1040)` | copy `in` to `out`, return true (the stub returns false and leaves `out` empty, and `Commit` would set an empty map script name) |
| `IsGameCoreThread` / `IsGameCoreExecuting` / `HasGameCoreLock` | false / false / true (the exe holds the lock through the `CvDllGame` wrapper) |
| `TunerEverConnected` | true in the reference (FireTuner driver); stored in `m_bTunerEverConnected`, normalised, no gameplay use [S] |
| `IsLoadedGame` | false |
| `getPlayerOption` | section 1, item 4 |
| `GetLengthSelectionList` 0, `IsUnitSelected` false, `getSelectionPlot`/`GetHeadSelectedUnit` null | |
| `CallHook`/`CallTestAll` | engine semantics (section 10.1); no listeners exist yet for `TeamMeet`, `DeclareWar`, `PlayerCanTrain`, `CanHavePromotion` |

### 5.4 Host start sequence for M2 (recommended)

1. Boot as M1, plus `FlushGameDatabaseData` before post-processing; pregame reset (`setGameMode(-1)`,
   `setGameType(0,0)`, `resetSlots`, `resetGame`, `resetPlayers`) and either `loadFromIni` or explicit
   `setMapRandomSeed/setSyncRandomSeed` with the reference's `exeMap/exeSync`.
2. Setup from the spec in the vpbench order (4.1), plus the slot setters of 4.2 (from `HHPREGAME` of the
   reference); `ResetGameOptions`, `ResetMapOptions`; map options and game options exactly as the setup screen
   leaves them (unset unless the spec sets them).
3. Start: `setPersistSettings(false)`, `VerifyHandicap(0)`, `setActivePlayer(0)`, NetInitInfo round trip
   (`Create/Write/Destroy`, `Create/Read/Commit/Destroy`), `setWorldSize(size,false)`, `setClimate`,
   `setSeaLevel`, `setEra`; for `.Civ5Map`: `Preload(path,false)`, `setMapNoPlayers(true)`,
   `setWorldSize(size,false)`; `closeInactiveSlots()`.
4. `Init(handicap of slot 0)` (the DLL pins the spec seeds from `HeadlessSeeds.txt`, or the host sets the pregame
   seeds to the spec values right before `Init`; both are equivalent [S]).
5. Leader choice for majors with `leaderHead == -1` (one leader per civ in VP: take the civ's leader) and
   `setLeaderHead`.
6. `Init2()`; `LaterInit()` for players 0–63; `RegisterScriptLibraries` on the gameplay Lua state;
   `SetGameOption` DYNAMIC_TURNS/SIMULTANEOUS_TURNS/PITBOSS = 0.
7. `SetGraphicsInitialized(true)` (recommended for exactness; the visual paths then run and call no-op OUT
   methods; try false only after digest parity holds), `SetupGraphical()` for players 0–63.
8. `DoGameStarted()`, `NetMessageStaticsReset()`, `SendPlayerOptions(true)` + deliver the 12
   `ResponsePlayerOption(0, hash, value)`, then `SelectSettler()` (state: selection count of the settler).
9. `SetPausePlayer` is a Lua call in the real game: either call nothing (start unpaused) or mimic
   `Game.SetPausePlayer(0)`/`(-1)` through the DLL's Lua API; then arm autoplay (section 7) before the first
   `Update`.

## 6. Phase D: turn loop

### 6.1 How `Update` is driven [M]

- The core thread calls `CvDllGame::Update()` repeatedly; the UI thread never calls `Update`. Each call is
  wrapped in `GetGame`/`Destroy` (lock acquire/release by the wrapper).
- Calls per turn (exact, SUM): S1 turn 0: 75 (with the human turn and DoM screen), 1–4: 25; AA100 turn 0: 31,
  1–99: 25 every turn; S3: 30, 19. Median gap between Updates ≈ 105 ms in every turn.
- Each Update in turns ≥ 1 processes one player (`HasReceivedTurnComplete(p)` and the hooks name the player):
  1, 2, …, 7, 22, …, 37, 63. The last one (no active player left) calls `CanAdvanceTurn` → true → `doTurn`
  (OUT `GetCacheFolderPath` for the digest, `setNoSelectionListCycle(false)`, `DoTurn`, `doTurn`,
  `setCanEndTurn(false)`, `setHasMovedUnit(false)`, `GameplayTurnChanged(n)`, `PublishNewGameTurn(n)`) and then
  runs player 0's turn.
- Update durations (AA100): median 3–14 ms, the longest 0.1–1.6 s (the `doTurn` update and big AI turns).

### 6.2 Per-update OUT pattern (turns ≥ 1) [M]

`CallHook("GameCoreUpdateBegin")` → `CallHook("PlayerPreAIUnitUpdate",[p])` → unit hooks
(`UnitGetSpecialExploreTarget`, `UnitSetXY`, nested `Map::UpdateDeferredFog`) → `HasReceivedTurnComplete(p)` →
false → `CallHook("GatherPerTurnReplayStats",[p+1])`, `CallTestAll("EventCanTake"/"EventCanActivate")`,
`CallHook("PlayerDoTurn",[p+1])` (next player's turn start) → `CallHook("GameCoreUpdateEnd")`, plus the
`CallTestAll`/`CallAccumulator` storm of AI decisions (`PlayerCanTrain`, `PlayerCanConstruct`,
`CityCanTrain`, `GetReligionToSpread`, …; 363,068 `CallTestAll` in 100 turns) and the visual notifications.
`FlushTurnReminders` at the end of each unpaused update.

### 6.3 Turn-end and AI-processed signalling [M][S]

| Call | When | Answer / effect |
|---|---|---|
| `CanAdvanceTurn` | once per turn, in the update that finds no active player | true |
| `SendAICivsProcessed` | `SetLastTurnAICivsProcessed`, when no AI player is left to process while a human is active: S1 turn 0 (autoplay armed late) and turn 5 (autoplay end); AA100 only turn 100 (autoplay end); **never during autoplay** | sets the engine flag |
| `allAICivsProcessedThisTurn` | after `SendAICivsProcessed` and on every update while a human is active (S1 T0: 44, T5: 56; AA100 T100: 41; never in turns where all players are AI) | false in the same update as `SendAICivsProcessed`, true from the next update on |
| `sendPlayerInitialAIProcessed` | the first update that sees `allAICivsProcessedThisTurn` true | no response message |
| `HasReceivedTurnComplete(p)` | each player update, UI Lua | false always |
| `GetAutorun` | with `allAICivsProcessedThisTurn` | false |
| `sendTurnComplete` | never called | |

### 6.4 Net loopback (H5) [M]

| Sent (core thread, inside `Update`) | Delivered (UI thread, depth 0) | Delay |
|---|---|---|
| `sendPlayerOption(hash, v)` ×12 (in a `BeginSendBundle`/`EndSendBundle`) | `ResponsePlayerOption(activePlayer, hash, v)` ×12 | after the sending call returns (same frame, 0–2 ms) |
| `sendMinorPledgeProtection(major, minor, bProtect, bPledgeNowBroken)` | `ResponsePledgeMinorProtection(same args)` | 25–100 ms, before the next `Update` |
| `sendMinorBullyGold(major, minor, gold)` | `ResponseMinorCivBullyGold(same args)` | same |
| `sendPlayerInfo(p)`, `sendPlayerInitialAIProcessed()` | nothing | |

Rule for the host: queue each `send*` that has a `Response*` with its arguments (and the active player for
player options) and deliver the queue in order after the current top-level inbound call returns (after the
`Update`, or after `SendPlayerOptions` at game start), always before the next `Update`. The full list of
`send*`/`Response*` pairs is in `ICvDLLUtility.h`/`CvDllNetMessageHandler.cpp`; only these three were exercised.

### 6.5 Autosaves [M]

`AutoSave(bInitial, bPostTurn)` from the core thread: `(true,false)` in the first unpaused update of turn 0,
`(false,true)` once per turn (first alive player's turn deactivation). The engine then synchronously calls
`SetTimeStr("",turn,true)`, `PreGame::write`, `Game::Write`, `Map::Write`, `Team::Write` ×64, `Player::Write` ×64,
`WriteSupportingClassData` — only at turn 0 (both calls) and when `turn % 10 == 0` (AA100: 10…90); otherwise only
`SetTimeStr`. No `WriteReplay`. The files are `AutoSave_Initial_<date>` and `AutoSave_Post_<date>` in
`ModdedSaves\single\auto` (a 50-turn VP autoplay game: all seven kept, `NumAutosavesKept = 5`). Host: no file at
these calls by default; `--autosave` and `--save-at` write real `.Civ5Save` files at them, into `<run>\saves`
([saves.md](saves.md)). The last turn's save comes after autoplay, not at these calls.

### 6.6 Inbound calls between updates [M]

UI thread, every few frames: `UnitIsMoving` (16,617 in AA100), `Map::UpdateLayout(false)`,
`PushCvCityInstance`/`PushCvUnitInstance` (UI Lua), rendering getters; at turn changes `RebroadcastNotifications`.
With a human active (S1 turn 0, turn 5): `UpdateTestEndTurn` every frame (954 in S1, none in AA100),
`EndTurnTimerReset`, `SetBusyUnitUpdatesRemaining(600)`, `UpdateSelectionList`. None is needed for autoplay [S]
(section 2); `UpdateTestEndTurn` matters only for human end-of-turn.

### 6.7 Callbacks the engine makes inside OUT calls [M]

Non-accessor inbound calls nested in an outbound call (S1, AA100):

| Inside | Callback | Count (AA100) | Host |
|---|---|---:|---|
| `GameplayUnitTeleported`, `GameplayUnitVisibility`, `GameplayUnitCreated`, `GameplayUnitEmbark`, `GameplayUnitDestroyed(InCombat)`, `GameplayCityCreated` | `CvDllMap::UpdateDeferredFog()` | 4,999 | not needed: it flushes the deferred-fog plot set (filled only when graphics are initialized, bounded by the plot count) and `CvPlot::updateFog` only notifies the engine (`GameplayFOWChanged`) [S] |
| `GameplayEraChanged` | `CvDllUnit::SetupGraphical()` | 8 | no |
| `AutoSave` | `SetTimeStr`, `PreGame::write`, `Game/Map/Team/Player::Write`, `WriteSupportingClassData` | 6.5 | no |
| `ICvUserInterface1::doTurn` | `CvDllPlayer::AddNotification(…, "Losing Gold!", …)` after `GetNumCities`/`GetGold`/`CalculateBaseNetGold` of each player | 1 | no for autoplay: `CvNotifications::Add` returns −1 for non-human players [S] |
| `GetAutorun` | `CvDllPreGame::autorun()` | — | the answer is the pregame flag (false) |
| `InsertIntoSelectionList`, `ClearSelectionList` | `ResetPathFinder` (no-op), `SelectionListIgnoreBuildingDefense`, `UpdateSymbolVisibility` (no-op) | few | no |
| `CreateLuaThread("VP_LUAAPI")` (first Lua use of the DLL) | `RegisterScriptLibraries(L)` | 1 | the `Map Script` and `WorldBuilderMapLoader` threads got no callback, so the libraries live in the shared state; the host registers them once in its single state |

## 7. Autoplay at the boundary

vpbench arms autoplay with InGame Lua (FireTuner, UI thread):
`Game.SetOption('GAMEOPTION_QUICK_COMBAT',true); Game.SetOption('GAMEOPTION_QUICK_MOVEMENT',true);
Game.SetAIAutoPlay(turns, 0)` (`work/vpbench/vpbench.py`). Visible effects [M] (AA100 t 18.917, S1 t 24.940):
`SetEndTurnBlockingChanged`, `UpdateEndTurn`, `GameplayActivePlayerChanged(8)`, `BeginSendBundle`,
12 × `getPlayerOption`/`sendPlayerOption`, `EndSendBundle`, `GameplayFOWChanged` ×50+, `PublishActivePlayer(8,0)`,
`setCanEndTurn(false)`, `clearSelectedCities`, `ClearSelectionList`, then the loopback
`ResponsePlayerOption(8, hash, v)` ×12. The observer is slot 8: `CvGame::InitPlayers` (in `Init2`) turns every
closed major slot into an `SS_OBSERVER` slot with an unused civ, and `setAIAutoPlay` takes the first observer
slot; slot 0 becomes an AI (`setSlotStatus(0, SS_COMPUTER)`, `VerifyHandicap(0)`, `CvPlayer::init(8)`,
`SlotStateChange`, production/research/policy choices for player 0) [S].

When the counter reaches 0 (start of the target turn, inside `doTurn`, core thread) [M]: `getPlayerOption`/
`sendPlayerOption` ×12, `PublishActivePlayer(0,8)`, `setCanEndTurn(false)`, `clearSelectedCities`,
`ClearSelectionList`, `PublishNewGameTurn(n)`, then player 0's human turn: `IsPolicyNotificationSeen` → false,
`SendAICivsProcessed`, `allAICivsProcessedThisTurn` → false then true, `sendPlayerInitialAIProcessed`; on the UI
thread `ResponsePlayerOption(0, …)` ×12, `EndTurnTimerReset`, `UpdateSelectionList` (→ `DoAutoUnitCycle` → true,
`getOriginalPlotIndex` → −1, `InsertIntoSelectionList`, `lookAtSelectionPlot`), `SetBusyUnitUpdatesRemaining(600)`,
`UpdateTestEndTurn` per frame; `GetHeadSelectedUnit` becomes non-null on the UI thread. The game then idles in the
human turn (S1: 55 more updates, AA100: 40).

Host: run the same Lua (the DLL's `Game.SetOption` → `CvGame::setOption` and `Game.SetAIAutoPlay` →
`CvGame::setAIAutoPlay`; quick combat and quick movement are game options, i.e. serialized state, and
`ResetGameOptions` in the front end cleared the boot-time values) before the first `Update`, deliver the 12
responses, and stop when the turn counter reaches the target; do not emulate the human turn.

## 8. Shutdown [M]

UI thread, after the last `Update`: `setGameStarted(false)` (t +0.6 s), `resetPlayers()`,
`NetMessageStaticsReset()`, `GetGameDeals` → `DestroyDeal(0)`, `CvDllGameContext::Uninit()` (OUT `GetDone` → true
×N while units are destroyed, `UninitGlobals`), `SetDLLIFace(null)` → false (OUT `ICvEngineUtility4::Destroy`).
No `CvDllGame::Uninit`. The host's order (`Uninit`, `SetGameDatabase(NULL)`, `SetDLLIFace(NULL)`, `FreeLibrary`)
is compatible; add `setGameStarted(false)` before `Uninit` (`CvDllGame::Uninit` traps if the game is still
started [S]) and answer `GetDone` true from `Uninit` on.

## 9. State-changing inbound calls (the exe drives these)

Getters are excluded; full generated list in appendix B. "Needed" = the host must make the call for parity.

| Call | Phase, thread | How often | Needed | Notes |
|---|---|---|---|---|
| `SetDLLIFace`, `SetGameDatabase`, `Init`, `SetEngineUserInterface`, `Set*Logging` | boot, UI | once | yes | logging flags only affect logs |
| `FlushGameDatabaseData`, `PerformDatabasePostProcessing`, `CacheGameDatabaseData` | boot (+2 no-op `Cache` in new game) | once | yes | |
| `RegisterScriptLibraries(L)` | boot (VP_LUAAPI thread), new game (InGame state) | 2 | yes | every Lua state that runs gameplay Lua |
| pregame setters (`set*`, `reset*`, `loadFromIni`, `ResetGameOptions`, `ResetMapOptions`, `SetGameOption`, `setVictory`, `setDLCAllowed`, `clearDLCAllowed`) | boot, front end, UI | ~1,900 | yes (final pregame state) | must reproduce the reference `HHPREGAME` exactly; order matters where setters have side effects (`setWorldSize(…,true)` resets slots) |
| `setPersistSettings`, `VerifyHandicap`, `setActivePlayer` | new game | once | yes | |
| NetInitInfo `Write`/`Read`/`Commit` | new game | once | yes | `Commit` re-applies pregame (5.1) |
| `WorldBuilderMapLoader::Preload(path,false)`, `setMapNoPlayers(true)` | new game (`.Civ5Map`) | once | yes | |
| `closeInactiveSlots` | new game | once | yes | random civs/CS (finding 1) |
| `CvDllRandom::Init/Get` (exe RNG objects 0, 1) | new game | 2 / 8 | no | engine-side RNG; leaders: one per civ |
| `CvDllGame::Init(handicap)`, `Init2()` | new game | once | yes | |
| `CvDllPlayer::LaterInit` | new game | 64 | yes | |
| `InitTacticalAnalysisMap` | new game | once | no (no-op) | |
| `SetGameOption(DYNAMIC_TURNS/SIMULTANEOUS_TURNS/PITBOSS, 0)` | new game | 3 | yes [I] | pregame option list entries |
| `EndTurnTimerSemaphoreDecrement/Increment`, `EndTurnTimerReset` | new game, loop | 9–10, 1–2 | no | turn timer |
| `SetGraphicsInitialized(true)`, `CvDllPlayer::SetupGraphical`, `CvDllUnit::SetupGraphical`, `Plot/Map::UpdateLayout`, `Map::UpdateDeferredFog` | new game, loop | once / 64 / … | recommended (7 in 5.4) | visual paths; not serialized |
| `DoGameStarted` | new game | once | **yes** | caches used by rules |
| `NetMessageStaticsReset` | new game, shutdown | 2 | yes (cheap) | |
| `SendPlayerOptions(true)` + `ResponsePlayerOption` | new game, autoplay switches | 1 + 12 per switch | **yes** | finding 4 |
| `NetworkSyncronization::Clear*Deltas` | new game (+ S1 end of T0) | 5 | no [I] | MP sync |
| `SelectSettler` | new game | once | **yes** | `CvGame::selectUnit` increments the settler's `m_iEverSelectedCount`, which is serialized (unit data of player 0) [S] |
| `UpdateSelectionList`, `SelectionListIgnoreBuildingDefense`, `ResetPathFinder` | autoplay end, nested in selection calls | 1–10 | no | `UpdateSelectionList` selects (and counts) a unit of the returning human, after the target turn; `ResetPathFinder` is a no-op [S] |
| `RebroadcastNotifications` | new game, active-player changes | 3 | no | |
| `CvDllGame::Update` | loop, core | one per player per turn | **yes** | 6.1 |
| `ResponsePledgeMinorProtection`, `ResponseMinorCivBullyGold` | loop, UI | 19, 3 per 100 turns | **yes** | 6.4 |
| `UnitIsMoving`, `UpdateTestEndTurn`, `SetBusyUnitUpdatesRemaining` | loop, UI | per frame | no (autoplay) | human end-turn and turn timer only |
| `SetTimeStr`, `PreGame::write`, `Game/Map/Team/Player::Write`, `WriteSupportingClassData` | loop (autosave on core; S1 T0 and manual saves on UI) | 2 + 1 per 10 turns | no | side effects normalised |
| `CvDllRandom::CopyFrom/Write/Read`, `RandomNumberGeneratorSyncCheck` | S1 end of T0 only | once | no | finding 9 |
| `CvDllPlayer::AddNotification` | loop, core (AA100) | 1 | no | nested in a DLL call |
| `setGameStarted(false)`, `resetPlayers`, `DestroyDeal`, `CvDllGameContext::Uninit` | shutdown | once | yes (order) | |

## 10. Lua

### 10.1 `GameEvents` dispatch (S4 hook probe, `probe-2-hook-probe-result.json`) [M]

`GameEvents` is a table; `GameEvents.X` for any name returns an event object with `Add`, `Remove`, `RemoveAll`,
`Count`, `Call`; custom names work. Listeners run **all, in registration order, no short-circuit**, each with the
pushed arguments. A listener that raises is logged to `Lua.log` and does not vote. `handled` (the return value of
`Call*`) is true iff at least one listener is registered (also if all raise); a never-indexed name → false.

| Dispatcher | `value` (out) | Measured cases |
|---|---|---|
| `CallHook` | always false, even with init true and no listeners | |
| `CallTestAll` | no listeners: unchanged; else true unless some listener returned a falsy value (false, nil); numbers incl. 0 are true; raising = no vote | `true,false`→false; `true,nil`→false; `3,4`→true; `error`→true; `false,error`→false; init true + `nil` → false |
| `CallTestAny` | no listeners: unchanged; else true iff some listener returned a truthy value | `nil,false`→false; `error`→false; `true,error`→true; init true + `false` → false |
| `CallAccumulator(int)` | init + sum of numeric results; non-numbers and raising listeners add 0 | `3,4`→7; `3,true`→3; `3,error`→3; init 11 + `nil` → 11 |

In-game calls [M]: all `CallTestAll`/`CallTestAny`/`CallAccumulator` in S1/S3/AA100 returned `handled=false`
(no listeners for any test or accumulator event); `CallHook` had listeners for `PlayerDoTurn`, `SetPopulation`,
`UnitPromoted`, `PlayerCityFounded`, `TeamTechResearched`, `CityConstructed`, `CityBoughtPlot`.
`MOD_API_DISABLE_LUA_HOOKS` is off. The DLL may defer hooks (`LuaSupport::DeferredHookScope`) and replays them
with `Count`, `GetType(i)`, `Get*`; it detects a 0- or 1-based index from `GetType(0)` [S]. The engine's `Push`
returns the 0-based index of the pushed value [M] (`Push(0)` → 0, then 1, 2, 3); the host returns the 1-based
count today.

### 10.2 Listener sources (AA100 hooks with `handled=true`) [M][S]

| Event | Registered by | Gameplay? |
|---|---|---|
| `PlayerCityFounded` | `CBP_IncaFunctions.lua` (plan appendix C) | yes |
| `TeamTechResearched` | `GlobalCityBombardRange.lua` | yes |
| `PlayerDoTurn` | EUI `DiploCorner.lua` (`UpdateForceResyncButton`) | no (UI) |
| `SetPopulation` | EUI `NotificationPanel.lua` | no |
| `UnitPromoted` | EUI `UnitFlagManager.lua` | no |
| `CityConstructed`, `CityBoughtPlot` | EUI `NotificationPanel.lua`, `ImprovedTechTree/TechTree.lua` | no |

The host runs every activated mod's InGameUIAddin (10.6), so these listeners are registered as in the real game.
`CallHook`'s return value is ignored by the DLL [S], so `handled` does not matter either way.

### 10.3 `include` and files [M]

One file per name, the later-activated mod wins (VP's `AssignStartingPlots.lua` over CP's), mods over DLC and
base; `include` returns a table of the included paths (empty for a missing name), with or without `.lua`; no
`debug` library (probe `hh-probe-include-20261025a`). Map scripts are loaded by relative path
(`Assets\Maps\Continents.lua`, or `<MODS>\(2) Vox Populi\Mapscripts/...` for mod scripts) through the VFS.

### 10.4 Lua states and contexts

| State | Created | DLL libraries | Used for |
|---|---|---|---|
| `VP_LUAAPI` thread | boot, inside `CacheGameDatabaseData` (`CreateLuaThread`, nested `RegisterScriptLibraries`, `FreeLuaThread`) | yes | VP's Lua API self-test ("Embedded script ran without errors") |
| front-end contexts (`ModsSinglePlayer`, …) | exe | no (`Game`, `Map`, `Players` nil) | setup |
| `Map Script` thread | `Init2` | (thread of the main state) | map generation |
| `WorldBuilderMapLoader` thread | `Init2` (`.Civ5Map`) | | random items, mod data, post-process script |
| InGame state | after `Init2` (`RegisterScriptLibraries`, t 14.714) | yes | UI contexts and gameplay add-ins, tuner |

`Events.*` and `LuaEvents.*` are engine/UI tables; the DLL itself fires no `Events` across the boundary (all
UI notifications are the OUT `Gameplay*`/`Publish*` methods). The host may drop them; Lua that calls
`Events.X(...)` needs a table whose members accept any call (plan H7).

### 10.5 Library semantics that differ from stock Lua 5.1 [M]

- **`ipairs` visits index 0.** `ipairs(t)` returns the stock iterator, `t` and an initial control of `-1` when
  `t[0] ~= nil` (`false` counts as non-nil), else `0`; the iterator is the stock one (`f(t,i)` returns `i+1, t[i+1]`
  or nothing at nil; `f(t,nil)` raises "number expected"; the same function object for every call). So
  `{[0]=z,a,b}` gives 0,1,2; `{a,b}` 1,2; `{[0]=z}` 0; `{[0]=z,[2]=b}` 0; `{[-1]=n,[0]=z,a}` 0,1; `{[0]=false,a}` 0,1.
  Measured in the front-end contexts (runs `hh-m5-ipairs-probe`, `hh-m5-ipairs-probe2`, probes
  `work/headless/probes/ipairs-semantics.lua` and `ipairs-iterator.lua`) and confirmed in the Map Script thread:
  VP's `Continental_Drift.lua` stores its height grids from `maps[0]` and sums them with `ipairs(maps)`, so the real
  heights include grid 0 (probe 2, run `hh-m5-cdrift-probe2`). The host installs this `ipairs` in every Lua state it
  creates (`HHScriptSystem::InstallEngineIpairs`: the main state, so every thread, and the database builder's
  map-script registration state).
- **No `debug` library** in the front-end contexts and in the Map Script thread (`pcall(debug.getinfo)` fails there,
  probe 2). The boot-time `VP_LUAAPI` thread has it: its chunk reads `debug.getinfo` into a local and logs "Embedded
  script ran without errors!". The host keeps `debug` in `_G` until boot's `CacheGameDatabaseData` is done and then
  removes it (`HHScriptSystem::HideDebugLibrary`; `--no-lua-debug` hides it from the start).

### 10.6 UI contexts in the host (add-ins, profiles, probes)

The host has no UI screens. Lua that the game runs in UI contexts runs in **stub UI contexts**: a Lua thread of the
InGame state (`CreateLuaThread`) whose environment adds the UI-only globals (`HHLuaGlobals.cpp`, `kUIContextLua`):

- `ContextPtr`: a `LuaContext` control (`CTypeName`); `SetHide`/`IsHidden` keep the hidden flag, `IsHotLoad` is
  false, `GetID` is the context name, `SetUpdate`, `SetShowHideHandler`, `SetInputHandler` store the handler and the
  host never calls it, `BuildInstanceForControl` fills the instance table with stub controls; other methods
  (capitalised names) are stubs, other keys nil.
- `Controls`, `Mouse`, `Keys`, `KeyEvents`, `MouseEvents`, `PopupPriority`: a chainable stub (any member, any call;
  arithmetic gives 0, concatenation and `tostring` give '').
- `UI`, `Network`, `Steam`, `OptionsManager`, `Matchmaking`: every member returns nil (nothing selected, nothing
  sent), except `UI.RemoveNotification`, the engine's dismiss (`MayUserDismissNotification`, then
  `DismissNotification`, for a notification shown to the active player). `UI.IsLoadedGame` is true only in a game
  loaded from a save (`loadgame`).
  With `--reseed MAP:SYNC` the host gives the loaded game new seeds through the official interfaces before the game
  starts ([saves.md](saves.md), section 4).
- `UIManager` (1024x768, nothing modal), `ContentManager.IsActive` (true), grid/world conversions (0), `Path`.
- `Modding.OpenUserData`: an in-memory store (`GetValue`/`SetValue`, `Query` gives no rows), empty at the start of
  every run: user data lives in the user's cache, which a run must not depend on.
- `Modding.OpenSaveData`: the game's save data, `<run>\cache\Civ5SavedGameDatabase.db`, which the DLL creates for a
  new game, embeds in every save and restores from a loaded one (`savedb` in the digest). `GetValue`/`SetValue` use
  its `SimpleValues` table, `Query` is `DB.Query` on it ([saves.md](saves.md), section 5).
- `Events`, `LuaEvents`, `GameEvents`, `Game`, `Map`, `Players`, `Teams`, `GameInfo`: the shared ones. Listeners
  are recorded per context (run report `addins.listeners`).

**InGameUIAddins.** After the load screen, the host loads every activated mod's InGameUIAddin entry points in
activation order, one context each (`setup/HHAddins.cpp`): the entry's `.lua` (or the `.xml`'s `<LuaContext>`
children first, then its own script; the XML `Hidden` attribute sets the hidden flag), found next to the entry or in
the VFS. An error is logged with the file and line in the run report (`addins.contexts[].error`,
`luaErrors.list`) and does not stop the run. `--skip-addin NAME` (repeatable; the context's file name with or
without `.lua`/`.xml`, case-insensitive, a boot self-test check) leaves one out, `--no-addins` all of
them. Then the host fires `Events.SequenceGameInitComplete`; at CONTINUE (autoplay start) `Events.LoadScreenClose`.
No other UI event is fired: a listener for `SerialEventGameMessagePopup`, `ActivePlayerTurnStart` and the like
never runs in the host. Where such UI code changes the game, a UI emulation profile does it (docs/profiles.md).

**Profiles** run in their own hidden contexts at the hooks `load-screen` (after `Game.SetPausePlayer`, before the
T000 checkpoint), `notification-added` and `ui-frame` (each update frame of the turn loop) and `popup` (the frame
after the DLL queued a popup); docs/profiles.md.

**Lua probes** (`--lua-probe FILE`, repeatable) run, in order, in one visible context named `InGame` after the T000
checkpoint and before turn 0 is processed, the point where a tuner can first inject into the real game. They keep
their listeners for the whole game. `probes/fingerprint.lua` is the per-turn fingerprint (plan 9.3,
`python/civ5headless/fingerprint.py`).

## 11. Runtime facts [M]

`HHRUNTIME` on the core thread at the first `Update`: x87 control word 0x027F (53-bit, round to nearest, all
exceptions masked), MXCSR 0x1F80 (later 0x1FA0: the sticky precision flag), `timeBeginPeriod(1)` = 0. The
host must set the x87 word on the thread that runs `Init2` too: the map script runs on the UI thread in the
real game, whose control word was not recorded (open question 3).

## 12. Host today vs this contract (gaps for M2/M3)

| # | Item | Host today | Contract | Impact |
|---|---|---|---|---|
| G1 | `getPlayerOption` | false | WAIT_END_TURN, SAFE_AUTOMATION true | player sections differ |
| G2 | net loopback (`sendPlayerOption`, `sendMinor*`) | stubs, nothing delivered | deliver `Response*` after the call/Update | options, pledges, bullying lost |
| G3 | `HasGameCoreLock` | false | depth counter | different code paths (caches, yields, hook lock release) |
| G4 | `IsGameCoreThread` | true | false outside `Update` | pathfinder instance, trade caches |
| G5 | `CanAdvanceTurn` | stub false | true | turn never advances |
| G6 | `allAICivsProcessedThisTurn` / `SendAICivsProcessed` | stub false / nothing | flag semantics (6.3) | only reached while a human is active (late arming, after autoplay ends) |
| G7 | `GetEvaluatedMapScriptPath` | stub false, empty buffer | copy, true | empty map script after `Commit` |
| G8 | `CallHook`/`CallTestAll`/`CallTestAny`/`CallAccumulator` | return false, no listeners run | 10.1 | gameplay listeners never run |
| G9 | `ICvEngineScriptSystemArgs1::Push` return | 1-based | 0-based | only if the DLL uses it (deferral uses `GetType`) |
| G10 | `IsOptionNoRewardPopups` | true | false | wonder notifications for the active player (unreachable for the observer) |
| G11 | `ShouldValidateGameDatabase` | false | true | validation logs only |
| G12 | `TunerEverConnected` | false | true | normalised field |
| G13 | `GetAudioTagIndex` | −1 | engine ids | UI getters only |
| G14 | `IsPlayerConnected` | stub false | true | notifications |
| G15 | `GetDone` | false always | true from `Uninit` | entity removal at shutdown |
| G16 | pregame reset + `loadFromIni`/seeds, NetInitInfo round trip, `closeInactiveSlots`, leader choice, `LaterInit`, graphics init, `DoGameStarted`, `SendPlayerOptions`, `SelectSettler`, LoadScreen pause, autoplay Lua | M2 in progress | 5.4, 7 | start state |

## 13. Open questions and how to resolve them

1. **Late front-end setters and turn-0 hooks are capped** (4.2). Resolve: record with a higher cap for
   `CvDllPreGame::*` and `ICvEngineScriptSystem1::Call*`, or reset the per-method cap at `CvDllGame::Init` and at
   the first `Update` (a small `HHTrace` change), then rerun S1.
   **Resolved (bfa298d6b):** cap periods restart at `CvDllGame::Init` and at the first `Update` (TURN lines with
   `phase=`); re-recorded as `work/test-runs/hh-m0-s1c-continents` (game_probe order, 5 turns, dumps at T0 and T5).
2. **Random civs/CS** (finding 1): decided, the seeds file pins at `setGameStarted` (255e26ba2). Still to verify
   with two runs that the `HHPREGAME slot` lines match (the second AA100 pair does this).
   **Verified:** `hh-m0-aa100b-{a,b,c}` and `hh-m0-s1b-continents` got the same civs.
3. **x87/MXCSR of the UI thread during `Init2`** (map generation runs there): extend D4 to log the control word
   at `CvGame::init` too.
   **Resolved (bfa298d6b):** `HeadlessRuntime.log` now has `point=gameInit` from the thread that runs `CvGame::init`.
   In `hh-m0-s1c-continents` it is 0x027F (53-bit, nearest), MXCSR as on the core thread.
4. **`GetEvaluatedMapScriptPath` output** is not recorded; inferred from the pregame snapshot (unchanged
   relative path). Resolve: have the proxy format the out buffer, or log `CvPreGame::mapScriptName()` after
   `Commit` (the pregame snapshot at `gameInit` already shows it; check a mod map script such as
   Communitu, whose path is absolute in the user setup).
   **Resolved:** in `hh-m0-communitu` the `gameInit` snapshot shows the absolute mod path exactly as the setup set
   it (`...\MODS\(2) Vox Populi\Mapscripts/Communitu_79a.lua`, mixed slashes kept), so the engine returns the name
   unchanged for a mod map script too.
5. **Which player option is the sixth hash (−304873808)**: order suggests `PLAYEROPTION_SAFE_AUTOMATION`;
   confirm with the `ResponsePlayerOption` → `GetInfoTypeForHash` lines beyond the cap, or by computing the
   engine's hash of the type names.
6. **UI-thread Lua that changes state** cannot be seen at the boundary: run an A/A pair with and without the EUI
   UI scripts' listeners (or compare host vs reference digests once G1–G8 are fixed).
7. **`SetGraphicsInitialized` false in the host** may be faster: test digest parity with true vs false after M2.
8. **The S1 end-of-turn-0 UI save + `RandomNumberGeneratorSyncCheck`** (finding 9): confirm it is caused by
   arming autoplay during the human turn (repeat S1 with arming before "continue") and that it does not change the
   digest.
9. **More `send*`/`Response*` pairs** (diplomacy, city-state gifts, trade, espionage) appear only in longer or
   richer games: keep the trace build for a 250-turn reference once and grep `send`/`Response`.
10. **Autosave interval** comes from `UserSettings.ini`; a different user setting changes when `Write` runs. It
    is result-neutral by the side-effect audit, but the digest A/A should include a run with
    `TurnsBetweenAutosave = 1` to prove it.
11. **InGameUIAddin XML with child contexts:** the host loads the `<LuaContext>` children before the parent's own
    script (assumed; the game's order was not measured). No add-in of the reference set depends on it.

## 14. The run report (`<run>/host-report.json`)

Every `newgame` writes `host-report.json` (format `civ5headless-host-report/1`, plan 9.2), also when it fails:

| Key | Content |
|---|---|
| `status`, `error` | `passed` or `failed`, and why. |
| `host` | exe path and sha256, the git head and dirty flag of the build. |
| `command`, `spec`, `gameDir`, `gameDirSource` | what ran; the game folder and how it was found (`--game-dir`, or the Steam registry entries and library folders). |
| `dll` | path, sha256, version, whether it has the digest additions, the seed mode, `hostChoseCivs` (the host drew civilizations or city-states at random, section 15). |
| `minorCivs` | every city-state slot after `Init2`: slot, `id` and `type` (MinorCivilizations), `chosenBy` (`dll`: the DLL chose, VP; `spec`: the host applied the spec's pin; `host`: the host drew it) and, for a slot the spec pins (`minorCivs`), `pinnedId`, `pinnedType`, `match`; with `chosenBy`, `pinned`, `matching` and `allMatch` (null without pins) over all slots. |
| `database` | game and localization databases, mods database, MODS folder. |
| `options` | turns, `--heap-perturb`, `--frame-skew`, UI frame, logging, idle updates, `--no-mods`, `--no-profiles`, `--no-addins`, `--skip-addin`, `--lua-probe`. |
| `mods` | the activated mods (id, version, name, folder) in activation order. |
| `profiles` | folder, load errors, the selected profiles (reason, hook, calls, failures) and the others (reason). |
| `addins` | contexts run, failed, skipped; per context the mod, entry, parent, status, error, path and sha256; the event listeners each context registered. |
| `uiFiles` | UI files that are not base game or official DLC: add-ins, overrides of base UI files, other DLC UI (EUI). `known` (in `profiles/known-ui-files.json` or covered by a selected profile) or `unknown`, with path and sha256 (docs/profiles.md). |
| `stubCalls` | every engine method the host only stubs that the DLL called: count, first phase, and whether it is on the known-harmless list (`host/engine/HHStubPolicy.cpp`); `unknownCount` counts the others. |
| `luaErrors` | errors that fail the run (`failing`), all errors (`total`) and the first 500 with context, file, line, message and phase. Add-in and probe errors are listed but do not fail the run. |
| `turns`, `timings`, `wallSeconds`, `memory` | turns played, updates, seconds per turn, phase timings, peak working set and private bytes, CPU seconds. |

**Known-harmless stubs.** The generated stubs answer false, 0 or NULL and do nothing. The list in
`HHStubPolicy.cpp` names the ones whose stub is the right answer for an autoplay game: renderer and UI
notifications (`Gameplay*`, `TradeVisuals_*`, `Publish*`, the engine's `DoTurn`), Steam statistics and
achievements, replays, advisor settings, and the UI side of selection, camera and screens. `Send*` network
methods are deliberately not on it: in the real game they are DLL calls (6.4). A stub call that is not on the list
is how a DLL that needs more engine support shows up: implement it in the host or add it to the list with the reason.

**Crashes.** A fatal exception in a game DLL is logged as `HHCRASH` lines (exception, `module+offset`, phase,
registers, return addresses on the stack) before the host exits; disassemble with `dumpbin /disasm
/range:<image base + offset>` (needs the toolchain's msdis DLL) or compare with the DLL's source.

## 15. Other DLLs: the stock BNW DLL [M]

Host-only coverage runs with the game's own `Assets\DLC\Expansion2\CvGameCore_Expansion2.dll`, no mods
(`--no-mods`), the DLC snapshot as the database (the main-menu database, which the stock DLL post-processed;
`c5h dbbuild` with no mods gives the same, dbdiff identical), Tiny Continents, quick speed, 150 turns:
three games played to the end, no Lua errors, every engine stub the DLL called on the known-harmless list. What
differs from VP:

- **`ICvGameContext1::Init` post-processes and caches the database** itself (`CvGlobals::init`), and caching looks
  text up: localization must be initialised before `Init`. `newgame` does; `builddb` now gives the DLL a
  localization database too (`--loc-db`, default an empty one with en_US).
- **Random civilizations and city-states are the exe's job**: the stock `closeInactiveSlots` leaves
  `NO_CIVILIZATION` and `NO_MINORCIV` (VP chooses them in `CvPreGame::onGameStarted`), and `CvGame::InitPlayers`
  dereferences the missing info. The host then chooses them: a city-state slot the resolved spec pins
  (`minorCivs`: `[{index, slot, type, id}]`, slot `MAX_MAJOR_CIVS + index`; the type looked up in the game
  database) gets that city-state, a major slot with a `civId` already has its civilization, and the rest are drawn
  (uniform, without repeats, among the IDs the database has, a DLL random number generator seeded with the engine
  seed; `ERROR` line, `host-report.json` `dll.hostChoseCivs`). A draw cannot be the exe's choice: a spec for a
  real-game comparison with this DLL names its civilizations and pins its city-states (the real game's, from the
  fingerprint's turn-0 `player` lines, `minor=` per slot). A DLL that chooses by itself (VP) keeps its choice;
  `host-report.json` `minorCivs` compares it with the pins slot by slot.
- **Info arrays have `MAX(ID) + 1` entries** (IDs with gaps are not renumbered): the boot count check accepts that.
- **No `Game.GetHeadlessDigest`, no seed pinning** (`seedMode` pregame): comparisons use the Lua fingerprint
  (`probes/fingerprint.lua`); dense and sparse sampling gave identical fingerprints on all three games.
- **VP-only Lua methods** (`Plot:GetLandmass`, `Plot:IsNaturalWonder`) are optional in the host's T000 map summary.
- **Autoplay** kills the active player's units and cities and makes its slot an observer (`CvGame::setAIAutoPlay`);
  when the autoplay turns run out the game is over (`saveReplay`, `RecordVictoryInformation`, `OpenEndGameMenu`).
- UI emulation: the base-game profiles select (`base-demographics`; `base-whos-winning` and
  `base-great-person-reward`, whose popups did not come up), and `eui-civ-ribbon` because EUI is installed as DLC
  (`UI_bc1`) in this game folder; EUI's other files are reported as unknown UI files.

**Plain Vox Populi** (upstream 5.4.8 with four determinism fixes, no digest additions; Community Patch, Vox Populi
and the EUI compatibility files from its repository; the mods registry generated from the `.modinfo` files, since
the host reads activation, entry points and imported files from the registry): three Tiny games of 100 turns
passed, 20 add-in contexts without errors, no unknown stub, dense and sparse fingerprints identical. The VP profiles
select by hash or content (`vp-whos-winning`, `cp-city-event-popup`); nine UI files differ from the reference set,
and the hazard scan finds only user-input calls in them.

## Appendix A: every outbound method seen (generated)

Counts are exact totals per trace (SUM lines). Threads: core = game-core thread, ui = main thread, other = render workers. `host today`: `impl` = overridden in `Civ5Headless`, `stub` = generated stub (value-initialised result: false/0/null). Proposal kinds: C constant, E emulate (state-dependent), N no-op, L script system. S4 includes the hook probe (its `CallTestAny`/`CallAccumulator` true values come from the probe listeners).

| method | S1 | S3 | S4 | AA100 | phases | threads | answers seen | host today | proposal |
|---|---:|---:|---:|---:|---|---|---|---|---|
| ICvEngineScriptSystem1::CallAccumulator | 316 | 147 | 20 | 6303 | turnloop | core,ui | `false;value=0`, `true;value=0`, `true;value=3` +4 | impl | L: engine semantics: value = init + sum of numeric results |
| ICvEngineScriptSystem1::CallCFunction | 3 |  |  | 3 | newgame | ui | `true` | impl | L: run the C function on the given Lua thread, protected; true |
| ICvEngineScriptSystem1::CallFunction | 1 | 2 | 2 | 1 | newgame | ui | `true` | impl | L: lua_pcall on the given thread; true on success |
| ICvEngineScriptSystem1::CallHook | 1634 | 757 | 243 | 37981 | newgame,turnloop | core,ui | `false;value=false`, `true;value=false` | impl | L: run every listener; returns "has listeners"; value forced false |
| ICvEngineScriptSystem1::CallTestAll | 7032 | 2737 | 466 | 363068 | newgame,turnloop | core,ui | `false;value=false` | impl | L: run every listener; AND of truthy results, errors do not vote |
| ICvEngineScriptSystem1::CallTestAny | 48 | 56 | 20 | 789 | turnloop | core,ui | `false;value=false`, `true;value=true`, `true;value=false` +1 | impl | L: run every listener; OR of truthy results, errors do not vote |
| ICvEngineScriptSystem1::CreateArgs | 9030 | 3697 | 749 | 408141 | newgame,turnloop | core,ui | `args0[]` | impl | L: new args object |
| ICvEngineScriptSystem1::CreateLuaThread | 2 | 2 | 2 | 2 | boot,newgame | ui | pointer | impl | L: new thread with the DLL libraries ("VP_LUAAPI", "Map Script", "WorldBuilderMapLoader") |
| ICvEngineScriptSystem1::DestroyArgs | 9030 | 3697 | 749 | 408141 | newgame,turnloop | core,ui | void | impl | L: free |
| ICvEngineScriptSystem1::FreeLuaThread | 2 | 2 | 2 | 2 | boot,newgame | ui | void | impl | L: free |
| ICvEngineScriptSystem1::LoadFile | 1 | 1 | 1 | 1 | newgame | ui | `true` | impl | L: VFS lookup + run the chunk; true |
| ICvEngineScriptSystemArgs1::Push | 22393 | 9448 | 1822 | 1036074 | newgame,turnloop | core,ui | `0`, `1`, `2` +1 | impl | L: append; returns the 0-based index of the pushed value (host returns the 1-based count today) |
| ICvEngineUtility1::AutoSave | 6 | 3 |  | 101 | turnloop | core | void | impl | N: no file unless asked; --autosave, --save-at write the game's .Civ5Save (saves.md) |
| ICvEngineUtility1::CanAdvanceTurn | 5 | 2 |  | 100 | turnloop | core | `true` | stub | C: true |
| ICvEngineUtility1::DoMapSetup | 1 | 1 | 1 | 1 | newgame | ui | void | stub | N: no-op (engine allocates its plot visuals; arg = plot count) |
| ICvEngineUtility1::DoTurn | 5 | 2 |  | 100 | turnloop | core | void | stub | N: no-op (engine-side turn bookkeeping); host may count turns here |
| ICvEngineUtility1::GameplayActivePlayerChanged | 2 | 2 |  | 2 | turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayCityCreated | 24 | 18 |  | 50 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayCityPopulationChanged | 31 | 19 |  | 390 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayCitySetDamage | 24 | 18 |  | 109 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayDoFX |  | 1 |  | 72 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayEraChanged |  |  |  | 24 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayFOWChanged | 8945 | 5633 |  | 53041 | turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayFeatureChanged | 925 | 593 | 582 | 963 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayGoldenAgeEnded |  |  |  | 3 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayGoldenAgeStarted |  |  |  | 4 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayMetTeam | 141 | 113 | 113 | 277 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayMinimapNotification |  | 1 |  | 73 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayMinimapUnitSelect | 2 | 2 | 1 | 1 | newgame,turnloop | ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayPlotStateChange | 8395 | 5588 | 26 | 9661 | newgame,turnloop,shutdown | ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayTechAcquired | 25 | 19 | 18 | 428 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayTurnChanged | 5 | 2 |  | 100 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitCreated | 82 | 54 | 54 | 686 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitDestroyed | 24 | 18 |  | 395 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitDestroyedInCombat |  |  |  | 138 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitEmbark |  | 1 |  | 420 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitFortify | 208 | 118 | 54 | 5346 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitGarrison |  |  |  | 103 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitSetDamage |  |  |  | 2454 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitShouldDimFlag | 769 | 265 | 108 | 43819 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitTeleported | 214 | 44 |  | 17232 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitVisibility | 69 | 45 | 1 | 3997 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayUnitWork |  |  |  | 2426 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayWallCreated |  |  |  | 41 | turnloop | core | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayWarStateChanged | 100 | 96 | 96 | 148 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GameplayYieldMightHaveChanged | 4721 | 2651 | 2470 | 6276 | newgame,turnloop | core,ui | void | stub | N: no-op (visual notification) |
| ICvEngineUtility1::GetAdvisorBadAttackInterrupt | 220 | 54 |  | 16781 | turnloop | core | `true` | stub | C: true (human-only path) |
| ICvEngineUtility1::GetAdvisorCityAttackInterrupt | 220 | 54 |  | 16781 | turnloop | core | `false` | stub | C: false (human-only path) |
| ICvEngineUtility1::GetAudioTagIndex | 13 | 13 | 13 | 13 | boot | ui | `4`, `5`, `-1` +5 | impl | E: real values are audio-script ids stored in info objects; -1 is safe unless an info getter feeds rules (not seen) |
| ICvEngineUtility1::GetAutorun | 100 | 53 |  | 41 | turnloop | core | `false` | impl | C: false |
| ICvEngineUtility1::GetCacheFolderPath | 12 | 11 | 3 | 116 | boot,newgame,turnloop,shutdown | core,ui | cache folder path | impl | C: per-run cache folder with trailing backslash |
| ICvEngineUtility1::GetDone | 82 | 54 | 54 | 686 | turnloop,shutdown | core,ui | `false`, `true` | impl | E: false while running; true from shutdown (Uninit) |
| ICvEngineUtility1::GetGameCoreLock | 463303 | 194601 | 22017 | 6775051 | boot,newgame,turnloop | core,ui | void | impl | E: recursive lock-depth counter per thread (no real mutex needed single-threaded) |
| ICvEngineUtility1::GetScriptSystem | 31170 | 11996 | 2250 | 1367610 | boot,newgame,turnloop | core,ui | pointer | impl | C: the host script system |
| ICvEngineUtility1::GetTutorialLevel | 1 | 1 | 1 | 1 | newgame | ui | `-1` | impl | C: -1 |
| ICvEngineUtility1::HasGameCoreLock | 53820 | 10559 | 6363 | 887001 | newgame,turnloop | core,ui | `true`, `false` | impl | E: true while the depth counter > 0 (always true inside Update and inside any CvDllGame/CvDllPlayer wrapper lifetime) |
| ICvEngineUtility1::HasReceivedTurnComplete | 246 | 102 | 6 | 2848 | newgame,turnloop | core,ui | `false` | stub | C: false |
| ICvEngineUtility1::InitGlobals | 1 | 1 | 1 | 1 | boot | ui | void | impl | N: no-op |
| ICvEngineUtility1::IsGameCoreExecuting | 537189 | 161898 | 161898 | 536946 | newgame,turnloop | ui | `false` | impl | E: false outside Update; only reached from non-core-thread RNG checks |
| ICvEngineUtility1::IsGameCoreThread | 538157 | 162124 | 161898 | 978628 | newgame,turnloop | core,ui | `true`, `false` | impl | E: false during boot/new game (UI thread in the real game), true inside Update |
| ICvEngineUtility1::IsModActivated |  |  |  | 36 | turnloop | core | `false` | impl | E: activated mod list (DLC_06 scenario id -> false) |
| ICvEngineUtility1::IsPlayerConnected | 1 | 1 | 1 | 1 | newgame | ui | `true` | stub | C: true for the human slot (only player 0 asked) |
| ICvEngineUtility1::PublishActivePlayer | 2 | 2 |  | 2 | turnloop | core,ui | void | stub | N: no-op |
| ICvEngineUtility1::PublishNewGameTurn | 5 | 2 |  | 100 | turnloop | core | void | stub | N: no-op (host may use it as the turn-start marker) |
| ICvEngineUtility1::ReleaseGameCoreLock | 463303 | 194601 | 22017 | 6775051 | boot,newgame,turnloop | core,ui | void | impl | E: decrement the depth counter |
| ICvEngineUtility1::TunerEverConnected | 1 | 1 | 1 | 1 | newgame | ui | `true` | impl | C: true in the reference (FireTuner driver); value is stored in CvGame (normalised in the digest) |
| ICvEngineUtility1::UninitGlobals | 1 | 1 | 1 | 1 | shutdown | ui | void | impl | N: no-op |
| ICvEngineUtility1::getPlayerOption | 36 | 36 | 12 | 36 | newgame,turnloop | core,ui | `false`, `true` | impl | E: user profile: WAIT_END_TURN=true, SAFE_AUTOMATION=true, others false (by hash) |
| ICvEngineUtility1::netMessageDebugLog | 669 | 207 |  | 24611 | turnloop | core | void | stub | N: no-op or log |
| ICvEngineUtility1::openSlot | 25 | 19 | 19 | 25 | newgame | ui | void | stub | Y: a closed slot becomes SS_COMPUTER (CvPlayer::setAlive(true): a liberated or revived player; hh-m5z-continents-s1 city-state 36, T140) |
| ICvEngineUtility1::closeSlot | – | – | – | – | game | ui | void | stub | Y: an AI or human slot becomes SS_CLOSED (CvPlayer::setAlive(false), majors and city-states; M5 era-medieval, hh-m5g-continents-s1 slot 5). Gameplay reads slot status: CvPreGame::isHuman, CvPlayer::init and the load path (flavor recipients, traits, potentially-alive minors), CvPreGame save (civ of non-AI slots written as none), observer hand-over (CvGame, SS_CLOSED slots) |
| ICvEngineUtility1::sendMinorBullyGold |  |  |  | 3 | turnloop | core | void | stub | E: net loopback: deliver ResponseMinorCivBullyGold(same args) after the Update returns |
| ICvEngineUtility1::sendMinorPledgeProtection |  |  |  | 19 | turnloop | core | void | stub | E: net loopback: deliver ResponsePledgeMinorProtection(same args) after the Update returns |
| ICvEngineUtility1::sendPlayerInfo | 63 | 63 | 63 | 63 | newgame | ui | void | stub | N: no-op (pregame broadcast) |
| ICvEngineUtility1::sendPlayerInitialAIProcessed | 2 | 1 |  | 1 | turnloop | core | void | stub | N: no-op (no response message seen) |
| ICvEngineUtility1::sendPlayerOption | 36 | 36 | 12 | 36 | newgame,turnloop | core,ui | void | stub | E: net loopback: deliver ResponsePlayerOption(activePlayer, hash, value) after the sending call returns |
| ICvEngineUtility1::shiftKey | 1648 | 1100 | 548 |  | turnloop,shutdown | ui | `false` | impl | C: false |
| ICvEngineUtility1::stripSpecialCharacters | 24 | 18 |  | 189 | turnloop | core | `szName=""`, `szName="TXT_KEY_CITY_NAME_MO...`, `szName="TXT_KEY_CITY_NAME_AS...` +97 | stub | E: in/out string; observed unchanged (TXT keys); no-op is exact for keys |
| ICvEngineUtility3::BeginSendBundle | 4 | 4 | 2 | 4 | newgame,turnloop | core,ui | void | stub | N: no-op (or start batching loopback messages) |
| ICvEngineUtility3::EndSendBundle | 4 | 4 | 2 | 4 | newgame,turnloop | core,ui | void | stub | N: no-op (or flush batch) |
| ICvEngineUtility3::FlushTurnReminders | 225 | 91 |  | 2541 | turnloop | core | void | stub | N: no-op |
| ICvEngineUtility3::SendAICivsProcessed | 2 | 1 |  | 1 | turnloop | core | void | stub | E: sets the engine flag read by allAICivsProcessedThisTurn from the next Update on |
| ICvEngineUtility3::ShouldValidateGameDatabase | 1 | 1 | 1 | 1 | boot | ui | `true` | impl | C: true in the real game (host answers false today); validation only logs |
| ICvEngineUtility3::TradeVisuals_DeactivatePopupRoute | 2 | 2 | 1 | 1 | newgame,turnloop | ui | void | stub | N: no-op |
| ICvEngineUtility3::TradeVisuals_DestroyRoute |  |  |  | 1 | turnloop | core | void | stub | N: no-op |
| ICvEngineUtility3::TradeVisuals_NewRoute |  |  |  | 4 | turnloop | core | void | stub | N: no-op |
| ICvEngineUtility3::TradeVisuals_UpdateRouteDirection |  |  |  | 50 | turnloop | core | void | stub | N: no-op |
| ICvEngineUtility3::allAICivsProcessedThisTurn | 100 | 53 |  | 41 | turnloop | core | `true`, `false` | stub | E: false between SendAICivsProcessed and the next Update, true otherwise |
| ICvEngineUtility4::Destroy | 1 | 1 | 1 | 1 | shutdown | ui | void | impl | N: no-op |
| ICvEngineUtility4::GetEvaluatedMapScriptPath | 1 | 1 | 1 | 1 | newgame | ui | `true` | stub | E: copy the input path (relative VFS path) to the out buffer; true |
| ICvUserInterface1::AddDeferredWonderCommand | 24 | 18 |  | 96 | turnloop | core | void | stub | N: no-op |
| ICvUserInterface1::AddNotification |  | 1 |  | 75 | turnloop | core | void | stub | N: no-op |
| ICvUserInterface1::AddPlotMessage |  |  |  | 1 | turnloop | core | `166879` | stub | N: no-op |
| ICvUserInterface1::AddPopup |  |  |  | 1 | turnloop | core | void | stub | N: no-op |
| ICvUserInterface1::CanDoInterfaceMode | 1620 | 1080 | 540 |  | turnloop,shutdown | ui | `false` | stub | C: false |
| ICvUserInterface1::ClearSelectionList | 4 | 4 | 1 | 3 | newgame,turnloop | core,ui | void | stub | N: no-op |
| ICvUserInterface1::DoAutoUnitCycle | 1 | 1 |  |  | turnloop | ui | `true` | stub | C: true (only after autoplay ends) |
| ICvUserInterface1::GetHeadSelectedUnit | 7355 | 4570 | 3761 | 16497 | newgame,turnloop | core,ui | `null`, `0x36563c08`, `0x719d45e0` +7 | impl | C: null (non-null only for the human after autoplay ended) |
| ICvUserInterface1::GetLengthSelectionList | 2 | 2 | 1 | 2 | newgame,turnloop | core,ui | `0` | impl | C: 0 |
| ICvUserInterface1::InsertIntoSelectionList | 2 | 2 | 1 | 1 | newgame,turnloop | ui | void | stub | N: no-op |
| ICvUserInterface1::IsLoadedGame | 25 | 19 | 19 | 25 | newgame | ui | `false` | impl | C: false; true after `loadgame` |
| ICvUserInterface1::IsOptionNoRewardPopups | 1 | 1 |  | 418 | turnloop | core | `false` | impl | C: false (host answers true today; popups are UI only) |
| ICvUserInterface1::IsPolicyNotificationSeen | 1 | 1 |  | 1 | turnloop | core | `false` | stub | C: false (human-only path) |
| ICvUserInterface1::IsSPAutoEndTurnEnabled | 954 | 501 | 6 |  | turnloop,shutdown | ui | `false` | stub | C: false |
| ICvUserInterface1::IsUnitSelected | 1096 | 485 | 151 | 55935 | newgame,turnloop | core,ui | `false`, `true` | impl | C: false |
| ICvUserInterface1::PublishActivePlayerTurnStart | 2 | 2 | 1 | 2 | newgame,turnloop | core,ui | void | stub | N: no-op |
| ICvUserInterface1::PublishEndTurnDirty | 251 | 77 | 1 | 5001 | newgame,turnloop | core,ui | void | stub | N: no-op |
| ICvUserInterface1::PublishPlayerTurnStatus | 251 | 77 | 1 | 5001 | newgame,turnloop | core,ui | void | stub | N: no-op |
| ICvUserInterface1::RemoveFromSelectionList | 24 | 18 |  | 395 | turnloop | core | `false` | stub | C: false |
| ICvUserInterface1::RemoveNotification |  |  |  | 66 | turnloop | ui | void | stub | N: no-op |
| ICvUserInterface1::SetDontShowPopups | 6 | 6 | 6 | 8 | newgame,turnloop | core,ui | void | stub | N: no-op |
| ICvUserInterface1::SetEndTurnBlockingChanged | 3 | 2 | 1 | 1 | turnloop,shutdown | ui | void | stub | N: no-op |
| ICvUserInterface1::SetSpecificCityInfoDirty | 180 | 76 |  | 8615 | turnloop | core | void | stub | N: no-op |
| ICvUserInterface1::UpdateCountryBorder | 172 | 130 |  | 538 | turnloop | core | void | stub | N: no-op |
| ICvUserInterface1::UpdateEndTurn | 3 | 2 | 1 | 1 | turnloop,shutdown | ui | void | stub | N: no-op |
| ICvUserInterface1::changeCycleSelectionCounter |  |  |  | 791 | turnloop | core | void | stub | N: no-op |
| ICvUserInterface1::clearSelectedCities | 4 | 4 | 1 | 3 | newgame,turnloop | core,ui | void | stub | N: no-op |
| ICvUserInterface1::doTurn | 5 | 2 |  | 100 | turnloop | core | void | stub | N: no-op |
| ICvUserInterface1::getHeadSelectedCity | 38 |  | 38 |  | turnloop,shutdown | ui | `null` | impl | C: null |
| ICvUserInterface1::getOriginalPlotIndex | 1 | 1 |  |  | turnloop | ui | `-1` | stub | C: -1 |
| ICvUserInterface1::getSelectionPlot | 1262 | 351 | 54 | 84084 | newgame,turnloop | core,ui | `null` | impl | C: null |
| ICvUserInterface1::isCitySelected | 262 | 148 |  | 29783 | turnloop | core | `false` | impl | C: false |
| ICvUserInterface1::isCitySelection | 1648 | 1100 | 548 |  | turnloop,shutdown | ui | `false` | impl | C: false |
| ICvUserInterface1::isDiploActive | 250 | 76 |  | 5000 | turnloop | core | `false` | impl | C: false |
| ICvUserInterface1::lookAtSelectionPlot | 1 | 1 |  |  | turnloop | ui | void | stub | N: no-op |
| ICvUserInterface1::makeSelectionListDirty | 2 | 2 | 1 | 1 | newgame,turnloop | ui | void | stub | N: no-op |
| ICvUserInterface1::setCanEndTurn | 7 | 4 |  | 102 | turnloop | core,ui | void | stub | N: no-op |
| ICvUserInterface1::setCycleSelectionCounter | 2 | 2 | 1 | 2 | newgame,turnloop | core,ui | void | stub | N: no-op |
| ICvUserInterface1::setDirty | 10665 | 6838 | 255 | 101623 | newgame,turnloop | core,ui | void | impl | N: no-op |
| ICvUserInterface1::setHasMovedUnit | 5 | 2 |  | 100 | turnloop | core | void | stub | N: no-op |
| ICvUserInterface1::setNoSelectionListCycle | 5 | 2 |  | 100 | turnloop | core | void | stub | N: no-op |

## Appendix B: every inbound non-accessor method seen (generated)

Inbound calls whose name starts with a mutator verb (see `trace_phases.py`); counts are exact totals per trace; phases and sample arguments come from logged lines only, so capped setters show only their boot calls (section 4.2).

| method | S1 | S3 | S4 | AA100 | phases | threads | sample args |
|---|---:|---:|---:|---:|---|---|---|
| CvDllDatabaseUtility::CacheGameDatabaseData | 3 | 3 | 3 | 3 | boot,newgame | ui |  |
| CvDllDatabaseUtility::FlushGameDatabaseData | 1 | 1 | 1 | 1 | boot | ui |  |
| CvDllDatabaseUtility::PerformDatabasePostProcessing | 1 | 1 | 1 | 1 | boot | ui |  |
| CvDllGame::DoGameStarted | 1 | 1 | 1 | 1 | newgame | ui |  |
| CvDllGame::EndTurnTimerReset | 2 | 1 |  | 1 | turnloop | ui |  |
| CvDllGame::EndTurnTimerSemaphoreDecrement | 9 | 9 | 9 | 10 | newgame,shutdown | ui |  |
| CvDllGame::EndTurnTimerSemaphoreIncrement |  |  |  | 1 | turnloop | ui |  |
| CvDllGame::Init | 1 | 1 | 1 | 1 | newgame | ui | 5 |
| CvDllGame::Init2 | 1 | 1 | 1 | 1 | newgame | ui |  |
| CvDllGame::InitTacticalAnalysisMap | 1 | 1 | 1 | 1 | newgame | ui | 4160, 2772 |
| CvDllGame::NetMessageStaticsReset | 2 | 2 | 2 | 2 | newgame,shutdown | ui |  |
| CvDllGame::SelectSettler | 1 | 1 | 1 | 1 | newgame | ui |  |
| CvDllGame::SelectionListIgnoreBuildingDefense | 4 | 4 | 2 | 2 | newgame,turnloop | ui |  |
| CvDllGame::SendPlayerOptions | 1 | 1 | 1 | 1 | newgame | ui | true |
| CvDllGame::SetTimeStr | 7 | 3 |  | 105 | turnloop | core,ui | "",0,true, "",1,true, "",2,true |
| CvDllGame::UnitIsMoving | 204 | 44 |  | 16617 | turnloop | ui |  |
| CvDllGame::Update | 230 | 101 | 9 | 2546 | turnloop | core |  |
| CvDllGame::UpdateSelectionList | 1 | 1 |  |  | turnloop | ui |  |
| CvDllGame::UpdateTestEndTurn | 954 | 501 | 6 |  | turnloop,shutdown | ui |  |
| CvDllGame::Write | 4 | 2 |  | 11 | turnloop | core,ui | ? |
| CvDllGame::WriteSupportingClassData | 4 | 2 |  | 11 | turnloop | core,ui | ? |
| CvDllGameContext::CreateNetInitInfo | 2 | 2 | 2 | 2 | newgame | ui |  |
| CvDllGameContext::CreateRandomNumberGenerator | 4 | 2 | 2 | 2 | newgame,turnloop | ui |  |
| CvDllGameContext::DestroyNetInitInfo | 2 | 2 | 2 | 2 | newgame | ui | 0, 1 |
| CvDllGameContext::DestroyRandomNumberGenerator | 4 | 2 | 2 | 2 | newgame,turnloop | ui | 0, 1, 2 |
| CvDllGameContext::Init | 1 | 1 | 1 | 1 | boot | ui |  |
| CvDllGameContext::RandomNumberGeneratorSyncCheck | 1 |  |  |  | turnloop | ui | 8,0x719d48e0,true |
| CvDllGameContext::ResetPathFinder | 10 | 10 | 4 | 6 | newgame,turnloop | core,ui |  |
| CvDllGameContext::SetAILogging | 1 | 1 | 1 | 1 | boot | ui | true |
| CvDllGameContext::SetAIPerfLogging | 1 | 1 | 1 | 1 | boot | ui | true |
| CvDllGameContext::SetAllowRClickMovementWhileScrolling | 1 | 1 | 1 | 1 | boot | ui | false |
| CvDllGameContext::SetBuilderAILogging | 1 | 1 | 1 | 1 | boot | ui | true |
| CvDllGameContext::SetDLLIFace | 2 | 2 | 2 | 2 | boot,shutdown | ui | 0x3ec80c40, null, 0x7f602b88 |
| CvDllGameContext::SetEngineUserInterface | 1 | 1 | 1 | 1 | boot | ui | 0x6d1cbd54, 0x6f0f5ba4, 0x6f3fab4c |
| CvDllGameContext::SetGameDatabase | 1 | 1 | 1 | 1 | boot | ui | 0x0a8c94dc, 0x0ac894dc, 0x0add94dc |
| CvDllGameContext::SetGraphicsInitialized | 1 | 1 | 1 | 1 | newgame | ui | true |
| CvDllGameContext::SetLogging | 1 | 1 | 1 | 1 | boot | ui | true |
| CvDllGameContext::SetOutOfSyncDebuggingEnabled | 1 | 1 | 1 | 1 | boot | ui | false |
| CvDllGameContext::SetPlayerAndCityAILogSplit | 1 | 1 | 1 | 1 | boot | ui | true |
| CvDllGameContext::Uninit | 1 | 1 | 1 | 1 | shutdown | ui |  |
| CvDllGameDeals::CreateDeal | 1 | 1 | 1 | 1 | boot | ui |  |
| CvDllGameDeals::DestroyDeal | 1 | 1 | 1 | 1 | shutdown | ui | 0 |
| CvDllMap::UpdateDeferredFog | 413 | 180 | 55 | 22918 | newgame,turnloop | core,ui |  |
| CvDllMap::UpdateLayout | 44 | 20 | 1 | 540 | turnloop,shutdown | ui | false |
| CvDllMap::UpdateSymbolVisibility | 1 | 1 | 1 | 1 | newgame | ui |  |
| CvDllMap::Write | 4 | 2 |  | 11 | turnloop | core,ui | ? |
| CvDllNetInitInfo::Commit | 1 | 1 | 1 | 1 | newgame | ui |  |
| CvDllNetInitInfo::Read | 1 | 1 | 1 | 1 | newgame | ui | ? |
| CvDllNetInitInfo::Write | 1 | 1 | 1 | 1 | newgame | ui | ? |
| CvDllNetMessageHandler::ResponseMinorCivBullyGold |  |  |  | 3 | turnloop | ui | 4,37,120, 4,28,92, 3,23,176 |
| CvDllNetMessageHandler::ResponsePlayerOption | 36 | 36 | 12 | 36 | newgame,turnloop | ui | 0,0,true, 0,1,false, 0,2,false |
| CvDllNetMessageHandler::ResponsePledgeMinorProtection |  |  |  | 19 | turnloop | ui | 4,27,true,false, 2,24,true,false, 6,28,true,false |
| CvDllNetworkSyncronization::ClearCityDeltas | 3 | 1 | 1 | 1 | newgame,turnloop | ui |  |
| CvDllNetworkSyncronization::ClearPlayerDeltas | 3 | 1 | 1 | 1 | newgame,turnloop | ui |  |
| CvDllNetworkSyncronization::ClearPlotDeltas | 3 | 1 | 1 | 1 | newgame,turnloop | ui |  |
| CvDllNetworkSyncronization::ClearRandomDeltas | 2 | 1 | 1 | 1 | newgame,turnloop | ui |  |
| CvDllNetworkSyncronization::ClearUnitDeltas | 3 | 1 | 1 | 1 | newgame,turnloop | ui |  |
| CvDllPlayer::AddNotification |  |  |  | 1 | turnloop | core | -1097390650,"Your treasury is empty and your economy is now producing ... |
| CvDllPlayer::LaterInit | 64 | 64 | 64 | 64 | newgame | ui |  |
| CvDllPlayer::RebroadcastNotifications | 3 | 3 | 1 | 3 | newgame,turnloop | ui |  |
| CvDllPlayer::SetBusyUnitUpdatesRemaining | 2 | 1 |  | 1 | turnloop | ui | 600 |
| CvDllPlayer::SetupGraphical | 64 | 64 | 64 | 64 | newgame | ui |  |
| CvDllPlayer::Write | 256 | 128 |  | 704 | turnloop | core,ui | ? |
| CvDllPlot::UpdateLayout | 29 | 23 | 23 | 28 | newgame | ui | false |
| CvDllPreGame::ResetGameOptions | 1 | 1 | 1 | 1 | frontend | ui |  |
| CvDllPreGame::ResetMapOptions | 1 | 1 | 1 | 1 | frontend | ui |  |
| CvDllPreGame::SetDLCAvailable | 64 | 64 | 64 | 64 | boot | ui | 0,0x7f5f8d70, 1,0x7f5f8d70, 2,0x7f5f8dc0 |
| CvDllPreGame::SetGameOption | 5 | 5 | 5 | 5 | boot,newgame | ui | "GAMEOPTION_QUICK_COMBAT",1, "GAMEOPTION_QUICK_MOVEMENT",1, "GAMEOPTIO... |
| CvDllPreGame::SetLoadFileName | 1 | 1 | 1 | 1 | boot | ui | "",0 |
| CvDllPreGame::VerifyHandicap | 1 | 1 | 1 | 1 | newgame | ui | 0 |
| CvDllPreGame::clearDLCAllowed | 1 | 1 | 1 | 1 | frontend | ui |  |
| CvDllPreGame::closeInactiveSlots | 1 | 1 | 1 | 1 | newgame | ui |  |
| CvDllPreGame::loadFromIni | 1 | 1 | 1 | 1 | boot | ui | ? |
| CvDllPreGame::resetGame | 1 | 1 | 1 | 1 | boot | ui |  |
| CvDllPreGame::resetPlayers | 2 | 2 | 2 | 2 | boot,shutdown | ui |  |
| CvDllPreGame::resetSlots | 1 | 1 | 1 | 1 | boot | ui |  |
| CvDllPreGame::setActivePlayer | 2 | 2 | 2 | 2 | boot,newgame | ui | -1, 0 |
| CvDllPreGame::setCivilization | 86 | 86 | 86 | 86 | boot | ui | 0,20, 1,-1, 2,-1 |
| CvDllPreGame::setCivilizationAdjective | 65 | 65 | 65 | 65 | boot | ui | 0,"", 1,"", 2,"" |
| CvDllPreGame::setCivilizationDescription | 65 | 65 | 65 | 65 | boot | ui | 0,"", 1,"", 2,"" |
| CvDllPreGame::setCivilizationKey | 1 | 1 | 1 | 1 | boot | ui | 0,"CIVILIZATION_MONGOL" |
| CvDllPreGame::setCivilizationKeyPackageID | 1 | 1 | 1 | 1 | boot | ui | 0,{293c1ee3-1176-44f6-ac1f-59663826de74} |
| CvDllPreGame::setCivilizationPassword | 64 | 64 | 64 | 64 | boot | ui | 0,"", 1,"", 2,"" |
| CvDllPreGame::setCivilizationShortDescription | 65 | 65 | 65 | 65 | boot | ui | 0,"", 1,"", 2,"" |
| CvDllPreGame::setClimate | 1 | 1 | 1 | 1 | newgame | ui | 0 |
| CvDllPreGame::setDLCAllowed | 23 | 23 | 23 | 23 | boot,frontend | ui | {293c1ee3-1176-44f6-ac1f-59663826de74},true, {b685d5de-7cca-4e75-81b4-... |
| CvDllPreGame::setEmailAddress | 64 | 64 | 64 | 64 | boot | ui | 0,"", 1,"", 2,"" |
| CvDllPreGame::setEra | 3 | 3 | 3 | 3 | boot,frontend,newgame | ui | 0 |
| CvDllPreGame::setGameMode | 2 | 2 | 2 | 2 | boot | ui | -1 |
| CvDllPreGame::setGameSpeed | 2 | 2 | 2 | 2 | boot,frontend | ui | 1, 2, 3 |
| CvDllPreGame::setGameStarted | 1 | 1 | 1 | 1 | shutdown | ui | false |
| CvDllPreGame::setGameType | 2 | 2 | 2 | 2 | boot | ui | 0,0 |
| CvDllPreGame::setHandicap | 65 | 65 | 65 | 65 | boot | ui | 0,5, 1,8, 2,8 |
| CvDllPreGame::setInternetGame | 1 | 1 | 1 | 1 | boot | ui | false |
| CvDllPreGame::setLeaderHead | 72 | 70 | 70 | 72 | boot | ui | 0,-1, 1,-1, 2,-1 |
| CvDllPreGame::setLeaderName | 65 | 65 | 65 | 65 | boot | ui | 0,"", 1,"", 2,"" |
| CvDllPreGame::setLoadWBScenario | 1 | 1 | 1 | 1 | frontend | ui | false |
| CvDllPreGame::setMapNoPlayers |  | 1 | 1 |  | newgame | ui | true |
| CvDllPreGame::setMapScriptName | 2 | 2 | 2 | 2 | boot,frontend | ui | "<Documents>\\My Games\\Sid Meier's Civilization 5\\MO... |
| CvDllPreGame::setMaxTurns | 2 | 2 | 2 | 2 | boot,frontend | ui | 0 |
| CvDllPreGame::setMinorCiv | 64 | 64 | 64 | 64 | boot | ui | 0,false, 1,false, 2,false |
| CvDllPreGame::setNetID | 63 | 63 | 63 | 63 | boot | ui | 0,-1, 1,-1, 2,-1 |
| CvDllPreGame::setNickname | 64 | 64 | 64 | 64 | boot | ui | 0,"", 1,"", 2,"" |
| CvDllPreGame::setNumMinorCivs | 2 | 2 | 2 | 2 | boot,frontend | ui | -1, 16, 12 |
| CvDllPreGame::setPersistSettings | 1 | 1 | 1 | 1 | newgame | ui | false |
| CvDllPreGame::setPitBossTurnTime | 1 | 1 | 1 | 1 | boot | ui | 0 |
| CvDllPreGame::setPlayable | 64 | 64 | 64 | 64 | boot | ui | 0,true, 1,true, 2,true |
| CvDllPreGame::setPlayerColor | 64 | 64 | 64 | 64 | boot | ui | 0,-1, 1,-1, 2,-1 |
| CvDllPreGame::setRandomMapScript | 1 | 1 | 1 | 1 | frontend | ui | false |
| CvDllPreGame::setRandomWorldSize | 1 | 1 | 1 | 1 | frontend | ui | false |
| CvDllPreGame::setReady | 193 | 193 | 193 | 193 | boot | ui | 0,false, 1,false, 2,false |
| CvDllPreGame::setSeaLevel | 1 | 1 | 1 | 1 | newgame | ui | 1 |
| CvDllPreGame::setSlotClaim | 65 | 65 | 65 | 65 | boot | ui | 0,2, 1,2, 2,2 |
| CvDllPreGame::setSlotStatus | 87 | 87 | 87 | 87 | boot | ui | 0,1, 1,1, 2,1 |
| CvDllPreGame::setTeamType | 86 | 86 | 86 | 86 | boot | ui | 0,0, 1,1, 2,2 |
| CvDllPreGame::setTurnNotifyEmail | 192 | 192 | 192 | 192 | boot | ui | 0,false, 1,false, 2,false |
| CvDllPreGame::setTurnNotifyEmailAddress | 192 | 192 | 192 | 192 | boot | ui | 0,"", 1,"", 2,"" |
| CvDllPreGame::setTurnNotifySteamInvite | 192 | 192 | 192 | 192 | boot | ui | 0,false, 1,false, 2,false |
| CvDllPreGame::setTurnTimer | 1 | 1 | 1 | 1 | boot | ui | 1 |
| CvDllPreGame::setVersionString | 1 | 1 | 1 | 1 | boot | ui | "403694 FINAL_RELEASE" |
| CvDllPreGame::setVictory | 5 | 5 | 5 | 5 | frontend | ui | 0,true, 1,true, 2,true |
| CvDllPreGame::setWhiteFlag | 64 | 64 | 64 | 64 | boot | ui | 0,false, 1,false, 2,false |
| CvDllPreGame::setWorldSize | 3 | 4 | 4 | 3 | boot,frontend,newgame | ui | 4,true, 3,true, 3,false |
| CvDllRandom::CopyFrom | 1 |  |  |  | turnloop | ui | 0x719d4910 |
| CvDllRandom::Init | 2 | 2 | 2 | 2 | newgame | ui | 139543027, 2002, 139671867 |
| CvDllRandom::Read | 1 |  |  |  | turnloop | ui | ? |
| CvDllRandom::Write | 1 |  |  |  | turnloop | ui | ? |
| CvDllScriptSystemUtility::PushCvCityInstance | 127 | 41 | 2 | 2502 | newgame,turnloop | ui | 0x920e4b30,null, 0x23703498,null, 0x920e50b0,null |
| CvDllScriptSystemUtility::PushCvDealInstance | 14 | 14 | 14 | 14 | newgame | ui | 0x23eca6f8,0x719d3c50, 0x23eca6f8,0x719d3c20, 0x23ec8df8,0x719d3ba8 |
| CvDllScriptSystemUtility::PushCvUnitInstance | 23 | 16 | 8 | 2 | newgame,turnloop,shutdown | ui | 0x924f7150,0x719d4460, 0x924f7150,0x719d04e0, 0x233f3270,0x719d4340 |
| CvDllScriptSystemUtility::RegisterScriptLibraries | 2 | 1 | 1 | 2 | boot,newgame | ui | 0x236ac758, 0x23eca6f8, 0x243a7120 |
| CvDllTeam::Write | 256 | 128 |  | 704 | turnloop | core,ui | ? |
| CvDllUnit::SetupGraphical |  |  |  | 8 | turnloop | core |  |
| CvDllWorldBuilderMapLoader::Preload |  | 1 | 1 |  | newgame | ui | L"Assets\\Maps\\Earth_Small.Civ5Map",false |

## Appendix: regenerating the reports

```
python tools/headless/trace_phases.py TRACE --run RUN_FOLDER --host-sources Civ5Headless --boot-trace HOST_TRACE --out phases.md --json phases.json
python tools/headless/trace_phases.py TRACE --window 12.26 12.29 --full      # every call in a time window
python tools/headless/contract_tables.py S1=s1.json S3=s3.json S4=s4.json AA100=aa100.json --host-sources Civ5Headless            # appendix A
python tools/headless/contract_tables.py S1=s1.json S3=s3.json S4=s4.json AA100=aa100.json --table in                            # appendix B
python tools/headless/trace_summary.py TRACE --out summary.md --json summary.json                                                  # per-turn view
```

`Civ5Headless/gen/HHInterfaces.gen.json` (method table) is generated by `tools/headless/gen_interfaces.py`.
