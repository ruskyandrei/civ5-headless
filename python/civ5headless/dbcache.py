"""Game database cache for host runs: one build per (mods input, game DLL, snapshot, builder exe).

  c5h dbcache [--mods-dir MODS] [--mods-db Civ5ModsDatabase.db | --mod ID[:VER]... | --no-mods] [--dll DLL]
              [--exe EXE] [--snapshot DB] [--cache DIR]

prints the path of the cached database, building it with dbbuild.py on a miss (about 3 s). Every input defaults to
the configuration (c5h doctor). --mod gives the mod set instead of the mods database: its registry is generated from
the .modinfo files (modsdb.py); --no-mods builds the database without mods (the DLC snapshot post-processed by the
DLL).

The key hashes the contents of every .modinfo/.xml/.sql/.lua file below the MODS folder (the only files the
builder reads), the mods database, the DLL (its post-processing runs on the database), the DLC snapshot and
the builder exe. Without a configured snapshot, the one built from the game's files is used (c5h snapshot).
Entries live in <db_cache>/<key>/built.db with dbbuild.py's report next to it
(built.db.build/) and an inputs.json; a finished entry has built.db.ok. Games started together on an empty cache share one
build: whoever comes first holds the entry's build.lock and the others wait for it (the snapshot and the mods registry
likewise).

database(cfg, ...) is what the run, queue and comparison tools use: a database given explicitly, else the configured
one (db), else the cached build for the mods and DLL (mods=[...]: a mod set, as --mod).
"""
from pathlib import Path
import argparse, hashlib, json, os, subprocess, sys, time

from . import config, modsdb, snapshot, winproc

SOURCE_EXT = {'.modinfo', '.xml', '.sql', '.lua'}
LOCK_FILE = 'build.lock'
PACKAGE_PARENT = Path(__file__).resolve().parents[1]
_sha_memo = {}


def sha256_file(path):
    path = Path(path).resolve()
    st = path.stat()
    memo = (str(path), st.st_size, st.st_mtime_ns)
    if memo in _sha_memo:
        return _sha_memo[memo]
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    _sha_memo[memo] = h.hexdigest().upper()
    return _sha_memo[memo]


def mods_hash(mods_dir):
    h = hashlib.sha256()
    root = Path(mods_dir)
    files = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in SOURCE_EXT)
    for p in files:
        h.update(str(p.relative_to(root)).replace('\\', '/').lower().encode('utf-8') + b'\0')
        h.update(bytes.fromhex(sha256_file(p)))
    return h.hexdigest().upper(), len(files)


def find_exe(exe=None, cfg=None):
    if exe:
        return Path(exe).resolve()
    cfg = cfg or config.load()
    return cfg.require('exe', 'pass --exe (build it with python tools/build_host.py)').resolve()


def get_db(mods_dir, mods_db, dll, exe, snapshot, cache, game_dir, log=print, config_file=None):
    """Path of the built database for these inputs; builds it on a miss. Returns (db, inputs, key)."""
    exe, snapshot = Path(exe).resolve(), Path(snapshot)
    mhash, nfiles = mods_hash(mods_dir)
    inputs = dict(modsDir=str(Path(mods_dir).resolve()), modsFiles=nfiles, modsSHA256=mhash,
                  modsDb=str(Path(mods_db).resolve()), modsDbSHA256=sha256_file(mods_db),
                  dll=str(Path(dll).resolve()), dllSHA256=sha256_file(dll),
                  snapshot=str(snapshot.resolve()), snapshotSHA256=sha256_file(snapshot),
                  exe=str(exe), exeSHA256=sha256_file(exe))
    key = hashlib.sha256('|'.join(inputs[k] for k in ('modsSHA256', 'modsDbSHA256', 'dllSHA256', 'snapshotSHA256', 'exeSHA256'))
                         .encode()).hexdigest()[:16].upper()
    entry = Path(cache) / key
    db = entry / 'built.db'
    if (entry / 'built.db.ok').is_file() and db.is_file():
        return db, inputs, key
    entry.mkdir(parents=True, exist_ok=True)
    with winproc.file_lock(entry / LOCK_FILE, waiting=lambda: log(f'another game is building {db}: waiting for it')):
        if (entry / 'built.db.ok').is_file() and db.is_file():
            return db, inputs, key
        (entry / 'built.db.ok').unlink(missing_ok=True)
        (entry / 'inputs.json').write_text(json.dumps(inputs, indent=1) + '\n', encoding='utf-8')
        t = time.monotonic()
        cmd = [sys.executable, '-B', '-m', 'civ5headless.dbbuild', '--mods-dir', str(mods_dir), '--mods-db', str(mods_db),
               '--dll', str(dll), '--exe', str(exe), '--snapshot', str(snapshot), '--game-dir', str(game_dir), '--out', str(db)]
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(PACKAGE_PARENT)] + [p for p in [os.environ.get('PYTHONPATH')] if p]))
        if config_file:
            env['C5H_CONFIG'] = str(config_file)  # the child reads the same file for anything not on its command line
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env)
        (entry / 'build_db.out.txt').write_text(r.stdout + r.stderr, encoding='utf-8')
        if r.returncode or not db.is_file():
            raise RuntimeError(f'database build failed (exit {r.returncode}); see {entry / "build_db.out.txt"}')
        (entry / 'built.db.ok').write_text(f'{time.monotonic() - t:.1f}\n', encoding='ascii')
        log(f'database built in {time.monotonic() - t:.1f} s: {db}')
    return db, inputs, key


def database(cfg, db=None, mods_dir=None, mods_db=None, dll=None, exe=None, log=print, mods=None):
    """(database, info): db if given, else cfg.db, else the cached build for the mods and DLL (built on a miss).
    mods: a mod set ('ID[:VER]' strings, [] for none) built from a generated registry instead of mods_db."""
    if db or cfg.db:
        p = Path(db or cfg.db).resolve()
        return p, dict(db=str(p))
    exe = find_exe(exe, cfg)
    dll = dll or modsdb.choose_dll(cfg, None, mods, mods_dir)[0]  # the rule run.py follows (modsdb.choose_dll)
    if mods is not None:
        mods_dir, mods_db = modsdb.resolve(cfg, mods, mods_dir, mods_db, log=log)
    path, inputs, key = get_db(mods_dir or cfg.require('mods_dir', 'pass --mods-dir'),
                               mods_db or cfg.require('mods_db', 'pass --mods-db'),
                               dll,
                               exe, snapshot.resolve(cfg, exe=exe, log=log), cfg.require('db_cache'),
                               cfg.require('game_dir'), log=log, config_file=cfg.file)
    return path, dict(inputs, db=str(path), cacheKey=key)


def main(argv=None, cfg=None):
    cfg = cfg or config.load()
    a = argparse.ArgumentParser(prog='c5h dbcache', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('--mods-dir', default=cfg.mods_dir)
    a.add_argument('--mods-db', default=None, help='mods database (default: config mods_db)')
    a.add_argument('--mod', action='append', help='activated mod GUID[:version] (repeatable; a generated registry)')
    a.add_argument('--no-mods', action='store_true', help='the database without mods')
    a.add_argument('--dll', help='game DLL (default: as c5h run chooses it: the configured one, or for --mod/--no-mods '
                   "the set's or the game's own)")
    a.add_argument('--exe', default=cfg.exe)
    a.add_argument('--snapshot', default=cfg.snapshot)
    a.add_argument('--cache', default=cfg.db_cache)
    args = a.parse_args(argv)
    if (args.mod or args.no_mods) and args.mods_db:
        a.error('--mod and --no-mods exclude --mods-db')
    log = lambda m: print(m, file=sys.stderr)
    try:
        mods = [] if args.no_mods else args.mod
        args.dll, why = modsdb.choose_dll(cfg, args.dll, mods, args.mods_dir)
        log(f'DLL {args.dll} ({why})')
        args.mods_dir, args.mods_db = modsdb.resolve(cfg, mods, args.mods_dir, args.mods_db, log=log)
    except config.ConfigError as error:
        print(f'c5h dbcache: {error}', file=sys.stderr)
        return 1
    missing = [k for k in ('mods_dir', 'mods_db', 'dll', 'exe', 'cache') if getattr(args, k) is None]
    if missing:
        a.error('not set: ' + ', '.join('--' + k.replace('_', '-') for k in missing) + ' (or set them in civ5headless.toml)')
    try:
        snap = snapshot.resolve(cfg, args.snapshot, exe=args.exe, log=log)
    except (config.ConfigError, snapshot.SnapshotError, RuntimeError) as error:
        print(f'c5h dbcache: {error}', file=sys.stderr)
        return 1
    db, inputs, key = get_db(args.mods_dir, args.mods_db, args.dll, args.exe, snap, args.cache,
                             cfg.require('game_dir'), log=log, config_file=cfg.file)
    print(db)
    return 0


if __name__ == '__main__':
    sys.exit(main())
