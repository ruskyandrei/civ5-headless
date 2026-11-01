"""Lua probe files for host games (--lua-probe) and real-game sessions (RealGameSession.start_game(lua_probes=...)).

Both run the probes, in order, in the InGame context once the game has started, before turn 0 is processed, so the
same files give the same lines in Lua.log on both sides.

  fingerprint_probes(cfg, folder, every=None, turns=None, split=None) -> [Path]

is the fingerprint probe (<probes>/fingerprint.lua, plan 9.3; read with civ5headless.fingerprint) preceded, when any
setting is given, by a generated fingerprint-settings.lua in folder that sets its configuration globals:
C5HFP_EVERY (fingerprint every k-th turn; default every turn), C5HFP_TURNS (these turns too) and C5HFP_SPLIT
(sections per printed line).

  run_settings(folder) -> Path

is the generated probe-settings.lua that c5h run puts before your --lua-probe files: C5H_RUN_DIR (the run folder) and
C5H_EXTRA_DIR (its extra folder, for files a probe writes).

Extras (c5h run --checkpoint-extra / --final-extra, docs/probes.md) are your own Lua files whose return values c5h
adds to the run's JSON: extra_lua(kind, files, folder) is the Lua that runs them at the end of c5h's checkpoint chunk
(kind 'checkpoint', every checkpoint) or final chunk (kind 'final', once after the last turn), with_extra(chunk, lua)
puts it there, and collect_extras(folder, ...) writes what they returned to extra/NAME.jsonl (one row per checkpoint)
and extra/NAME-final.json after the game.
"""
from __future__ import annotations
import json
from pathlib import Path
import re

from . import config

FINGERPRINT = 'fingerprint.lua'
SETTINGS = 'fingerprint-settings.lua'
RUN_SETTINGS = 'probe-settings.lua'
EXTRA_DIR = 'extra'
EXTRA_KINDS = ('checkpoint', 'final')


def probes_dir(cfg, probes=None):
    """The probes folder: probes if given, else the configured one. Raises config.ConfigError."""
    return Path(probes) if probes else cfg.require('probes', 'pass the probes folder')


def fingerprint_lua(cfg, probes=None):
    """<probes>/fingerprint.lua. Raises config.ConfigError when it is missing."""
    path = probes_dir(cfg, probes) / FINGERPRINT
    if not path.is_file():
        raise config.ConfigError(f'{path} not found (probes in {config.FILE_NAME} names the folder holding {FINGERPRINT})')
    return path


def fingerprint_options(value, every=None, turns=None, split=None):
    """dict(every, turns, split) when fingerprints are wanted, else None. value: a flag, or a dict with those keys;
    every, turns (a list, or a string such as '1,50,100') and split fill what it leaves out and each implies it."""
    if isinstance(value, dict):
        unknown = set(value) - {'every', 'turns', 'split'}
        if unknown:
            raise ValueError(f'unknown fingerprint settings: {", ".join(sorted(unknown))} (every, turns, split)')
        every, turns, split = value.get('every', every), value.get('turns', turns), value.get('split', split)
    if not (value or every or turns or split):
        return None
    if isinstance(turns, str):
        turns = turns.replace(',', ' ').split()
    return dict(every=every, turns=[int(t) for t in turns or []], split=split)


def fingerprint_settings(every=None, turns=None, split=None):
    """Lua that sets the fingerprint probe's configuration globals, or '' when nothing is set."""
    lines = []
    if every is not None:
        if int(every) < 1:
            raise ValueError('fingerprint every must be at least 1')
        lines.append(f'C5HFP_EVERY = {int(every)}')
    if turns:
        lines.append('C5HFP_TURNS = {' + ', '.join(str(int(t)) for t in turns) + '}')
    if split is not None:
        if int(split) < 1:
            raise ValueError('fingerprint split must be at least 1')
        lines.append(f'C5HFP_SPLIT = {int(split)}')
    if not lines:
        return ''
    return '-- civ5headless: fingerprint probe settings (read by fingerprint.lua, which runs next)\n' + '\n'.join(lines) + '\n'


def fingerprint_probes(cfg, folder, every=None, turns=None, split=None, probes=None):
    """The files that run the fingerprint probe with these settings, in order (settings file written to folder)."""
    probe = fingerprint_lua(cfg, probes)
    text = fingerprint_settings(every, turns, split)
    if not text:
        return [probe]
    settings = Path(folder) / SETTINGS
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(text, encoding='utf-8')
    return [settings, probe]


def long_string(text):
    """text as a Lua long string literal: [[...]], or [=[...]=] and so on when text holds the closing brackets. The
    newline after the opening bracket is dropped by Lua, so a file's first line stays line 1."""
    level = 0
    while ']' + '=' * level + ']' in text + ']':
        level += 1
    eq = '=' * level
    return f'[{eq}[\n{text}]{eq}]'


def run_settings(folder):
    """Writes <folder>/probe-settings.lua, which sets C5H_RUN_DIR and C5H_EXTRA_DIR (created here) for the --lua-probe
    files that run after it, and returns its path."""
    folder = Path(folder).resolve()
    (folder / EXTRA_DIR).mkdir(parents=True, exist_ok=True)
    path = folder / RUN_SETTINGS
    path.write_text('-- civ5headless: settings for the --lua-probe files that run next (docs/probes.md)\n'
                    f'C5H_RUN_DIR = {long_string(str(folder))}\n'
                    f'C5H_EXTRA_DIR = {long_string(str(folder / EXTRA_DIR))}\n', encoding='utf-8')
    return path


def extra_files(files):
    """[(name, Path)] for extra Lua files, the name being the file's stem. Raises ValueError for a file that is not
    there and for two files of one name (their results would share extra/NAME)."""
    out, seen = [], {}
    for f in files or []:
        path = Path(f).resolve()
        if not path.is_file():
            raise ValueError(f'extra Lua file {f} not found')
        name = path.stem
        if name.lower() in seen:
            raise ValueError(f'two extra Lua files are named {name}: {seen[name.lower()]} and {path} (rename one)')
        seen[name.lower()] = path
        out.append((name, path))
    return out


# Runs at the end of c5h's checkpoint or final chunk, where out is the table the chunk returns. Each file is compiled
# with loadstring (its own name in error messages, without the '@' this Lua leaves in front) and called with ctx; what
# it returns goes into out.extra[name], copied with non-finite numbers made nil (they are not JSON) and cycles cut, and
# an error into out.extraErrors[name] and Lua.log. ctx.state is a table kept for the file between calls (in _G). Each
# call gets fresh globals of its own, as the host keeps one thread for the checkpoint chunk.
EXTRA = r'''-- c5h: --%(kind)s-extra files (docs/probes.md)
do
 local all=_G.C5H_EXTRA_STATE or {};_G.C5H_EXTRA_STATE=all
 local env=getfenv(1)
 local huge=math.huge
 local function clean(x,seen)
  local t=type(x)
  if t=='number' then if x~=x or x==huge or x==-huge then return nil end;return x end
  if t~='table' then return x end
  if seen[x] then return '(cycle)' end
  seen[x]=true
  local c={};for k,v in pairs(x) do c[k]=clean(v,seen) end
  seen[x]=nil
  return c
 end
 local extra,errors={},{}
 local function run(name,path,source)
  local key='%(kind)s:'..name
  local state=all[key] or {};all[key]=state
  local f,err=loadstring(source,'@'..path)
  local ok,r=false,err
  if f then setfenv(f,setmetatable({},{__index=env}));ok,r=pcall(f,{name=name,kind='%(kind)s',turn=Game.GetGameTurn(),state=state,runDir=%(run_dir)s,extraDir=%(extra_dir)s}) end
  if ok then extra[name]=clean(r,{}) else
   local message=(string.gsub(tostring(r),'^@',''))
   errors[name]=message;print('c5h %(kind)s extra '..name..': '..message)
  end
 end
%(calls)s
 out.extra=extra
 if next(errors) then out.extraErrors=errors end
end
'''


def extra_lua(kind, files, folder):
    """The Lua that runs the extra files [(name, Path)] (extra_files) at the end of c5h's checkpoint or final chunk
    (kind), or '' without files."""
    if kind not in EXTRA_KINDS:
        raise ValueError(f'extra kind is one of {", ".join(EXTRA_KINDS)}, not {kind!r}')
    if not files:
        return ''
    folder = Path(folder).resolve()
    calls = []
    for name, path in files:
        source = Path(path).read_text(encoding='utf-8-sig')
        calls.append(f' run({long_string(name)},{long_string(str(path))},{long_string(source)})')
    return EXTRA % dict(kind=kind, run_dir=long_string(str(folder)), extra_dir=long_string(str(folder / EXTRA_DIR)),
                        calls='\n'.join(calls))


def with_extra(chunk, lua):
    """chunk (one that ends with 'return out') with lua run just before that return; chunk itself when lua is ''."""
    if not lua:
        return chunk
    at = chunk.rindex('return out')
    return chunk[:at] + lua + chunk[at:]


CHECKPOINT_FILE = re.compile(r'turn-(\d+)(-final)?\.json$')


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        return None


def collect_extras(folder, checkpoint_names=(), final_names=(), final=None):
    """After a game: writes <folder>/extra/NAME.jsonl for each checkpoint extra (one row per checkpoint the extras ran
    in, in turn order, the final checkpoint last: turn, final, seconds, then value or error; not the start turn's
    checkpoint, which the host takes with its own Lua) and extra/NAME-final.json for each final extra (its value, or
    {"error": ...}), from the checkpoints and final, the final chunk's result (lua-before-dump.json).
    Returns one summary per extra: kind, name, path, rows, errors and the first error."""
    folder = Path(folder)
    out_dir = folder / EXTRA_DIR
    summaries = []
    if not (checkpoint_names or final_names):
        return summaries
    out_dir.mkdir(parents=True, exist_ok=True)
    points = []
    for p in (folder / 'checkpoints').glob('turn-*.json'):
        m = CHECKPOINT_FILE.search(p.name)
        if m:
            points.append((int(m.group(1)), bool(m.group(2)), p))
    points.sort(key=lambda x: (x[1], x[0]))  # the per-turn checkpoints by turn, then the final one
    checkpoints = []
    for _, final_point, p in points:
        cp = _read(p) or {}
        data = cp.get('data') or {}
        if 'extra' in data or 'extraErrors' in data:  # not the start turn's checkpoint: it runs no extras
            checkpoints.append((final_point, cp))
    for name in checkpoint_names:
        path = out_dir / f'{name}.jsonl'
        rows, errors, first = 0, 0, None
        with path.open('w', encoding='utf-8', newline='\n') as f:
            for final_point, cp in checkpoints:
                data = cp.get('data') or {}
                row = dict(turn=data.get('turn'), final=final_point, seconds=cp.get('seconds'))
                error = (data.get('extraErrors') or {}).get(name)
                if error is not None:
                    row['error'] = error
                    errors += 1
                    first = first or error
                else:
                    row['value'] = (data.get('extra') or {}).get(name)
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
                rows += 1
        summaries.append(dict(kind='checkpoint', name=name, path=str(path), rows=rows, errors=errors, firstError=first))
    final = final or {}
    for name in final_names:
        path = out_dir / f'{name}-final.json'
        error = (final.get('extraErrors') or {}).get(name)
        value = dict(error=error) if error is not None else (final.get('extra') or {}).get(name)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        summaries.append(dict(kind='final', name=name, path=str(path), rows=1 if final else 0,
                              errors=int(error is not None), firstError=error))
    return summaries
