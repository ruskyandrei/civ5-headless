"""Generate the mods registry (Civ5ModsDatabase.db) for a mod set from the .modinfo files, without the game.

  c5h modsdb [--mods-dir MODS] [--mod ID[:VER]]... [--out DB] [--path-as DIR] [--compare REGISTRY]

The game keeps its registry in the user's cache folder: every installed mod with its properties, files, actions,
entry points and relationships, and which mods are activated. The host reads activation, entry points and imported
files from it, and the database builder reads the activated mods from it, so a MODS folder alone is not enough.
This writes the same tables, filled from the .modinfo files of the given mods, all of them activated.

Without --mod the configured active mods are used (c5h doctor); a mod given without a version is its highest one
below the MODS folder. The activation order (Mods.RowID) is the order given, except that a mod comes after the mods
it depends on or references (the database builder's order). A dependency outside the set, a version outside a
dependency's range, or a mod that blocks another in the set is an error, as it is in the game's mod browser.

Rows are written as the game writes them: properties, files (then the .modinfo itself), actions, entry points and
relationships in .modinfo order, text with the file's line ends, ModSystemSettings Version 14. EvaluatedPath is the
mod's folder below --path-as (default: the MODS folder) plus the file's path as written; the host re-roots it at the
first MODS component to its --mods-dir. Only the given mods are listed (the game also lists installed mods that are
not activated), and ScannedFiles, the game's change detection, is left empty.

Without --out the registry is cached in <db_cache>/modsdb/<key>/Civ5ModsDatabase.db and its path printed; run, queue
and dbcache call the same code for an explicit mod set (--mod, --no-mods).

--compare REGISTRY compares the result with another registry (the game's) for the mods in the set: activation order
and every table row by row. EvaluatedPath matches when --path-as is the folder the other registry records (the
user's MODS folder); --ignore md5 leaves out the hashes, which the game updates only when it rescans a mod.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import xml.etree.ElementTree as ET

from . import config, dbbuild, winproc

FORMAT = 1           # bump when the rows written change, so cached registries are rebuilt
FILE_NAME = 'Civ5ModsDatabase.db'

# The game's schema (Civ5ModsDatabase.db, mod system version 14), statement for statement.
SCHEMA = [
    "CREATE TABLE SteamWorkshopSubscribedFiles('PublishedFileId' INTEGER PRIMARY KEY, 'FileName' TEXT, 'Title' TEXT, "
    "'State' TEXT, 'LastUpdated' INTEGER)",
    "CREATE TABLE SteamWorkshopMods('PublishedFileId' INTEGER NOT NULL, 'ModId' TEXT NOT NULL, 'Version' INTEGER NOT NULL)",
    "CREATE TABLE Mods('RowID' INTEGER PRIMARY KEY  AUTOINCREMENT  NOT NULL , 'ModID' TEXT NOT NULL , 'Version' TEXT, "
    "'Type' INTEGER DEFAULT 1, 'Installed' BOOL DEFAULT 0, 'Enabled' BOOL DEFAULT 0, 'Activated' BOOL DEFAULT 0, "
    "'LastRetrieved' DATETIME)",
    "CREATE TABLE ModActions('ModID' TEXT NOT NULL , 'Version' INTEGER NOT NULL, 'Location' TEXT NOT NULL , "
    "'Action' TEXT NOT NULL , 'Param1' TEXT, FOREIGN KEY (ModID, Version) REFERENCES Mods(ModID, Version))",
    "CREATE TABLE ModRelationships('ModID' TEXT NOT NULL, 'Version' INTEGER NOT NULL, 'Type' INTEGER NOT NULL, "
    "'RelModId' TEXT NOT NULL, MinVersion INTEGER DEFAULT 0, MaxVersion INTEGER DEFAULT 2147483647, RelModTitle TEXT, "
    "FOREIGN KEY(ModID, Version) REFERENCES Mods(ModID, Version))",
    "CREATE TABLE DlcRelationships('ModID' TEXT NOT NULL, 'Version' INTEGER NOT NULL, 'Type' INTEGER NOT NULL, "
    "'PackageId' TEXT NOT NULL, MinVersion INTEGER DEFAULT 0, MaxVersion INTEGER DEFAULT 1, "
    "FOREIGN KEY(ModID, Version) REFERENCES Mods(ModID, Version))",
    "CREATE TABLE GameVersionRelationships('ModID' TEXT NOT NULL, 'Version' INTEGER NOT NULL, 'Type' INTEGER NOT NULL, "
    "MinVersion DEFAULT '1.0.0.0', MaxVersion NOT NULL, FOREIGN KEY(ModID, Version) REFERENCES Mods(ModID, Version))",
    "CREATE TABLE ModEntryPoints('ModID' TEXT NOT NULL , 'Version' INTEGER NOT NULL, 'Name' TEXT NOT NULL , "
    "'Description' TEXT NOT NULL, 'Type' TEXT NOT NULL, 'File' TEXT, FOREIGN KEY (ModID, Version) REFERENCES Mods(ModID, Version))",
    "CREATE TABLE ModRatings('RecordID')",
    "CREATE TABLE ModFiles('ModID' TEXT NOT NULL , 'Version' INTEGER NOT NULL, 'Path' TEXT NOT NULL , 'md5' TEXT, "
    "'EvaluatedPath' TEXT, Import INTEGER DEFAULT 0, PRIMARY KEY ('ModID', 'Version', 'Path'), "
    "FOREIGN KEY (ModID, Version) REFERENCES Mods(ModID, Version))",
    "CREATE TABLE ModProperties('ModID' TEXT NOT NULL , 'Version' INTEGER NOT NULL, 'Name' TEXT NOT NULL , 'Value' TEXT, "
    "PRIMARY KEY ('ModID', 'Version', 'Name'), FOREIGN KEY (ModID, Version) REFERENCES Mods(ModID, Version))",
    "CREATE TABLE ModSystemSettings('Name' TEXT PRIMARY KEY NOT NULL, 'Value' TEXT)",
    "CREATE TABLE ScannedFiles('FileName' TEXT PRIMARY KEY NOT NULL, 'HighDateTime' INTEGER NOT NULL, "
    "'LowDateTime' INTEGER NOT NULL)",
    "CREATE INDEX ModsEnabledIndex On Mods('Enabled')",
    "CREATE INDEX ModActionsIndex On ModActions('ModID', 'Version', 'Location')",
    "CREATE INDEX ModRelationshipsIndex ON ModRelationships('ModID', 'Version', 'Type')",
    "CREATE INDEX DlcRelationshipsIndex ON DlcRelationships('ModID', 'Version', 'Type')",
    "CREATE INDEX GameVersionRelationshipsIndex ON GameVersionRelationships('ModID', 'Version', 'Type')",
    "CREATE TRIGGER OnDeleteMod AFTER DELETE ON Mods BEGIN DELETE from ModProperties where ModID = old.ModID and "
    "Version = old.Version; DELETE from ModFiles where ModID = old.ModID and Version = old.Version; DELETE from "
    "ModActions where ModID = old.ModID and Version = old.Version; DELETE from ModEntryPoints where ModID = old.ModID "
    "and Version = old.Version; DELETE from ModRelationships where ModID = old.ModID and Version = old.Version; DELETE "
    "from DlcRelationships where ModID = old.ModID and Version = old.Version; DELETE from GameVersionRelationships "
    "where ModID = old.ModID and Version = old.Version; DELETE from SteamWorkshopMods where ModId = old.ModID and "
    "Version = old.Version;END",
    "CREATE TRIGGER OnDeleteScannedFile AFTER DELETE ON ScannedFiles BEGIN DELETE FROM Mods WHERE EXISTS (SELECT 1 "
    "FROM ModFiles WHERE EvaluatedPath = old.FileName AND ModID = Mods.ModID AND Version = Mods.Version);END",
    "CREATE TRIGGER OnDeleteSteamWorkshopMod AFTER DELETE On SteamWorkshopMods BEGIN Update "
    "SteamWorkshopSubscribedFiles SET State = 'Invalid' WHERE PublishedFileId = old.PublishedFileId; END",
]
SYSTEM_VERSION = '14'
RELATIONSHIPS = (('Dependencies', 2), ('References', 1), ('Blocks', -1))
# Per-mod tables and the columns compared (rowid order within a mod).
TABLES = {
    'ModProperties': ('Name', 'Value'),
    'ModFiles': ('Path', 'md5', 'EvaluatedPath', 'Import'),
    'ModActions': ('Location', 'Action', 'Param1'),
    'ModEntryPoints': ('Name', 'Description', 'Type', 'File'),
    'ModRelationships': ('Type', 'RelModId', 'MinVersion', 'MaxVersion', 'RelModTitle'),
    'DlcRelationships': ('Type', 'PackageId', 'MinVersion', 'MaxVersion'),
    'GameVersionRelationships': ('Type', 'MinVersion', 'MaxVersion'),
}


class ModsDbError(config.ConfigError):
    pass


def parse_mods(specs):
    """'ID[:VER]' strings (or (id, version) pairs) as [(id, version or None)]."""
    out = []
    for s in specs:
        if isinstance(s, (tuple, list)):
            mid, ver = s
        else:
            mid, _, ver = str(s).partition(':')
        mid = dbbuild.norm_id(mid)
        if not mid:
            raise ModsDbError(f'empty mod id in {s!r}')
        try:
            out.append((mid, int(ver) if ver not in (None, '') else None))
        except ValueError:
            raise ModsDbError(f'bad version in {s!r} (ID[:VERSION])') from None
    return out


def activated(mods_db, saved_games_only=False):
    """The mods a registry (Civ5ModsDatabase.db) activates, as [(id, version)] in its order; None when it cannot be read.
    saved_games_only: only those whose AffectsSavedGames property is not 0 (the ones a save lists)."""
    sql = 'SELECT ModID, Version FROM Mods m WHERE Activated = 1'
    if saved_games_only:
        sql += (" AND NOT EXISTS (SELECT 1 FROM ModProperties p WHERE p.ModID = m.ModID AND p.Version = m.Version"
                " AND p.Name = 'AffectsSavedGames' AND trim(p.Value) = '0')")
    try:
        con = sqlite3.connect(f'file:{Path(mods_db).as_posix()}?mode=ro', uri=True)
        try:
            rows = con.execute(sql + ' ORDER BY rowid').fetchall()
        finally:
            con.close()
        return parse_mods([(mid, ver) for mid, ver in rows])
    except (sqlite3.Error, ModsDbError, TypeError):
        return None


def select(mods_dir, specs):
    """The mods for specs below mods_dir, in activation order (dbbuild.ModInfo list). Raises ModsDbError."""
    wanted = parse_mods(specs)
    if not wanted:
        return []
    mods = dbbuild.scan_mods(Path(mods_dir))
    active = {}
    for mid, ver in wanted:
        found = [m for (i, v), m in mods.items() if i == mid and (ver is None or v == ver)]
        if not found:
            have = sorted(v for i, v in mods if i == mid)
            raise ModsDbError(f'mod {mid}' + (f' v{ver}' if ver is not None else '') + f' is not below {mods_dir}' +
                              (f' (versions there: {", ".join(map(str, have))})' if have else ''))
        active.setdefault(mid, max(found, key=lambda m: m.version))
    problems = []
    for m in active.values():
        for dep, lo, hi in m.dependencies:
            if dep not in active:
                problems.append(f'{m.name} (v{m.version}) depends on mod {dep}, which is not in the set')
            elif not lo <= active[dep].version <= hi:
                problems.append(f'{m.name} (v{m.version}) needs {active[dep].name} v{lo}-{hi}, not v{active[dep].version}')
        root = ET.fromstring(m.path.read_bytes())
        for b in root.findall('Blocks/Mod'):
            other = active.get(dbbuild.norm_id(b.get('id')))
            if other is not None and other is not m:
                problems.append(f'{m.name} (v{m.version}) blocks {other.name} (v{other.version})')
    if problems:
        raise ModsDbError('; '.join(problems))
    try:
        return dbbuild.order_mods(mods, [m.key() for m in active.values()])
    except SystemExit as error:  # a dependency cycle
        raise ModsDbError(str(error)) from None


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def modinfo_rows(info_path, path_as):
    """{table: [row dict]} for one .modinfo, as the game registers it; EvaluatedPath below path_as."""
    data = Path(info_path).read_bytes()
    root = ET.fromstring(data)
    crlf = b'\r\n' in data  # the XML parser gives \n; the game keeps the file's line ends in text values

    def text(value):
        value = (value or '').strip()
        return value.replace('\n', '\r\n') if crlf else value

    mid, ver = root.get('id'), _int(root.get('version'), 0)
    base = str(path_as).rstrip('\\/') + '\\' + Path(info_path).parent.name + '\\'
    rows = {t: [] for t in ('Mods',) + tuple(TABLES)}
    rows['Mods'].append(dict(ModID=mid, Version=str(ver), Type=1, Installed=1, Enabled=1, Activated=1))
    props = root.find('Properties')
    for p in (props if props is not None else []):
        rows['ModProperties'].append(dict(Name=p.tag, Value=text(p.text)))
    paths = set()
    for f in root.findall('Files/File'):
        rel = text(f.text)
        if rel.lower() in paths:
            continue
        paths.add(rel.lower())
        rows['ModFiles'].append(dict(Path=rel, md5=f.get('md5'), EvaluatedPath=base + rel, Import=1 if f.get('import') == '1' else 0))
    if Path(info_path).name.lower() not in paths:
        rows['ModFiles'].append(dict(Path=Path(info_path).name, md5=None, EvaluatedPath=base + Path(info_path).name, Import=0))
    actions = root.find('Actions')
    for location in (actions if actions is not None else []):
        for action in location:
            rows['ModActions'].append(dict(Location=location.tag, Action=action.tag, Param1=text(action.text)))
    for e in root.findall('EntryPoints/EntryPoint'):
        rows['ModEntryPoints'].append(dict(Name=text(e.findtext('Name')), Description=text(e.findtext('Description')),
                                           Type=e.get('type') or '', File=e.get('file')))
    for group, kind in RELATIONSHIPS:
        for r in root.findall(f'{group}/*'):
            if r.tag == 'Mod':
                rows['ModRelationships'].append(dict(Type=kind, RelModId=r.get('id') or '', MinVersion=_int(r.get('minversion'), 0),
                                                     MaxVersion=_int(r.get('maxversion'), 2147483647), RelModTitle=r.get('title')))
            elif r.tag == 'Dlc':
                rows['DlcRelationships'].append(dict(Type=kind, PackageId=r.get('id') or '', MinVersion=_int(r.get('minversion'), 0),
                                                     MaxVersion=_int(r.get('maxversion'), 1)))
            elif r.tag == 'Game':
                rows['GameVersionRelationships'].append(dict(Type=kind, MinVersion=r.get('minversion') or '1.0.0.0',
                                                             MaxVersion=r.get('maxversion') or ''))
    for table in TABLES:
        for row in rows[table]:
            row.update(ModID=mid, Version=ver)
    return rows


def _insert(con, table, row):
    cols = list(row)
    con.execute(f'INSERT INTO {table}({", ".join(cols)}) VALUES({", ".join("?" * len(cols))})', [row[c] for c in cols])


def write(ordered, out, path_as):
    """Write the registry for ordered (dbbuild.ModInfo list, activation order) to out (replaced)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f'{out.name}.{os.getpid()}.tmp')
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        for stmt in SCHEMA:
            con.execute(stmt)
        con.execute('INSERT INTO ModSystemSettings VALUES(?, ?)', ('Version', SYSTEM_VERSION))
        for m in ordered:
            rows = modinfo_rows(m.path, path_as)
            for table in ('Mods',) + tuple(TABLES):
                for row in rows[table]:
                    _insert(con, table, row)
        con.commit()
    finally:
        con.close()
    winproc.replace(tmp, out)
    return out


def build(mods_dir, mods, out, path_as=None):
    """Registry for mods ('ID[:VER]' strings or (id, version) pairs) below mods_dir. Returns a summary dict."""
    mods_dir = Path(mods_dir).resolve()
    ordered = select(mods_dir, mods)
    write(ordered, out, path_as or mods_dir)
    return dict(out=str(Path(out).resolve()), modsDir=str(mods_dir), pathAs=str(path_as or mods_dir),
                mods=[dict(id=m.id, version=m.version, name=m.name, folder=m.folder.name) for m in ordered])


def key_of(mods_dir, ordered, path_as):
    h = hashlib.sha256()
    h.update(json.dumps([FORMAT, os.path.normcase(str(mods_dir)), os.path.normcase(str(path_as))]).encode('utf-8'))
    for m in ordered:
        h.update(f'|{m.id}|{m.version}|{m.folder.name}|'.encode('utf-8'))
        h.update(hashlib.sha256(m.path.read_bytes()).digest())
    return h.hexdigest()[:16].upper()


def cached(cfg, mods, mods_dir=None, log=print):
    """(path, info): the generated registry for mods below mods_dir (default: config mods_dir), cached in
    <db_cache>/modsdb/<key>/. An empty mod set gives a registry with no mods (and needs no MODS folder)."""
    wanted = parse_mods(mods)
    if wanted:
        mods_dir = Path(mods_dir or cfg.require('mods_dir', 'pass --mods-dir')).resolve()
    else:
        mods_dir = Path(mods_dir).resolve() if mods_dir else cfg.require('db_cache') / 'modsdb' / 'no-mods' / 'MODS'
        mods_dir.mkdir(parents=True, exist_ok=True)
    ordered = select(mods_dir, wanted)
    key = key_of(mods_dir, ordered, mods_dir)
    entry = cfg.require('db_cache') / 'modsdb' / key
    path = entry / FILE_NAME
    info = dict(modsDb=str(path), modsDir=str(mods_dir), cacheKey=key,
                mods=[dict(id=m.id, version=m.version, name=m.name, folder=m.folder.name) for m in ordered])
    if (entry / 'mods.json').is_file() and path.is_file():
        return path, info
    write(ordered, path, mods_dir)
    (entry / 'mods.json').write_text(json.dumps(info, indent=1) + '\n', encoding='utf-8')
    log(f'mods registry generated for {len(ordered)} mods: {path}')
    return path, info


def resolve(cfg, mods=None, mods_dir=None, mods_db=None, log=print):
    """(mods_dir, mods_db) for a game: for a mod set (a list, [] for none) the generated registry and the MODS folder
    it describes, else the ones given or configured. Raises ModsDbError."""
    if mods is None:
        return (Path(mods_dir) if mods_dir else cfg.mods_dir), (Path(mods_db) if mods_db else cfg.mods_db)
    if mods_db:
        raise ModsDbError('a mod set (--mod, --no-mods) and a mods database (--mods-db) exclude each other')
    path, info = cached(cfg, mods, mods_dir, log=log)
    return Path(info['modsDir']), path


def game_dll(cfg):
    """The game's own DLL (Assets/DLC/Expansion2), or None."""
    p = cfg.game_dir / 'Assets/DLC/Expansion2' / config.DLL_NAME if cfg.game_dir else None
    return p if p and p.is_file() else None


def choose_dll(cfg, dll=None, mods=None, mods_dir=None, given_as='--dll'):
    """(DLL, why) for a game. The DLL given wins, then a dll set in civ5headless.toml. For an explicit mod set (mods:
    'ID[:VER]' strings, [] for none) it is the first mod of the set, in activation order, that ships
    CvGameCore_Expansion2.dll, else the game's own DLL. Otherwise it is the configured one (discovery: the active
    mods'). Raises ConfigError (ModsDbError for a bad mod set) when there is none."""
    if dll:
        return Path(dll).resolve(), f'{given_as} given'
    if cfg.dll and cfg.sources.get('dll', config.Source('unset')).kind == 'file':
        return Path(cfg.dll).resolve(), f'dll in {cfg.file.name if cfg.file else config.FILE_NAME}'
    if mods is not None:
        if parse_mods(mods):
            for m in select(mods_dir or cfg.require('mods_dir', 'pass --mods-dir'), mods):
                if (m.folder / config.DLL_NAME).is_file():
                    return (m.folder / config.DLL_NAME).resolve(), f'the mod set: {m.name or m.id} ships the DLL'
        own = game_dll(cfg)
        if own is None:
            raise config.ConfigError("the game's own DLL (Assets/DLC/Expansion2) is not there; pass --dll")
        return own.resolve(), ("no mods: the game's own DLL" if not parse_mods(mods) else
                               "no mod of the set ships the DLL: the game's own")
    dll = cfg.require('dll', 'pass --dll')
    source = cfg.sources.get('dll', config.Source('unset'))
    return Path(dll).resolve(), (f'{source.kind}: {source.note}' if source.note else
                                 'configured' if source.kind == 'unset' else source.kind)


def _rows(con, table, cols, mid, ver):
    return [tuple(r) for r in con.execute(f'SELECT {", ".join(cols)} FROM {table} WHERE lower(ModID) = ? AND Version = ? '
                                          'ORDER BY rowid', (mid, ver))]


def compare(a, b, mods=None, ignore=()):
    """Differences between registries a and b for the activated mods of a (or mods: [(id, version)]), leaving out the
    columns named in ignore (e.g. md5, which the game updates only when it rescans a mod).
    Returns dict(identical, order: [a order, b order] when they differ, tables: {table: [mod notes]})."""
    ca = sqlite3.connect(Path(a).resolve().as_uri() + '?mode=ro', uri=True)
    cb = sqlite3.connect(Path(b).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        act_a, act_b = dbbuild.activated_from_registry(Path(a)), dbbuild.activated_from_registry(Path(b))
        mods = list(mods) if mods is not None else act_a
        out = dict(identical=True, mods=[f'{i} v{v}' for i, v in mods], order=None, tables={})
        if act_a != act_b:
            out.update(identical=False, order=[[f'{i} v{v}' for i, v in act_a], [f'{i} v{v}' for i, v in act_b]])
        for mid, ver in mods:
            ra = ca.execute('SELECT Type, Installed, Enabled, Activated FROM Mods WHERE lower(ModID) = ? AND Version = ?',
                            (mid, str(ver))).fetchall()
            rb = cb.execute('SELECT Type, Installed, Enabled, Activated FROM Mods WHERE lower(ModID) = ? AND Version = ?',
                            (mid, str(ver))).fetchall()
            checks = [('Mods', ra, rb)]
            for table, cols in TABLES.items():
                cols = [c for c in cols if c not in ignore]
                checks.append((table, _rows(ca, table, cols, mid, ver), _rows(cb, table, cols, mid, ver)))
            for table, x, y in checks:
                if x != y:
                    out['identical'] = False
                    first = next((i for i in range(min(len(x), len(y))) if x[i] != y[i]), min(len(x), len(y)))
                    out['tables'].setdefault(table, []).append(dict(
                        mod=f'{mid} v{ver}', rows=[len(x), len(y)], firstDifference=first,
                        a=list(x[first]) if first < len(x) else None, b=list(y[first]) if first < len(y) else None))
        return out
    finally:
        ca.close()
        cb.close()


def main(argv=None, cfg=None):
    cfg = cfg or config.load()
    ap = argparse.ArgumentParser(prog='c5h modsdb', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mods-dir', type=Path, default=cfg.mods_dir, help='MODS folder (or a copy) holding the .modinfo files '
                    '(default: config mods_dir)')
    ap.add_argument('--mod', action='append', default=[], help='mod GUID[:version] to activate (repeatable; default: the '
                    'configured active mods)')
    ap.add_argument('--out', type=Path, help='registry to write (default: the cache, path printed)')
    ap.add_argument('--path-as', type=Path, help='MODS folder recorded in EvaluatedPath (default: --mods-dir)')
    ap.add_argument('--compare', type=Path, metavar='REGISTRY', help='compare the result with this registry')
    ap.add_argument('--ignore', action='append', default=[], metavar='COLUMN',
                    help='leave this column out of the comparison (repeatable), e.g. md5')
    ap.add_argument('--json', action='store_true', help='print the summary (and comparison) as JSON')
    a = ap.parse_args(argv)
    mods = a.mod or [(m.id, m.version) for m in cfg.active_mods]
    if not mods:
        ap.error('no --mod given and no active mods configured (c5h doctor)')
    if a.mods_dir is None:
        ap.error('mods_dir is not set: pass --mods-dir or set mods_dir in civ5headless.toml')
    try:
        if a.out:
            summary = build(a.mods_dir, mods, a.out, a.path_as)
        else:
            if a.path_as:
                ap.error('--path-as needs --out')
            path, summary = cached(cfg, mods, a.mods_dir, log=lambda m: print(m, file=sys.stderr))
            summary = dict(summary, out=str(path))
    except (ModsDbError, config.ConfigError, ET.ParseError, OSError) as error:
        print(f'c5h modsdb: {error}', file=sys.stderr)
        return 1
    code = 0
    if a.compare:
        summary['compare'] = compare(summary['out'], a.compare, ignore=set(a.ignore))
        code = 0 if summary['compare']['identical'] else 1
    if a.json:
        print(json.dumps(summary, indent=1))
        return code
    for m in summary['mods']:
        print(f"  {m['name']} (v{m['version']}): {m['folder']}", file=sys.stderr)
    if a.compare:
        c = summary['compare']
        print(f"compared with {a.compare}: {'identical' if c['identical'] else 'differs'}", file=sys.stderr)
        if c['order']:
            print(f"  activation order: {c['order'][0]} vs {c['order'][1]}", file=sys.stderr)
        for table, notes in c['tables'].items():
            for n in notes:
                print(f"  {table} {n['mod']}: {n['rows'][0]} vs {n['rows'][1]} rows, first difference at {n['firstDifference']}: "
                      f"{n['a']} vs {n['b']}", file=sys.stderr)
    print(summary['out'])
    return code


if __name__ == '__main__':
    sys.exit(main())
