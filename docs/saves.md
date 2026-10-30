# Saved games

The host reads and writes the game's own saved games, `.Civ5Save` files. A save the real game wrote continues in the
host, and a save the host wrote opens in the real game's Load menu, at any turn. This page covers how to use saves,
the file format, what the host does when it saves and loads, and what has been checked against the real game.
[running.md](running.md) section 3.6 is the short version.

## 1. Using saves

### Saving

Every save the host writes stays in the run folder, `<run>\saves\`; nothing goes to the game's own save folders. A
save serializes the whole game and compresses it: about 0.1 s and 240 KB on a Tiny map at turn 5.

| `c5h run` option | Host option | Saves |
|---|---|---|
| (default) | (default) | the last turn, once autoplay has ended: `Final_<turn and date>` |
| `--no-save-final` | `--no-save-final` | not the last turn |
| `--autosave none` | (none) | no autosaves (the default, as autosaves slow a game down) |
| `--autosave game` | `--autosave game` | the real game's setting: `UserSettings.ini` `[AutoSave] TurnsBetweenAutosave` (10 unless changed) |
| `--autosave N` | `--autosave N` | every N turns |
| `--save-at T` | `--save-at T` | at turn T (repeatable): `Turn_<turn and date>` |

Names are the real game's: `<prefix>_<turn and date>.Civ5Save`, the date as the game formats it
(`ICvGame1::SetTimeStr`, e.g. `0050 BC-2000`). Autosaves go to `saves\auto\`: `AutoSave_Initial_0000 BC-4000` and
`AutoSave_Post_0000 BC-4000` at turn 0, then `AutoSave_Post_<turn and date>` on every N-th turn. All are kept.

Lua in the game can save too: `UI.SaveGame(name)`, the in-game Save menu's call, writes `saves\<name>.Civ5Save`
(probes, add-ins).

`status.json` lists what was written (`saves`: kind, turn, path, bytes; `finalSave`), `host-report.json` and
`newgame-report.json` add the time each took, and the report has a Saves row. A save that fails fails the run.

### Loading

```
c5h run SAVE.Civ5Save --turns T [any other c5h run option]
c5h run --load SAVE.Civ5Save --turns T [...]
```

A `.Civ5Save` given where the spec goes is loaded, the same as `--load`. Any save works: the real game's (its
`Saves\single` or `ModdedSaves\single`, autosaves included) or one a host run wrote.

- `--turns` is the turn to play to; it must be after the save's turn.
- **Mods:** the save's, unless `--mod`, `--no-mods` or `--mods-db` is given. A save lists only the mods that affect
  saved games (`AffectsSavedGames`, section 2), so when the configured registry's active mods that affect saved games
  are exactly the save's, that registry is used as it is, with its other mods (EUI compatibility files, for
  example) and the cached game database that new games use. Otherwise a registry is generated for the save's mods,
  as for `--mod`. The mods must be in the MODS folder; the game need not have them active.
- **DLL:** chosen as for any game (`--dll`, a `dll` in `civ5headless.toml`, else the mod set's).
- **Seeds:** without `--seeds` the game goes on with the save's random state. With `--seeds MAP SYNC` it gets new seeds
  from there on ([A save as a baseline](#a-save-as-a-baseline)).
- **Run folder:** without `--run`, it is named after the save. `save-header.json` holds the save's header;
  `status.json` has `loadedFrom`, `loadWarnings` and `startTurn` (the save's turn), and `save` is
  `loaded-<save name>-t<turn>-<file hash>`, so runs from one save group together. The run ends with its own final
  save, so a game can be continued run after run.

The host checks the save before loading it:

- The save must be a single-player game.
- The DLL's GUID (`GetDLLGUID`) must be the save's. This only tells DLL families apart: Vox Populi builds share one
  GUID, so a save loads into another VP build. That is useful for A/B runs from one save, and it is the user's job to
  know whether the builds' saved data is compatible.
- The active mods that affect saved games (ID and version) must be the save's mods. A different activation order is a
  warning.
- A save from another game version is a warning.
- After reading, the DLL must have consumed the body exactly; otherwise the save is reported as written by another
  DLL.

`c5h save FILE...` shows a save's header: turn, game type, civilization, handicap, map script, world size, speed,
eras, DLL GUID and version, mods and DLC (`--json` prints all of it).

### A save as a baseline

One save can start many games: an ensemble from one position, or an A/B test of two DLL builds where every arm starts
from the same turn.

```
c5h run my-spec.json --turns 101 --save-at 100 --run base
c5h run "runs\base\saves\Turn_0100 <date>.Civ5Save" --turns 130 --seeds 1001 2002 --run base-s1
c5h run "runs\base\saves\Turn_0100 <date>.Civ5Save" --turns 130 --seeds 1003 2004 --run base-s2
```

`--save-at T` saves when turn T ends, so the game must go past T: `--turns 100 --save-at 100` writes only the final save.

In a queue, `seeds` x `variants` over a save works as it does over a spec:

```json
{"defaults": {"load": "base.Civ5Save", "turns": 130},
 "variants": {"A": {"dll": "a.dll", "abRole": "baseline"}, "B": {"dll": "b.dll", "abRole": "test"}},
 "seeds": [[1001, 2002], [1003, 2004]]}
```

What the new seeds change:

- **The map stays the save's.** Only what is rolled from then on changes, so this gives what a new Vox Populi game
  cannot: other rolls on the same map. A new VP game with another map seed gets another map, and with another sync seed
  the same game ([verification.md](verification.md), "The ensemble").
- **Give both seeds.** A DLL rolls from one of them, and the other one alone leaves the game as it was. Measured from a
  save of turn 30, played to turn 45 (fingerprints, 16 turns):

  | Compared | Vox Populi (5.4.8) | Stock DLL |
  |---|---|---|
  | The save loaded twice, no new seeds | identical | identical |
  | New seeds (111, 222) twice | identical | identical |
  | No new seeds against new seeds | differ from turn 31 | differ from turn 32 |
  | Seeds (111, 222) against (333, 444) | differ from turn 31 | differ from turn 31 |
  | New map seed only, against no new seeds | differ from turn 31 | **identical** |
  | New sync seed only, against no new seeds | **identical** | differ from turn 32 |

  `c5h run` and the queue warn when a game gets only one new seed. `c5h verify` reports which seed your DLL responds to
  (`trajectory.reseed`).
- **The same save and seeds give the same game**, so a game played with `--ai-logging on` can be played again with `--detail
  full` to see why it went as it did. A game played with AI logging off, the default, has no such replay: turning it on plays
  another game ([ai-logging.md](ai-logging.md)).
- **The run's later saves carry the new seeds.**

What it records: `status.json` and `setup.json` have `reseed` (`{"map": M, "sync": S}`), `status.save` is
`loaded-<save name>-t<turn>-<file hash>-m<map>-s<sync>`, so `abcompare.py` pairs the arms that had the same seeds, and
`host-report.json` has `load.reseed`, with the game generator's seed before and after. The run report and the queue's
summary page show the seeds.

Things to keep in mind:

- **Compare loaded games with loaded games.** A loaded game does not reproduce the game that wrote the save (section 6:
  VP's espionage AI lists, part of the turn played again). All arms from one save share these effects, so an A/B from
  one save is sound.
- **A/B across DLL builds:** VP builds share a GUID, so a save loads into another build. The host fails the load if the
  DLL does not use up the body exactly, but whether the saved data suits the other build stays your call.
- **Use a `--save-at` save rather than a final one with the stock DLL.** The stock DLL ends an autoplayed game on its last
  turn, because the active player is dead, so its final saves hold an ended game. Such a game plays on in extended play,
  where no victory can end it; the load warns about it (`loadWarnings`). VP does not end the game there.
- **One save is one position.** An odd position shows in every arm: use several baselines.

### From Python and queues

- `run.play(SAVE, turns, autosave='game', save_at=[50], save_final=False)`: a save given as the spec is loaded (or
  `run.play(None, turns, load=SAVE)`); `save_final` is True by default. `seeds={'map': M, 'sync': S}` gives it new seeds.
- Queue jobs take a save as `spec` or as `load`, and `autosave`, `saveAt` and `saveFinal` (default true). A job that
  loads a save gets the save's mod set unless it names one. Its `seeds` are new seeds for the game.
- `savegame.read_header(path)` returns a `SaveHeader`; its `mod_set` is the save's mods as `--mod` values.
  `savegame.is_save(path)` tells a save from a spec.
- The real-game session (`realgame/session.py`):
  - `load_game(save, lua_probes, fingerprint)` loads a save as the game's Load menu does: the mods are activated in
    the Mods menu, then `Events.PlayerChoseToLoadGame` runs in `ModsSinglePlayer`. The mods are those of
    `set_mods`, else the save's (only those that affect saved games, so the others stay off). The game is left
    behind the load screen, as `start_game` leaves a new one; `autoplay` then continues it.
  - `save_game(name)` saves as the in-game Save menu does (`UI.SaveGame`), then moves the file the game wrote out of
    its `ModdedSaves\single` (or `Saves\single`) into `<run>\saves\`.

## 2. The file

Values are little-endian. A string is a u32 length followed by its bytes; a GUID is its 16 bytes as in memory. The
layout was decoded from real saves (128 of them, unmodded and VP) and is checked by `savegame.py` and
`setup/HHSaveGame.cpp`.

1. **Header (uncompressed).**
   1. `"CIV5"`, u32 8 (save version), the game version `"1.0.3.279 (403694)"`, the build `"403694"`.
   2. u32 game turn (the pregame's), u8 game type (0: single player).
   3. Type names: the active player's civilization and handicap, the start era, the active player's current era,
      the game speed, the world size, the map script (a path).
   4. The DLC: u32 n, then n × (GUID, u32 1, name).
   5. The mods that affect saved games, in activation order: u32 n, then n × (ID, u32 version, name). These are the
      activated mods whose `ModProperties` `AffectsSavedGames` is not 0. When it loads a save, the real game activates
      exactly this list if the active list differs (section 6).
   6. The leader and civilization names (empty unless the player set them), the player colour.
   7. The DLL's GUID and version string.
   8. A constant GUID `{0d41152c-948e-498f-8cdf-699d04c98a95}`, then u32 0.
2. **Pregame:** `ICvPreGame1::write`, as the DLL writes it.
3. **Marker:** u32 2.
4. **Body:** one zlib stream in 64 KiB pieces, each a u32 length and its bytes. The stream comes from the game's
   `zlib1.dll` (1.2.3) at the default level: `Z_NO_FLUSH` until the last input, then one `Z_SYNC_FLUSH`, with no final
   block and no Adler checksum. The host deflates and inflates with that same `zlib1.dll`. Uncompressed, the body is:
   1. `Game::Write`, which embeds `<cache>\Civ5SavedGameDatabase.db` (u32 size and the file);
   2. `Map::Write`;
   3. `Team::Write` for teams 0–63;
   4. `Player::Write` for players 0–63;
   5. `Game::WriteSupportingClassData`.

The DLC list is the keyed packages of `Assets\DLC\*\*.Civ5Pkg` without `<Upgrade>`, in folder-name order, minus
the DLC the activated mods block (`DlcRelationships` type -1 in the mods registry; VP blocks `DLC_SP_Maps`).
Unmodded saves list 12 DLC, VP saves 11.

## 3. Saving in the host

`setup/HHSaveGame.cpp`, `WriteSave`:

1. The header from the pregame, the active player (`GetCurrentEra`, `GetPlayerColor`), the database's type names, the
   DLC list above and the activated mods.
2. `PreGame::write`, then the body in the order above, into memory.
3. Deflate, then write `<name>.tmp` and rename it over the target, so a run never leaves half a save.

Autosaves and `--save-at` hang on the DLL's `gDLL->AutoSave(bInitial, bPostTurn)` calls (engine contract 6.5):
`(true, false)` in the first unpaused update of a new game, and `(false, true)` once per turn, when the first
living player's turn ends. The real game does the same in autoplay. Its `ModdedSaves\single\auto` after a 50-turn
VP autoplay game held `AutoSave_Initial_0000`, then `AutoSave_Post_0000` to `AutoSave_Post_0050` every 10 turns;
that is `TurnsBetweenAutosave = 10`, and all seven were kept although `NumAutosavesKept` is 5.

- `--autosave N` saves at the initial call and at the post-turn call of every turn divisible by N, named
  `AutoSave_Initial_` and `AutoSave_Post_` as the real game names them.
- `--save-at T` saves at the first post-turn call of turn T.
- The final save (unless `--no-save-final`) comes once autoplay has ended, after the final checkpoint, the final Lua
  and the dump have read the game, so it is the game they describe.
- `UI.SaveGame(name)` saves at once, from whatever Lua calls it.

Each save goes to `<run>\saves\` (autosaves to `saves\auto\`). The mod list in the header is the activated mods whose
`AffectsSavedGames` property is not 0, in activation order, as the real game writes it: Community Patch and Vox
Populi have it, EUI compatibility files, Squads and Small Resource Icons do not.

The real game's other autosave, `AutoSave_<turn and date>` at the start of a human player's turn, cannot happen in
autoplay, which has no human turns.

Saving does not change the game: a 12-turn game saved at turn 6 has the same state digest on all 12 turns as the
same game unsaved.

## 4. Loading in the host

`host loadgame --load SAVE` boots as `newgame` does: the DLL, the mods' files, the database and the UI emulation.
Then `HHLoadSavedGame` runs:

1. **Header and checks** (section 1).
2. **Pregame:** `resetGame`, `resetPlayers`, `clearDLCAllowed`, `setDLCAllowed` for each of the save's DLC,
   `PreGame::read(stream, true)`, `setGameType(type, GAME_LOADED)`, `setLoadFileName(path)`.
3. **Body:** inflate; `CacheGameDatabaseData`; `Game::Read`, `Map::Read`, teams 0–63 `Read`, players 0–63 `Read`,
   `ReadSupportingClassData`; the body must be used up exactly. From here on `IsLoadedGame` is true, for the DLL
   (`ICvUserInterface1::IsLoadedGame`) and for Lua (`UI.IsLoadedGame()`).
4. **New seeds** (`--reseed MAP:SYNC`; either side may be empty, which keeps the save's), through the official interfaces:
   `ICvPreGame1::setMapRandomSeed(MAP)`, `setSyncRandomSeed(SYNC)`, then `ICvGame1::GetRandomNumberGenerator()`,
   `ICvRandom1::Reset(SYNC % 52319761)` (the reduction `CvGame::init` applies). The map generator (`m_mapRand`) cannot be
   reached and keeps the save's state; after the map is made, VP uses it only in `CvPlot::getInlandCorner` and the
   simultaneous-turn shuffle.
5. **Start**, as for a new game (`HHGameStartedSteps`):
   - `LaterInit` for players 0–63 (`CvPlayer::gameStartInit`, which checks `IsLoadedGame`);
   - the tactical map, the game options, `SetGraphicsInitialized`, `SetupGraphical`, `DoGameStarted`;
   - the net message reset, the player options, the delivered net messages;
   - the selection list (no settler is selected);
   - the notifications.

   A new game's own steps are left out: closing dead city-state slots, selecting the settler.
6. **Digest logging** on for DLLs with the digest additions, which otherwise start it in `CvGame::init`. A load does
   not run `CvGame::init`.

Then the load screen, the UI add-ins, the probes and autoplay, as for a new game. Autoplay is armed with the same
Lua as the real-game session's (`Game.SetAIAutoPlay(n, 0)`). The checkpoint the host writes at the start is named
after the save's turn.

**Assumption (not traced):** the real game's load sequence was not recorded with the trace DLL. Steps 2 and 5
follow the DLL's own code and what the engine does for a new game. Section 6 says how far the result has been
checked against the real game.

## 5. Save data and Lua

- `Modding.OpenSaveData()` is the game's save data: `<run>\cache\Civ5SavedGameDatabase.db`. The DLL creates it for
  a new game, embeds it in every save and restores it from a loaded one.
  - `GetValue(name)` and `SetValue(name, value)` use its `SimpleValues` table.
  - `Query(sql, ...)` is `DB.Query` on it. TableSaverLoader (Squads) uses it, called both as `Query(...)` and as
    `:Query(...)`.
- `UI.IsLoadedGame()` is true in a loaded game.
- `Modding.OpenUserData` is still an in-memory store, empty at every start.

## 6. Checks against the real game

Run on 2026-10-26, on a Tiny Continents fixed-seed game.

- **Host alone (field-map VP build, digest additions):**
  - A game saved at turn 6 and the same game unsaved have identical state digests on all 12 turns.
  - Loading the turn-6 save and continuing does not reproduce the continuous game. The random number generators
    are identical after the load, but the state differs from the first digest on. One cause is in VP itself:
    `CvPlayer::Read` calls `reset()`, which fills the espionage AI's per-civ lists (22 entries each), and the
    espionage AI's `operator>>` then appends the 22 saved entries. After every load the first 22 entries are the
    defaults, the saved values are never read again, and each list grows by 22 per save and load. The other cause
    is where a save resumes: at the first player's post-turn point, so the rest of that turn is played again after
    the load. Both happen in the real game too, since the host runs the DLL's own `Read`. So a loaded game is
    compared with the same save loaded in the real game, not with the game that wrote the save.
- **Real game (Vox Populi, DLL `E8E58D95` as installed; the host ran the same DLL):**
  - A host save of turn 5 loads in the real game, which plays on from it (turns 5 to 8).
  - That save loaded in the host and in the real game, then played to turn 8: fingerprints identical on all 4 turns.
    The two turn-8 saves have bodies of the same size (13,003,632 bytes uncompressed) that differ in 67 bytes, in
    13 runs: two update counters, the end of the embedded save database, 6 map bytes and 1 player byte. Their
    pregame records differ in length by 94 bytes.
  - A real-game save of turn 8, taken after the real game's autoplay ended (player 0 human again), loaded in both and
    played to turn 10: identical at turn 8, then player 0 has 50 gold in the real game and 51 in the host at turn 9.
    Saved again right after the load, the two bodies differ only in two swapped bytes in player 0's data, so the
    loaded state is all but the same and the difference is likely an ordering the real game's load sequence sets up
    differently. **Open:** finding it needs the real game's load traced (section 4).
  - Loading needs the save's mod list to be the active one (header item 5). With a different list the real game
    re-activates the mods during the load; with Vox Populi's front end active that rebuild fails ("database table is
    locked") and the game crashes on the half-built database. Loading from the main menu with no mods active
    crashed too (VP's DLL in `CvGame::Read`). So the host writes only the mods that affect saved games, as the real
    game does, and the session loads from the Mods menu.
- **A long game:** the Tiny Continents game played in the host to turn 330 (9.4 min, no winner yet; host DLL
  `E8E58D95` with the installed mods). Its final save (764 KB; 17.2 MB uncompressed; written in 0.21 s) loaded in the
  real game in 16 s from the Mods menu, at turn 330 with player 0 (Sweden) human again, ready to play on.

## 7. Limits

- Single-player saves only. Hotseat, network and cloud saves are refused or not read.
- The load sequence is reconstructed, not traced (section 4).
- A save's DLL check is by GUID only (section 1).
- A real save loaded in the host and in the real game can differ after the first turn (section 6: 1 gold at the next
  turn, in one test).
- A loaded game resumes where the save was taken. For an autosave or `--save-at` save that is the post-turn point of
  the first player, so part of that turn is played again. That is the real game's behaviour too.
- The game option `GAMEOPTION_NEW_RANDOM_SEED`, if a save has it, reseeds the random number generators on load, so
  continuing that save is not repeatable. The host does what the DLL does. `--seeds` overrides that reseed, so it makes
  such a save repeatable.
- A save of an ended game plays on in extended play, where no victory can end it (the DLL's `Read` makes
  `GAMESTATE_OVER` that). The stock DLL's final saves are such saves ([A save as a baseline](#a-save-as-a-baseline)).
