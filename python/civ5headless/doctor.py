"""c5h doctor: the configuration, where each value came from, and what is missing.

  c5h doctor [--json]

Prints every configuration value with its source (file, discovery, environment, default, unset) and the activated
mods, then checks, in three groups:
  host games         Python, the game folder (the Firaxis DLLs and ICU data the host loads), the DLL, the mods and
                     localization databases, the host exe, the UI emulation profiles and the fingerprint probe, and
                     the game database (db, a snapshot, or what the DLC snapshot is built from);
  real game          the game executable, config.ini (EnableTuner = 1, LoggingEnabled = 1, seeds left pinned); the
                     file is only read, never edited;
  building the host  the VC9 toolchain (tools/toolchain.py; python tools/get_toolchain.py makes one).
Exit code 1 when a host-game check fails; real-game and toolchain problems are warnings.
"""
import argparse
import importlib.util
import json
import sys

from . import config

FIRAXIS_DLLS = ('CvGameDatabaseWin32Final Release.dll', 'CvLocalizationWin32Final Release.dll', 'lua51_Win32.dll')
HOST, REAL, BUILD = 'host games', 'real game', 'building the host'


def checks(cfg):
    """[(group, status, what, detail)]; status ok, warn or FAIL."""
    out = []

    def add(group, status, what, detail):
        out.append((group, status, what, detail))

    def path_check(key, kind, why, severity='FAIL', group=HOST):
        p = getattr(cfg, key)
        if p is None:
            add(group, severity, key, f'not set ({why}): ' + cfg.sources[key].note)
            return False
        exists = p.is_dir() if kind == 'dir' else p.is_file()
        add(group, 'ok' if exists else severity, key, str(p) + ('' if exists else f' does not exist ({why})'))
        return exists

    v = sys.version_info
    add(HOST, 'ok' if v >= (3, 11) else 'FAIL', 'Python', f'{sys.version.split()[0]} ({sys.executable})'
        + ('' if v >= (3, 11) else '; 3.11 or newer is needed'))
    if path_check('game_dir', 'dir', 'the host loads the Firaxis DLLs from it'):
        missing = [n for n in FIRAXIS_DLLS + ('Locale/icudt44l.dat',) if not (cfg.game_dir / n).is_file()]
        add(HOST, 'FAIL' if missing else 'ok', 'game files',
            'missing ' + ', '.join(missing) if missing else 'Firaxis database, localization and Lua DLLs, ICU data')
    if path_check('dll', 'file', 'the game DLL to run'):
        add(HOST, 'ok', 'DLL source', cfg.sources['dll'].note or cfg.sources['dll'].kind)
    path_check('mods_dir', 'dir', 'the mods the host loads', 'warn')
    path_check('mods_db', 'file', 'the activated mods', 'warn')
    if cfg.mods_db and cfg.mods_db.is_file():
        add(HOST, 'ok' if cfg.active_mods else 'warn', 'active mods',
            f'{len(cfg.active_mods)} activated in {cfg.mods_db.name}' if cfg.active_mods else
            'none activated (the game lists none while it sits in the mods browser; pin a copy taken in a '
            'modded session, or give the mod set with --mod)')
        if cfg.active_mods and cfg.mods_dir and cfg.mods_dir.is_dir():
            folders = config.mod_folders(cfg.mods_dir)
            absent = [f'{m.name or m.id} v{m.version}' for m in cfg.active_mods if (m.id, m.version) not in folders]
            if absent:
                add(HOST, 'FAIL', 'active mods installed', 'not in mods_dir: ' + ', '.join(absent))
    path_check('loc_db', 'file', "the game's text; written by the game at its first start")
    path_check('exe', 'file', 'build it: python tools/build_host.py')
    if path_check('profiles', 'dir', "the host's UI emulation profiles; without them games need --no-profiles"):
        names = sorted(p.parent.name for p in cfg.profiles.glob('*/profile.json'))
        known = (cfg.profiles / 'known-ui-files.json').is_file()
        add(HOST, 'ok' if names and known else 'warn', 'UI emulation profiles',
            f'{len(names)} profiles' + ('' if known else ', no known-ui-files.json') if names else 'no */profile.json in profiles')
    if path_check('probes', 'dir', 'Lua probes; --fingerprint needs probes/fingerprint.lua', 'warn'):
        fp = cfg.probes / 'fingerprint.lua'
        add(HOST, 'ok' if fp.is_file() else 'warn', 'fingerprint probe',
            str(fp) if fp.is_file() else f'{fp} does not exist (--fingerprint needs it)')
    database_check(cfg, add)
    add(HOST, 'ok', 'AI logging', f'{cfg.ai_logging}: ai_logging in {cfg.file.name}' if cfg.ai_logging else
        'off, the default; on writes the AI logs, is slower and plays another game with some DLLs (docs/ai-logging.md)')

    # the real game (session.py): read only
    game_exe = cfg.game_dir / 'CivilizationV_DX11.exe' if cfg.game_dir else None
    add(REAL, 'ok' if game_exe and game_exe.is_file() else 'warn', 'game executable',
        str(game_exe) if game_exe and game_exe.is_file() else 'CivilizationV_DX11.exe not found in game_dir')
    if path_check('config_ini', 'file', 'written by the game at its first start', 'warn', REAL):
        from .realgame import seeds
        ini = cfg.config_ini
        tuner = seeds.ini_value(ini, 'EnableTuner')
        add(REAL, 'ok' if tuner == '1' else 'warn', 'EnableTuner',
            '1: the game opens its tuner port' if tuner == '1' else
            f'is {tuner!r}: set EnableTuner = 1 in config.ini (section [Debugging]) for real-game automation')
        logging = seeds.ini_value(ini, 'LoggingEnabled')
        add(REAL, 'ok' if logging == '1' else 'warn', 'LoggingEnabled',
            '1: Lua.log and the probes\' output are written' if logging == '1' else
            f'is {logging!r}: set LoggingEnabled = 1 in config.ini so the game writes Lua.log (probe output)')
        pinned = {k: v for k, v in seeds.read(ini).items() if v}
        marker = (cfg.workspace / 'cache' / seeds.MARKER) if cfg.workspace else None
        if marker and marker.is_file():
            add(REAL, 'warn', 'seeds', f'a real-game session ended without restoring config.ini ({marker}); the next '
                'session restores it')
        elif pinned:
            add(REAL, 'warn', 'seeds', 'config.ini pins ' + ', '.join(f'{k} = {v}' for k, v in pinned.items())
                + ': every game started from the menu gets these seeds')
        else:
            add(REAL, 'ok', 'seeds', 'not pinned (the session pins them per game and restores them)')

    toolchain_check(cfg, add)
    for note in cfg.notes:
        add(HOST, 'warn', 'configuration', note)
    return out


def database_check(cfg, add):
    if cfg.db:
        ok = cfg.db.is_file()
        add(HOST, 'ok' if ok else 'FAIL', 'game database', f'db {cfg.db}' + ('' if ok else ' does not exist'))
        return
    if cfg.snapshot:
        ok = cfg.snapshot.is_file()
        add(HOST, 'ok' if ok else 'FAIL', 'DLC snapshot', f'snapshot {cfg.snapshot}' + ('' if ok else ' does not exist'))
        return
    from . import snapshot
    try:
        core = snapshot.core_db(cfg)
    except config.ConfigError:
        add(HOST, 'FAIL', 'DLC snapshot',
            "none: the database builder starts from the DLC snapshot (base game, expansions, DLC), which c5h builds from "
            f"the game's files and the game's cache ({snapshot.CORE_DB} in cache_dir, "
            f"{cfg.cache_dir or 'not set'}). The game writes that cache at its first start: start Civilization V once "
            "to the main menu. Or capture a snapshot from the real game (python -m civ5headless.realgame.dbcapture "
            "--run NAME --snapshot-only) and set snapshot in civ5headless.toml to its dlc-debug.db")
        return
    cached = None
    if cfg.game_dir and cfg.exe and cfg.exe.is_file() and cfg.db_cache:
        _, key = snapshot.inputs(cfg.game_dir, cfg.user_dir, core, cfg.exe)
        entry = cfg.db_cache / 'snapshot' / key
        cached = entry / 'snapshot.db' if (entry / 'snapshot.db.ok').is_file() else None
    add(HOST, 'ok', 'DLC snapshot', f'built from the game files and {core.name}: '
        + (f'cached at {cached}' if cached else 'built on first use (about a second; c5h snapshot builds it now)'))


def toolchain_check(cfg, add):
    repo = config.System().repository()
    script = repo / 'tools/toolchain.py' if repo else None
    if not script or not script.is_file():
        add(BUILD, 'ok', 'toolchain', 'not checked (no repository checkout; only needed to build the host)')
        return
    spec = importlib.util.spec_from_file_location('c5h_toolchain', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        _, _, info = module.find(cfg.toolchain)
        banner = next((line for line in info['compiler'].splitlines() if 'Version' in line), info['compiler'])
        add(BUILD, 'ok', 'toolchain', f"{info['root']}: {banner.strip()}")
    except (RuntimeError, OSError) as error:
        tried = [line.strip() for line in str(error).splitlines()[1:] if line.startswith('  ')]
        add(BUILD, 'warn', 'toolchain', 'no MSVC 2008 SP1 x86 toolchain (' + '; '.join(tried) + '). Only needed to build '
            'the host: python tools/get_toolchain.py makes one in toolchain\\ (--iso for a downloaded SDK ISO)')


def main(argv=None, cfg=None):
    ap = argparse.ArgumentParser(prog='c5h doctor', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json', action='store_true', help='print the configuration and the checks as JSON')
    a = ap.parse_args(argv)
    if isinstance(cfg, Exception):
        print(f'configuration error: {cfg}')
        return 1
    cfg = cfg or config.load()
    results = checks(cfg)
    failed = sum(s == 'FAIL' for _, s, _, _ in results)
    if a.json:
        print(json.dumps(dict(cfg.to_dict(), checks=[dict(group=g, status=s, what=w, detail=d) for g, s, w, d in results]),
                         indent=1))
        return 1 if failed else 0
    print(f'config file: {cfg.file}' if cfg.file else
          f'config file: none ({config.FILE_NAME} in the current folder or the workspace, or C5H_CONFIG); all values below are discovered or defaults')
    print()
    width = max(len(k) for k in config.KEYS)
    for key, value, source, note in cfg.rows():
        print(f'  {key:<{width}}  {source:<11}  {value if value else "-"}' + (f'  ({note})' if note else ''))
    if cfg.active_mods:
        print(f'\nactive mods ({cfg.mods_db}):')
        for m in cfg.active_mods:
            print(f'  {m.name or "?"} v{m.version}  {m.id}')
    for group in (HOST, REAL, BUILD):
        print(f'\n{group}:')
        for g, status, what, detail in results:
            if g == group:
                print(f'  {status:<5} {what}: {detail}')
    real_warnings = sum(g == REAL and s != 'ok' for g, s, _, _ in results)
    print(f'\n{failed} check(s) failed' if failed else '\nready for host games', end='')
    print('; real game: ' + ('ready' if not real_warnings else f'{real_warnings} warning(s)'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
