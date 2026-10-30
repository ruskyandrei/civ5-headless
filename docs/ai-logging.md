# AI logging

The game DLL can log what its AI decides and why, as CSV files per player and per city. It is the largest cost in a long
Vox Populi game that you can turn off, and the logs are only useful if you read them.

**`c5h` keeps AI logging off unless you ask for it.** Turn it on when you need what the logs hold, and keep them with
`--detail full`:

```
c5h run my-spec.json --turns 150 --ai-logging on --detail full
```

In a queue, set `"aiLogging": "on"` ([Turning it on and off](#turning-it-on-and-off)).

The two things to remember:

- Off saves about 30% of the time of a 150-turn Vox Populi game, and about 10% with the stock DLL.
- With Vox Populi, the same seed plays a different game with it on. So never mix the two in a comparison. The games are
  the same kind of game, and each setting repeats exactly.

The real game may have it on: `config.ini` decides there. To compare a `c5h run` game seed by seed with a real game whose
`config.ini` has `AILog = 1`, or with a run from before off became the default, use `--ai-logging on`.

Measured on 2026-10-30, with Vox Populi (`Release-5.4.8-9-g6b97a2de`, a clang build 9 commits past the 5.4.8 release with two
crash fixes, which takes the same CPU as the release) and the stock DLL.

## What it costs

Tiny Continents, 150 turns, detail `summary`, one game per P-core, four runs of each with Vox Populi and two with the stock
DLL (means):

| DLL | | AI logging on | off | Saved |
|---|---|---:|---:|---:|
| Vox Populi | CPU | 24.3 s | 17.0 s | 30% |
| Vox Populi | host, wall | 25.5 s | 17.9 s | 30% |
| Stock | CPU | 9.65 s | 8.75 s | 9% |
| Stock | host, wall | 9.9 s | 8.85 s | 11% |

With Vox Populi the saving grows with the game: CPU -18% on a Duel map at 15 turns (where the boot is most of the game),
wall -30% on Tiny at 150 turns, and -40% on Duel at 100 turns.

**Detail levels do not save this time.** `--detail summary` and `standard` only decide which logs reach the disk. The DLL
does the work either way, and the host then discards the lines unwritten. A Standard game's first 100 turns took 66.6 s
at `standard`, with no AI logs written, and 69.3 s at `full`, with all of them written. So writing is a small part of the
cost. The rest is the DLL building the lines, plus extra AI work in Vox Populi: with logging on, `GetBestSettlePlot`
works out every plot's value afresh instead of using the cached one.

**Disk**, only at `--detail full`: Vox Populi 46 MB in 690 files, the stock DLL 28 MB in 163 files (Tiny, 150 turns).

## The same seed, on and off

| | Vox Populi | Stock DLL |
|---|---|---|
| The same seed, on against off | **a different game.** All 32 of 32 seeds differ by turn 150. First difference: turn 46 on the median, earliest 32, latest 132 | the same game: 151 of 151 turns, on the one seed tried |
| The same seed, off twice | identical (151 of 151 turns on Tiny, 101 of 101 on Duel, also with `--heap-perturb`) | identical (it matches the game with logging on) |
| The same seed, on twice | identical | identical |
| `--game-log-flags 0` against `--no-game-logging` | the same game (151 of 151 turns) | not tried |

Vox Populi, over those 32 pairs of games on Tiny:

- **It starts small.** In the two games inspected the first visible difference was one unit and 10 culture.
- **It grows into another game.** At turn 150 the same player leads in 23 of 32 pairs, and a player's score differs by 107
  on average, on scores of about 834.
- **On average the games are alike.** At turns 50, 100 and 150 no average of score, cities, population, techs, policies,
  units, military, gold, culture, faith or wonders differs in a statistically distinguishable way, apart from one
  borderline result (cities at turn 50, +0.04). At turn 150, score differs by -0.1 (95% range -16 to +18, of 849).

So a game with logging off is a representative game, and an ensemble or an A/B test with it off is as good as one with
it on. What it is not is the same game as a logged one with that seed.

### Why they differ

Code that runs only with AI logging on changes what the AI does. Two places in Vox Populi 5.4.8:

- **`CvPlayer::GetBestSettlePlot`** (`CvPlayer.cpp`). With logging on, it recomputes the shared flavor multipliers
  (`ComputeFlavorMultipliers`) and scores each plot with a fresh `PlotFoundValue`. With it off, it uses the cached found
  value. Where the AI settles can therefore differ.
- **`CvTacticalAI::PlotPillageMoves`** (`CvTacticalAI.cpp`). The line that lets undamaged units pillage citadels and improved
  resources (`iBaseDamage = 0`) sits inside the logging block. With logging off, a unit must have taken damage before it goes
  for such a target: 25 points, half that in a zone the enemy dominates, none in a water zone. This is a real rule difference,
  and pillaging is not one of the averages compared above.

There may be others. In an earlier fork of Vox Populi, fixing these two made logging on and off give identical 100-turn
digests on two maps ([history/host-ledger.md](history/host-ledger.md)). A DLL without such code plays the same game either
way, which is what the stock DLL did on the one seed tried. `--game-log-flags 14`, everything but `AILog`, played the same
game as 0: the `AILog` flag alone decides.

### Which one is "the real game"?

That depends on the real game's `config.ini`, because the real game runs the same DLL code. This project's comparisons with
the real game ran with `LoggingEnabled`, `AILog`, `AIPerfLog`, `BuilderAILog` and `PlayerAndCityAILogSplit` all 1. That
is why `civ5headless.exe` run by itself has it on: the same seed then plays the same game in both, as with
`--ai-logging on`. A real game with `AILog = 0` takes the branches a host game with logging off takes. That pair has not
been compared.

`c5h verify` with the real game reads these settings from `config.ini` and gives them to its host games, whatever
`ai_logging` says, so the two sides always match. `c5h parity`, `host-first` and `skew` run the host themselves and leave it
at its own default, on.

## What the logs hold

Each flag is a setting of the real game's `config.ini`:

| `--game-log-flags` | `config.ini` | Vox Populi, Tiny, 60 turns |
|---|---|---|
| 1 | `AILog` | nearly every log below (8.4 MB beyond the logs that are always there), all of the cost, and the game difference above |
| 2 | `AIPerfLog` | no file in these games; no cost |
| 4 | `BuilderAILog` | `BuilderTaskingLog`; no measurable cost, and with `AILog` also `BuilderEventLog` |
| 8 | `PlayerAndCityAILogSplit` | one file per player and per city instead of one file (147 files become 320); nothing on its own |

`--ai-logging on` is 15, all four, and `off` is 0. With `AILog` on, the logs are (`<civ>` and `<city>` from the split):

| Logs | What they hold |
|---|---|
| `PlayerTacticalAILog_<civ>` | every tactical zone each turn: the city, its posture (counterattack, attrition, ...), its targets, and the moves chosen |
| `PlayerHomelandAILog_<civ>` | moves outside tactical zones (exploring, garrisons, ...), and some plans for what to build or invest in |
| `MilitaryAILog_<civ>`, `MilitarySummary`, `OperationalAILog_<civ>` | threats, attack targets with their scores, armies, operations |
| `DiplomacyAI_Log_<civ>`, `DiplomacyAI_Messages_Log_<civ>` | for each other player: approach, proximity, strength, threat, war goals; the messages sent |
| `GrandStrategyAI_<civ>`, `PlayerEconomyAILog_<civ>`, `EconomicMonitorLog_<civ>`, `FlavorAILog_<civ>` | the grand strategy, economic strategies and flavors over time |
| `TechAILog_<civ>`, `PolicyAILog_<civ>`, `CultureAILog_<civ>`, `ReligionLog`, `PromotionLog`, `TradePlayerRouteLog` | what each system chose |
| `CityStrategyAILog_<civ>_<city>`, `CityStrategyAIProductionLog_<civ>_<city>`, `CitySpecializationLog_<civ>` | what each city considered and built |
| `CitySites_<civ>_NNN.txt` | a settling decision: for every plot, its terrain, owner, danger, fertility and value (half of the disk) |
| `Score_*`, `WorldState_*`, `GlobalMedian_*`, `PlayerProximityLog`, `PlayerHappinessStats`, `ExpenditureLog` | standings and totals |

Log names vary between DLLs; the stock DLL has most of these families.

They answer "why did the AI do that?". They are not what you need for numbers. These all work with AI logging off, and
none of them depends on it:

- the run report, checkpoints, replay data and `final-state.json`;
- `--checkpoint-extra`, `--final-extra` and `--lua-probe`, for your own numbers ([probes.md](probes.md));
- fingerprints and digests, `c5h fingerprint compare`, `c5h compare` and `queue compare`.

## Turning it on and off

It is off unless something below turns it on.

| Where | How |
|---|---|
| `c5h run` | `--ai-logging on` or `off` |
| Python | `run.play(spec, turns, ai_logging='on')` |
| Queue | `"aiLogging": "on"` in `defaults`, a variant or a job. A job's or variant's value replaces the default's |
| `civ5headless.toml` | `ai_logging = "on"`: every game that does not choose, including `c5h verify --host-only` |

- `on` is all four flags of [the table above](#what-the-logs-hold) (`--game-log-flags 15`), and `off` is `--game-log-flags
  0`. A run's choice wins over the file.
- `c5h verify` with the real game does not use this setting: its host games log as `config.ini` says.
- A logging option given straight to the host wins over all of these: `--host-arg=--game-log-flags --host-arg=N` for one flag
  on its own, or `--host-arg=--no-game-logging`.
- `--no-game-logging` also turns off the DLL's other logs (`EventLogging`, `EventCityLogging`, `CityTileScorer`,
  `InstantYieldSummary`). It played the same game as `--game-log-flags 0`.
- **Telling the games apart:** `status.json` records what a game played with, however it was set: `aiLogging` (`true` or
  `false`) and `gameLogFlags` (the flags in force, 0 with `--no-game-logging`). The run report's Run row says "AI logging on"
  or "off". Runs from before these fields played with it on unless told otherwise, and their `host-report.json` says
  (`options.gameLogging`, `options.gameLogFlags`).
- **The comparison tools warn:** `c5h compare`, `c5h fingerprint compare` and `c5h queue compare` say so when the two games
  differ in this. They still compare, with the same exit code. They cannot tell for a bare log file, or for a reference
  recorded from the real game, which records no setting: check `AILog` in its `config.ini` yourself.

## When to turn it on

- **You want to read the logs.** Play with `--ai-logging on --detail full`, which keeps them (`--host-arg=--no-dumps` leaves
  out the state dumps). Without `--detail full` the logs are not kept, and `--detail full` alone has no AI logs to keep: it
  warns about that.
- **You compare seed by seed with a logged game:** a logged run, including any run from before off became the default, or a
  real game with `AILog = 1`. `c5h verify` with the real game sees to this itself.

**Decide before you play.** A game played with logging off cannot be played again with logs: after some turns it is another
game. From one save of turn 80 of a game played with logging off, the save played on with logging off and with it on differed
from turn 82, while each setting repeated itself. The first turn or so after a `--save-at` save can show how the AI reasons
at that point, but its logs will not explain the later turns of the game without logs. If you may want the logs of a
particular game, play it with logging on.

**Never mix the settings in one comparison.** In an A/B test or an ensemble, give every game the same setting. A difference
between a logged and an unlogged game with Vox Populi says nothing about your change. `aiLogging` in `status.json` shows
which is which, and the comparison tools warn about a mixed pair.
