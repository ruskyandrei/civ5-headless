"""Pin a real game's seeds through config.ini, and put the file back afterwards.

The stock game code reads `MapRandSeed` and `SyncRandSeed` from the [CONFIG] section when the game DLL loads
(CvPreGame::loadFromIni; 0 means the clock). With both set, a game started from the menu uses them for everything
the pregame seeds decide, the random civs and city-states included, so the host can replay it from the same seeds
with any DLL. They must be in the file before the game (or the mods' DLL) loads.

Edits change only the value on those two lines (or add them to [CONFIG]); comments, order and line endings stay.
The original values are restored with restore(marker); pin() records them in a marker file first, so a session that
died can be cleaned up by the next one, and `c5h doctor` warns about pinned seeds.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import shutil

KEYS = ('MapRandSeed', 'SyncRandSeed')
MARKER = 'config-ini-seeds.json'
SECTION = 'CONFIG'
MAX_SEED = 2**31 - 1
LOG_FLAGS = (('AILog', 1), ('AIPerfLog', 2), ('BuilderAILog', 4), ('PlayerAndCityAILogSplit', 8))  # --game-log-flags


class SeedError(Exception):
    pass


def _split(text):
    return text.splitlines(keepends=True)


def read(config_ini):
    """{'MapRandSeed': int, 'SyncRandSeed': int} (0 when a key is missing or not a number)."""
    out = dict.fromkeys(KEYS, 0)
    section = None
    for line in _split(Path(config_ini).read_text(encoding='utf-8-sig', errors='replace')):
        s = line.strip()
        m = re.fullmatch(r'\[(.+)\]', s)
        if m:
            section = m.group(1).strip()
            continue
        if section != SECTION or '=' not in s or s.startswith(';'):
            continue
        key, _, value = s.partition('=')
        if key.strip() in KEYS:
            try:
                out[key.strip()] = int(value.strip())
            except ValueError:
                pass
    return out


def ini_value(config_ini, key):
    """The value of key in any section of config.ini (the first one), or None."""
    for line in _split(Path(config_ini).read_text(encoding='utf-8-sig', errors='replace')):
        s = line.strip()
        if '=' in s and not s.startswith(';') and s.partition('=')[0].strip() == key:
            return s.partition('=')[2].strip()
    return None


def host_log_args(config_ini):
    """The civ5headless.exe options that log as a real game with this config.ini does. A host game compared with a real
    one needs them: with some DLLs AILog changes the game (docs/ai-logging.md)."""
    if ini_value(config_ini, 'LoggingEnabled') != '1':
        return ['--no-game-logging']
    return ['--game-log-flags', str(sum(bit for key, bit in LOG_FLAGS if ini_value(config_ini, key) == '1'))]


def edit(text, values):
    """text with the [CONFIG] values replaced (or added at the end of the section)."""
    lines = _split(text)
    newline = '\r\n' if '\r\n' in text else '\n'
    section, seen, end_of_config = None, set(), None
    for i, line in enumerate(lines):
        s = line.strip()
        m = re.fullmatch(r'\[(.+)\]', s)
        if m:
            if section == SECTION and end_of_config is None:
                end_of_config = i
            section = m.group(1).strip()
            continue
        if section != SECTION or s.startswith(';') or '=' not in s:
            continue
        key = s.partition('=')[0].strip()
        if key in values:
            ending = line[len(line.rstrip('\r\n')):]
            lines[i] = f'{key} = {values[key]}' + (ending or newline)
            seen.add(key)
    missing = [k for k in values if k not in seen]
    if missing:
        add = [f'{k} = {values[k]}{newline}' for k in missing]
        if section == SECTION and end_of_config is None:
            end_of_config = len(lines)
        if end_of_config is None:  # no [CONFIG] section
            if lines and not lines[-1].endswith(('\n', '\r')):
                lines[-1] += newline
            lines += [f'[{SECTION}]{newline}'] + add
        else:
            at = end_of_config
            while at > 0 and not lines[at - 1].strip():
                at -= 1  # before the blank lines that end the section
            if at > 0 and not lines[at - 1].endswith(('\n', '\r')):
                lines[at - 1] += newline
            lines[at:at] = add
    return ''.join(lines)


def _write(config_ini, values):
    path = Path(config_ini)
    raw = path.read_bytes()
    bom = raw.startswith(b'\xef\xbb\xbf')
    text = edit(raw.decode('utf-8-sig', errors='surrogateescape'), values)
    path.write_bytes((b'\xef\xbb\xbf' if bom else b'') + text.encode('utf-8', errors='surrogateescape'))


def pin(config_ini, map_seed, sync_seed, marker, backup=None):
    """Write the seeds (None for either: 0, the clock). The first pin records the original values in the marker file
    (and copies the whole file to backup); returns the values written. One game runs at a time, so one marker
    (the session uses <workspace>/cache/config-ini-seeds.json) covers every session."""
    for seed in (map_seed, sync_seed):
        if seed is not None and not 1 <= int(seed) <= MAX_SEED:
            raise SeedError(f'seed {seed} is outside 1..{MAX_SEED} (0 would mean the clock)')
    config_ini, marker = Path(config_ini), Path(marker)
    if not config_ini.is_file():
        raise SeedError(f'{config_ini} not found (the game writes it at its first start)')
    if not marker.is_file():
        if backup:
            Path(backup).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(config_ini, backup)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(dict(file=str(config_ini), original=read(config_ini)), indent=1) + '\n', encoding='utf-8')
    values = {'MapRandSeed': int(map_seed or 0), 'SyncRandSeed': int(sync_seed or 0)}
    _write(config_ini, values)
    return values


def restore(marker):
    """Put the original values back (only those two lines) and remove the marker; returns them, or None when
    nothing was pinned. Also cleans up after a session that died before restoring."""
    marker = Path(marker)
    if not marker.is_file():
        return None
    data = json.loads(marker.read_text(encoding='utf-8'))
    path = Path(data['file'])
    if path.is_file():
        _write(path, data['original'])
    marker.unlink()
    return data['original']
