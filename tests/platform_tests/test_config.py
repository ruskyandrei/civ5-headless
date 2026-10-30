"""config.load: civ5headless.toml plus discovery, against a fake machine (registry, Documents, Steam, MODS)."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from civ5headless import config

CP_ID = 'd1b6328c-ff44-4b0d-aad7-c657f83610cd'
VP_ID = '8411a7a8-dad3-4622-a18e-fcc18324c799'
UI_ID = 'dab4ce8a-73a5-4beb-81c1-808e4baf45a8'


class FakeSystem(config.System):
    def __init__(self, registry=None, documents=None, environ=None, cwd=None, repository=None, local_app_data=None,
                 kit=None):
        self.registry, self.docs, self.env = registry or {}, documents, environ or {}
        self.cwd_path, self.repo, self.lad, self.kit_root = cwd, repository, local_app_data, kit

    def registry_value(self, hive, key, name):
        return self.registry.get((hive, key, name))

    def documents(self):
        return self.docs

    def environ(self):
        return self.env

    def cwd(self):
        return self.cwd_path

    def repository(self):
        return self.repo

    def kit(self):
        return self.kit_root

    def local_app_data(self):
        return self.lad


def touch(path, data=b''):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def modinfo(folder, mod_id, version, name):
    touch(folder / f'{name}.modinfo', f'<?xml version="1.0"?><Mod id="{mod_id}" version="{version}">'
                                      f'<Properties><Name>{name}</Name></Properties></Mod>'.encode())


def mods_database(path, mods):
    """mods: [(id, version, activated, name)] in RowID order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE Mods('RowID' INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, 'ModID' TEXT NOT NULL, 'Version' TEXT, "
                "'Activated' BOOL DEFAULT 0)")
    con.execute('CREATE TABLE ModProperties(ModID TEXT, Version INTEGER, Name TEXT, Value TEXT)')
    for mid, ver, act, name in mods:
        con.execute('INSERT INTO Mods(ModID, Version, Activated) VALUES(?, ?, ?)', (mid, str(ver), act))
        con.execute("INSERT INTO ModProperties VALUES(?, ?, 'Name', ?)", (mid, ver, name))
    con.commit()
    con.close()


class Machine:
    """A fake machine in a temporary folder: Steam with two libraries (the game in the second), Documents with
    My Games (MODS with three mods, cache with the databases), and a repository checkout with a built host."""

    def __init__(self, root):
        self.root = Path(root)
        self.steam = self.root / 'Program Files (x86)/Steam'
        self.library = self.root / 'SteamLibrary'
        self.game = self.library / "steamapps/common/Sid Meier's Civilization V"
        touch(self.game / 'Assets/DLC/Expansion2/CvGameCore_Expansion2.dll')
        touch(self.library / 'steamapps/appmanifest_8930.acf',
              b'"AppState"\n{\n\t"appid"\t\t"8930"\n\t"installdir"\t\t"Sid Meier\'s Civilization V"\n}\n')
        vdf = ('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"' + str(self.steam).replace('\\', '\\\\') + '"\n'
               '\t\t"apps"\n\t\t{\n\t\t\t"228980"\t\t"1"\n\t\t}\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"'
               + str(self.library).replace('\\', '\\\\') + '"\n\t\t"apps"\n\t\t{\n\t\t\t"8930"\t\t"9231302100"\n\t\t}\n\t}\n}\n')
        touch(self.steam / 'steamapps/libraryfolders.vdf', vdf.encode())
        self.documents = self.root / 'profile/OneDrive/Documents'  # a redirected Documents folder
        self.user = self.documents / "My Games/Sid Meier's Civilization 5"
        self.mods = self.user / 'MODS'
        modinfo(self.mods / '(1) Community Patch', CP_ID, 151, '(1) Community Patch')
        touch(self.mods / '(1) Community Patch/CvGameCore_Expansion2.dll')
        modinfo(self.mods / '(2) Vox Populi', VP_ID, 17, '(2) Vox Populi')
        modinfo(self.mods / 'Small Icons', UI_ID, 4, 'UI - Small Resource Icons')
        mods_database(self.user / 'cache/Civ5ModsDatabase.db',
                      [(CP_ID, 151, 1, '(1) Community Patch'), (VP_ID, 17, 1, '(2) Vox Populi'), (UI_ID, 4, 0, 'UI - Small Resource Icons')])
        touch(self.user / 'cache/Localization-Merged.db')
        touch(self.user / 'config.ini', b'EnableTuner = 1\n')
        self.repo = self.root / 'repo'
        touch(self.repo / 'pyproject.toml')
        (self.repo / 'host').mkdir()
        build = self.repo / 'out/Release/20260101-000000'
        touch(build / 'civ5headless.exe')
        touch(self.repo / 'out/latest.txt', str(build).encode())
        self.cwd = self.root / 'work'
        self.cwd.mkdir()

    def system(self, **kw):
        args = dict(registry={('HKCU', r'Software\Valve\Steam', 'SteamPath'): str(self.steam).replace('\\', '/').lower()},
                    documents=self.documents, environ={}, cwd=self.cwd, repository=self.repo,
                    local_app_data=self.root / 'AppData/Local')
        args.update(kw)
        return FakeSystem(**args)


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.m = Machine(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def same(self, a, b):
        self.assertEqual(os.path.normcase(str(a)), os.path.normcase(str(b)))

    def test_everything_discovered(self):
        cfg = config.load(system=self.m.system())
        self.assertIsNone(cfg.file)
        self.same(cfg.game_dir, self.m.game)
        self.assertEqual(cfg.sources['game_dir'].kind, 'discovery')
        self.assertIn('SteamLibrary', cfg.sources['game_dir'].note)
        self.same(cfg.user_dir, self.m.user)
        self.same(cfg.mods_dir, self.m.mods)
        self.same(cfg.cache_dir, self.m.user / 'cache')
        self.same(cfg.mods_db, self.m.user / 'cache/Civ5ModsDatabase.db')
        self.same(cfg.loc_db, self.m.user / 'cache/Localization-Merged.db')
        self.same(cfg.config_ini, self.m.user / 'config.ini')
        self.assertEqual([(m.id, m.version, m.name) for m in cfg.active_mods],
                         [(CP_ID, 151, '(1) Community Patch'), (VP_ID, 17, '(2) Vox Populi')])
        self.same(cfg.dll, self.m.mods / '(1) Community Patch/CvGameCore_Expansion2.dll')
        self.assertIn('active mod', cfg.sources['dll'].note)
        self.same(cfg.exe, self.m.repo / 'out/Release/20260101-000000/civ5headless.exe')
        self.same(cfg.workspace, self.m.repo)
        self.assertEqual(cfg.sources['workspace'].kind, 'default')
        self.same(cfg.runs, self.m.repo / 'runs')
        self.same(cfg.db_cache, self.m.repo / 'cache/db')
        for key in ('snapshot', 'db', 'toolchain'):
            self.assertIsNone(getattr(cfg, key))
            self.assertEqual(cfg.sources[key].kind, 'unset')
        self.assertEqual(cfg.notes, [])
        self.assertTrue(all(p is None or p.is_absolute() for _, p, _, _ in cfg.rows()))

    def test_hklm_install_path(self):
        system = self.m.system(registry={('HKLM', r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'): str(self.m.steam)})
        cfg = config.load(system=system)
        self.same(cfg.game_dir, self.m.game)
        self.assertIn('HKLM', cfg.sources['game_dir'].note)

    def test_old_libraryfolders_format(self):
        vdf = ('"LibraryFolders"\n{\n\t"TimeNextStatsReport"\t\t"1600000000"\n\t"ContentStatsID"\t\t"-1"\n'
               '\t"1"\t\t"' + str(self.m.library).replace('\\', '\\\\') + '"\n}\n')
        (self.m.steam / 'steamapps/libraryfolders.vdf').write_text(vdf, encoding='utf-8')
        self.same(config.load(system=self.m.system()).game_dir, self.m.game)

    def test_install_dir_from_app_manifest(self):
        renamed = self.m.library / 'steamapps/common/Civ5'
        self.m.game.rename(renamed)
        (self.m.library / 'steamapps/appmanifest_8930.acf').write_text('"AppState" { "installdir" "Civ5" }', encoding='utf-8')
        self.same(config.load(system=self.m.system()).game_dir, renamed)

    def test_nothing_found(self):
        cfg = config.load(system=self.m.system(registry={}, documents=None, repository=None))
        for key in ('game_dir', 'user_dir', 'mods_dir', 'mods_db', 'exe'):
            self.assertIsNone(getattr(cfg, key), key)
            self.assertEqual(cfg.sources[key].kind, 'unset', key)
        self.assertIn('registry', cfg.sources['game_dir'].note)
        self.same(cfg.workspace, self.m.root / 'AppData/Local/civ5headless')
        with self.assertRaises(config.ConfigError) as error:
            cfg.require('game_dir', 'pass --game-dir')
        self.assertIn('game_dir', str(error.exception))
        self.assertIn('--game-dir', str(error.exception))

    def test_my_games_missing(self):
        empty = self.m.root / 'EmptyDocuments'
        empty.mkdir()
        cfg = config.load(system=self.m.system(documents=empty))
        self.assertIsNone(cfg.user_dir)
        self.assertIn('does not exist', cfg.sources['user_dir'].note)
        self.assertIsNone(cfg.mods_db)
        # no mods: the game's own DLL
        self.same(cfg.dll, self.m.game / 'Assets/DLC/Expansion2/CvGameCore_Expansion2.dll')

    def test_dll_fallbacks(self):
        mods_database(self.m.root / 'none.db', [(CP_ID, 151, 0, '(1) Community Patch')])
        cfg = config.load(system=self.m.system(), path=self.write_toml(f'mods_db = "{(self.m.root / "none.db").as_posix()}"'))
        self.assertEqual(cfg.active_mods, [])
        self.same(cfg.dll, self.m.mods / '(1) Community Patch/CvGameCore_Expansion2.dll')
        self.assertIn('Community Patch folder', cfg.sources['dll'].note)

    def write_toml(self, text, folder=None, name=config.FILE_NAME):
        p = (folder or self.m.cwd) / name
        p.write_text(text, encoding='utf-8')
        return p

    def test_file_values_and_relative_paths(self):
        conf = self.m.root / 'conf'
        conf.mkdir()
        p = self.write_toml('game_dir = "game"\nsnapshot = "%SNAPDIR%/dlc.db"\nruns = \'..\\runs\'\n'
                            'exe = "C5H.exe"\nunknown_key = "x"\n[verify]\nworkers = 8\n', folder=conf)
        cfg = config.load(p, system=self.m.system(environ={'SNAPDIR': str(self.m.root / 'snap')}))
        self.same(cfg.file, p)
        self.same(cfg.game_dir, conf / 'game')
        self.assertEqual(cfg.sources['game_dir'].kind, 'file')
        self.same(cfg.snapshot, self.m.root / 'snap/dlc.db')
        self.same(cfg.runs, self.m.root / 'runs')
        self.same(cfg.exe, conf / 'C5H.exe')
        self.assertEqual(cfg.raw['verify'], {'workers': 8})
        self.assertEqual(len(cfg.notes), 1)
        self.assertIn('unknown_key', cfg.notes[0])
        # values derived from a discovered user_dir stay discovered
        self.assertEqual(cfg.sources['mods_dir'].kind, 'discovery')

    def test_file_search_order(self):
        here = self.write_toml('runs = "here-runs"')
        cfg = config.load(system=self.m.system())
        self.same(cfg.file, here)
        other = self.write_toml('runs = "env-runs"', folder=self.m.root, name='other.toml')
        cfg = config.load(system=self.m.system(environ={'C5H_CONFIG': str(other)}))
        self.same(cfg.file, other)
        self.same(cfg.runs, self.m.root / 'env-runs')
        here.unlink()
        in_repo = self.write_toml('runs = "repo-runs"', folder=self.m.repo)
        cfg = config.load(system=self.m.system())
        self.same(cfg.file, in_repo)
        self.same(cfg.runs, self.m.repo / 'repo-runs')

    def test_file_overrides_user_dir_and_derived(self):
        pinned = self.m.root / 'pinned'
        modinfo(pinned / 'MODS/(1) Community Patch', CP_ID, 151, '(1) Community Patch')
        touch(pinned / 'MODS/(1) Community Patch/CvGameCore_Expansion2.dll')
        p = self.write_toml(f'mods_dir = "{(pinned / "MODS").as_posix()}"')
        cfg = config.load(p, system=self.m.system())
        self.same(cfg.mods_dir, pinned / 'MODS')
        self.same(cfg.dll, pinned / 'MODS/(1) Community Patch/CvGameCore_Expansion2.dll')
        self.same(cfg.mods_db, self.m.user / 'cache/Civ5ModsDatabase.db')

    def test_profiles_and_probes(self):
        cfg = config.load(system=self.m.system())
        for key in ('profiles', 'probes'):
            self.assertIsNone(getattr(cfg, key), key)
            self.assertEqual(cfg.sources[key].kind, 'unset')
        (self.m.repo / 'profiles').mkdir()
        (self.m.repo / 'probes').mkdir()
        cfg = config.load(system=self.m.system())  # the workspace is the repository
        self.same(cfg.profiles, self.m.repo / 'profiles')
        self.assertEqual(cfg.sources['profiles'].kind, 'default')
        ws = self.m.root / 'ws'
        (ws / 'probes').mkdir(parents=True)
        p = self.write_toml(f'workspace = "{ws.as_posix()}"\nprofiles = "my-profiles"')
        cfg = config.load(p, system=self.m.system())
        self.same(cfg.profiles, self.m.cwd / 'my-profiles')
        self.assertEqual(cfg.sources['profiles'].kind, 'file')
        self.same(cfg.probes, ws / 'probes')  # the workspace's first
        (ws / 'probes').rmdir()
        cfg = config.load(p, system=self.m.system())
        self.same(cfg.probes, self.m.repo / 'probes')  # then the repository's
        self.assertEqual(cfg.sources['probes'].kind, 'discovery')

    def test_release_kit(self):
        kit = self.m.root / 'civ5-headless-0.1.0'
        touch(kit / 'kit.json', b'{}')
        touch(kit / 'bin/civ5headless.exe')
        (kit / 'profiles').mkdir()
        (kit / 'probes').mkdir()
        cfg = config.load(system=self.m.system(repository=None, kit=kit))
        self.same(cfg.workspace, kit)
        self.assertEqual(cfg.sources['workspace'].note, 'the kit folder')
        self.same(cfg.exe, kit / 'bin/civ5headless.exe')
        self.assertEqual(cfg.sources['exe'].note, 'bin\\ in the kit')
        self.same(cfg.profiles, kit / 'profiles')
        self.same(cfg.probes, kit / 'probes')
        self.same(cfg.runs, kit / 'runs')
        ws = self.m.root / 'ws'
        ws.mkdir()
        p = self.write_toml(f'workspace = "{ws.as_posix()}"')
        cfg = config.load(p, system=self.m.system(repository=None, kit=kit))
        self.same(cfg.runs, ws / 'runs')
        self.same(cfg.profiles, kit / 'profiles')  # the kit's, when the workspace has none
        self.assertEqual(cfg.sources['profiles'].note, 'profiles\\ in the kit')
        (kit / 'bin/civ5headless.exe').unlink()
        cfg = config.load(system=self.m.system(repository=None, kit=kit))
        self.assertIsNone(cfg.exe)
        self.assertIn('kit', cfg.sources['exe'].note)

    def test_toolchain_from_environment(self):
        cfg = config.load(system=self.m.system(environ={'C5H_TOOLCHAIN': str(self.m.root / 'tc')}))
        self.same(cfg.toolchain, self.m.root / 'tc')
        self.assertEqual(cfg.sources['toolchain'].kind, 'environment')

    def test_errors(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.m.root / 'missing.toml', system=self.m.system())
        bad = self.write_toml('game_dir = "games\\xyz"')  # an invalid escape in a basic string
        with self.assertRaises(config.ConfigError) as error:
            config.load(bad, system=self.m.system())
        self.assertIn(config.FILE_NAME, str(error.exception))
        with self.assertRaises(config.ConfigError):
            config.load(system=self.m.system(environ={'C5H_CONFIG': str(self.m.root / 'nope.toml')}))

    def test_ai_logging(self):
        self.assertIsNone(config.load(system=self.m.system()).ai_logging)
        for text, want in (('"off"', 'off'), ('"On"', 'on'), ('false', 'off'), ('true', 'on')):
            cfg = config.load(self.write_toml(f'ai_logging = {text}\n'), system=self.m.system())
            self.assertEqual(cfg.ai_logging, want)
            self.assertEqual(cfg.notes, [])
            self.assertEqual(cfg.to_dict()['aiLogging'], want)
        with self.assertRaises(config.ConfigError) as error:
            config.load(self.write_toml('ai_logging = "maybe"\n'), system=self.m.system())
        self.assertIn('ai_logging is on or off', str(error.exception))

    def test_to_dict(self):
        d = config.load(system=self.m.system()).to_dict()
        self.assertEqual(set(d['values']), set(config.KEYS))
        self.assertEqual(d['values']['game_dir']['source'], 'discovery')
        self.assertEqual(len(d['activeMods']), 2)


class VdfTest(unittest.TestCase):
    def test_nested_and_escapes(self):
        data = config.parse_vdf('"a"\n{\n\t"path"\t\t"Steam\\\\Lib \\"x\\""\n\t"empty"\t""\n\t"b" { "c" "1" }\n}\n')
        self.assertEqual(data, {'a': {'path': 'Steam\\Lib "x"', 'empty': '', 'b': {'c': '1'}}})


class KnownFolderTest(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows only')
    def test_documents(self):
        docs = config.System().documents()
        self.assertTrue(docs is None or docs.is_absolute())


if __name__ == '__main__':
    unittest.main()
