"""The mods registry generator (modsdb.py): rows as the game writes them, activation order, mod set checks, caching,
comparison, and (C5H_INTEGRATION=1) the configured active mods against the game's own registry."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from civ5headless import config, modsdb, winproc

BASE_ID = '0b5e0000-1111-4222-8333-944444444444'
ADDON_ID = 'ADD00000-1111-4222-8333-944444444444'
OTHER_ID = '07e00000-1111-4222-8333-944444444444'

BASE = f'''\ufeff<?xml version="1.0" encoding="utf-8"?>
<Mod id="{BASE_ID}" version="3">
  <Properties>
    <Name>Base Mod</Name>
    <Description>First line.

Second line.</Description>
    <Homepage />
    <SupportsSinglePlayer>1</SupportsSinglePlayer>
  </Properties>
  <Dependencies>
    <Game minversion="1.0.3.124" maxversion="1.0.3.999" />
    <Dlc id="293C1EE3-1176-44f6-AC1F-59663826DE74" minversion="0" maxversion="999" />
  </Dependencies>
  <References />
  <Blocks>
    <Mod id="{OTHER_ID}" minversion="0" maxversion="999" title="Other Mod" />
    <Dlc id="3F49DF54-68B6-44d1-A930-A168628FAA59" minversion="0" maxversion="999" />
  </Blocks>
  <Files>
    <File md5="AAAA" import="0">SQL/Base.sql</File>
    <File md5="BBBB" import="1">UI/TopPanel.lua</File>
  </Files>
  <Actions>
    <OnGetDLLPath>
      <SetDllPath>CvGameCore_Expansion2.dll</SetDllPath>
    </OnGetDLLPath>
    <OnModActivated>
      <UpdateDatabase>SQL/Base.sql</UpdateDatabase>
    </OnModActivated>
  </Actions>
  <EntryPoints>
    <EntryPoint type="InGameUIAddin" file="UI/Panel.lua">
      <Name>Panel</Name>
      <Description>
      </Description>
    </EntryPoint>
  </EntryPoints>
</Mod>
'''

ADDON = f'''<?xml version="1.0" encoding="utf-8"?>
<Mod id="{ADDON_ID}" version="1">
  <Properties>
    <Name>Addon</Name>
  </Properties>
  <Dependencies>
    <Mod id="{BASE_ID}" minversion="2" maxversion="999" title="Base Mod" />
  </Dependencies>
  <References>
    <Mod id="{OTHER_ID}" minversion="0" maxversion="999" title="Other Mod" />
  </References>
  <Files>
    <File md5="CCCC" import="1">Addon.lua</File>
  </Files>
</Mod>
'''

OTHER = f'''<?xml version="1.0" encoding="utf-8"?>
<Mod id="{OTHER_ID}" version="5">
  <Properties><Name>Other Mod</Name></Properties>
</Mod>
'''


def write_mod(mods_dir, folder, text, crlf=False):
    """A mod folder with its .modinfo (named after the folder)."""
    path = Path(mods_dir) / folder / f'{folder}.modinfo'
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace('\n', '\r\n') if crlf else text
    path.write_bytes(data.encode('utf-8'))
    return path


def make_mods(mods_dir):
    write_mod(mods_dir, 'Base Mod (v 3)', BASE, crlf=True)
    write_mod(mods_dir, 'Addon (v 1)', ADDON)
    write_mod(mods_dir, 'Other (v 5)', OTHER)
    return Path(mods_dir)


def rows(db, sql, *args):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.mods = make_mods(self.dir / 'MODS')
        self.out = self.dir / 'Civ5ModsDatabase.db'

    def tearDown(self):
        self.tmp.cleanup()

    def test_rows_as_the_game_writes_them(self):
        summary = modsdb.build(self.mods, [f'{ADDON_ID}:1', BASE_ID.upper()], self.out)
        # the dependency first; the given order otherwise
        self.assertEqual([m['id'] for m in summary['mods']], [BASE_ID, ADDON_ID.lower()])
        self.assertEqual(rows(self.out, 'SELECT RowID, ModID, Version, Type, Installed, Enabled, Activated, LastRetrieved FROM Mods'),
                         [(1, BASE_ID, '3', 1, 1, 1, 1, None), (2, ADDON_ID, '1', 1, 1, 1, 1, None)])
        props = rows(self.out, 'SELECT Name, Value FROM ModProperties WHERE ModID = ? ORDER BY rowid', BASE_ID)
        self.assertEqual(props, [('Name', 'Base Mod'), ('Description', 'First line.\r\n\r\nSecond line.'), ('Homepage', ''),
                                 ('SupportsSinglePlayer', '1')])
        base = str(self.mods.resolve()) + '\\Base Mod (v 3)\\'
        self.assertEqual(rows(self.out, 'SELECT Path, md5, EvaluatedPath, Import FROM ModFiles WHERE ModID = ? ORDER BY rowid', BASE_ID),
                         [('SQL/Base.sql', 'AAAA', base + 'SQL/Base.sql', 0), ('UI/TopPanel.lua', 'BBBB', base + 'UI/TopPanel.lua', 1),
                          ('Base Mod (v 3).modinfo', None, base + 'Base Mod (v 3).modinfo', 0)])
        self.assertEqual(rows(self.out, 'SELECT Location, Action, Param1 FROM ModActions WHERE ModID = ? ORDER BY rowid', BASE_ID),
                         [('OnGetDLLPath', 'SetDllPath', 'CvGameCore_Expansion2.dll'), ('OnModActivated', 'UpdateDatabase', 'SQL/Base.sql')])
        self.assertEqual(rows(self.out, 'SELECT Name, Description, Type, File FROM ModEntryPoints'),
                         [('Panel', '', 'InGameUIAddin', 'UI/Panel.lua')])
        self.assertEqual(rows(self.out, 'SELECT ModID, Version, Type, RelModId, MinVersion, MaxVersion, RelModTitle FROM ModRelationships ORDER BY rowid'),
                         [(BASE_ID, 3, -1, OTHER_ID, 0, 999, 'Other Mod'), (ADDON_ID, 1, 2, BASE_ID, 2, 999, 'Base Mod'),
                          (ADDON_ID, 1, 1, OTHER_ID, 0, 999, 'Other Mod')])
        self.assertEqual(rows(self.out, 'SELECT Type, PackageId, MinVersion, MaxVersion FROM DlcRelationships ORDER BY rowid'),
                         [(2, '293C1EE3-1176-44f6-AC1F-59663826DE74', 0, 999), (-1, '3F49DF54-68B6-44d1-A930-A168628FAA59', 0, 999)])
        self.assertEqual(rows(self.out, 'SELECT ModID, Type, MinVersion, MaxVersion FROM GameVersionRelationships'),
                         [(BASE_ID, 2, '1.0.3.124', '1.0.3.999')])
        self.assertEqual(rows(self.out, 'SELECT * FROM ModSystemSettings'), [('Version', '14')])
        self.assertEqual(rows(self.out, 'SELECT count(*) FROM ScannedFiles'), [(0,)])

    def test_schema_is_the_games(self):
        modsdb.build(self.mods, [BASE_ID], self.out)
        stored = [r[0] for r in rows(self.out, 'SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid')]
        self.assertEqual([s for s in stored if 'sqlite_sequence' not in s], modsdb.SCHEMA)

    def test_write_retries_a_denied_replace(self):
        """The registry lands though a scanner or a running game holds the file for a moment (WinError 5)."""
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise PermissionError(5, 'Access is denied')
            real(src, dst)
        with mock.patch.object(winproc.os, 'replace', flaky), mock.patch.object(winproc.time, 'sleep'):
            modsdb.build(self.mods, [BASE_ID], self.out)
        self.assertEqual(len(calls), 2)
        self.assertEqual(rows(self.out, 'SELECT ModID FROM Mods'), [(BASE_ID,)])
        self.assertEqual(list(self.out.parent.glob('*.tmp')), [])

    def test_path_as_and_highest_version(self):
        write_mod(self.mods, 'Base Mod (v 2)', BASE.replace('version="3"', 'version="2"'))
        modsdb.build(self.mods, [BASE_ID], self.out, path_as='Somewhere\\MODS\\')
        self.assertEqual(rows(self.out, 'SELECT Version FROM Mods'), [('3',)])
        self.assertEqual(rows(self.out, "SELECT EvaluatedPath FROM ModFiles WHERE Path = 'UI/TopPanel.lua'"),
                         [('Somewhere\\MODS\\Base Mod (v 3)\\UI/TopPanel.lua',)])
        modsdb.build(self.mods, [f'{BASE_ID}:2'], self.out)
        self.assertEqual(rows(self.out, 'SELECT Version FROM Mods'), [('2',)])

    def test_mod_set_errors(self):
        cases = [([ADDON_ID], 'not in the set'),
                 ([BASE_ID, ADDON_ID, OTHER_ID], 'blocks Other Mod'),
                 ([f'{BASE_ID}:9'], 'versions there: 3'),
                 (['ffffffff-0000-0000-0000-000000000000'], 'is not below'),
                 ([f'{BASE_ID}:x'], 'bad version')]
        for mods, message in cases:
            with self.subTest(mods=mods), self.assertRaises(modsdb.ModsDbError) as ctx:
                modsdb.build(self.mods, mods, self.out)
            self.assertIn(message, str(ctx.exception))
        write_mod(self.mods, 'Base Mod (v 1)', BASE.replace('version="3"', 'version="1"'))
        with self.assertRaises(modsdb.ModsDbError) as ctx:
            modsdb.build(self.mods, [f'{BASE_ID}:1', ADDON_ID], self.out)
        self.assertIn('needs Base Mod v2-999, not v1', str(ctx.exception))
        self.assertTrue(issubclass(modsdb.ModsDbError, config.ConfigError))

    def test_compare(self):
        modsdb.build(self.mods, [BASE_ID, ADDON_ID], self.out)
        same = self.dir / 'same.db'
        modsdb.build(self.mods, [BASE_ID, ADDON_ID], same)
        self.assertTrue(modsdb.compare(self.out, same)['identical'])
        other = self.dir / 'other.db'
        modsdb.build(self.mods, [BASE_ID, ADDON_ID], other, path_as=self.dir / 'elsewhere')
        result = modsdb.compare(self.out, other)
        self.assertFalse(result['identical'])
        self.assertEqual(list(result['tables']), ['ModFiles'])
        self.assertEqual(result['tables']['ModFiles'][0]['firstDifference'], 0)
        self.assertTrue(modsdb.compare(self.out, other, ignore={'EvaluatedPath'})['identical'])
        con = sqlite3.connect(same)
        con.execute("UPDATE Mods SET RowID = 9 WHERE RowID = 1")
        con.commit()
        con.close()
        self.assertEqual(modsdb.compare(self.out, same)['order'][1][0], f'{ADDON_ID.lower()} v1')

    def test_cached_and_resolve(self):
        cfg = config.Config(mods_dir=self.mods, db_cache=self.dir / 'cache', mods_db=self.dir / 'game.db')
        logs = []
        path, info = modsdb.cached(cfg, [BASE_ID, ADDON_ID], log=logs.append)
        self.assertEqual(path.parent.parent, self.dir / 'cache/modsdb')
        self.assertEqual([m['name'] for m in info['mods']], ['Base Mod', 'Addon'])
        again, _ = modsdb.cached(cfg, [f'{BASE_ID}:3', f'{ADDON_ID}:1'], log=logs.append)
        self.assertEqual((again, len(logs)), (path, 1))  # same mods, same entry, no second write
        write_mod(self.mods, 'Addon (v 1)', ADDON.replace('Addon.lua', 'Changed.lua'))
        self.assertNotEqual(modsdb.cached(cfg, [BASE_ID, ADDON_ID], log=logs.append)[0], path)
        # resolve: no mod set -> as given or configured; a mod set -> the registry; [] -> no mods, no MODS needed
        self.assertEqual(modsdb.resolve(cfg), (self.mods, self.dir / 'game.db'))
        self.assertEqual(modsdb.resolve(cfg, None, 'm', 'r.db'), (Path('m'), Path('r.db')))
        mods_dir, registry = modsdb.resolve(cfg, [BASE_ID], log=logs.append)
        self.assertEqual((mods_dir, rows(registry, 'SELECT ModID FROM Mods')), (self.mods.resolve(), [(BASE_ID,)]))
        mods_dir, registry = modsdb.resolve(config.Config(db_cache=self.dir / 'cache'), [], log=logs.append)
        self.assertTrue(mods_dir.is_dir())
        self.assertEqual(rows(registry, 'SELECT count(*) FROM Mods'), [(0,)])
        with self.assertRaises(modsdb.ModsDbError):
            modsdb.resolve(cfg, [BASE_ID], mods_db='r.db')

    def test_main(self):
        cfg = config.Config(mods_dir=self.mods, db_cache=self.dir / 'cache',
                            active_mods=[config.Mod(BASE_ID, 3, 'Base Mod')])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = modsdb.main(['--out', str(self.out), '--json'], cfg)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual([m['name'] for m in json.loads(out.getvalue())['mods']], ['Base Mod'])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(modsdb.main(['--mod', ADDON_ID, '--out', str(self.out)], cfg), 1)
            self.assertEqual(modsdb.main(['--mod', BASE_ID, '--compare', str(self.out)], cfg), 0)
        self.assertIn('not in the set', err.getvalue())


@unittest.skipUnless(os.environ.get('C5H_INTEGRATION') == '1', 'set C5H_INTEGRATION=1 to compare with the game registry')
class GameRegistryTest(unittest.TestCase):
    """The configured active mods, generated from the MODS folder, against the configured registry (the game's)."""

    def test_identical_to_the_game_registry(self):
        cfg = config.load()
        if not cfg.active_mods or cfg.mods_dir is None:
            self.skipTest('no active mods configured')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'gen.db'
            modsdb.build(cfg.mods_dir, [(m.id, m.version) for m in cfg.active_mods], out)
            # md5 changes when a mod's files are redeployed and the game has not rescanned the mod since
            result = modsdb.compare(out, cfg.mods_db, ignore={'md5'})
            self.assertIsNone(result['order'])
            self.assertEqual(result['tables'], {})


if __name__ == '__main__':
    unittest.main()
