"""Configuration: civ5headless.toml plus discovery for anything it leaves out.

  c5h doctor        prints every value, where it came from, and what is missing

config.load(path=None) -> Config. The file is the first of: the path given, C5H_CONFIG, civ5headless.toml in the
current folder, civ5headless.toml in the workspace. Every key is optional and top-level (civ5headless.example.toml
lists them); relative paths are relative to the file's folder, and ~ and %VAR% are expanded. Use forward slashes or
single-quoted TOML strings for Windows paths. Tables ([verify], ...) are left to the code that reads them
(Config.raw). ai_logging ("on" or "off") is the one key that is not a path: the AI logging of games that do not choose
(run.py, docs/ai-logging.md).

Each value has a source:
- file: from the file;
- discovery: found on this machine:
  - game_dir: the Steam library that holds Sid Meier's Civilization V. Steam comes from the registry (HKCU
    Software\\Valve\\Steam SteamPath, then the HKLM InstallPath), the libraries from steamapps/libraryfolders.vdf,
    the install folder from appmanifest_8930.acf;
  - user_dir: <Documents>\\My Games\\Sid Meier's Civilization 5, with Documents from SHGetKnownFolderPath
    (FOLDERID_Documents), which follows a redirected (OneDrive) Documents folder;
  - mods_dir, cache_dir, config_ini: MODS, cache and config.ini in user_dir; mods_db and loc_db:
    Civ5ModsDatabase.db and Localization-Merged.db in cache_dir; active_mods: the mods with Activated = 1 in
    mods_db, in activation order (the live database lists none while the game sits in the mods browser);
  - dll: CvGameCore_Expansion2.dll of the first active mod that has one, else the Community Patch folder's, else
    the game's own (Assets/DLC/Expansion2);
  - exe: out/latest.txt in the repository (tools/build_host.py), else bin/civ5headless.exe in the kit, else
    civ5headless.exe next to the package;
  - toolchain: toolchain/ in the repository;
- environment: toolchain from C5H_TOOLCHAIN;
- default: workspace is the repository root for a checkout, the kit folder for an unpacked release kit (kit.json
  beside python/, tools/make_kit.py), else %LOCALAPPDATA%\\civ5headless; runs is <workspace>/runs and db_cache
  <workspace>/cache/db; profiles (the host's UI emulation profiles) and probes (Lua probes such as fingerprint.lua)
  are <workspace>/profiles and <workspace>/probes, else the repository's or the kit's folders;
- unset: nothing found. snapshot (the DLC snapshot the database builder starts from) and db (a built game database
  used as is) are never discovered.
"""
from __future__ import annotations
import ctypes
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sqlite3
import tomllib
import uuid
import xml.etree.ElementTree as ET

FILE_NAME = 'civ5headless.toml'
CIV5_APP_ID = '8930'
GAME_FOLDER = "Sid Meier's Civilization V"
USER_FOLDER = ('My Games', "Sid Meier's Civilization 5")
DLL_NAME = 'CvGameCore_Expansion2.dll'
CP_FOLDER = '(1) Community Patch'
EXE_NAME = 'civ5headless.exe'
KIT_MARKER = 'kit.json'
PACKAGE = Path(__file__).resolve().parent
FOLDERID_DOCUMENTS = '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}'

# Path keys in the order they are resolved (later ones derive from earlier ones).
KEYS = ('workspace', 'game_dir', 'user_dir', 'mods_dir', 'cache_dir', 'mods_db', 'loc_db', 'config_ini', 'dll',
        'snapshot', 'db', 'db_cache', 'exe', 'profiles', 'probes', 'toolchain', 'runs')
DESCRIPTIONS = {
    'workspace': 'folder for runs and caches',
    'game_dir': "game folder (Sid Meier's Civilization V)",
    'user_dir': "Documents\\My Games\\Sid Meier's Civilization 5",
    'mods_dir': 'MODS folder (or a pinned copy)',
    'cache_dir': "the game's cache folder",
    'mods_db': 'Civ5ModsDatabase.db: the activated mods',
    'loc_db': 'Localization-Merged.db',
    'config_ini': "the game's config.ini",
    'dll': 'game DLL',
    'snapshot': 'DLC snapshot the database builder starts from',
    'db': 'built game database, used as is instead of the cache',
    'db_cache': 'database cache',
    'exe': 'civ5headless.exe',
    'profiles': "the host's UI emulation profiles",
    'probes': 'Lua probes (fingerprint.lua)',
    'toolchain': 'VC9 toolchain (building the host)',
    'runs': 'run folders',
}


class ConfigError(Exception):
    pass


def on_off(value, name='ai_logging'):
    """'on' or 'off' from 'on', 'off', True or False, and None for None; raises ValueError for anything else."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 'on' if value else 'off'
    if isinstance(value, str) and value.strip().lower() in ('on', 'off'):
        return value.strip().lower()
    raise ValueError(f'{name} is on or off, not {value!r}')


@dataclass(frozen=True)
class Source:
    kind: str       # file, discovery, environment, default or unset
    note: str = ''  # how it was found, or why not


@dataclass(frozen=True)
class Mod:
    id: str
    version: int
    name: str


@dataclass
class Config:
    """Resolved absolute paths (None when unset); sources[key] says where each came from."""
    workspace: Path | None = None
    game_dir: Path | None = None
    user_dir: Path | None = None
    mods_dir: Path | None = None
    cache_dir: Path | None = None
    mods_db: Path | None = None
    loc_db: Path | None = None
    config_ini: Path | None = None
    dll: Path | None = None
    snapshot: Path | None = None
    db: Path | None = None
    db_cache: Path | None = None
    exe: Path | None = None
    profiles: Path | None = None
    probes: Path | None = None
    toolchain: Path | None = None
    runs: Path | None = None
    ai_logging: str | None = None                          # on or off for games that do not choose (None: off, run.py)
    file: Path | None = None                               # the civ5headless.toml read, if any
    active_mods: list[Mod] = field(default_factory=list)   # Activated = 1 in mods_db, in activation order
    sources: dict[str, Source] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)          # problems found while loading
    raw: dict = field(default_factory=dict)                 # the parsed file

    def set(self, key, value, kind, note=''):
        setattr(self, key, Path(value) if value is not None else None)
        self.sources[key] = Source(kind if value is not None else 'unset', note)

    def require(self, key, hint=''):
        """The value of key, or ConfigError naming the key and how to set it."""
        value = getattr(self, key)
        if value is None:
            note = self.sources.get(key, Source('unset')).note
            raise ConfigError(f'{key} ({DESCRIPTIONS[key]}) is not set' + (f' ({note})' if note else '') +
                              f'; set {key} in {FILE_NAME}' + (f' or {hint}' if hint else ''))
        return value

    def rows(self):
        """(key, value, source, note) for every path key, in KEYS order."""
        return [(k, getattr(self, k), self.sources.get(k, Source('unset')).kind, self.sources.get(k, Source('unset')).note)
                for k in KEYS]

    def to_dict(self):
        return dict(file=str(self.file) if self.file else None,
                    values={k: dict(value=str(v) if v else None, source=s, note=n) for k, v, s, n in self.rows()},
                    aiLogging=self.ai_logging, activeMods=[m.__dict__ for m in self.active_mods], notes=list(self.notes))


class System:
    """What discovery reads from the machine. Tests substitute a fake with the same methods."""

    def registry_value(self, hive, key, name):
        """A string value from HKCU or HKLM (32- and 64-bit views), or None."""
        try:
            import winreg
        except ImportError:
            return None
        root = {'HKCU': winreg.HKEY_CURRENT_USER, 'HKLM': winreg.HKEY_LOCAL_MACHINE}[hive]
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as k:
                    value = winreg.QueryValueEx(k, name)[0]
                    return value if isinstance(value, str) else None
            except OSError:
                continue
        return None

    def documents(self):
        """The Documents known folder (follows redirection), or None."""
        return known_folder(FOLDERID_DOCUMENTS)

    def environ(self):
        return os.environ

    def cwd(self):
        return Path.cwd()

    def repository(self):
        """The repository root when the package runs from a checkout, else None."""
        root = PACKAGE.parents[1]
        return root if (root / 'pyproject.toml').is_file() and (root / 'host').is_dir() else None

    def kit(self):
        """The kit root when the package runs from an unpacked release kit (kit.json beside python/), else None."""
        root = PACKAGE.parents[1]
        return root if (root / KIT_MARKER).is_file() else None

    def local_app_data(self):
        value = os.environ.get('LOCALAPPDATA')
        return Path(value) if value else Path.home() / 'AppData/Local'


class _GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort), ('Data3', ctypes.c_ushort),
                ('Data4', ctypes.c_ubyte * 8)]


def known_folder(folder_id):
    """SHGetKnownFolderPath(folder_id) as a Path, or None."""
    try:
        shell32 = ctypes.WinDLL('shell32')
        ole32 = ctypes.WinDLL('ole32')
    except (AttributeError, OSError):
        return None
    guid = _GUID.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    out = ctypes.c_void_p()
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    try:
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
            return None
        return Path(ctypes.wstring_at(out.value))
    finally:
        if out.value:
            ole32.CoTaskMemFree(out)


# ---- Steam ----

def parse_vdf(text):
    """Valve KeyValues text (libraryfolders.vdf, appmanifest_*.acf) as nested dicts."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    pos = 0

    def unescape(s):
        return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), s)

    def block():
        nonlocal pos
        out = {}
        while pos < len(tokens):
            string, brace = tokens[pos]
            pos += 1
            if brace == '}':
                return out
            if brace == '{':
                continue  # stray brace
            key = unescape(string)
            if pos >= len(tokens):
                break
            value, vbrace = tokens[pos]
            pos += 1
            out[key] = block() if vbrace == '{' else unescape(value)
        return out

    return block()


def steam_roots(system):
    """(Steam folder, how it was found), without duplicates."""
    out, seen = [], set()
    for hive, key, name in (('HKCU', r'Software\Valve\Steam', 'SteamPath'),
                            ('HKLM', r'SOFTWARE\Valve\Steam', 'InstallPath'),
                            ('HKLM', r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')):
        value = system.registry_value(hive, key, name)
        if not value:
            continue
        path = Path(os.path.normpath(value))
        if os.path.normcase(str(path)) in seen:
            continue
        seen.add(os.path.normcase(str(path)))
        out.append((path, f'{hive} {key} {name}'))
    return out


def steam_libraries(root):
    """Library folders of one Steam installation: itself plus libraryfolders.vdf's (both file formats)."""
    libs = [root]
    for vdf in (root / 'steamapps/libraryfolders.vdf', root / 'config/libraryfolders.vdf'):
        try:
            data = parse_vdf(vdf.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            continue
        folders = data.get('libraryfolders') or data.get('LibraryFolders') or {}
        for key, value in folders.items():
            if isinstance(value, dict) and value.get('path'):
                libs.append(Path(value['path']))
            elif isinstance(value, str) and key.isdigit():
                libs.append(Path(value))  # pre-2021 format: "1" "<library folder>"
        break
    unique = []
    for lib in libs:
        if os.path.normcase(str(lib)) not in {os.path.normcase(str(u)) for u in unique}:
            unique.append(lib)
    return unique


def find_game(system):
    """(game folder, note) from the Steam libraries, or (None, why not)."""
    roots = steam_roots(system)
    if not roots:
        return None, 'no Steam installation in the registry'
    for root, how in roots:
        for lib in steam_libraries(root):
            folder = GAME_FOLDER
            manifest = lib / f'steamapps/appmanifest_{CIV5_APP_ID}.acf'
            if manifest.is_file():
                state = parse_vdf(manifest.read_text(encoding='utf-8', errors='replace')).get('AppState') or {}
                folder = state.get('installdir') or folder
            game = lib / 'steamapps/common' / folder
            if (game / 'Assets').is_dir():
                return game, f'Steam library {lib} ({how}, libraryfolders.vdf)'
    return None, 'not in any Steam library (' + ', '.join(str(r) for r, _ in roots) + ')'


# ---- mods ----

def active_mods(mods_db):
    """Activated mods of a Civ5ModsDatabase.db in activation (RowID) order."""
    con = sqlite3.connect(Path(mods_db).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        rows = con.execute('SELECT ModID, Version FROM Mods WHERE Activated = 1 ORDER BY RowID').fetchall()
        names = {}
        try:
            for mid, ver, value in con.execute("SELECT ModID, Version, Value FROM ModProperties WHERE Name = 'Name'"):
                names[(str(mid).lower(), int(ver))] = value
        except sqlite3.Error:
            pass
    finally:
        con.close()
    return [Mod(str(mid).strip('{}').lower(), int(ver or 0), names.get((str(mid).lower(), int(ver or 0)), ''))
            for mid, ver in rows]


def mod_folders(mods_dir):
    """{(mod id, version): folder} from the .modinfo files one level below mods_dir."""
    out = {}
    for info in sorted(Path(mods_dir).glob('*/*.modinfo')):
        try:
            root = ET.fromstring(info.read_bytes())
        except (OSError, ET.ParseError):
            continue
        out.setdefault(((root.get('id') or '').strip('{}').lower(), int(root.get('version') or 0)), info.parent)
    return out


def find_dll(cfg):
    if cfg.mods_dir and cfg.active_mods:
        folders = mod_folders(cfg.mods_dir)
        for m in cfg.active_mods:
            folder = folders.get((m.id, m.version))
            if folder and (folder / DLL_NAME).is_file():
                return folder / DLL_NAME, f'active mod {m.name or m.id}'
    if cfg.mods_dir and (cfg.mods_dir / CP_FOLDER / DLL_NAME).is_file():
        return cfg.mods_dir / CP_FOLDER / DLL_NAME, 'Community Patch folder in MODS'
    if cfg.game_dir and (cfg.game_dir / 'Assets/DLC/Expansion2' / DLL_NAME).is_file():
        return cfg.game_dir / 'Assets/DLC/Expansion2' / DLL_NAME, "the game's own DLL"
    return None, 'no active mod with a DLL, no Community Patch folder, no game DLL'


# ---- loading ----

def find_file(path, system):
    if path:
        p = Path(path)
        if not p.is_file():
            raise ConfigError(f'config file {p} not found')
        return p.resolve()
    env = system.environ().get('C5H_CONFIG')
    if env:
        if not Path(env).is_file():
            raise ConfigError(f'C5H_CONFIG names {env}, which does not exist')
        return Path(env).resolve()
    for folder in (system.cwd(), system.repository()):
        if folder and (Path(folder) / FILE_NAME).is_file():
            return (Path(folder) / FILE_NAME).resolve()
    return None


def expand(value, base, environ):
    text = re.sub(r'%([^%]+)%', lambda m: environ.get(m.group(1), m.group(0)), str(value))
    p = Path(os.path.expanduser(text))
    return Path(os.path.abspath(p if p.is_absolute() else Path(base) / p))


def load(path=None, system=None):
    """Config from the file (path, C5H_CONFIG, ./civ5headless.toml, <workspace>/civ5headless.toml) plus discovery."""
    system = system or System()
    environ = system.environ()
    cfg = Config()
    cfg.file = find_file(path, system)
    if cfg.file:
        try:
            cfg.raw = tomllib.loads(cfg.file.read_text(encoding='utf-8'))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
            raise ConfigError(f'{cfg.file}: {error}') from None
    base = cfg.file.parent if cfg.file else system.cwd()
    for key, value in cfg.raw.items():
        if isinstance(value, dict):
            continue  # tables belong to other readers
        if key == 'ai_logging':
            try:
                cfg.ai_logging = on_off(value)
            except ValueError as error:
                raise ConfigError(f'{cfg.file}: {error}') from None
        elif key not in KEYS:
            cfg.notes.append(f'unknown key {key!r} in {cfg.file.name}')
        elif not isinstance(value, str):
            cfg.notes.append(f'{key} in {cfg.file.name} is not a string')
        elif value.strip():
            cfg.set(key, expand(value, base, environ), 'file')
    given = set(cfg.sources)
    repo = system.repository()
    kit = None if repo else system.kit()

    def default(key, value, kind, note=''):
        if key not in given:
            cfg.set(key, value, kind, note)

    def derived(key, parent, *parts):
        """key = parent/parts when it exists (discovery), else unset with the path tried."""
        if key in given:
            return
        base_dir = getattr(cfg, parent)
        if base_dir is None:
            cfg.set(key, None, 'unset', f'{parent} is not set')
            return
        p = base_dir.joinpath(*parts)
        cfg.set(key, p if p.exists() else None, 'discovery', f'{parent}\\' + '\\'.join(parts) + ('' if p.exists() else ' does not exist'))

    if repo:
        default('workspace', repo, 'default', 'the repository root')
    elif kit:
        default('workspace', kit, 'default', 'the kit folder')
    else:
        default('workspace', system.local_app_data() / 'civ5headless', 'default', 'LOCALAPPDATA')
    if 'game_dir' not in given:
        game, note = find_game(system)
        cfg.set('game_dir', game, 'discovery', note)
    if 'user_dir' not in given:
        docs = system.documents()
        if docs is None:
            cfg.set('user_dir', None, 'unset', 'no Documents known folder')
        else:
            p = Path(docs).joinpath(*USER_FOLDER)
            cfg.set('user_dir', p if p.is_dir() else None, 'discovery',
                    'Documents known folder' if p.is_dir() else f'{p} does not exist')
    derived('mods_dir', 'user_dir', 'MODS')
    derived('cache_dir', 'user_dir', 'cache')
    derived('mods_db', 'cache_dir', 'Civ5ModsDatabase.db')
    derived('loc_db', 'cache_dir', 'Localization-Merged.db')
    derived('config_ini', 'user_dir', 'config.ini')
    if cfg.mods_db and cfg.mods_db.is_file():
        try:
            cfg.active_mods = active_mods(cfg.mods_db)
        except sqlite3.Error as error:
            cfg.notes.append(f'{cfg.mods_db}: cannot read the activated mods ({error})')
    if 'dll' not in given:
        dll, note = find_dll(cfg)
        cfg.set('dll', dll, 'discovery', note)
    default('snapshot', None, 'unset', "built from the game's files when needed (c5h snapshot)")
    default('db', None, 'unset', 'games use the cached build for the mods and DLL')
    default('db_cache', cfg.workspace / 'cache/db', 'default', 'workspace\\cache\\db')
    default('runs', cfg.workspace / 'runs', 'default', 'workspace\\runs')
    if 'exe' not in given:
        exe, note = find_exe(repo, kit)
        cfg.set('exe', exe, 'discovery', note)
    for key in ('profiles', 'probes'):
        if key not in given:
            folder, kind, note = find_folder(cfg.workspace, repo or kit, key, 'repository' if repo else 'kit')
            cfg.set(key, folder, kind, note)
    if 'toolchain' not in given:
        if environ.get('C5H_TOOLCHAIN'):
            cfg.set('toolchain', expand(environ['C5H_TOOLCHAIN'], system.cwd(), environ), 'environment', 'C5H_TOOLCHAIN')
        elif repo and (repo / 'toolchain').is_dir():
            cfg.set('toolchain', repo / 'toolchain', 'discovery', 'toolchain\\ in the repository')
        else:
            cfg.set('toolchain', None, 'unset', 'no C5H_TOOLCHAIN, no toolchain\\ in the repository'
                    ' (tools/toolchain.py also looks for an installed Visual Studio 2008)')
    return cfg


def find_folder(workspace, root, name, label='repository'):
    """(folder, source kind, note) for <workspace>/name, else <root>/name (the repository or the kit)."""
    if workspace and (workspace / name).is_dir():
        return workspace / name, 'default', f'workspace\\{name}'
    if root and (root / name).is_dir():
        return root / name, 'discovery', f'{name}\\ in the {label}'
    return None, 'unset', f'no {name}\\ in the workspace' + ('' if root is None or root == workspace else f' or the {label}')


def find_exe(repo, kit=None):
    if repo:
        marker = repo / 'out/latest.txt'
        if marker.is_file():
            p = Path(marker.read_text(encoding='utf-8').strip())
            p = p / EXE_NAME if p.is_dir() else p
            if p.is_file():
                return p, 'out\\latest.txt (tools/build_host.py)'
    if kit and (kit / 'bin' / EXE_NAME).is_file():
        return kit / 'bin' / EXE_NAME, 'bin\\ in the kit'
    for folder in (PACKAGE, PACKAGE / 'bin', PACKAGE.parent):
        if (folder / EXE_NAME).is_file():
            return folder / EXE_NAME, 'next to the package'
    return None, ('no civ5headless.exe in the kit\'s bin\\' if kit else
                  'no out\\latest.txt in the repository and no civ5headless.exe next to the package'
                  ' (python tools/build_host.py, or use a release kit)')
