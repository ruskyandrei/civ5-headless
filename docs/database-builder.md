# Database builder

The builder makes the game database for a set of activated mods without launching the game. It starts from the DLC
snapshot (the database with the base game, the expansions and the installed DLC), which it also builds from the game's
files, and applies the mods with Firaxis's own loader code, which `CvGameDatabaseWin32Final Release.dll` exports. Plan
section 4.5 gives the design.

## Usage

```
c5h dbcache                                             # the cached database for the configured mods and DLL
c5h dbbuild --mods-dir <MODS> --mods-db <Civ5ModsDatabase.db> --out <built.db> [--dll <DLL>] [--oracle <game.db>]
c5h snapshot [--out <snapshot.db>] [--oracle <captured snapshot>] [--dll <DLL>]
```

Host games use `c5h dbcache` (through `dbcache.database`): one build per (mods input, DLL, snapshot, host exe), kept
in `<db_cache>/<key>/built.db`, one build however many games ask for it at once (they wait on `build.lock` in the entry). `c5h dbbuild` builds one database; inputs left out come from the configuration
(`c5h doctor`).

Inputs:

- `--mods-dir` is a MODS folder or a pinned copy of one. Only the `.modinfo`, `.xml`, `.sql` and `.lua` files are read.
- `--mods-db` is the game's `Civ5ModsDatabase.db`. It supplies the activated mods (`Activated = 1`, in RowID order)
  and a cross-check of the modinfo action lists. Use `--mod GUID[:version]` (repeatable) to name the mods instead.
  `c5h modsdb` generates a registry for a mod set from the `.modinfo` files (`c5h dbcache --mod` and `c5h run --mod`
  use it), for mods the game has not activated.
- `--snapshot` defaults to the configured `snapshot`, else to the snapshot built from the game's files (`c5h
  snapshot`, cached in `<db_cache>/snapshot/<key>/`).
- `--dll` is the game DLL used for post-processing. It defaults to the DLL in the CP mod folder (`c5h dbcache`: the
  configured DLL).
- `--path-as` sets the MODS folder recorded in `MapScripts.FileName`. It defaults to the user's real MODS folder, so a
  build from a pinned copy matches the game's database.

Outputs:

- The database itself.
- `<out>.build/`, which holds:
  - `plan.txt`;
  - `builddb-report.json`, with every step, its time and its logged errors;
  - `build-summary.json`;
  - `Logs/`: `Database.log` (the engine's format), `xml.log` (the DLL's remapper), `Lua.log` and `Headless.log`;
  - `dbdiff.json`, when `--oracle` is given.

Exit code 0 means the build succeeded and, when `--oracle` was given, matched it.

### Results

Pinned inputs of the source repository against databases the game wrote (`Civ5DebugDatabase.db`), built from a DLC
snapshot captured from the game:

| Pair (pinned input → game database) | dbdiff |
|---|---|
| `mods-reference-08D19878-input` → `mods-reference-08D19878.db` | identical |
| `mods-headless-host-0ed40de35-input` → `mods-headless-host-0ed40de35.db` | identical (and it differs from the first pair by the 20 `Stacking_Settings` rows, as the inputs do) |

With the snapshot built from the game's files instead, the modded databases are identical to those built from the
captured snapshot (Vox Populi, 5 mods; see [DLC snapshot](#dlc-snapshot)).

- **Speed:** the host step takes about 2 s and the whole build about 3 s; the DLC snapshot about 1 s more the first
  time. A cold file cache adds a few seconds.
- **Map script rule check:** `tests/platform_tests/test_dbbuild_mapscripts.py` (with `C5H_INTEGRATION=1` and a
  captured snapshot configured) re-registers the base and DLC map scripts from a snapshot whose map script tables
  were emptied. The result is identical to the game's rows.

### Building without the wrapper

```
civ5headless.exe builddb --game-dir <game> --plan <plan.txt> --out <built.db> [--dll <dll>] [--run <dir>]
```

The plan format is documented at the top of `host/setup/HHDbBuilder.cpp`. Each line is one step: `snapshot`, `phase`,
`mod`, `attach`, `detach`, `xml`, `sql`, `missing`, `vfs`, `mapscript` or `postprocess`.

## DLC snapshot

The game builds its database at start-up in two layers, and `c5h snapshot` (`snapshot.py`) repeats them with the same
`builddb` steps:

1. **Core:** the game's cached `Civ5CoreDatabase.db` (cache folder), which the game writes at its first start from
   the base game and expansion XML (its `ScannedFiles` table lists them). It is copied as is, so the game must have
   been started once (it writes `Localization-Merged.db` then too, which host games need anyway).
2. **DLC:** every `Assets/DLC/*/*.Civ5Pkg` in (`Priority`, folder name) order; a package without `Priority` counts as
   0. Each package applies its top-level `<GameData>` files and those of every `<Gameplay>` section. A file comes from
   `<gameplay owner>/DLC/<package folder>/` when the owner has one by that name, else from the package's own folder;
   the owner is the highest-priority package whose `<Gameplay>` names a `<GameCore>` (Expansion2): it carries updated
   copies of the older DLC's data, which the originals would fail against (DLC_03 and DLC_06 insert into columns
   Brave New World renamed).
3. **PackageID:** rows a package adds to `Civilizations` and `Leaders` get its `PackageID` (the GUID without braces
   and dashes, upper case); base rows keep NULL.
4. **DownloadableContent:** one row per package with a `<Key>` (by folder name, first of each GUID): `TXT_KEY_<id>_NAME`
   and `_DESCRIPTION`, the package's `Version`, `IsFiraxisContent` 1, `IsActive` 0 for an `<Upgrade>` package,
   `IsBaseContentUpgrade` 1 for `Ownership` FREE.
5. **Map scripts:** `Assets/Maps/*.lua`, then the user's `Maps` folder (recorded with its full path), then each
   package's `<MapDirectory>` in package order, each folder alphabetically.
6. **Post-processing** is not run by default: every modded build runs it with its own DLL. `--dll` runs it here too.

Against a snapshot captured from the game (`Civ5DebugDatabase.db` at the main menu without mods), the built one
differs only in the tables post-processing fills (`Defines`, `Resources.ResourceUsage`); with the Vox Populi DLL as
`--dll`, only in the three defines that DLL adds. The stock Expansion2 DLL crashes the host's post-processing step at
the time of writing, so that variant is unchecked.

The cache key covers the DLC and map folders (file names, sizes and times), the core database, the host and the DLL,
so a game update or a new DLC gives a new snapshot. `<snapshot>.dlc.json` next to it records the DLC manifest the
staleness check below reads.

## Steps

1. **DLC snapshot.** `dbbuild` checks the snapshot against the installed game:
   - keyed DLC packages that are installed but missing from the snapshot's `DownloadableContent` produce a warning;
   - when a manifest is present (`<snapshot>.dlc.json`, written with `--write-dlc-manifest`), the hashes of each DLC
     folder's data files and of the game exe/database DLL are compared, and changes produce a warning.
2. **Mod order.** Mods start in the registry's RowID order. Then a stable depth-first sort puts each mod after the
   active mods it depends on or references (`<Dependencies>` and `<References>`).
   - The observed order CP, VP, EUI compatibility, Squads, Small Resource Icons matches this rule.
   - Alphabetical order by name would also match, so the current data can't separate the two tie-breaks.
3. **Blocked DLC.** A mod's `<Blocks><Dlc>` makes the game rebuild without that DLC: VP blocks DLC_SP_Maps. The
   builder takes the DLC back out of the snapshot:
   - it sets `DownloadableContent.IsActive = 0`;
   - it deletes the rows of the DLC's `<GameData>` XML (only `<Row>` inserts can be undone, and anything else stops the
     build);
   - it deletes the map scripts under its `<MapDirectory>`.
4. **Text database.** A schema-only copy of the game's localization schema is attached as `Localization`, with
   `LocalizedText`, the `Language_en_US` view and its INSTEAD OF triggers.
   - Mods' `Language_*` statements resolve against it, as they do in the game. 106 of the update files touch text,
     many of them mixed with gameplay statements.
   - The game database never holds `Language_*` tables, and the oracle confirms this.
   - The text is thrown away. `Localization-Merged.db` is still reused (plan 4.5 step 5).
5. **Mod files.** For each mod, the `OnModActivated` `UpdateDatabase` files are applied in modinfo order:
   - `.xml` goes through `Database::XMLSerializer::Load`;
   - `.sql` goes through `Database::Connection::ExecuteMultiple` on the whole file.
6. **Map scripts.** Each `MapScript` entry point runs in a fresh Lua state, and its `GetMapScriptInfo` result goes into
   `MapScripts`, `MapScriptOptions` and `MapScriptOptionPossibleValues` under `<path-as>\<mod folder>\<entry file>`.
   The path keeps the modinfo's forward slashes, as the game stores it.
   - The Lua state provides:
     - `include` over a VFS index: game Lua, then active DLC with Expansion and Expansion2 last, then mods' `import="1"`
       files, with later entries winning. It returns the list of loaded files, as the engine's does; CP's CPK checks
       `#result == 1`.
     - `GameInfo` and `DB.Query`/`CreateQuery` on the database being built.
     - `GameDefines`, plus an empty `MapModData`.
     - `Locale.Lookup`/`ConvertTextKey` over the attached text. Continental Drift's name is `L("TXT_KEY_CONTINENTAL_DRIFT")`.
   - Engine rules confirmed by the oracle and by the base-script check:
     - a missing `IsAdvancedMap` is stored as 1, although the table default is 0;
     - a missing `SortPriority` is stored as 0;
     - `Folder` fills `FolderType`;
     - a value is either a string or `{name, description}`;
     - `OptionID` is the option's 1-based index as text, and `Value` and `SortIndex` are the value's 1-based index;
     - other missing fields take the table defaults.
7. **Post-processing.** The game DLL is loaded (`SetDLLIFace`, `SetGameDatabase`, `Init`) and its
   `PerformDatabasePostProcessing` runs:
   - the `Remapper`-table ID remapping (the remap lines in `xml.log` equal the game's);
   - `PostDefines`;
   - `ResourceUsage`;
   - the hard-coded defines.

   A captured snapshot already went through post-processing at the DLC level, and that is harmless: the remapping
   changes nothing there, and the defines are `INSERT OR REPLACE`. A built snapshot has not, and gives the same
   database.

## Findings

- **Error semantics.** Probed with the Firaxis code in the host:
  - **SQL file:** `ExecuteMultiple` runs statements in autocommit until the first error. Earlier statements stay, and
    the rest of the file is skipped. Only the SQLite message is logged.
  - **XML file:** `XMLSerializer::Load` uses one transaction per file, so any error (duplicate key, unknown table,
    unknown column) rolls back the whole file. The errors are logged as `... While executing - '...'` / `In
    XMLSerializer while updating table T from file F.`
  - **Missing UpdateDatabase file:** CP lists `Text/en_US/CoreGameOptionTextChanges.xml`, but the file is `.sql`. The
    builder logs and skips it, and the game's log shows no error for it either.
  - **Loading continues** after any error.
  - **Unconfirmed:** the current mods contain no failing file, so the oracle cannot confirm the SQL semantics in the
    real engine (an outer transaction, for example). Test edit `sqlerror` below checks it.
  - The game's `Database.log` for the reference launch shows only DLC-layer errors: `ArtDefine_StrategicView`
    duplicates and `ContentPackage.LocalizedText`, logged on every DLC rebuild. It shows no mod errors. The builder's
    `Database.log` likewise shows only the missing-file line.
- **Engine-only steps.** dbdiff found these, and nothing else differs:
  - DLC blocking, step 3;
  - map script registration with the engine's defaults, step 6;
  - the DLL's post-processing, step 7.

  `Civ5ModsDatabase` adds no rows to the game database.
- `sqlite_stat1` and `sqlite_sequence` are not compared. The DLL runs `ANALYZE` itself in `CacheGameDatabaseData`.

## Test edits (acceptance against game launches)

`tools/dbbuild_test_edits.py` changes only existing UpdateDatabase files of Vox Populi and the Community Patch, so no
modinfo or registry change is needed:

| Edit | File | Change | Built value (unedited game) |
|---|---|---|---|
| `xmlrow` | VP `Units/NewUnits.xml` | `UNIT_HEAVY_SKIRMISHER` PortraitIndex 10 → 47 | 47 (10) |
| `sqlupdate` | VP `Text/en_US/WorldMap/TerrainTextChanges.sql` | `+ UPDATE Units SET Combat = Combat + 3` for the warrior | 11 (8) |
| `newtable` | CP `StackingFormations.sql` | `+ CREATE TABLE HeadlessBuilderTest` with 3 rows | 3 rows (no table) |
| `sqlerror` | VP `Units/UnitChanges.sql` | `+` an insert into a missing table, then Scout Cost + 7 | 90 (90): the update is skipped |

- `python tools/dbbuild_test_edits.py --input <pinned input>` builds the edited scratch copy and prints the checks.
  All four pass.
- To check against the game:
  1. Run `--apply-to "<Documents>\My Games\Sid Meier's Civilization 5\MODS"`. It backs up each file as
     `*.hhtest-orig`.
  2. Launch the game with the mods and capture `cache\Civ5DebugDatabase.db` (`python -m
     civ5headless.realgame.dbcapture`).
  3. Run `--revert` on the same folder.
  4. Build from a pinned copy of the edited deployment with `--oracle <captured db>`, or run this script on the
     matching unedited input with `--oracle`.
  5. dbdiff should print `identical`.
- If the game applies the Scout update in `sqlerror`, the engine does not stop at the first SQL error. The builder's
  `sql` step must then change.
- **Checked 2026-10-25** (source repository run `hh-m4-testedits`, trace DLL 008A4227, headless-host package): the game
  gave 47, 11, 3 rows and Scout Cost 90, and its `Database.log` has `no such table: HeadlessBuilderMissingTable`.
  So the engine also stops an SQL file at its first error. The built database (input
  `mods-headless-host-0ed40de35-input` plus the edits) is identical to the game's (dbdiff).

## Limits

- **Text:** the text database starts empty. A mod `INSERT` that conflicts with a base-game text key fails in the
  game, which stops that SQL file or rolls back that XML file, but succeeds in the builder. No current mod does this.
  An attached pre-mod `Localization-Merged.db` would close the gap.
- **Blocked DLC:** a blocked DLC whose GameData uses `<Update>`/`<Delete>` cannot be undone from the snapshot, and the
  builder stops with a message. Building the snapshot without that package (`snapshot.py`) would handle it; not done
  yet, since Vox Populi blocks only DLC_SP_Maps, whose rows the current removal handles.
- **Map script environment:** the map script Lua state is a front-end subset. `Locale` functions other than
  `Lookup`/`ConvertTextKey` return their first argument.
