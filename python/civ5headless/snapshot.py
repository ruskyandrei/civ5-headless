"""Build the DLC snapshot from the game's own files: the game database with the base game, the expansions and every
installed DLC, before any mod. The database builder (dbbuild) starts each modded build from it.

  c5h snapshot [--out DB] [--core Civ5CoreDatabase.db] [--dll DLL] [--oracle REF.db] [--force]

Without --out the snapshot is cached in <db_cache>/snapshot/<key>/snapshot.db and its path printed; dbcache and
dbbuild call the same code when no snapshot is configured, so setting `snapshot` is only needed to pin one.

The game builds this database at start-up in two layers, and the builder repeats them:
  1. Core: the game's cached Civ5CoreDatabase.db (cache_dir), which the game writes at its first start from the base
     game and expansion XML. It is copied as is, so the game has to have been started once (it also writes
     Localization-Merged.db then, which host games need anyway).
  2. DLC: every Assets/DLC/*/*.Civ5Pkg in (Priority, folder name) order. Each package applies its top-level
     <GameData> files and those of every <Gameplay> section. A file is taken from <gameplay owner>/DLC/<package
     folder>/ when the owner has one by that name (the owner is the highest-priority package whose Gameplay section
     names a GameCore, Expansion2 in the full game; it carries updated copies of the older DLC's data), else from the
     package's own folder. Rows a package adds to Civilizations and Leaders get its PackageID. Packages with a <Key>
     are listed in DownloadableContent. Then the map scripts are registered: Assets/Maps, the user's Maps folder,
     then each package's MapDirectory.

The game's PerformDatabasePostProcessing is not run by default: every modded build runs it with its own DLL, and the
databases built from this snapshot are then identical to those built from one captured from the game. --dll runs
it here as well (with the game's own DLL the snapshot then matches the captured one table for table).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from . import config, dbbuild, dbdiff, winproc

FORMAT = 1                     # bump when the build changes, so cached snapshots are rebuilt
CORE_DB = 'Civ5CoreDatabase.db'
SOURCE_EXT = ('.xml', '.sql', '.civ5pkg', '.lua')
TAGGED_TABLES = ('Civilizations', 'Leaders')


class SnapshotError(Exception):
    pass


def package_id(guid):
    return (guid or '').strip().strip('{}').replace('-', '').upper()


def packages(game_dir):
    """Every DLC package in load order: dicts with priority, folder, path (the .Civ5Pkg), root, id."""
    out = []
    for pkg in Path(game_dir, 'Assets/DLC').glob('*/*.Civ5Pkg'):
        root = ET.fromstring(pkg.read_bytes())
        priority = (root.findtext('Priority') or '').strip()
        out.append(dict(priority=int(priority) if priority else 0, folder=pkg.parent.name, path=pkg, root=root,
                        id=package_id(root.findtext('GUID'))))
    return sorted(out, key=lambda p: (p['priority'], p['folder'].lower()))


def files_by_name(folder):
    out = {}
    if folder.is_dir():
        for p in sorted(folder.rglob('*')):
            if p.is_file():
                out.setdefault(p.name.lower(), []).append(p)
    return out


def gamedata_files(pkg, owner):
    """The package's GameData files in order, each resolved through the gameplay owner's DLC folder."""
    root = pkg['root']
    sections = [root] + root.findall('Gameplay')
    override = files_by_name(owner['path'].parent / 'DLC' / pkg['folder']) if owner else {}
    own = files_by_name(pkg['path'].parent)
    out = []
    for section in sections:
        for el in section.findall('GameData'):
            name = (el.text or '').strip()
            hits = override.get(name.lower()) or own.get(name.lower())
            if not hits:
                raise SnapshotError(f'{pkg["path"]}: GameData {name} not found in its folder')
            if len(hits) > 1:
                raise SnapshotError(f'{pkg["path"]}: GameData {name} is ambiguous: ' + ', '.join(map(str, hits)))
            out.append(hits[0])
    return out


def downloadable_content_sql(pkgs):
    lines, seen = [], set()
    for p in sorted((p for p in pkgs if p['root'].findtext('Key')), key=lambda p: p['folder'].lower()):
        if p['id'] in seen:
            continue
        seen.add(p['id'])
        r = p['root']
        free = (r.findtext('Ownership') or '').strip().upper() == 'FREE'
        active = 0 if r.find('Upgrade') is not None else 1
        version = int((r.findtext('Version') or '1').strip())
        lines.append('INSERT INTO DownloadableContent(PackageID, FriendlyNameKey, DescriptionKey, Version, IsFiraxisContent, '
                     f"IsActive, IsBaseContentUpgrade) VALUES('{p['id']}', 'TXT_KEY_{p['id']}_NAME', "
                     f"'TXT_KEY_{p['id']}_DESCRIPTION', {version}, 1, {active}, {int(free)});")
    return lines


def map_scripts(game_dir, user_dir, pkgs):
    """[(file, name recorded in MapScripts.FileName)] in the game's registration order."""
    dirs = [(Path(game_dir, 'Assets/Maps'), 'Assets\\Maps\\')]
    if user_dir:
        dirs.append((Path(user_dir, 'Maps'), str(Path(user_dir, 'Maps')) + '\\'))
    for p in pkgs:
        for section in [p['root']] + p['root'].findall('Gameplay'):
            for md in section.findall('MapDirectory'):
                rel = (md.text or '').strip()
                dirs.append((p['path'].parent / rel, 'Assets\\DLC\\' + p['folder'] + '\\' + rel.replace('/', '\\') + '\\'))
    out = []
    for folder, prefix in dirs:
        if folder.is_dir():
            out += [(f, prefix + f.name) for f in sorted(folder.glob('*.lua'), key=lambda f: f.name.lower())]
    return out


def write_plan(game_dir, user_dir, core_db, work, postprocess):
    """Writes the builddb plan and its helper files into work; returns the plan path."""
    work.mkdir(parents=True, exist_ok=True)
    text_db = work / 'text-schema.db'
    text_db.unlink(missing_ok=True)
    con = sqlite3.connect(text_db)
    for stmt in dbbuild.TEXT_SCHEMA:
        con.execute(stmt)
    con.commit()
    con.close()
    pkgs = packages(game_dir)
    owners = [p for p in pkgs if p['root'].find('Gameplay/GameCore') is not None]
    owner = max(owners, key=lambda p: p['priority']) if owners else None
    mark = work / 'mark.sql'
    mark.write_text('CREATE TEMP TABLE IF NOT EXISTS c5h_mark(t TEXT PRIMARY KEY, r INTEGER);\n' + ''.join(
        f"INSERT OR REPLACE INTO c5h_mark VALUES('{t}', (SELECT IFNULL(MAX(rowid), -1) FROM {t}));\n" for t in TAGGED_TABLES),
        encoding='utf-8')
    plan = [f'snapshot\t{Path(core_db).resolve()}', 'phase\tdlc', f'attach\t{text_db}\tLocalization']
    for p in pkgs:
        files = gamedata_files(p, owner)
        if not files:
            continue
        tag = work / f'package-{p["folder"]}.sql'
        tag.write_text(''.join(f"UPDATE {t} SET PackageID = '{p['id']}' WHERE rowid > (SELECT r FROM c5h_mark WHERE t = '{t}');\n"
                               for t in TAGGED_TABLES), encoding='utf-8')
        plan.append(f'sql\t{mark}')
        plan += [f"{'sql' if f.suffix.lower() == '.sql' else 'xml'}\t{f}" for f in files]
        plan.append(f'sql\t{tag}')
    dlc_sql = work / 'downloadable-content.sql'
    dlc_sql.write_text('\n'.join(downloadable_content_sql(pkgs)) + '\n', encoding='utf-8')
    plan += [f'sql\t{dlc_sql}', 'phase\tmapscripts']
    plan += [f'mapscript\t{f}\t{recorded}' for f, recorded in map_scripts(game_dir, user_dir, pkgs)]
    plan.append('detach\tLocalization')
    if postprocess:
        plan += ['phase\tpostprocess', 'postprocess']
    plan += [f'vfs\t{k}\t{v}' for k, v in sorted(dbbuild.build_vfs(Path(game_dir), [], set()).items())]
    path = work / 'plan.txt'
    path.write_text('\n'.join(plan) + '\n', encoding='utf-8')
    return path


def stat_digest(h, folder, suffixes):
    if folder.is_dir():
        for p in sorted(folder.rglob('*')):
            if p.is_file() and p.suffix.lower() in suffixes:
                st = p.stat()
                h.update(f'{p.relative_to(folder)}|{st.st_size}|{st.st_mtime_ns}\n'.lower().encode('utf-8'))


def inputs(game_dir, user_dir, core_db, exe, dll=None):
    """What a snapshot is built from, and the cache key over it. The game's files count by size and time, the core
    database, host and DLL by content."""
    from .dbcache import sha256_file
    h = hashlib.sha256(f'format {FORMAT}\n'.encode())
    for folder, suffixes in ((Path(game_dir, 'Assets/DLC'), SOURCE_EXT), (Path(game_dir, 'Assets/Maps'), ('.lua',)),
                             (Path(user_dir, 'Maps') if user_dir else None, ('.lua',))):
        if folder:
            h.update(f'[{folder}]\n'.encode('utf-8'))
            stat_digest(h, folder, suffixes)
    out = dict(gameDir=str(game_dir), userDir=str(user_dir) if user_dir else None, gameFilesDigest=h.hexdigest().upper(),
               core=str(core_db), coreSHA256=sha256_file(core_db), exe=str(exe), exeSHA256=sha256_file(exe),
               dll=str(dll) if dll else None, dllSHA256=sha256_file(dll) if dll else None)
    key = hashlib.sha256('|'.join(str(out[k]) for k in ('gameFilesDigest', 'coreSHA256', 'exeSHA256', 'dllSHA256'))
                         .encode()).hexdigest()[:16].upper()
    return out, key


def build(game_dir, user_dir, core_db, exe, out, dll=None, log=print):
    """Builds the snapshot into out; returns the summary dict (status 'built' when it worked)."""
    t0 = time.monotonic()
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    work = out.with_name(out.name + '.build')
    shutil.rmtree(work, ignore_errors=True)
    plan = write_plan(Path(game_dir), user_dir and Path(user_dir), core_db, work, postprocess=dll is not None)
    cmd = [str(exe), 'builddb', '--game-dir', str(game_dir), '--plan', str(plan), '--out', str(out), '--run', str(work)]
    if dll:
        cmd += ['--dll', str(Path(dll).resolve())]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors='replace', stdin=subprocess.DEVNULL)
    (work / 'builddb.out.txt').write_text(proc.stdout + proc.stderr, encoding='utf-8')
    report_path = work / 'builddb-report.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
    failed = [s for s in report.get('steps', []) if not s.get('ok')]
    summary = dict(out=str(out), status=report.get('status', 'failed') if proc.returncode == 0 else 'failed',
                   host_exit=proc.returncode, steps=len(report.get('steps', [])),
                   steps_with_errors=[{k: s.get(k) for k in ('kind', 'path', 'note', 'errors') if s.get(k)} for s in failed],
                   postprocess=str(dll) if dll else None, run=str(work))
    if failed and summary['status'] == 'built':
        summary['status'] = 'errors'
    summary['seconds'] = round(time.monotonic() - t0, 2)
    (work / 'snapshot-summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    if summary['status'] != 'built':
        log(f"snapshot build {summary['status']} (host exit {proc.returncode}); see {work}")
        for s in summary['steps_with_errors'][:10]:
            log(f"  error in {s.get('kind')} {s.get('path')}: {s.get('note', '')} {(s.get('errors') or [''])[0]}")
    return summary


def core_db(cfg):
    cache = cfg.cache_dir
    if cache is None or not (cache / CORE_DB).is_file():
        raise config.ConfigError(
            f"no {CORE_DB} in the game's cache folder ({cache or 'cache_dir is not set'}); the game writes it at its first "
            "start: start Civilization V once (to the main menu, without mods), or set snapshot in "
            f"{config.FILE_NAME} to a snapshot captured elsewhere")
    return cache / CORE_DB


def cached(cfg, exe=None, dll=None, log=print, force=False):
    """The cached snapshot for the installed game (built on a miss). Returns (path, inputs, key)."""
    game_dir = cfg.require('game_dir')
    exe = Path(exe or cfg.require('exe', 'build the host: python tools/build_host.py')).resolve()
    core = core_db(cfg)
    info, key = inputs(game_dir, cfg.user_dir, core, exe, dll)
    entry = cfg.require('db_cache') / 'snapshot' / key
    db = entry / 'snapshot.db'
    if not force and (entry / 'snapshot.db.ok').is_file() and db.is_file():
        return db, info, key
    entry.mkdir(parents=True, exist_ok=True)
    with winproc.file_lock(entry / 'build.lock', waiting=lambda: log(f'another game is building {db}: waiting for it')):
        if not force and (entry / 'snapshot.db.ok').is_file() and db.is_file():
            return db, info, key
        (entry / 'snapshot.db.ok').unlink(missing_ok=True)
        (entry / 'inputs.json').write_text(json.dumps(info, indent=1) + '\n', encoding='utf-8')
        summary = build(game_dir, cfg.user_dir, core, exe, db, dll=dll, log=log)
        if summary['status'] != 'built':
            raise RuntimeError(f"DLC snapshot build failed ({summary['status']}); see {summary['run']}")
        # dbbuild compares this with the installed game to warn about a stale snapshot
        (entry / 'snapshot.db.dlc.json').write_text(json.dumps(dbbuild.dlc_manifest(Path(game_dir)), indent=1) + '\n',
                                                    encoding='utf-8')
        (entry / 'snapshot.db.ok').write_text(f"{summary['seconds']}\n", encoding='ascii')
        log(f"DLC snapshot built in {summary['seconds']:.1f} s: {db}")
    return db, info, key


def resolve(cfg, snapshot=None, exe=None, log=print):
    """The snapshot to build from: the one given, the configured one, else the cached build from the game's files."""
    if snapshot or cfg.snapshot:
        return Path(snapshot or cfg.snapshot)
    return cached(cfg, exe=exe, log=log)[0]


def main(argv=None, cfg=None):
    cfg = cfg or config.load()
    ap = argparse.ArgumentParser(prog='c5h snapshot', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, help='build into this file instead of the cache')
    ap.add_argument('--core', type=Path, help=f'core database to start from (default: {CORE_DB} in cache_dir)')
    ap.add_argument('--dll', type=Path, help='run PerformDatabasePostProcessing with this game DLL')
    ap.add_argument('--exe', type=Path, default=cfg.exe, help='civ5headless.exe (default: config exe)')
    ap.add_argument('--oracle', type=Path, help='compare the result with this database (a snapshot captured from the game)')
    ap.add_argument('--force', action='store_true', help='rebuild the cached snapshot')
    a = ap.parse_args(argv)
    if a.exe is None:
        ap.error('no civ5headless.exe: build it (python tools/build_host.py), pass --exe or set exe in civ5headless.toml')
    log = lambda m: print(m, file=sys.stderr)
    try:
        if a.out or a.core:
            summary = build(cfg.require('game_dir'), cfg.user_dir, a.core or core_db(cfg), a.exe.resolve(),
                            a.out or (cfg.require('db_cache') / 'snapshot' / 'snapshot-check.db'), dll=a.dll, log=log)
            if summary['status'] != 'built':
                return 1
            db = Path(summary['out'])
        else:
            db, _, _ = cached(cfg, exe=a.exe, dll=a.dll, log=log, force=a.force)
    except (config.ConfigError, SnapshotError, RuntimeError) as error:
        print(f'c5h snapshot: {error}', file=sys.stderr)
        return 1
    print(db)
    if a.oracle:
        diff = dbdiff.diff(a.oracle, db)
        print('oracle:', 'identical' if diff['identical'] else
              'DIFFERENT (' + ', '.join(t['table'] for t in diff['tables_differ'][:12]) + ')')
        if not diff['identical']:
            print(f'  details: c5h dbdiff "{a.oracle}" "{db}"')
            if not a.dll and {t['table'] for t in diff['tables_differ']} <= {'Defines', 'Resources'}:
                print('  (only the tables post-processing fills differ: expected without --dll)')
                return 0
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
