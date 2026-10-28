"""One host game: play a new game from a spec, or continue a saved one, in civ5headless.exe and write a vpbench-style
run folder.

  c5h run (SPEC.json | SAVE.Civ5Save | --load SAVE.Civ5Save) --turns N [--run NAME | --run-dir DIR] [--seeds MAP SYNC]
          [--autosave none|game|N] [--save-at T]... [--no-save-final]
          [--dll DLL] [--db DB] [--mods-dir MODS] [--mods-db Civ5ModsDatabase.db | --mod ID[:VER]... | --no-mods]
          [--loc-db DB] [--game-dir DIR] [--exe EXE]
          [--profiles DIR | --no-profiles] [--skip-addin NAME]... [--no-addins]
          [--fingerprint] [--fingerprint-every K] [--fingerprint-turns T,T,...] [--fingerprint-split N]
          [--lua-probe FILE]... [--checkpoint-extra FILE]... [--final-extra FILE]... [--heap-perturb N] [--frame-skew K]
          [--diagnostics 1] [--improved-mask N | --ab-role baseline|test] [--checkpoint-every 1]
          [--maximum-seconds S] [--stall-seconds S] [--max-memory-mb MB] [--affinity MASK] [--low-priority]
          [--cpu-limit PERCENT] [--efficiency-mode]
          [--detail summary|standard|full] [--no-report] [--host-arg ARG]...

From Python: run.play(spec, turns, ...) -> Result (see play).

Inputs left out come from the configuration (civ5headless.toml or discovery; c5h doctor): the game folder, the DLL,
the MODS folder, the mods and localization databases, the profiles folder, the host exe and the runs folder (run
folder <runs>/NAME; without --run or --run-dir, NAME is <spec stem>-<date>-<time>). The game database is --db, else
the configured db, else the cached build for the mods and DLL (dbcache.py).

Mods: the activated mods are those of the mods database (--mods-db, default the game's registry). --mod gives the
mod set instead: a registry is generated from the .modinfo files (modsdb.py), so the game need not have activated
them. --no-mods plays without mods (the database is the DLC snapshot post-processed by the DLL).

DLL: --dll, else a dll set in civ5headless.toml, else for --no-mods the game's own and for --mod the first mod of the
set that ships one (else the game's own), else the discovered one (the active mods'). events.jsonl and the report say
which and why (modsdb.choose_dll).

Saves (docs/saves.md): the host writes the real game's .Civ5Save files, which the real game loads, and loads the real
game's. A .Civ5Save given instead of the spec (or --load SAVE) continues that game: --turns is still the turn to play to
(after the save's), the mod set is the save's unless --mod, --no-mods or --mods-db says otherwise (the configured
registry when it activates just those mods, else one generated for them, so the game need not have them activated),
and the DLL is chosen as above; the host refuses a save whose DLL (its GUID) or mods differ. Seeds do not apply: the
save carries its random state.
Every save stays in the run folder, <run>/saves. The host saves the last turn, Final_<turn and date>, unless
--no-save-final; more only when asked: --autosave none (default: none, as autosaves slow a game down), game (the
game's own setting, UserSettings.ini TurnsBetweenAutosave) or N (every N turns): as the real game, at turn 0
(AutoSave_Initial_ and AutoSave_Post_) and then every N-th turn (AutoSave_Post_<turn and date>) in saves/auto, all
kept; --save-at T saves at turn T (repeatable), Turn_<turn and date>. status.json lists them (saves, finalSave). Saves
are kept at every detail level.

Detail (--detail, default standard): summary keeps what happened and how fast (no per-turn checkpoints, digests or
logs but Lua.log), standard adds the per-turn checkpoints and the small logs, full keeps everything the host writes
(state dumps, the database copies, the DLL's AI logs). The host is told not to write what the level leaves out; the
database copies it needs while it runs are deleted afterwards. report.html is written at the end (--no-report: not;
c5h report RUN rebuilds it).

UI: the host runs the activated mods' InGameUIAddins (--skip-addin NAME leaves one out, --no-addins all) and the UI
emulation profiles from --profiles (default: config profiles; --no-profiles: none). --fingerprint runs the Lua
fingerprint probe (probes/fingerprint.lua; c5h fingerprint compares two runs), every turn or every K-th
(--fingerprint-every) plus --fingerprint-turns, before the --lua-probe files (which get C5H_RUN_DIR and C5H_EXTRA_DIR
from a generated probe-settings.lua).

Your own data (docs/probes.md): --lua-probe FILE runs once the game has started and can register GameEvents listeners
and print to Lua.log; --checkpoint-extra FILE runs at every checkpoint and what it returns goes into the checkpoint
(data.extra.NAME, NAME the file's stem) and, after the game, into extra/NAME.jsonl (one row per checkpoint);
--final-extra FILE runs once after the last turn and what it returns goes into extra/NAME-final.json. The extra folder
is kept at every detail level; an error in an extra file is logged (events.jsonl, Lua.log) and never fails the game.

Background runs: --cpu-limit P caps the host at P percent of one core (a hard cap on its job object; the host is
single-threaded). The game plays the same, only slower, and --maximum-seconds and --stall-seconds stretch by 100/P.
--efficiency-mode runs the host in Windows' efficiency mode, as Task Manager's: EcoQoS (lower clocks, efficient cores
on hybrid CPUs) and idle priority. status.json records both (cpuLimit, efficiencyMode, and totalCpuLimit for a queue's
cap shared by its games, queue.py); the timings of such a run are not the host's speed.

The run folder has the files vpbench writes and its readers use (compare.py, abcompare.py, samerun.py):
events.jsonl, setup.json, spec-resolved.json, status.json, turn-times.jsonl, turn-seen.json,
checkpoints/turn-NNNN.json (+ turn-NNNN-final.json), replay-messages.json, replay-data.json; plus final-state.json
(players and the final map), extra/ (the extras' results), report.html and the host's own outputs (host-report.json,
newgame-report.json, Logs/ with HeadlessDigest.log and Lua.log, t000-*.json; with --detail full dump-t000/, dump-tN/
and db/).

Differences from a real-game run (docs/user-guide.md, "Run folders"):
- checkpoints are taken exactly when the game turn advances (every --checkpoint-every turns), not by a
  30-second poll during the turn; turn-times.jsonl/turn-seen.json are exact, not 5-second polls;
- status.process comes from the host process (CPU seconds, peak working set, peak private bytes);
  pollSeconds is 0; finalSave is the host's save of the last turn (none with --no-save-final); status.save is a seed key
  "headless-<spec stem>-m<map seed>-s<sync seed>-<spec hash>" that groups runs of the same game for abcompare.py
  (for --load: "loaded-<save name>-t<turn>-<file hash>", and loadedFrom is the save);
- the improved-AI mask: --improved-mask needs a DLL with Game.SetStackingAIImprovedMask; for an A/B of
  two DLL builds use --ab-role, which records improvedMask 0 (baseline) or -1 (test: every player runs the
  test DLL) in setup.json.
"""
from dataclasses import dataclass
from pathlib import Path
import argparse, fnmatch, functools, hashlib, json, os, re, shutil, subprocess, sys, time
from datetime import datetime, timezone
from types import SimpleNamespace

from . import config, dbcache, modsdb, probes, savegame, winproc
from . import spec as gamespec

# vpbench.SETUP (the real-game benchmark of the Vox Populi fork this came from), as a function. Keep in sync.
SETUP = r'''
local out={turn=Game.GetGameTurn(),activePlayer=Game.GetActivePlayer(),speed=GameInfo.GameSpeeds[Game.GetGameSpeedType()].Type,
 world=GameInfo.Worlds[Map.GetWorldSize()].Type,map=PreGame.GetMapScript(),plots=Map.GetNumPlots(),version=(Game.GetDLLVersion and Game.GetDLLVersion() or ''),diagnostics=(Game.GetStackingDiagnosticsLevel and Game.GetStackingDiagnosticsLevel() or -1),players={}}
for owner=0,63 do local p=Players[owner]
 if p and p:IsEverAlive() and not p:IsBarbarian() then local civ=GameInfo.Civilizations[p:GetCivilizationType()]
  out.players[#out.players+1]={owner=owner,civilization=civ and civ.Type or '',minor=p:IsMinorCiv(),human=p:IsHuman(),handicap=GameInfo.HandicapInfos[p:GetHandicapType()].Type} end end
return out
'''

# Run before autoplay is armed (vpbench order: setup info, diagnostics level, A/B mask, then arm).
BEFORE_ARM = r'''
local out={}
out.setup=(function() %(setup)s end)()
local level=%(diagnostics)d
if level>0 then
 assert(Game.SetStackingDiagnosticsLevel,'this DLL has no Game.SetStackingDiagnosticsLevel')
 out.diagnosticsSet=Game.SetStackingDiagnosticsLevel(level)
end
local mask=%(mask)s
if mask~=nil then
 assert(Game.SetStackingAIImprovedMask,'this DLL has no Game.SetStackingAIImprovedMask (use --ab-role for a DLL A/B)')
 out.improvedMaskSet=Game.SetStackingAIImprovedMask(mask)
end
out.improvedMask=Game.GetStackingAIImprovedMask and Game.GetStackingAIImprovedMask() or -2
out.diagnosticsLevel=Game.GetStackingDiagnosticsLevel and Game.GetStackingDiagnosticsLevel() or -1
return out
'''

# vpbench.CHECKPOINT as on ai-improvements (the abcompare.py strength metrics included; every extra getter
# is pcall-guarded, so DLLs without them leave the field out). Keep in sync.
CHECKPOINT = r'''
local function v(f,...) if f then local ok,r=pcall(f,...);if ok then return r end end;return nil end
local parts={};for row in GameInfo.Projects() do if row.Spaceship==true or row.Spaceship==1 then parts[#parts+1]=row.ID end end
local league=v(Game.GetActiveLeague)
local active=Game.GetActivePlayer()
local out={turn=Game.GetGameTurn(),activePlayer=active,autoplay=Game.GetAIAutoPlay(),observer=Players[active]:IsObserver(),human=Players[active]:IsHuman(),pausePlayer=Game.GetPausePlayer(),replayCount=Game.GetNumReplayMessages(),winner=Game.GetWinner(),players={},cities={},wars={},livingMajors={}}
for owner=0,63 do
 local p=Players[owner]
 if p and p:IsAlive() and not p:IsObserver() then
  local civ=GameInfo.Civilizations[p:GetCivilizationType()]
  out.players[#out.players+1]={owner=owner,team=p:GetTeam(),civilization=civ and civ.Type or '',minor=p:IsMinorCiv(),barbarian=p:IsBarbarian(),unitCount=p:GetNumUnits(),cityCount=p:GetNumCities(),military=p:GetNumMilitaryUnits(),score=(not p:IsMinorCiv() and not p:IsBarbarian()) and p:GetScore() or 0,techs=Teams[p:GetTeam()]:GetTeamTechs():GetNumTechsKnown()}
  if not p:IsMinorCiv() and not p:IsBarbarian() then
   out.livingMajors[#out.livingMajors+1]=owner
   %(strength)s
  end
  for city in p:Cities() do
   out.cities[#out.cities+1]={plot=city:Plot():GetPlotIndex(),owner=owner,name=city:GetName(),originalOwner=city:GetOriginalOwner(),foundedTurn=city:GetGameTurnFounded(),acquiredTurn=city:GetGameTurnAcquired(),hp=city:GetMaxHitPoints()-city:GetDamage(),maxHP=city:GetMaxHitPoints(),population=city:GetPopulation()}
  end
  if not p:IsBarbarian() then
   for other=owner+1,63 do local q=Players[other];if q and q:IsAlive() and not q:IsObserver() and not q:IsBarbarian() and Teams[p:GetTeam()]:IsAtWar(q:GetTeam()) then out.wars[#out.wars+1]={owner,other} end end
  end
 end
end
local camp=GameInfoTypes.IMPROVEMENT_BARBARIAN_CAMP;out.barbCamps=0
for i=0,Map.GetNumPlots()-1 do if Map.GetPlotByIndex(i):GetImprovementType()==camp then out.barbCamps=out.barbCamps+1 end end
return out
'''
STRENGTH = r'''-- Strength metrics for A/B comparison (abcompare.py); nil when a getter is absent.
   local t=Teams[p:GetTeam()];local ss=0;for _,id in ipairs(parts) do ss=ss+(v(t.GetProjectCount,t,id) or 0) end
   local e=out.players[#out.players]
   e.improvedAI=v(p.IsStackingImprovedAI,p);e.era=v(p.GetCurrentEra,p);e.policies=v(p.GetNumPolicies,p);e.population=v(p.GetTotalPopulation,p)
   e.land=v(p.GetTotalLand,p);e.might=v(p.GetMilitaryMight,p);e.gold=v(p.GetGold,p);e.goldRate=v(p.CalculateGoldRate,p);e.science=v(p.GetScience,p)
   e.culture=v(p.GetTotalJONSCulturePerTurn,p);e.faith=v(p.GetTotalFaithPerTurn,p);e.tourism=v(p.GetTourism,p);e.happiness=v(p.GetExcessHappiness,p)
   e.wonders=v(p.GetNumWorldWonders,p);e.influentialOn=v(p.GetNumCivsInfluentialOn,p);e.spaceshipParts=ss
   e.votes=league and v(league.CalculateStartingVotesForMember,league,owner) or nil'''

# After autoplay: victory, replay messages (vpbench: Type ~= 2, Plots dropped) and per-major replay data; then the
# players (names, colours, final standing, a major's master: its master team's leader, -1 for none; none without the
# vassal getters) and the final map (plot owners, cities) for the run report, through plain getters (pcall-guarded
# where some DLLs lack them). It runs after the last turn's digest and checkpoint.
FINAL = r'''
local out={}
local vic=Game.GetVictory();local info=vic and vic>=0 and GameInfo.Victories[vic]
out.victory={winner=Game.GetWinner(),victory=info and info.Type or '',turn=Game.GetGameTurn()}
local msgs={}
for i=0,Game.GetNumReplayMessages()-1 do local m=Game.GetReplayMessage(i);if m and m.Type~=2 then m.index=i;m.Plots=nil;msgs[#msgs+1]=m end end
out.messages=msgs
local series={}
for owner=0,63 do local p=Players[owner]
 if p and p:IsEverAlive() and not p:IsBarbarian() and not p:IsMinorCiv() and not p:IsObserver() then
  local ok,d=pcall(function() return p:GetReplayData() end)
  if ok and d then local o={};for k,s in pairs(d) do local a={};for t,x in pairs(s) do a[#a+1]={t,x} end;o[k]=a end;series[tostring(owner)]=o end
 end
end
out.replayData=series
local function v(f,...) if f then local ok,r=pcall(f,...);if ok then return r end end;return nil end
local function rgb(t) local c=t and GameInfo.Colors[t];if c then return string.format('#%02x%02x%02x',math.floor(c.Red*255+0.5),math.floor(c.Green*255+0.5),math.floor(c.Blue*255+0.5)) end end
local wonders={};for b in GameInfo.Buildings() do local c=GameInfo.BuildingClasses[b.BuildingClass];if c and c.MaxGlobalInstances==1 then wonders[#wonders+1]=b.ID end end
local players={}
for owner=0,63 do local p=Players[owner]
 if p and p:IsEverAlive() and not p:IsObserver() and not p:IsBarbarian() then
  local civ=GameInfo.Civilizations[p:GetCivilizationType()];local leader=GameInfo.Leaders[p:GetLeaderType()];local pc=GameInfo.PlayerColors[p:GetPlayerColor()]
  local e={owner=owner,team=p:GetTeam(),minor=p:IsMinorCiv(),alive=p:IsAlive(),civilization=civ and civ.Type or '',
   leader=leader and leader.Type or '',name=v(p.GetCivilizationShortDescription,p),leaderName=v(p.GetName,p),
   colors={pc and rgb(pc.PrimaryColor) or '',pc and rgb(pc.SecondaryColor) or ''},score=not p:IsMinorCiv() and v(p.GetScore,p) or nil,cities=p:GetNumCities(),
   population=v(p.GetTotalPopulation,p),techs=v(function() return Teams[p:GetTeam()]:GetTeamTechs():GetNumTechsKnown() end),
   policies=v(p.GetNumPolicies,p),era=v(p.GetCurrentEra,p),wonders=v(p.GetNumWorldWonders,p)}
  if e.era then local r=GameInfo.Eras[e.era];e.eraType=r and r.Type end
  if not e.minor then e.master=v(function() local t=Teams[e.team];if t.IsVassalOfSomeone then return t:IsVassalOfSomeone() and Teams[t:GetMaster()]:GetLeaderID() or -1 end end) end
  if e.wonders==nil then local n=0;for c in p:Cities() do for _,id in ipairs(wonders) do if c:IsHasBuilding(id) then n=n+1 end end end;e.wonders=n end
  local rel=v(p.GetReligionCreatedByPlayer,p);if rel and rel>0 then local r=GameInfo.Religions[rel];local n=v(Game.GetReligionName,rel) or (r and r.Description);e.religion=n and Locale and v(Locale.ConvertTextKey,n) or n end
  players[#players+1]=e
 end
end
out.players=players
local w,h=Map.GetGridSize();local own={}
for i=0,Map.GetNumPlots()-1 do own[i+1]=Map.GetPlotByIndex(i):GetOwner() end
local cities={}
for owner=0,63 do local p=Players[owner]
 if p and p:IsAlive() then for c in p:Cities() do cities[#cities+1]={plot=c:Plot():GetPlotIndex(),owner=owner,name=c:GetName(),population=c:GetPopulation(),capital=c:IsCapital(),originalOwner=c:GetOriginalOwner()} end end
end
out.map={width=w,height=h,owners=table.concat(own,','),cities=cities}
return out
'''

# Detail levels: what a run folder keeps (docs/user-guide.md, "Run folders").
# - summary: what happened and how fast: status, events, setup, spec, host report, turn times, replay data and
#   messages, the final checkpoint and state, the turn-0 map, Lua.log and Headless.log, report.html;
# - standard (default): also the per-turn checkpoints and the small logs (the digests, Database.log, CustomMods.log, ...);
# - full: everything the host writes: the state dumps (dump-t000, dump-tN), the database copies (db/) and every log of
#   the DLL (its AI logs).
# The host is told not to write the dumps (--no-dumps) and the logs a level leaves out (--keep-log; the DLL still logs,
# so results do not change). What it must write anyway (the database copies the DLL opens) is deleted afterwards.
DETAILS = ('summary', 'standard', 'full')
DEFAULT_DETAIL = 'standard'
DETAIL_LOGS = {'summary': ('Lua.log', 'Headless.log'),
               'standard': ('Lua.log', 'Headless*.log', 'Database.log', 'Localization.log', 'CustomMods.log',
                            'include-resolution.log', 'vfs-index.log', 'xml*.log', 'stopwatch.log', 'net_message_debug.log',
                            'CvAssert*.log')}
REQUESTED_LOGS = ('Stacking-*.log', 'RandCalls.csv', 'HeadlessDumps')  # asked for by an option: kept at every level
ESSENTIAL_LOGS = DETAIL_LOGS['standard'] + REQUESTED_LOGS  # what --prune kept (now --detail standard)


def utc(): return datetime.now(timezone.utc).isoformat()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        return default


def spec_key(resolved, spec_path):
    blob = json.dumps(resolved, sort_keys=True, separators=(',', ':')).encode('utf-8')
    stem = Path(spec_path).stem.replace('-resolved', '').replace('.resolved', '').replace('spec-', '') or 'spec'
    s = resolved.get('seeds', {})
    return f"headless-{stem}-m{s.get('map')}-s{s.get('sync')}-{hashlib.sha256(blob).hexdigest()[:8]}"


def resolve_spec(spec_path, db, seeds=None):
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8-sig'))
    if seeds:
        spec = dict(spec, seeds=dict(spec.get('seeds') or {}, **seeds))
    if spec.get('specVersion') and not seeds:
        return spec  # already resolved
    if spec.get('specVersion'):
        return dict(spec, seeds=dict(spec['seeds'], **seeds))  # resolved spec with new seeds: IDs unchanged
    return gamespec.resolve(spec, db_path=Path(db))


class Run:
    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def log(self, action, quiet=False, **values):
        row = dict(utc=utc(), action=action, **values)
        with (self.dir / 'events.jsonl').open('a', encoding='utf-8') as s:
            s.write(json.dumps(row) + '\n')
        if not quiet:
            print(json.dumps(row), flush=True)


def turn_lines(path, offset):
    """New complete lines of turn-times.jsonl after byte offset."""
    try:
        with open(path, 'rb') as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b'\n') + 1
    rows = []
    for line in data[:end].splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows, offset + end


def detail_of(detail=None):
    """The detail level: detail if given, else standard (also what the old prune option asks for)."""
    detail = detail or DEFAULT_DETAIL
    if detail not in DETAILS:
        raise ValueError(f'detail must be one of {", ".join(DETAILS)}, not {detail!r}')
    return detail


@functools.lru_cache(maxsize=16)
def _exe_text(path, stamp):
    try:
        return Path(path).read_bytes()
    except OSError:
        return b''


def host_supports(exe, option):
    """Whether civ5headless.exe knows option (its usage text names it); older hosts do not know --no-dumps and
    --keep-log, and their runs are tidied afterwards instead."""
    try:
        st = Path(exe).stat()
    except OSError:
        return False
    return option.encode('ascii') in _exe_text(str(exe), (st.st_size, st.st_mtime_ns))


def detail_host_args(detail, exe):
    """The host options that keep it from writing what the detail level leaves out (none for full)."""
    if detail == 'full':
        return []
    args = ['--no-dumps'] if host_supports(exe, '--no-dumps') else []
    if host_supports(exe, '--keep-log'):
        for pattern in DETAIL_LOGS[detail] + REQUESTED_LOGS:
            args += ['--keep-log', pattern]
    return args


def tidy(run, detail):
    """Delete what the detail level leaves out (full: nothing): the database copies (db/, which the DLL needs while it
    runs), dumps and logs an older host wrote anyway, and for summary the per-turn checkpoints and the raw final Lua
    result (its parts are in replay-data.json, replay-messages.json and final-state.json)."""
    run = Path(run)
    if detail == 'full':
        return
    for p in [run / 'db'] + [p for p in run.glob('dump-t*') if p.is_dir()]:
        shutil.rmtree(p, ignore_errors=True)
    logs = run / 'Logs'
    if logs.is_dir():
        keep = [s.lower() for s in DETAIL_LOGS[detail] + REQUESTED_LOGS]
        for p in logs.iterdir():
            if not any(fnmatch.fnmatchcase(p.name.lower(), k) for k in keep):
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    if detail == 'summary':
        for p in (run / 'checkpoints').glob('turn-*.json'):
            if not p.name.endswith('-final.json'):
                p.unlink(missing_ok=True)
        (run / 'lua-before-dump.json').unlink(missing_ok=True)


def prune(run):
    """The old --prune: tidy(run, 'standard')."""
    tidy(run, 'standard')


def run_folder(a, cfg):
    if getattr(a, 'run_dir', None):
        return Path(a.run_dir).resolve()
    load = getattr(a, 'load', None)
    stem = Path(a.spec).stem if a.spec else re.sub(r'[^\w.-]+', '-', Path(load or 'game').stem).strip('-')
    name = getattr(a, 'run', None) or f'{stem}-{datetime.now():%Y%m%d-%H%M%S}'
    return (cfg.require('runs') / name).resolve()


def load_key(header):
    """status.save for a loaded game: "loaded-<save name>-t<turn>-<file hash>" (runs from the same save group)."""
    with open(header.path, 'rb') as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:8]
    stem = re.sub(r'[^\w.-]+', '-', Path(header.path).stem).strip('-')
    return f'loaded-{stem}-t{header.turn}-{digest}'


def load_mod_set(cfg, header):
    """The mod set to load a save with when none is given: None (the configured registry) when the configured
    registry's mods that affect saved games are the save's (it may activate UI mods beside them, as the player's game
    did; the database is the one new games use), else the save's mods (a generated registry)."""
    active = modsdb.activated(cfg.mods_db, saved_games_only=True) if cfg is not None and cfg.mods_db else None
    if active is not None and sorted(active) == sorted(modsdb.parse_mods(header.mod_set)):
        return None
    return header.mod_set


def save_host_args(a, exe):
    """The host options for the saves: the host saves the last turn unless told not to (save_final False), and more
    only when asked. Raises ValueError when more is asked of a host without saves."""
    autosave = autosave_mode(getattr(a, 'autosave', None))
    args = [] if autosave == 'none' else ['--autosave', autosave]
    for t in getattr(a, 'save_at', None) or []:
        args += ['--save-at', str(int(t))]
    if (args or getattr(a, 'load', None)) and not host_supports(exe, '--save-at'):
        raise ValueError(f'{exe} predates saves (no --autosave, --save-at, loadgame); use a newer host')
    if not getattr(a, 'save_final', True) and host_supports(exe, '--no-save-final'):
        args += ['--no-save-final']
    return args


def fingerprint_options(a):
    """dict(every, turns, split) when the run takes Lua fingerprints, else None. a.fingerprint is a flag or a dict
    with those keys; a.fingerprint_every, _turns and _split (each implies the flag) fill what it leaves out."""
    return probes.fingerprint_options(getattr(a, 'fingerprint', None),
                                      *(getattr(a, f'fingerprint_{k}', None) for k in ('every', 'turns', 'split')))


def newgame(a, quiet=False, cfg=None):
    """Play one game; returns 0 when completed. a: argparse namespace (see add_arguments) or an equivalent object;
    cfg: the configuration that supplies whatever a leaves out (default config.load())."""
    cfg = cfg or config.load()
    folder = run_folder(a, cfg)
    if (folder / 'status.json').is_file():
        raise FileExistsError(f'{folder} already has a status.json; use a new run name')
    run = Run(folder)
    status = dict(state='failed', backend='headless')
    job = None
    try:
        no_mods = bool(getattr(a, 'no_mods', False))
        detail = detail_of(getattr(a, 'detail', None))
        mod_set = [] if no_mods else getattr(a, 'mod', None)
        # --load: the save's header; its mod set unless one is given
        save_file = Path(a.load).resolve() if getattr(a, 'load', None) else None
        header = savegame.read_header(save_file) if save_file else None
        start_turn = header.turn if header else 0
        if header:
            write(run.dir / 'save-header.json', header.as_dict())
            run.log('load', quiet=quiet, save=str(save_file), turn=header.turn, civilization=header.civilization,
                    mods=header.mod_set, dllGuid=header.dllGuid)
            if a.target_turn <= header.turn:
                raise ValueError(f'the save is at turn {header.turn}: --turns {a.target_turn} must be after it')
            if mod_set is None and not getattr(a, 'mods_db', None):
                mod_set = load_mod_set(cfg, header)
                no_mods = mod_set == []
        # The DLL: --dll, a dll in civ5headless.toml, the explicit mod set's (or the game's own), else discovery's.
        if a.dll and getattr(a, 'dll_reason', None):
            dll, dll_reason = Path(a.dll).resolve(), a.dll_reason
        else:
            dll, dll_reason = modsdb.choose_dll(cfg, a.dll, mod_set, getattr(a, 'mods_dir', None))
        run.log('dll', quiet=quiet, dll=str(dll), reason=dll_reason)
        mods_dir, mods_db = modsdb.resolve(cfg, mod_set, getattr(a, 'mods_dir', None), getattr(a, 'mods_db', None),
                                           log=lambda m: run.log('mods_registry', quiet=quiet, message=m))
        loc_db = getattr(a, 'loc_db', None) or cfg.loc_db
        game_dir = getattr(a, 'game_dir', None) or cfg.require('game_dir', 'pass --game-dir')
        exe = dbcache.find_exe(a.exe, cfg)
        no_profiles = bool(getattr(a, 'no_profiles', False))
        profiles = None if no_profiles else getattr(a, 'profiles', None) or cfg.profiles
        profiles = Path(profiles).resolve() if profiles else None
        if not profiles and not no_profiles:
            run.log('warning', quiet=quiet, message="no profiles folder configured: the host looks for profiles\\ next to "
                    "its exe and in the folders above (set profiles in civ5headless.toml, or pass --profiles or --no-profiles)")
        fingerprint = fingerprint_options(a)
        lua_probes = probes.fingerprint_probes(cfg, run.dir, **fingerprint) if fingerprint else []
        user_probes = [Path(p).resolve() for p in getattr(a, 'lua_probe', None) or []]
        if user_probes:
            lua_probes += [probes.run_settings(run.dir)] + user_probes
        checkpoint_extras = probes.extra_files(getattr(a, 'checkpoint_extra', None))
        final_extras = probes.extra_files(getattr(a, 'final_extra', None))
        if checkpoint_extras or final_extras:
            (run.dir / probes.EXTRA_DIR).mkdir(exist_ok=True)  # ctx.extraDir, for files an extra writes itself
        db, dbinfo = dbcache.database(cfg, db=a.db, mods_dir=mods_dir, mods_db=mods_db, dll=dll, exe=exe,
                                      log=lambda m: run.log('database_built', quiet=quiet, message=m))
        if header:
            seed_key = load_key(header)
        else:
            resolved = resolve_spec(a.spec, db, getattr(a, 'seeds', None))
            write(run.dir / 'spec-resolved.json', resolved)
            seed_key = spec_key(resolved, a.spec)
        dll_sha = dbcache.sha256_file(dll)
        mask = a.improved_mask
        (run.dir / 'hh-before-arm.lua').write_text(BEFORE_ARM % dict(setup=SETUP, diagnostics=a.diagnostics,
                                                                       mask='nil' if mask is None else int(mask)), encoding='utf-8')
        (run.dir / 'hh-checkpoint.lua').write_text(probes.with_extra(CHECKPOINT % dict(strength=STRENGTH),
                                                                     probes.extra_lua('checkpoint', checkpoint_extras, run.dir)),
                                                   encoding='utf-8')
        (run.dir / 'hh-final.lua').write_text(probes.with_extra(FINAL, probes.extra_lua('final', final_extras, run.dir)),
                                              encoding='utf-8')
        game = ['loadgame', '--load', str(save_file)] if save_file else ['newgame', '--spec', str(run.dir / 'spec-resolved.json')]
        cmd = [str(exe)] + game + ['--out', str(run.dir.resolve()),
               '--dll', str(dll), '--db', str(db), '--turns', str(a.target_turn - start_turn),
               '--lua-before-arm', str(run.dir / 'hh-before-arm.lua'),
               '--checkpoint-lua', str(run.dir / 'hh-checkpoint.lua'), '--checkpoint-every', str(a.checkpoint_every),
               '--lua-before-dump', str(run.dir / 'hh-final.lua')]
        if no_mods:
            cmd += ['--no-mods']
        else:
            if mods_dir: cmd += ['--mods-dir', str(Path(mods_dir).resolve())]
            if mods_db: cmd += ['--mods-db', str(Path(mods_db).resolve())]
        if loc_db: cmd += ['--loc-db', str(Path(loc_db).resolve())]
        cmd += ['--game-dir', str(game_dir)]
        if no_profiles:
            cmd += ['--no-profiles']
        elif profiles:
            cmd += ['--profiles', str(profiles)]
        if getattr(a, 'no_addins', False): cmd += ['--no-addins']
        for name in getattr(a, 'skip_addin', None) or []:
            cmd += ['--skip-addin', name]
        for probe in lua_probes:
            cmd += ['--lua-probe', str(probe)]
        if getattr(a, 'heap_perturb', None): cmd += ['--heap-perturb', str(a.heap_perturb)]
        if getattr(a, 'frame_skew', None): cmd += ['--frame-skew', str(a.frame_skew)]
        cmd += save_host_args(a, exe)
        cmd += detail_host_args(detail, exe)
        cmd += list(a.host_arg or [])
        write(run.dir / 'hh-command.json', dict(command=cmd, exe=str(exe), exeSHA256=dbcache.sha256_file(exe), dll=str(dll),
                                                dllSHA256=dll_sha, dllReason=dll_reason, database=dbinfo,
                                                modsDb=str(mods_db) if mods_db else None, noMods=no_mods,
                                                mods=list(mod_set) if mod_set is not None else None,
                                                profiles=str(profiles) if profiles else None, fingerprint=fingerprint,
                                                detail=detail, load=str(save_file) if save_file else None, startTurn=start_turn,
                                                extras={kind: [dict(name=n, path=str(f), sha256=dbcache.sha256_file(f)) for n, f in files]
                                                        for kind, files in (('checkpoint', checkpoint_extras), ('final', final_extras))}))
        affinity = int(a.affinity, 0) if isinstance(a.affinity, str) else a.affinity
        cpu_limit = getattr(a, 'cpu_limit', None) or None
        parent_job = getattr(a, 'parent_job', None)  # a queue's job: a CPU cap its games share
        total_cpu_limit = parent_job.cpu_limit if parent_job else None
        share = min(x for x in (cpu_limit, getattr(a, 'cpu_share', None), 100) if x)  # of a core, at most
        slower = 100 / share  # the time limits count uncapped seconds
        maximum_seconds = a.maximum_seconds and round(a.maximum_seconds * slower)
        stall_seconds = a.stall_seconds and round(a.stall_seconds * slower)
        job = winproc.Job(memory_mb=a.max_memory_mb, affinity=affinity, cpu_limit=cpu_limit, within=parent_job)
        meter = winproc.LoadMeter().start()  # what else ran (the real game) during this run
        started = time.monotonic(); started_utc = utc()
        with open(run.dir / 'host-stdout.txt', 'wb') as out:
            proc = subprocess.Popen(cmd, cwd=str(run.dir), stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    creationflags=winproc.CREATE_NO_WINDOW)
            if parent_job:
                parent_job.assign(proc)  # first, so the game's job nests in the queue's
            job.assign(proc)
            efficiency_mode = False
            if getattr(a, 'efficiency_mode', False):
                efficiency_mode = winproc.set_efficiency_mode(proc)
                if not efficiency_mode:
                    run.log('efficiency_mode_unavailable', quiet=quiet, error='this Windows has no EcoQoS; idle priority only')
            elif getattr(a, 'low_priority', False):
                winproc.set_priority(proc)  # leave the foreground (and a real game) responsive
            run.log('launched', quiet=quiet, pid=proc.pid, affinity=hex(affinity) if affinity else None, memoryLimitMB=a.max_memory_mb,
                    cpuLimit=cpu_limit, totalCpuLimit=total_cpu_limit, efficiencyMode=efficiency_mode, dll=str(dll),
                    dllSHA256=dll_sha, target=a.target_turn)
            offset = 0; last_turn = start_turn; last_progress = started; seen = {start_turn: 0.0}; killed = None
            while True:
                try:
                    code = proc.wait(timeout=a.poll)
                    break
                except subprocess.TimeoutExpired:
                    pass
                rows, offset = turn_lines(run.dir / 'turn-times.jsonl', offset)
                now = time.monotonic()
                for r in rows:
                    seen[r['turn']] = round(r['seconds'], 3); last_progress = now
                    if r['turn'] % 10 == 0 or r['turn'] < start_turn + 3:
                        run.log('checkpoint', quiet=quiet, turn=r['turn'], seconds=round(r['seconds'], 1))
                    last_turn = r['turn']
                if maximum_seconds and now - started > maximum_seconds:
                    killed = f'maximum run time ({maximum_seconds} s) reached at turn {last_turn}'
                elif stall_seconds and now - last_progress > stall_seconds:
                    killed = f'no turn progress for {stall_seconds} s at turn {last_turn}'
                if killed:
                    job.terminate(3)
                    code = proc.wait()
                    break
            host_seconds = time.monotonic() - started
            rows, offset = turn_lines(run.dir / 'turn-times.jsonl', offset)
            for r in rows: seen[r['turn']] = round(r['seconds'], 3)
            pstats = winproc.process_stats(proc)
            load = meter.stop()
            load['otherBusyCores'] = round(load['systemBusyCores'] - (pstats.get('CPUSeconds') or 0) / max(host_seconds, 1e-6), 2)
            jstats = job.stats()
        report = read(run.dir / 'newgame-report.json', {}) or {}
        ng = report.get('newgame', {})
        before = read(run.dir / 'lua-before-arm.json') or {}
        final = read(run.dir / 'lua-before-dump.json') or {}
        extras = probes.collect_extras(run.dir, [n for n, _ in checkpoint_extras], [n for n, _ in final_extras], final)
        for x in extras:
            run.log('extra', quiet=quiet, **x)
        setup = dict(before.get('setup') or {})
        setup['improvedMask'] = before.get('improvedMask', -2)
        if mask is None and a.ab_role:
            setup['improvedMask'] = {'baseline': 0, 'test': -1}[a.ab_role]
        setup.update(backend='headless', abRole=a.ab_role, diagnosticsLevel=before.get('diagnosticsLevel'),
                     dllSHA256=dll_sha, specKey=seed_key)
        write(run.dir / 'setup.json', setup)
        if before.get('diagnosticsSet') is not None:
            run.log('diagnostics_enabled', quiet=quiet, level=before.get('diagnosticsSet'))
        if before.get('improvedMaskSet') is not None:
            run.log('improved_mask', quiet=quiet, mask=before.get('improvedMaskSet'))
        write(run.dir / 'turn-seen.json', {str(k): seen[k] for k in sorted(seen)})
        write(run.dir / 'replay-messages.json', final.get('messages') or [])
        write(run.dir / 'replay-data.json', final.get('replayData') or {})
        if final.get('players') or final.get('map'):
            write(run.dir / 'final-state.json', dict(turn=(final.get('victory') or {}).get('turn'), players=final.get('players') or [],
                                                     map=final.get('map')))
        finals = sorted((run.dir / 'checkpoints').glob('turn-*-final.json'))
        end_turn = max(seen) if seen else 0
        if finals:
            end_turn = max(end_turn, (read(finals[-1]) or {}).get('data', {}).get('turn', end_turn))
        wall = max(seen.values()) if seen else 0.0
        played = end_turn - start_turn
        saves = ng.get('saves') or report.get('saves') or []
        prefixes = sorted({p.name.rsplit('-', 1)[0] for p in (run.dir / 'Logs').glob('Stacking-*-r*-*.log')})
        process = dict(pstats, PeakCommit=jstats.get('peakProcessCommit'), HostSeconds=round(host_seconds, 1))
        status = dict(state='failed', backend='headless', startedUTC=started_utc, startTurn=start_turn, endTurn=end_turn, turns=played,
                      wallSeconds=round(wall, 1), secondsPerTurn=round(wall / played, 3) if played > 0 else None,
                      hostSeconds=round(host_seconds, 1), process=process, cpuLimit=cpu_limit, totalCpuLimit=total_cpu_limit,
                      efficiencyMode=efficiency_mode, pollSeconds=0, dll=str(dll), dllSHA256=dll_sha,
                      dllReason=dll_reason, detail=detail, save=seed_key,
                      spec=str(Path(a.spec).resolve()) if a.spec else None, replayMessages=len(final.get('messages') or []), exitCode=code,
                      hostStatus=report.get('status'), hostError=report.get('error') or ng.get('error'),
                      luaErrors=ng.get('lua_errors'), updates=(ng.get('autoplay') or {}).get('updates'),
                      background=load, diagnosticsLogPrefix=prefixes[-1] if prefixes else None, logsFolder=str((run.dir / 'Logs').resolve()))
        if save_file:
            status.update(loadedFrom=str(save_file), loadWarnings=(ng.get('load') or {}).get('warnings') or [])
        if extras:
            status['extras'] = extras
        if saves:
            status['saves'] = [dict(kind=x.get('kind'), turn=x.get('turn'), path=x.get('path'), bytes=x.get('bytes'))
                               for x in saves if not x.get('error')]
            final_save = [x['path'] for x in status['saves'] if x['kind'] == 'final']
            if final_save:
                status['finalSave'] = final_save[-1]
            for x in saves:
                if x.get('error'):
                    run.log('save_failed', quiet=quiet, kind=x.get('kind'), turn=x.get('turn'), error=x['error'])
        victory = final.get('victory') or {}
        if victory.get('winner', -1) is not None and victory.get('winner', -1) >= 0:
            status.update(endReason='victory', winner=victory['winner'], victory=victory.get('victory'))
            run.log('victory', quiet=quiet, turn=victory.get('turn'), winner=victory['winner'], victory=victory.get('victory'), seconds=round(wall))
        else:
            status['endReason'] = 'target_turn'
        dumps = sorted(str(p) for p in run.dir.rglob('*.dmp'))
        if dumps: status['minidumps'] = dumps
        if killed:
            status.update(error=killed, endReason='killed')
        elif code != 0 or report.get('status') != 'passed':
            status['error'] = f"host exit {code}, status {report.get('status')}: {status.get('hostError') or 'see host-stdout.txt and Logs/Headless.log'}"
            if a.max_memory_mb and jstats.get('peakProcessCommit', 0) >= a.max_memory_mb * 2 ** 20 * 0.95:
                status['error'] += f' (memory limit {a.max_memory_mb} MB reached)'
        elif status['endReason'] == 'target_turn' and end_turn < a.target_turn:
            status['error'] = f'autoplay stopped at turn {end_turn} before the target {a.target_turn}'
        else:
            status['state'] = 'completed'
        if status['state'] == 'completed' and status['endReason'] == 'target_turn':
            run.log('target_reached', quiet=quiet, turn=end_turn, seconds=round(wall, 1))
        write(run.dir / 'status.json', status)
        if status['state'] == 'completed':
            run.log('completed', quiet=quiet, turns=status['turns'], wallSeconds=status['wallSeconds'], secondsPerTurn=status['secondsPerTurn'],
                    hostSeconds=status['hostSeconds'], peakWorkingSetMB=round((process.get('PeakWorkingSet') or 0) / 2 ** 20))
        else:
            run.log('failed', quiet=quiet, error=status.get('error'))
        finish(run, detail, getattr(a, 'report', True), quiet)
        return 0 if status['state'] == 'completed' else 1
    except BaseException as error:  # noqa: BLE001 (KeyboardInterrupt included: the job kills the host)
        status.update(error=f'{type(error).__name__}: {error}', utc=utc())
        write(run.dir / 'status.json', status)
        run.log('failed', quiet=quiet, error=status['error'])
        if isinstance(error, KeyboardInterrupt):
            raise
        finish(run, None, getattr(a, 'report', True), quiet)
        return 1
    finally:
        if job:
            job.close()


def finish(run, detail, report=True, quiet=True):
    """After a game: the run report (report.html, from what the folder holds before tidying) and the detail level's
    tidying (detail None: none). A report that fails is logged in events.jsonl, never fatal."""
    model = None
    if report:
        try:
            from . import report as run_report
            model = run_report.collect(run.dir)
        except Exception as error:  # noqa: BLE001
            run.log('report_failed', quiet=quiet, error=f'{type(error).__name__}: {error}')
    if detail:
        tidy(run.dir, detail)
    if model is not None:
        try:
            path = run_report.write(run.dir, model)
            run.log('report', quiet=quiet, path=str(path), bytes=path.stat().st_size)
        except Exception as error:  # noqa: BLE001
            run.log('report_failed', quiet=quiet, error=f'{type(error).__name__}: {error}')


@dataclass
class Result:
    """One played game: the run folder, whether it completed, its status.json and the host's report."""
    dir: Path
    ok: bool
    status: dict

    @property
    def report(self):
        """host-report.json when the host writes one (plan 9.2), else newgame-report.json."""
        return read(self.dir / 'host-report.json') or read(self.dir / 'newgame-report.json', {}) or {}

    @property
    def digest_log(self):
        return self.dir / 'Logs/HeadlessDigest.log'

    @property
    def lua_log(self):
        return self.dir / 'Logs/Lua.log'


def play(spec, turns, *, run=None, run_dir=None, cfg=None, seeds=None, dll=None, db=None, mods_dir=None,
         mods_db=None, mods=None, no_mods=False, loc_db=None, game_dir=None, exe=None, profiles=None, no_profiles=False,
         skip_addins=(), no_addins=False, fingerprint=False, lua_probes=(), checkpoint_extras=(), final_extras=(),
         heap_perturb=None, frame_skew=None,
         diagnostics=0, improved_mask=None, ab_role=None, checkpoint_every=1, max_seconds=21600, stall_seconds=900,
         max_memory_mb=3500, affinity=None, low_priority=False, cpu_limit=None, efficiency_mode=False, detail=None,
         report=True, prune=False, host_args=(), poll=1.0, quiet=True, load=None, autosave=None, save_at=(), save_final=True):
    """Play one game in civ5headless.exe and return its Result.

    spec: a spec file (resolved or not) or a spec dict (written to the run folder as spec.json), or a .Civ5Save to
    continue (or None with load=SAVE; its mod set unless mods, no_mods or mods_db is given); turns: autoplay to this
    turn; autosave: None or 'none' (default), 'game' or a number of turns; save_at: turns to save at; save_final: save
    the last turn (default True); the saves go to <run>/saves;
    run / run_dir: the run folder (<runs>/run, or run_dir; default <runs>/<spec or save stem>-<date>-<time>);
    cfg: a config.Config (default config.load()); seeds: {'map': M, 'sync': S} over the spec's;
    mods: the mod set as 'ID[:VER]' strings (a registry is generated; instead of mods_db); no_mods: play without mods;
    profiles / no_profiles, skip_addins / no_addins: the host's UI emulation profiles and InGameUIAddins;
    fingerprint: True, or dict(every=K, turns=[...], split=N), runs the Lua fingerprint probe before lua_probes;
    lua_probes: files for the host's --lua-probe; checkpoint_extras, final_extras: Lua files whose results go into
    the checkpoints and extra/NAME.jsonl, and into extra/NAME-final.json (docs/probes.md);
    heap_perturb, frame_skew: the host options of the same name;
    detail: 'summary', 'standard' (default) or 'full', what the run folder keeps (prune=True, the old option, is
    'standard'); report: write report.html; host_args: further host arguments. The other keywords are the c5h run
    options of the same name; anything left None comes from the configuration (the DLL: modsdb.choose_dll). Raises
    FileExistsError when the run folder already holds a game, ValueError for an unknown detail level or a cpu_limit
    outside 1-100."""
    detail = detail_of(detail)
    if cpu_limit is not None and not 1 <= cpu_limit <= 100:
        raise ValueError(f'cpu_limit is a percent of one core, 1 to 100, not {cpu_limit!r}')
    cfg = cfg or config.load()
    if spec is not None and not isinstance(spec, dict) and savegame.is_save(spec):
        if load is not None:
            raise ValueError('play takes a save as spec or as load=, not both')
        spec, load = None, spec
    if (spec is None) == (load is None):
        raise ValueError('play needs a spec or a save to load (load=), not both')
    if isinstance(spec, dict):
        folder = Path(run_dir).resolve() if run_dir else cfg.require('runs') / (run or f'spec-{datetime.now():%Y%m%d-%H%M%S}')
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'spec.json').write_text(json.dumps(spec, indent=1) + '\n', encoding='utf-8')
        spec, run_dir = folder / 'spec.json', folder
    a = SimpleNamespace(run=run, run_dir=str(run_dir) if run_dir else None, spec=str(spec) if spec else None,
                        load=str(load) if load else None, autosave=None if autosave is None else str(autosave),
                        save_at=[int(t) for t in save_at or []], save_final=bool(save_final),
                        target_turn=int(turns),
                        seeds=dict(seeds) if seeds else None, dll=dll, db=db, mods_dir=mods_dir, mods_db=mods_db,
                        mod=list(mods) if mods is not None else None, no_mods=no_mods, loc_db=loc_db,
                        game_dir=game_dir, exe=exe, profiles=profiles, no_profiles=no_profiles,
                        skip_addin=list(skip_addins or []), no_addins=no_addins, fingerprint=fingerprint,
                        lua_probe=list(lua_probes or []), checkpoint_extra=list(checkpoint_extras or []),
                        final_extra=list(final_extras or []),
                        heap_perturb=heap_perturb, frame_skew=frame_skew, diagnostics=int(diagnostics or 0),
                        improved_mask=improved_mask, ab_role=ab_role, checkpoint_every=int(checkpoint_every or 1),
                        maximum_seconds=max_seconds, stall_seconds=stall_seconds, max_memory_mb=max_memory_mb or None,
                        affinity=affinity, low_priority=low_priority, cpu_limit=cpu_limit,
                        efficiency_mode=bool(efficiency_mode), detail=detail, report=bool(report),
                        host_arg=list(host_args or []), poll=poll)
    folder = run_folder(a, cfg)
    a.run_dir = str(folder)
    code = newgame(a, quiet=quiet, cfg=cfg)
    status = read(folder / 'status.json', {}) or {}
    return Result(folder, code == 0 and status.get('state') == 'completed', status)


def add_arguments(n):
    """The c5h run options (queue.py builds the same namespace from a queue's jobs)."""
    n.add_argument('spec', nargs='?', help='game spec (spec.py format, resolved or not), or a .Civ5Save to continue '
                   '(as --load)')
    n.add_argument('--load', metavar='SAVE', help="continue this saved game (.Civ5Save, the real game's or the host's) "
                   'instead of starting one from a spec')
    n.add_argument('--turns', '--target-turn', dest='target_turn', type=int, required=True,
                   help="autoplay to this turn (with --load: a turn after the save's)")
    n.add_argument('--run', help='run name: <runs>/NAME (default <spec stem>-<date>-<time>)')
    n.add_argument('--run-dir', help='run folder (instead of --run)')
    n.add_argument('--seeds', nargs=2, type=int, metavar=('MAP', 'SYNC'), help="map and sync seeds over the spec's")
    n.add_argument('--dll', help='game DLL (default: config dll)')
    n.add_argument('--db', help='built game database (default: config db, else the cached build from the mods and DLL)')
    n.add_argument('--mods-dir', help='MODS folder or pinned copy, read only (default: config mods_dir)')
    n.add_argument('--mods-db', help='Civ5ModsDatabase.db with the activated mods (default: config mods_db)')
    n.add_argument('--mod', action='append', help='activated mod GUID[:version] (repeatable): the mod set, from a '
                   'generated registry instead of --mods-db')
    n.add_argument('--no-mods', action='store_true', help='play without mods')
    n.add_argument('--loc-db', help='Localization-Merged.db (default: config loc_db)')
    n.add_argument('--game-dir', help='game folder (default: config game_dir)')
    n.add_argument('--exe', help='civ5headless.exe (default: config exe)')
    n.add_argument('--profiles', help='UI emulation profiles folder (default: config profiles)')
    n.add_argument('--no-profiles', action='store_true', help='run no UI emulation profile')
    n.add_argument('--skip-addin', action='append', help="leave out this InGameUIAddin: its file name without extension, "
                   "e.g. SmallResourceIcons (repeatable)")
    n.add_argument('--no-addins', action='store_true', help="run none of the mods' InGameUIAddins")
    n.add_argument('--fingerprint', action='store_true', help='run the Lua fingerprint probe (probes/fingerprint.lua)')
    n.add_argument('--fingerprint-every', type=int, help='fingerprint every K-th turn (default every turn; implies --fingerprint)')
    n.add_argument('--fingerprint-turns', help='fingerprint these turns too, e.g. 1,50,100 (implies --fingerprint)')
    n.add_argument('--fingerprint-split', type=int, help='fingerprint sections per Lua.log line (default 12)')
    n.add_argument('--lua-probe', action='append', help='Lua file the host runs once the game has started (repeatable)')
    n.add_argument('--checkpoint-extra', action='append', metavar='FILE', help='Lua file run at every checkpoint; what it '
                   'returns goes into the checkpoint and extra/NAME.jsonl (repeatable; docs/probes.md)')
    n.add_argument('--final-extra', action='append', metavar='FILE', help='Lua file run once after the last turn; what it '
                   'returns goes into extra/NAME-final.json (repeatable; docs/probes.md)')
    n.add_argument('--heap-perturb', type=int, help='host heap layout perturbation seed (0: off)')
    n.add_argument('--frame-skew', type=int, help='an extra engine update before every K-th one')
    n.add_argument('--diagnostics', type=int, default=0, help='Stacking diagnostics level during autoplay (behaviour runs)')
    n.add_argument('--improved-mask', type=int, default=None, help='A/B: Game.SetStackingAIImprovedMask(N) before arming (DLLs with the switch)')
    n.add_argument('--ab-role', choices=['baseline', 'test'], help='A/B of two DLL builds: record improvedMask 0 or -1 in setup.json')
    n.add_argument('--checkpoint-every', type=int, default=1, help='checkpoint every n turns (default 1)')
    n.add_argument('--maximum-seconds', type=int, default=21600)
    n.add_argument('--stall-seconds', type=int, default=900, help='kill the host after this long without a new turn')
    n.add_argument('--max-memory-mb', type=int, default=3500, help='per-process commit limit (job object); 0 = none')
    n.add_argument('--affinity', default=None, help='CPU affinity mask for the host (e.g. 0x3)')
    n.add_argument('--low-priority', action='store_true', help='run the host below normal priority')
    n.add_argument('--cpu-limit', type=int, metavar='PERCENT', help='cap the host at this percent of one core (1-100; '
                   'hard cap, the game plays the same, only slower); the time limits stretch to match')
    n.add_argument('--efficiency-mode', action='store_true', help="Windows efficiency mode, as Task Manager's: EcoQoS "
                   '(lower clocks, efficient cores) and idle priority')
    n.add_argument('--detail', choices=DETAILS, help='what the run folder keeps: summary (results, timings, Lua.log, the '
                   'report), standard (default: also per-turn checkpoints, digests and the small logs) or full (also state '
                   'dumps, the database copies and the DLL\'s AI logs)')
    n.add_argument('--prune', action='store_true', help='the old name of --detail standard')
    n.add_argument('--no-report', dest='report', action='store_false', help='no report.html (c5h report RUN writes it later)')
    n.add_argument('--poll', type=float, default=1.0, help='progress poll interval in seconds')
    n.add_argument('--autosave', default='none', help="autosaves: none (default), game (the game's own setting, "
                   'UserSettings.ini) or N (every N turns)')
    n.add_argument('--save-at', type=int, action='append', metavar='TURN', help='save at this turn (repeatable)')
    n.add_argument('--no-save-final', dest='save_final', action='store_false',
                   help='do not save the last turn (by default <run>/saves/Final_<turn and date>.Civ5Save)')
    n.add_argument('--host-arg', action='append', help='extra civ5headless.exe argument (repeatable), e.g. --host-arg=--no-game-logging')


def autosave_mode(value):
    """'none', 'game' or a positive number of turns as text; raises ValueError otherwise."""
    value = str(value if value is not None else 'none').strip().lower()
    if value in ('none', 'game') or (value.isdigit() and int(value) > 0):
        return str(int(value)) if value.isdigit() else value
    raise ValueError(f'autosave is none, game or a number of turns, not {value!r}')


def check_args(a, error):
    if a.target_turn <= 0: error('--turns N (> 0) is required')
    if a.spec and savegame.is_save(a.spec):
        if a.load: error('give the save as SPEC or as --load, not both')
        a.spec, a.load = None, a.spec
    if bool(a.spec) == bool(a.load): error('give a spec or --load SAVE (one of them)')
    if a.load and a.seeds: error('--seeds does not apply to --load (the save carries its random state)')
    try:
        a.autosave = autosave_mode(a.autosave)
    except ValueError as e:
        error(f'--{e}')
    if any(t <= 0 for t in a.save_at or []): error('--save-at takes turn numbers > 0')
    if a.improved_mask is not None and a.ab_role: error('--improved-mask and --ab-role exclude each other')
    if a.mod and a.no_mods: error('--mod and --no-mods exclude each other')
    if (a.mod or a.no_mods) and a.mods_db: error('--mod and --no-mods exclude --mods-db (the registry is generated)')
    if a.profiles and a.no_profiles: error('--profiles and --no-profiles exclude each other')
    if a.prune and a.detail not in (None, 'standard'): error('--prune is --detail standard; it excludes --detail ' + a.detail)
    a.detail = detail_of(a.detail)
    try:
        fingerprint_options(a)
    except ValueError:
        error('--fingerprint-turns takes turn numbers, e.g. 1,50,100')
    for option in ('checkpoint_extra', 'final_extra'):
        try:
            probes.extra_files(getattr(a, option, None))
        except ValueError as e:
            error(f"--{option.replace('_', '-')}: {e}")
    if a.max_memory_mb == 0: a.max_memory_mb = None
    if a.cpu_limit is not None and not 1 <= a.cpu_limit <= 100: error('--cpu-limit takes a percent of one core, 1 to 100')
    a.seeds =dict(map=a.seeds[0], sync=a.seeds[1]) if a.seeds else None


def main(argv=None, cfg=None):
    p = argparse.ArgumentParser(prog='c5h run', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_arguments(p)
    a = p.parse_args(argv)
    check_args(a, p.error)
    try:
        return newgame(a, cfg=cfg or config.load())
    except FileExistsError as error:
        print(f'c5h run: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
