# Your own data from a game: probes and extras

How to get information out of a host game that the run folder does not already hold: your own Lua, run inside the
game, whose results land in the run folder as JSON lines, CSV or log lines. You need no compiler and no host
rebuild, and with the rules in section 7 your Lua does not change the game it reads.

Examples write `c5h`: that is `c5h.cmd` in the kit or checkout folder. The three files in `examples\probes\` are
the examples used below, ready to run.

## 1. What a run already records

Check this first: much of what you may want is already there ([running.md](running.md), section 6, lists every
file).

| File | What it holds | When it is written |
|---|---|---|
| `checkpoints\turn-NNNN.json` | **Every player:** civilization, team, score, techs, units, military units, city count. **Major civilizations also:** era, policies, population, land, military might, gold and gold per turn, science, culture, faith, tourism, happiness, wonders, how many civilizations they are influential on, spaceship parts, World Congress votes. **Every city:** name, owner, original owner, turn founded and acquired, population, hit points. **Also:** the wars and the barbarian camps | during the game, each checkpoint (`--checkpoint-every N`, default every turn); at `--detail summary` only the final one is kept |
| `replay-data.json` | each major civilization's replay series (the data behind the game's own graphs) | at the end |
| `replay-messages.json` | the game's event log: cities, wonders, wars, religions, eras | at the end |
| `final-state.json` | every player at the end; who owns each plot, and the cities on the final map | at the end |
| `status.json` | winner, victory, turns, time, CPU, memory | at the end |
| `Logs\Lua.log` with `--fingerprint` | per player and turn: score, gold, cities, population, techs, policies, units, culture, faith, wonders, religions, wars (`C5HM\|...` lines) | during the game |
| `saves\` | `.Civ5Save` files of the turns you ask for (`--save-at T`, `--autosave N`) and of the last turn, which the real game opens | as asked |

## 2. Three ways in

| Option | Runs | Your Lua's output | Real game too? |
|---|---|---|---|
| `--checkpoint-extra FILE` | at every checkpoint (every turn, or every `--checkpoint-every` turns) and at the final one | what the file **returns**, as JSON: `extra\NAME.jsonl`, one line per checkpoint | no (host only) |
| `--final-extra FILE` | once, after the last turn | what the file **returns**, as JSON: `extra\NAME-final.json` | no (host only) |
| `--lua-probe FILE` | once, when the game has started (before turn 0 is played); the `GameEvents` listeners it registers run for the rest of the game | what it **prints** to `Logs\Lua.log`, or files it writes itself | yes (recipe 8.6) |

NAME is the file name without `.lua`. All three options can be given several times, and together.

- For values over the turns (gold, cities, techs, anything you can read from the game at a point in time), use a
  **checkpoint extra**.
- For the end state in more detail than `final-state.json`, use a **final extra**.
- For things that happen between checkpoints (a city captured, a war declared, a unit killed, a wonder built), use a
  **probe** with event listeners. To get those as JSON as well, combine it with an extra (recipe 8.2).

## 3. A first extra

`examples\probes\turn-stats.lua` returns a table with one entry per living major civilization:

```lua
local ctx = ...                       -- name, kind, turn, state (kept between checkpoints), runDir, extraDir
local lastGold = ctx.state.gold or {}
local players = {}
for id = 0, GameDefines.MAX_MAJOR_CIVS - 1 do
	local p = Players[id]
	if p and p:IsAlive() and not p:IsMinorCiv() then
		local civ = GameInfo.Civilizations[p:GetCivilizationType()]
		local gold = p:GetGold()
		players[#players + 1] = {
			player = id, civ = civ and civ.Type, score = Game.GetPlayerScore(id), gold = gold,
			goldChange = lastGold[id] and gold - lastGold[id],   -- nil (left out) at the first checkpoint
			cities = p:GetNumCities(), population = p:GetTotalPopulation(),
			techs = Teams[p:GetTeam()]:GetTeamTechs():GetNumTechsKnown(),
			-- the file has a few more: era, culture, faith, units, military
		}
		lastGold[id] = gold
	end
end
ctx.state.gold = lastGold
return {players = players}
```

```
c5h run examples\specs\continents-tiny.json --turns 150 --checkpoint-extra examples\probes\turn-stats.lua
```

The run folder gets `extra\turn-stats.jsonl`, one JSON object per line:

```
{"turn": 1, "final": false, "seconds": 0.157, "value": {"players": [{"cities": 1, "civ": "CIVILIZATION_SWEDEN", "gold": 5, ...}, ...]}}
{"turn": 2, "final": false, "seconds": 0.403, "value": {"players": [...]}}
...
{"turn": 150, "final": true, "seconds": 45.596, "value": {"players": [...]}}
```

Read it from Python:

```python
import json

with open(r'runs\NAME\extra\turn-stats.jsonl', encoding='utf-8') as f:
    rows = [json.loads(line) for line in f]
for row in rows:
    if 'error' in row:
        continue                              # the file failed at this checkpoint: row['error'] says why
    for p in row['value']['players']:
        print(row['turn'], p['civ'], p['gold'], p['cities'])
```

`c5h run` prints one `extra` event per file at the end (rows, errors, the first error), and `status.json` has the
same in `extras`.

## 4. Extras in detail

### When they run

- **Checkpoint extras** run in the update where the turn number changes, every `--checkpoint-every` turns (default
  1), and once more after autoplay ends (the row with `"final": true`, the same turn as the last one). The `turn`
  of a row is the new turn. They run after c5h's own checkpoint Lua, in the same chunk, so a row and
  `checkpoints\turn-NNNN.json` describe the same moment.
- They do not run at the turn the game starts from (turn 0, or a loaded save's turn): the host takes that checkpoint
  with its own Lua. A `--lua-probe` runs at that point (section 5) if you need it.
- **Final extras** run once after the last turn, after the final checkpoint, before the final save.
- A loaded game (`--load SAVE`) runs them in the same way from the turn after the save's.

### The file and its argument

The file is a Lua chunk. c5h calls it with one argument, `ctx`, and keeps what it returns:

| `ctx` field | Meaning |
|---|---|
| `name` | NAME, the file name without `.lua` |
| `kind` | `checkpoint` or `final` |
| `turn` | `Game.GetGameTurn()` |
| `state` | a table kept for this file from call to call (empty at the first), for differences, running totals and caches of database lookups |
| `runDir`, `extraDir` | the run folder and its `extra` folder (for files you write yourself) |

Each call starts with fresh globals: those the file sets are gone at the next call. Keep what must last in
`ctx.state`. Data from a probe comes through `_G` (recipe 8.2).

### From return value to JSON

| Lua | JSON |
|---|---|
| `nil` | `null` as a row's `value`; a table field that is `nil` is left out |
| `true`, `false` | `true`, `false` |
| numbers | numbers; whole numbers stay whole. NaN and infinities are left out (they are not JSON) |
| strings | strings |
| a table with keys exactly `1..n` | an array |
| any other table | an object with sorted keys; number keys become strings (`[0]` becomes `"0"`); keys that are neither strings nor numbers are dropped |
| an empty table | `{}` (Lua cannot tell an empty list from an empty object) |
| a function, a game object (userdata) | its type name, `"function"` or `"userdata"` |
| a table that contains itself | `"(cycle)"` where it repeats; deeper than 64 levels, `"(too deep)"` |

Return plain values: `p:GetGold()`, not `p`.

### Errors

An error (a syntax error, a call on nil, `error(...)`) costs that file that one checkpoint, never the game:

- the row has `"error": "<file>:<line>: <message>"` instead of `value` (for a syntax error too: the line it is on);
- `Logs\Lua.log` gets a line `c5h checkpoint extra NAME: <message>`;
- the `extra` event and `status.json` count the errors and give the first one.

The other extras and c5h's own checkpoint are not affected.

### Where the results go

- `extra\NAME.jsonl` and `extra\NAME-final.json`, written when the game ends, kept at every `--detail` level.
- While the game runs, each `checkpoints\turn-NNNN.json` carries the values as `data.extra.NAME` (errors as
  `data.extraErrors.NAME`), so you can follow them live. `--detail summary` deletes the per-turn checkpoints at the
  end; `extra\` stays.
- `hh-command.json` lists the files with their SHA-256 (`extras`); the generated `hh-checkpoint.lua` and
  `hh-final.lua` hold their text.

### Queues and Python

Queue jobs take `checkpointExtras` and `finalExtras` (lists of files, relative to the queue file), like `luaProbes`:

```json
{"workers": 8,
 "defaults": {"spec": "../specs/continents-tiny.json", "turns": 150,
              "luaProbes": ["../probes/city-captures.lua"],
              "checkpointExtras": ["../probes/turn-stats.lua"],
              "finalExtras": ["../probes/game-summary.lua"]},
 "seeds": [[1001, 2002], [1003, 2004], [1005, 2006]]}
```

Saved as `examples\queues\stats-batch.json`, `c5h queue run examples\queues\stats-batch.json` plays the three games
into `runs\stats-batch\run-m1001-s2002\` and so on (a queue without `out` uses `runs\<queue file name>`), each with
its own `extra\`.

From Python, `run.play` takes `checkpoint_extras=[...]` and `final_extras=[...]`:

```python
from civ5headless import run

r = run.play('examples/specs/continents-tiny.json', 150,
             checkpoint_extras=['examples/probes/turn-stats.lua'], final_extras=['examples/probes/game-summary.lua'])
print(r.ok, r.dir / 'extra')
```

## 5. Probes in detail

A probe (`--lua-probe FILE`) runs once, in the game's `InGame` context, after the turn-0 checkpoint and before turn 0
is played: the point where FireTuner can first reach the real game. What it does at its top level reads the game at
that moment; the `GameEvents` listeners it registers stay for the rest of the game. Several probes run in the order
given, in one context, so a later one sees the globals of an earlier one.

### Printing

`print(...)` writes a line to `Logs\Lua.log`, with the seconds since the start and a context name in front:

```
[18.413] Main: CAPTURE|turn=77|x=20|y=19|city=York|from=3|to=0|population=5|conquest=true
```

The context name differs (`InGame` at the top level, `Main` inside a listener), and the game, the DLL and the mods
print to the same log, so start every line with a tag of your own and look for the tag anywhere in the line. Lua has
no JSON encoder; `TAG|key=value|key=value` lines are easy to write and to read:

```python
from pathlib import Path

def tagged(run, tag):
    """The TAG|key=value|... lines of a run's Lua.log as dicts (values are strings)."""
    out = []
    with open(Path(run) / 'Logs' / 'Lua.log', encoding='utf-8', errors='replace') as f:
        for line in f:
            at = line.find(tag + '|')
            if at >= 0:
                out.append(dict(kv.split('=', 1) for kv in line[at + len(tag) + 1:].strip().split('|') if '=' in kv))
    return out

for c in tagged(r'runs\NAME', 'CAPTURE'):
    print(c['turn'], c['city'], c['from'], '->', c['to'])
```

`Lua.log` is written line by line, so you can watch it while the game runs.

### Writing your own files

In the host, Lua has its standard `io` and `os` libraries, so a probe can write CSV or JSON lines itself. `c5h run`
puts a generated `probe-settings.lua` before your probes that sets two globals:

| Global | Value |
|---|---|
| `C5H_RUN_DIR` | the run folder |
| `C5H_EXTRA_DIR` | its `extra` folder (created before the game starts) |

Open, append and close for every write, so nothing is lost when a game stops early. `examples\probes\city-captures.lua`
does that (shortened here):

```lua
local csv = nil
if io and C5H_EXTRA_DIR then          -- nil outside c5h run
	csv = C5H_EXTRA_DIR .. '/city-captures.csv'
	local f = io.open(csv, 'w')
	if f then f:write('turn,x,y,city,from,to,population,conquest\n'); f:close() else csv = nil end
end

GameEvents.CityCaptureComplete.Add(function(oldOwner, isCapital, x, y, newOwner, population, conquest)
	local city = Map.GetPlot(x, y):GetPlotCity()
	local name = city and city:GetName() or '?'
	print(string.format('CAPTURE|turn=%d|x=%d|y=%d|city=%s|from=%d|to=%d', Game.GetGameTurn(), x, y, name, oldOwner, newOwner))
	if csv then
		local f = io.open(csv, 'a')
		if f then f:write(string.format('%d,%d,%d,"%s",%d,%d,%d,%s\n', Game.GetGameTurn(), x, y, name, oldOwner, newOwner,
			population or -1, tostring(conquest))); f:close() end
	end
end)
```

Writing files is host-only as far as this project knows: the probes in this repository stick to `print`, so that they
also run unchanged in the real game, and nothing here checks what the real game's Lua offers. Guard with
`if io and C5H_EXTRA_DIR then`, as above, for a probe that should run in both.

### Errors

- **A listener that raises** is logged in `Lua.log` (`Runtime Error: <file>:<line>: ...`) and listed in
  `host-report.json` (`luaErrors.list`) and the report's Health section. The game goes on, the other listeners of the
  event run, and the failing one runs again at the next event.
- **A probe that does not load** (a syntax error, or an error at its top level) fails the run: the host still plays the
  game to the end, but `status.json` says `failed` ("1 Lua error(s)"), as for any Lua the game could not load.
  `Lua.log` and `Logs\Headless.log` give the file and line. Try a new probe on a short game first (`--turns 3`).

## 6. The Lua you can use

Your probes and extras run in the game's own Lua (5.1) with the game's API: `Game`, `Map`, `Players`, `Teams`,
`GameInfo` and `GameInfoTypes`, `GameDefines`, `DB.Query`, `Locale` and `GameEvents`, so any Civilization V Lua API
reference applies. For what a DLL adds, its source is the reference: Community Patch and Vox Populi list their events
with their argument types in `CvGameCoreDLL_Expansion2\CustomMods.h` (the `GAMEEVENT_...` lines).

Things to know:

- **Only `GameEvents` fire, plus two UI events.** The host has no user interface: `Events.SequenceGameInitComplete`
  and `Events.LoadScreenClose` are fired once, and no other `Events.*` ever is (`ActivePlayerTurnStart`,
  `SerialEventCityCaptured`, `SerialEventUnitCreated` and the rest). Use the `GameEvents` the DLL fires.
- **Which `GameEvents` exist depends on the DLL.** These are the stock game's (the arguments as the Community Patch
  DLL source passes them, which keeps the stock ones):

  | Event | Arguments |
  |---|---|
  | `PlayerDoTurn` | player (once per living player per turn) |
  | `PlayerCityFounded` | player, x, y |
  | `CityCaptureComplete` | old owner, was capital, x, y, new owner, population, conquest |
  | `CityConstructed` | player, city ID, building, bought with gold, bought with faith or culture |
  | `CityTrained` | player, city ID, unit ID, bought with gold, bought with faith or culture |
  | `TeamSetHasTech` | team, tech, has it |
  | `PlayerAdoptPolicy` | player, policy |
  | `PlayerAdoptPolicyBranch` | player, branch |
  | `UnitKilledInCombat` | killer's player, killed unit's player, killed unit type |
  | `SetPopulation` | x, y, old population, new population |
  | `ReligionFounded` | player, holy city ID, religion, beliefs |
  | `PantheonFounded` | player, capital ID, religion, belief |
  | `TeamMeet` | the two teams |
  | `DeclareWar`, `MakePeace` | team, other team |

  Community Patch and Vox Populi add many more, and pass more arguments to some: `CityCaptureComplete` gets two more
  at the end, and with their war-and-peace event option on, `DeclareWar` and `MakePeace` get the declaring player,
  the other team and a flag instead. An unknown name is not an error: `GameEvents.NoSuchEvent.Add(f)` accepts the
  listener, which then never runs. Check a new listener with a `print` first.
- **The engine's `ipairs` visits index 0.** `ipairs(t)` starts at `t[0]` when `t[0]` is not nil, as in the game.
  Lists you build with `t[#t + 1] = v` start at 1 and are unaffected.
- **No `debug` library**, as in the game.
- **Names:** database rows hold types and text keys (`civ.Type` is `CIVILIZATION_SWEDEN`, `civ.ShortDescription` a
  `TXT_KEY_...`); `Locale.ConvertTextKey(key)` gives the text in the game's language. `city:GetName()` is already
  text.

## 7. Do not change the game

Your Lua runs inside the game it reads. A call that changes the game state changes the game, and then a run with
your file plays a different game from a run without it, or from the real game.

- Never call setters or actions: `Set*`, `Change*`, `Do*`, `Push*`, `Kill`, `Game.SetOption`, `Game.SetAIAutoPlay`,
  and so on.
- Prefer getters that return stored values. Some getters fill a cache the AI reads later, and so change the game
  without looking like it. The fingerprint probe (`probes\fingerprint.lua`) avoids `GetScore`, `GetMilitaryMight`,
  `CalculateGoldRate` and the happiness and yield getters for that reason, and uses `Game.GetPlayerScore(id)` for
  the score. The files in `examples\probes\` use only getters of the stored-value kind (checked below).
- Do not draw the game's random numbers (`Game.Rand`): that moves the synchronised random sequence.

**Check it.** Play the same fixed-seed spec twice, with your files and without, and compare:

```
c5h run examples\specs\continents-tiny-fixed-seed.json --turns 150 --run with-mine --checkpoint-extra my\stats.lua
c5h run examples\specs\continents-tiny-fixed-seed.json --turns 150 --run without
c5h compare runs\with-mine runs\without
```

`compare` reads the per-turn state digests of DLLs with the headless digest additions
([dll-additions.md](dll-additions.md)). With another DLL (the stock one, a plain Community Patch or Vox Populi), add
`--fingerprint` to both runs and use `c5h fingerprint compare runs\with-mine runs\without`. For the three example
files the digests of a 150-turn Tiny game were identical on all 150 turns, and both runs took 45.6 s.

**Time.** A checkpoint extra runs every checkpoint: keep it light, or raise `--checkpoint-every`. Loops over every
plot or over every building of every city belong in a final extra, or in an extra that works only every tenth turn
(`if ctx.turn % 10 ~= 0 then return nil end`; the other rows then hold `null`).

## 8. Recipes

### 8.1 Values over the turns

`--checkpoint-extra examples\probes\turn-stats.lua` (section 3). For other values, change the table it builds. For
per-city values, return a list of cities:

```lua
local cities = {}
for id = 0, GameDefines.MAX_MAJOR_CIVS - 1 do
	local p = Players[id]
	if p and p:IsAlive() then
		for city in p:Cities() do
			cities[#cities + 1] = {owner = id, name = city:GetName(), x = city:GetX(), y = city:GetY(),
				population = city:GetPopulation(), food = city:GetFood(), production = city:GetProduction()}
		end
	end
end
return cities
```

### 8.2 Events as JSON, counted per checkpoint

A probe counts the events into a table in `_G`; a checkpoint extra returns the counts. Two files:

```lua
-- combat-probe.lua (--lua-probe): kills and losses per player
local counts = {}
_G.MY_COMBAT = counts                  -- _G: the extras see _G, not this probe's own globals
local function add(player, key)
	counts[player] = counts[player] or {kills = 0, losses = 0}
	counts[player][key] = counts[player][key] + 1
end
GameEvents.UnitKilledInCombat.Add(function(killer, killed, unitType)
	add(killer, 'kills')
	add(killed, 'losses')
end)
```

```lua
-- combat.lua (--checkpoint-extra): the running totals at every checkpoint
local out = {}
for player, c in pairs(_G.MY_COMBAT or {}) do
	out[#out + 1] = {player = player, kills = c.kills, losses = c.losses}
end
return out
```

```
c5h run examples\specs\continents-tiny.json --turns 150 --lua-probe combat-probe.lua --checkpoint-extra combat.lua
```

`extra\combat.jsonl` then has the totals at every turn. `examples\probes\city-captures.lua` and
`game-summary.lua` work the same way (`_G.C5H_CAPTURES`).

### 8.3 The end of the game in detail

`--final-extra examples\probes\game-summary.lua`: every major civilization's cities (name, position, population,
number of buildings, founder), its wonders and policies by type, its techs and its captures, in
`extra\game-summary-final.json`. A final extra can afford whole-map loops:

```lua
-- terrain.lua (--final-extra): how many plots of each terrain each player owns
local out = {}
for i = 0, Map.GetNumPlots() - 1 do
	local plot = Map.GetPlotByIndex(i)
	local owner = plot:GetOwner()
	if owner >= 0 then
		local terrain = GameInfo.Terrains[plot:GetTerrainType()].Type
		out[owner] = out[owner] or {}
		out[owner][terrain] = (out[owner][terrain] or 0) + 1
	end
end
return out
```

### 8.4 A CSV for a spreadsheet

From the JSON lines, in Python:

```python
import csv, json

with open(r'runs\NAME\extra\turn-stats.jsonl', encoding='utf-8') as f, \
        open(r'runs\NAME\extra\turn-stats.csv', 'w', newline='', encoding='utf-8') as out:
    w = csv.writer(out)
    w.writerow(['turn', 'player', 'civ', 'gold', 'cities', 'techs'])
    for row in map(json.loads, f):
        if not row['final'] and 'value' in row:
            for p in row['value']['players']:
                w.writerow([row['turn'], p['player'], p['civ'], p['gold'], p['cities'], p['techs']])
```

Or straight from a probe with `io` (section 5).

### 8.5 Many games

Put the files in a queue's `defaults` (section 4) and read every game's results afterwards:

```python
import json
from pathlib import Path

for game in sorted(Path(r'runs\stats-batch').iterdir()):
    summary = game / 'extra' / 'game-summary-final.json'
    if summary.is_file():
        s = json.loads(summary.read_text(encoding='utf-8'))
        best = max(s['players'], key=lambda p: p['techs'])
        print(game.name, s['turn'], best['civ'], best['techs'])
```

### 8.6 The same probe in the real game

The real-game session runs probes at the same point as the host, so a probe that only prints gives the same lines in
both (`RealGameSession.start_game(..., lua_probes=[...])`, or `python -m civ5headless.realgame.probe`;
[user-guide.md](user-guide.md), section 8). Extras and `C5H_RUN_DIR` exist only in `c5h run`.

The other way to share code between the host and the real game is a mod: an `InGameUIAddin` with the same Lua runs
in the host (which runs every activated mod's add-ins) and in the game. It becomes part of the mod set, and so of the
game database's cache key.

## 9. Troubleshooting

| Symptom | Look at |
|---|---|
| A row has `error` | the message names the file and line; `Logs\Lua.log` has the same |
| `extra\NAME.jsonl` is empty | the game ended before its first checkpoint: `status.json` and `host-stdout.txt` say why |
| `value` is `null` everywhere | the file returns nothing: its last statement must be `return ...` |
| A field is missing | it was `nil`, NaN or infinite |
| A listener never prints | the event does not exist in this DLL, or never happens in an AI game (section 6) |
| A probe's globals are `nil` in an extra | set them on `_G` (`_G.NAME = ...`) |
| Two runs that should be the same game differ | your Lua changes the game (section 7) |
| `--checkpoint-extra: two extra Lua files are named X` | two files share a file name; rename one |
