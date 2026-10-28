# civ5-headless

Runs Civilization V (Brave New World) AI autoplay games without the game: `civ5headless.exe` loads a game DLL built
from the Civ V SDK (the stock DLL, Community Patch, Vox Populi or your own) and plays it with your mods, maps and
setup options, several games at a time, much faster than the real game. A verification suite measures how closely the
headless games represent real Civ V autoplay for your DLL and mods.

Status: 0.3.2, which shows vassals in the run report and takes a queue job's seeds as `[map, sync]`
([CHANGELOG.md](CHANGELOG.md)). It was developed in a Vox Populi fork:
[docs/plan.md](docs/plan.md) has the plan, and [docs/history](docs/history) shows how the host was built and checked
against the real game.

## Running it: the release kit

Running needs no compiler. Each release has a kit, `civ5-headless-<version>-<commit>.zip`: unpack it anywhere and run
`c5h.cmd` from that folder. It holds the prebuilt `civ5headless.exe`, the `c5h` tools, the UI emulation profiles,
the probes, examples and the user docs. Runs and caches go into the kit's folder.

- The exe needs only what Civ V itself needs: the Visual C++ 2008 runtime (the game installs it, and its own DLLs use
  it) and the Firaxis DLLs in your game folder.
- The `c5h` tools need Python 3.11 or newer, unless the kit bundles one in `runtime\`.
- Civ V with Brave New World must have been started once, so its caches exist.

```
c5h doctor                                               # what was found and what is missing
c5h run examples\specs\continents-tiny.json --turns 30   # a short game with your active mods and DLL
c5h verify --host-only                                   # the host-only part of the verification suite
```

A full game: Continental Drift (Standard size, Epic speed, Immortal) on a new random map. The game stops at the
first victory, or at turn 750, Epic's last. A Vox Populi test game ended in a cultural victory at turn 404 after 31
minutes. Progress is printed every 10 turns, and the run folder's `report.html` shows the game.

```
c5h run examples\specs\continental-drift.json --turns 750
```

Each example spec comes twice: `NAME.json` draws new seeds for every run (a new map and game each time),
`NAME-fixed-seed.json` replays the same game every time.

[docs/running.md](docs/running.md) shows every option with examples: game specs, seeds, DLLs and mod sets, UI
profiles, fingerprints, run outputs, queues, comparisons, the verification suite and the configuration file.

Your own Lua gets any other data out of a game: values at every turn as JSON lines (`--checkpoint-extra`), the end
state (`--final-extra`) and events as they happen (`--lua-probe`). [docs/probes.md](docs/probes.md) shows how to
write them, with examples in `examples\probes\`.

Games can run quietly in the background: `--cpu-limit 50` holds a game to half of one core, `--efficiency-mode` runs
it in Windows' efficiency mode, and `c5h queue run --total-cpu-limit 200` holds a whole queue to two cores' worth
([docs/running.md](docs/running.md#running-in-the-background)). The games play the same, only slower.

Games save and load as the real game's `.Civ5Save` files, in both directions: `c5h run SAVE.Civ5Save --turns T`
continues a save from the real game (or from another run), every run saves its last turn into its `saves\` folder
(`--no-save-final`: not), and `--autosave` and `--save-at` write more; the real game loads them
([docs/saves.md](docs/saves.md)).

`python tools/make_kit.py` makes the kit from a commit and a host built at that commit, with python.org's embeddable
Python (pinned version and hash) in `runtime\`.

## Layout

| Folder | Contents |
|---|---|
| `host/` | The C++ host (driver, engine services, Lua runtime, new-game setup, database builder) |
| `sdk/` | The Civ V SDK headers and import libraries the host builds against ([sdk/README.md](sdk/README.md)) |
| `python/civ5headless/` | The `c5h` tools: configuration, game specs, database builder and cache, runners, comparison |
| `tests/` | Unit tests (`python -m unittest discover -s tests`) |
| `tools/` | Host build (`build_host.py`, `toolchain.py`), interface stub generator, PE import lister, development tools |
| `profiles/` | UI emulation profiles the host runs in place of the game's UI ([docs/profiles.md](docs/profiles.md)) |
| `probes/` | Lua probes for the real game and the host |
| `examples/` | Game specs, queues and example probes |
| `docs/` | User guide, probes, engine contract, database builder, DLL additions, plan, history |

## Building

Only needed to change the host. The host must be built with MSVC 2008 SP1 (VC9), the game DLL's compiler, because
C++ objects cross the engine interfaces, and as a 32-bit program ([docs/why-32-bit.md](docs/why-32-bit.md) explains
why). `tools/get_toolchain.py` makes a portable copy in `toolchain/` from Microsoft's Windows SDK 7.0 ISO
(downloaded, or `--iso`), without installing anything:

```
python tools/get_toolchain.py [--iso GRMSDK_EN_DVD.iso]
python tools/build_host.py [--toolchain DIR]
```

`tools/toolchain.py` describes where the toolchain is looked for (a `--toolchain` folder, `C5H_TOOLCHAIN`, the
portable copy in `toolchain/`, or an installed Visual Studio 2008).

## Python tools

Python 3.11 or newer, standard library only. `c5h.cmd` runs them from the checkout (`python -m civ5headless` with
`python` on `PYTHONPATH`); `pip install -e .` installs a `c5h` command (pip fetches setuptools for it):

```
c5h doctor                                               # what was found and what is missing
c5h run examples/specs/continents-tiny.json --turns 30   # one game; the run folder goes to runs/
c5h compare runs/A runs/B                                # per-turn state digests of two runs
```

The game folder, the `My Games` folder, the mods and the host are discovered (Steam registry and libraries, the
Documents known folder, `out/latest.txt`), and the game database is built from the game's files and the mods.
`--mod ID[:VER]` plays any mod set in a MODS folder (the mods registry is generated from the `.modinfo` files),
`--no-mods` none.
Anything else, or anything to override, goes in `civ5headless.toml`; see
[civ5headless.example.toml](civ5headless.example.toml). `c5h -h` lists the commands; the
[user guide](docs/user-guide.md) covers them, and `civ5headless.realgame` drives the real game for references.

Tests: `python -m unittest discover -s tests`; with `C5H_INTEGRATION=1` they also run the host
on a short game and build the DLC snapshot.

## Licence

GNU General Public License v3 ([LICENSE](LICENSE)), as for the Civ V SDK sources this builds on (Community Patch
Project licence).
