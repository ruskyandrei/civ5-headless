"""Saved games: the .Civ5Save header reader, c5h save, and c5h run --load and the save options against a fake host."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
import uuid
from unittest import mock

from civ5headless import cli, config, modsdb, queue, run, savegame

from .test_modsdb import ADDON_ID, BASE_ID, make_mods
from .test_spec import make_db

HERE = Path(__file__).resolve().parent
DLL_GUID = '{bf9bf7f0-e078-4d4e-8a3e-84712f85aa2b}'


def text(s):
    b = s.encode('utf-8')
    return struct.pack('<I', len(b)) + b


def guid(s):
    return uuid.UUID(s.strip('{}')).bytes_le


def make_save(path, turn=5, mods=(), version=8, game_type=0, tail=savegame.TAIL_GUID):
    """A .Civ5Save header as the game writes it (mods: (id, version, name)), then a few bytes standing for the rest."""
    b = b'CIV5' + struct.pack('<I', version) + text('1.0.3.279 (403694)') + text('403694') + struct.pack('<IB', turn, game_type)
    for s in ('CIVILIZATION_GREECE', 'HANDICAP_PRINCE', 'ERA_ANCIENT', 'ERA_CLASSICAL', 'GAMESPEED_QUICK', 'WORLDSIZE_TINY',
              'Assets\\Maps\\Continents.lua'):
        b += text(s)
    dlc = [('{293ac8ee-5a17-4d2f-90bf-b2e7a6b1bd6a}', 'Mongolia'), ('{0e3751a1-f840-4e1b-9706-519bf484e59d}', 'Expansion - Gods and Kings')]
    b += struct.pack('<I', len(dlc)) + b''.join(guid(g) + struct.pack('<I', 1) + text(n) for g, n in dlc)
    b += struct.pack('<I', len(mods)) + b''.join(text(i) + struct.pack('<I', v) + text(n) for i, v, n in mods)
    b += text('') + text('') + text('PLAYERCOLOR_GREECE') + guid(DLL_GUID) + text('1.0.0') + guid(tail) + struct.pack('<I', 0)
    b += struct.pack('<I', 3) + b'pregame and body'
    Path(path).write_bytes(b)
    return path


class HeaderTest(unittest.TestCase):
    def test_read_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_save(Path(tmp) / 'AutoSave_0012 BC-3520.Civ5Save', turn=12,
                          mods=[(BASE_ID, 3, 'Base Mod'), (ADDON_ID, 1, 'Addon')])
            h = savegame.read_header(p)
            self.assertEqual((h.turn, h.gameType, h.civilization, h.handicap, h.startEra, h.currentEra, h.speed, h.worldSize),
                             (12, 0, 'CIVILIZATION_GREECE', 'HANDICAP_PRINCE', 'ERA_ANCIENT', 'ERA_CLASSICAL',
                              'GAMESPEED_QUICK', 'WORLDSIZE_TINY'))
            self.assertEqual(h.mapScript, 'Assets\\Maps\\Continents.lua')
            self.assertEqual([d['name'] for d in h.dlc], ['Mongolia', 'Expansion - Gods and Kings'])
            self.assertEqual(h.dlc[0]['id'], '{293ac8ee-5a17-4d2f-90bf-b2e7a6b1bd6a}')
            self.assertEqual(h.mod_set, [f'{BASE_ID}:3', f'{ADDON_ID}:1'])
            self.assertEqual((h.playerColor, h.dllGuid, h.dllVersion), ('PLAYERCOLOR_GREECE', DLL_GUID, '1.0.0'))
            self.assertEqual(h.fileBytes, p.stat().st_size)
            self.assertEqual(p.read_bytes()[h.headerBytes:h.headerBytes + 4], struct.pack('<I', 3))  # the pregame's version
            self.assertIn('turn 12, single player', savegame.describe(h))
            self.assertEqual(json.loads(json.dumps(h.as_dict()))['mods'][1]['name'], 'Addon')

    def test_not_a_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'x.Civ5Save').write_bytes(b'PK\x03\x04')
            cut = make_save(tmp / 'cut.Civ5Save')
            cut.write_bytes(cut.read_bytes()[:60])
            for path, message in ((tmp / 'x.Civ5Save', 'no CIV5'), (make_save(tmp / 'v7.Civ5Save', version=7), 'save version 7'),
                                  (cut, 'cut short'), (make_save(tmp / 't.Civ5Save', tail=str(uuid.uuid4())), 'does not end')):
                with self.subTest(path=path.name), self.assertRaises(savegame.SaveError) as ctx:
                    savegame.read_header(path)
                self.assertIn(message, str(ctx.exception))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_save(Path(tmp) / 'a.Civ5Save', turn=3)
            (Path(tmp) / 'empty.toml').write_text('', encoding='utf-8')
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(['--config', str(Path(tmp) / 'empty.toml'), 'save', str(p), '--json'])
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(json.loads(out.getvalue())['turn'], 3)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                self.assertEqual(savegame.main([str(p), str(Path(tmp) / 'missing.Civ5Save')]), 1)
            self.assertIn('missing.Civ5Save', err.getvalue())


class OptionTest(unittest.TestCase):
    def test_autosave_modes(self):
        self.assertEqual([run.autosave_mode(v) for v in (None, 'none', 'GAME', '5', 7)], ['none', 'none', 'game', '5', '7'])
        for bad in ('0', '-2', 'often'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                run.autosave_mode(bad)

    def test_save_host_args(self):
        a = mock.Mock(autosave='game', save_at=[10, 20], save_final=True, load=None)
        with mock.patch.object(run, 'host_supports', return_value=True):
            self.assertEqual(run.save_host_args(a, 'host.exe'), ['--autosave', 'game', '--save-at', '10', '--save-at', '20'])
            # by default only the host's own final save; --no-save-final turns it off
            self.assertEqual(run.save_host_args(mock.Mock(autosave=None, save_at=None, save_final=True, load=None), 'host.exe'), [])
            self.assertEqual(run.save_host_args(mock.Mock(autosave=None, save_at=None, save_final=False, load=None), 'host.exe'),
                             ['--no-save-final'])
        with mock.patch.object(run, 'host_supports', return_value=False):  # a host from before saves saves nothing
            self.assertEqual(run.save_host_args(mock.Mock(autosave='none', save_at=[], save_final=False, load=None), 'old.exe'), [])
            with self.assertRaises(ValueError):
                run.save_host_args(mock.Mock(autosave='none', save_at=[], save_final=True, load='a.Civ5Save'), 'old.exe')

    def test_load_mod_set(self):
        """--load keeps the configured registry when its mods that affect saved games are the save's (UI mods may be
        active beside them, as a save lists only the others), else the save's mods."""
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / 'mods.db'
            con = sqlite3.connect(registry)
            con.execute('CREATE TABLE Mods(ModID TEXT, Version TEXT, Activated BOOL)')
            con.execute('CREATE TABLE ModProperties(ModID TEXT, Version TEXT, Name TEXT, Value TEXT)')
            con.executemany('INSERT INTO Mods VALUES(?,?,?)', [(BASE_ID, '3', 1), (ADDON_ID, '1', 1), ('07e00000-1111-4222-8333-944444444444', '5', 0)])
            con.executemany('INSERT INTO ModProperties VALUES(?,?,?,?)', [(ADDON_ID, '1', 'AffectsSavedGames', '0'),
                                                                           (BASE_ID, '3', 'AffectsSavedGames', '1')])
            con.commit()
            con.close()
            self.assertEqual(len(modsdb.activated(registry)), 2)
            self.assertEqual(modsdb.activated(registry, saved_games_only=True), [(BASE_ID, 3)])
            cfg = config.Config(mods_db=registry)
            fits = savegame.read_header(make_save(Path(tmp) / 'a.Civ5Save', mods=[(BASE_ID, 3, 'Base Mod')]))
            self.assertIsNone(run.load_mod_set(cfg, fits))
            other = savegame.read_header(make_save(Path(tmp) / 'b.Civ5Save', mods=[(BASE_ID, 2, 'Base Mod')]))
            self.assertEqual(run.load_mod_set(cfg, other), [f'{BASE_ID}:2'])
            self.assertEqual(run.load_mod_set(None, fits), [f'{BASE_ID}:3'])

    def test_queue_jobs_from_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            make_save(tmp / 'a.Civ5Save', mods=[(BASE_ID, 3, 'Base Mod')])
            make_save(tmp / 'bare', turn=2)  # a save without the extension: its first bytes say what it is
            jobs = {j['name']: j for j in queue.expand({'jobs': [
                {'name': 'spec-save', 'spec': 'a.Civ5Save', 'turns': 9},
                {'name': 'bare', 'spec': 'bare', 'turns': 9, 'saveFinal': False, 'autosave': 'GAME'}]}, tmp / 'q.json')}
            self.assertEqual((jobs['spec-save']['load'], jobs['spec-save']['spec']), (str((tmp / 'a.Civ5Save').resolve()), None))
            self.assertEqual(jobs['spec-save']['mods'], [f'{BASE_ID}:3'])  # no configuration given: the save's mods
            self.assertEqual((jobs['bare']['load'], jobs['bare'].get('noMods'), jobs['bare']['autosave']),
                             (str((tmp / 'bare').resolve()), True, 'game'))
            ns = queue.job_args(jobs['bare'], tmp / 'out', dict(db='x.db', modsDir=None, modsDb=None), None)
            self.assertEqual((ns.spec, ns.load, ns.save_final, ns.autosave), (None, str((tmp / 'bare').resolve()), False, 'game'))
            self.assertTrue(queue.job_args(jobs['spec-save'], tmp / 'out', dict(db='x.db', modsDir=None, modsDb=None), None).save_final)

    def test_run_option_checks(self):
        for argv, message in ((['--turns', '3'], 'one of them'),
                              (['spec.json', '--load', 'a.Civ5Save', '--turns', '3'], 'one of them'),
                              (['--load', 'a.Civ5Save', '--turns', '3', '--seeds', '1', '-2'], 'numbers from 0'),
                              (['a.Civ5Save', '--turns', '3', '--seeds', str(2 ** 32), '2'], 'numbers from 0'),
                              (['a.Civ5Save', '--load', 'b.Civ5Save', '--turns', '3'], 'not both'),
                              (['spec.json', '--turns', '3', '--autosave', 'often'], 'autosave is none, game'),
                              (['spec.json', '--turns', '3', '--save-at', '0'], 'turn numbers > 0')):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as ctx:
                    run.main(argv, config.Config())
                self.assertEqual(ctx.exception.code, 2)
                self.assertIn(message, err.getvalue())


@unittest.skipUnless(os.name == 'nt', 'Windows only (job objects)')
class LoadTest(unittest.TestCase):
    """c5h run --load end to end with fake_host.cmd standing in for civ5headless.exe."""

    def test_load_takes_the_saves_mods_and_turn(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}), \
                mock.patch.object(run, 'host_supports', return_value=True):
            tmp = Path(tmp)
            db = tmp / 'game.db'
            make_db(db)
            own = tmp / 'Assets/DLC/Expansion2/CvGameCore_Expansion2.dll'
            own.parent.mkdir(parents=True)
            own.write_bytes(b'x')
            mods_dir = make_mods(tmp / 'MODS')
            cfg = config.Config(game_dir=tmp, mods_dir=mods_dir, mods_db=tmp / 'game-registry.db', exe=HERE / 'fake_host.cmd',
                                db=db, runs=tmp / 'runs', db_cache=tmp / 'cache')
            save = make_save(tmp / 'Turn_0005 BC-3800.Civ5Save', turn=5, mods=[(BASE_ID, 3, 'Base Mod'), (ADDON_ID, 1, 'Addon')])
            r = run.play(save, 9, cfg=cfg, autosave=3, save_at=[7], no_profiles=True)  # a save as the spec: a load
            self.assertTrue(r.ok, r.status.get('error'))
            self.assertEqual(r.dir.name[:len('Turn_0005-BC-3800-')], 'Turn_0005-BC-3800-')
            argv = json.loads((r.dir / 'argv.json').read_text(encoding='utf-8'))[1:]

            def value(opt):
                return [argv[i + 1] for i, a in enumerate(argv) if a == opt]

            self.assertEqual(argv[:3], ['loadgame', '--load', str(save.resolve())])
            self.assertEqual(value('--turns'), ['4'])  # turns 6 to 9
            self.assertNotIn('--spec', argv)
            self.assertEqual((value('--autosave'), value('--save-at'), '--no-save-final' in argv), (['3'], ['7'], False))
            # the save's mod set: a generated registry with both mods, activated
            con = sqlite3.connect(value('--mods-db')[0])
            self.assertEqual([tuple(x) for x in con.execute('SELECT ModID, Version FROM Mods WHERE Activated = 1 ORDER BY RowID')],
                             [(BASE_ID, '3'), (ADDON_ID, '1')])
            con.close()
            st = r.status
            self.assertEqual((st['startTurn'], st['endTurn'], st['turns'], st['wallSeconds'], st['secondsPerTurn']),
                             (5, 9, 4, 1.0, 0.25))
            self.assertEqual((st['loadedFrom'], st['spec']), (str(save.resolve()), None))
            self.assertTrue(st['save'].startswith('loaded-Turn_0005-BC-3800-t5-'))
            self.assertEqual(st['finalSave'], str(r.dir / 'saves/Final_0009.Civ5Save'))
            self.assertEqual([(x['kind'], x['turn']) for x in st['saves']], [('final', 9)])
            self.assertEqual(json.loads((r.dir / 'save-header.json').read_text(encoding='utf-8'))['turn'], 5)
            self.assertFalse((r.dir / 'spec-resolved.json').exists())
            self.assertEqual(json.loads((r.dir / 'turn-seen.json').read_text(encoding='utf-8'))['5'], 0.0)
            self.assertIn('Loaded from', (r.dir / 'report.html').read_text(encoding='utf-8'))
            # the configured registry when it activates just the save's mods
            registry = Path(value('--mods-db')[0])
            cfg.mods_db = tmp / 'game-registry.db'
            cfg.mods_db.write_bytes(registry.read_bytes())
            cfg.dll = own  # as for a new game, the configured registry plays the configured DLL
            r = run.play(None, 6, load=save, cfg=cfg, run='configured', report=False)
            self.assertTrue(r.ok, r.status.get('error'))
            argv = json.loads((r.dir / 'argv.json').read_text(encoding='utf-8'))[1:]
            self.assertEqual(value('--mods-db'), [str(cfg.mods_db.resolve())])
            # an unmodded save plays without mods; a target at or before the save's turn fails
            bare = make_save(tmp / 'bare.Civ5Save', turn=2)
            r = run.play(None, 3, load=bare, cfg=cfg, run='bare1', report=False, save_final=False)
            self.assertTrue(r.ok, r.status.get('error'))
            argv = json.loads((r.dir / 'argv.json').read_text(encoding='utf-8'))[1:]
            self.assertIn('--no-mods', argv)
            self.assertFalse((r.dir / 'saves').exists())  # --no-save-final: no saves
            self.assertIn('--no-save-final', json.loads((r.dir / 'argv.json').read_text(encoding='utf-8')))
            r = run.play(None, 2, load=bare, cfg=cfg, run='bare2', report=False)
            self.assertFalse(r.ok)
            self.assertIn('must be after it', r.status['error'])
            with self.assertRaises(ValueError):
                run.play(None, 3, cfg=cfg)

    def game_kit(self, tmp):
        """A configuration for fake_host.cmd, and an unmodded save of turn 5."""
        db = tmp / 'game.db'
        make_db(db)
        own = tmp / 'Assets/DLC/Expansion2/CvGameCore_Expansion2.dll'
        own.parent.mkdir(parents=True)
        own.write_bytes(b'x')
        cfg = config.Config(game_dir=tmp, mods_dir=make_mods(tmp / 'MODS'), mods_db=tmp / 'game-registry.db',
                            exe=HERE / 'fake_host.cmd', db=db, runs=tmp / 'runs', db_cache=tmp / 'cache')
        return cfg, make_save(tmp / 'base.Civ5Save', turn=5)

    def test_new_seeds_for_a_loaded_game(self):
        """--seeds with a save: the host's --reseed, the seeds in status.json, setup.json and the report, and a save key
        that groups the same seeds; one seed is a warning, a host without --reseed a clear error."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}):
            tmp = Path(tmp)
            cfg, save = self.game_kit(tmp)
            with mock.patch.object(run, 'host_supports', return_value=True):
                a = run.play(save, 8, cfg=cfg, run='a', seeds={'map': 11, 'sync': 22}, no_profiles=True)
                self.assertTrue(a.ok, a.status.get('error'))
                argv = json.loads((a.dir / 'argv.json').read_text(encoding='utf-8'))[1:]
                self.assertEqual(argv[:5], ['loadgame', '--load', str(save.resolve()), '--reseed', '11:22'])
                self.assertEqual(a.status['reseed'], {'map': 11, 'sync': 22})
                self.assertEqual(json.loads((a.dir / 'setup.json').read_text(encoding='utf-8'))['reseed'], {'map': 11, 'sync': 22})
                self.assertEqual(json.loads((a.dir / 'hh-command.json').read_text(encoding='utf-8'))['reseed'], {'map': 11, 'sync': 22})
                self.assertRegex(a.status['save'], r'^loaded-base-t5-[0-9a-f]{8}-m11-s22$')
                self.assertIn('map 11, sync 22 (new, over the save)', (a.dir / 'report.html').read_text(encoding='utf-8'))
                events = [json.loads(l) for l in (a.dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertFalse([e for e in events if e['action'] == 'warning' and 'one new seed' in e['message']])
                # the same seeds, the same key; none: the save's key, nothing passed
                again = run.play(save, 8, cfg=cfg, run='a2', seeds={'map': 11, 'sync': 22}, no_profiles=True, report=False)
                self.assertEqual(again.status['save'], a.status['save'])
                kept = run.play(save, 8, cfg=cfg, run='kept', no_profiles=True, report=False)
                self.assertNotIn('--reseed', json.loads((kept.dir / 'argv.json').read_text(encoding='utf-8')))
                self.assertNotIn('reseed', kept.status)
                self.assertRegex(kept.status['save'], r'^loaded-base-t5-[0-9a-f]{8}$')
                # one seed: the other side empty, and a warning
                sync = run.play(save, 8, cfg=cfg, run='sync', seeds={'sync': 22}, no_profiles=True, report=False)
                self.assertTrue(sync.ok, sync.status.get('error'))
                self.assertIn(':22', json.loads((sync.dir / 'argv.json').read_text(encoding='utf-8')))
                self.assertRegex(sync.status['save'], r'-s22$')
                events = [json.loads(l) for l in (sync.dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertTrue([e for e in events if e['action'] == 'warning' and 'one new seed' in e['message']])
                # seeds that cannot be seeds fail the game before the host starts
                bad = run.play(save, 8, cfg=cfg, run='bad', seeds={'map': -1, 'sync': 22}, no_profiles=True, report=False)
                self.assertFalse(bad.ok)
                self.assertIn('the map seed is a number', bad.status['error'])
            with mock.patch.object(run, 'host_supports', side_effect=lambda exe, option: option != '--reseed'):
                old = run.play(save, 8, cfg=cfg, run='old', seeds={'map': 11, 'sync': 22}, no_profiles=True, report=False)
                self.assertFalse(old.ok)
                self.assertIn('no --reseed', old.status['error'])
                self.assertFalse((old.dir / 'argv.json').exists())

    def test_queue_seeds_on_a_load(self):
        """seeds x variants over one save: each game has its own seeds (they used to be dropped, leaving copies)."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}), \
                mock.patch.object(run, 'host_supports', return_value=True), contextlib.redirect_stdout(io.StringIO()) as out:
            tmp = Path(tmp)
            cfg, save = self.game_kit(tmp)
            qpath = tmp / 'q.json'
            qpath.write_text(json.dumps({
                'out': 'runs/q', 'workers': 2, 'affinity': 'none',
                'defaults': {'load': save.name, 'turns': 7, 'noProfiles': True, 'report': False},
                'variants': {'A': {'abRole': 'baseline'}, 'B': {'abRole': 'test', 'smallBlockAllocator': True}},
                'seeds': [[1, 2], [3, 4]],
                'jobs': [{'name': 'sync-only', 'seeds': {'sync': 9}}]}), encoding='utf-8')
            self.assertEqual(queue.main(['run', str(qpath)], cfg), 0)
            status = {p.name: json.loads((p / 'status.json').read_text(encoding='utf-8')) for p in (tmp / 'runs/q').iterdir() if p.is_dir()}
            self.assertEqual(sorted(status), ['A-m1-s2', 'A-m3-s4', 'B-m1-s2', 'B-m3-s4', 'sync-only'])
            self.assertEqual(status['B-m3-s4']['reseed'], {'map': 3, 'sync': 4})
            self.assertEqual(status['A-m1-s2']['save'], status['B-m1-s2']['save'])  # abcompare pairs them
            self.assertNotEqual(status['A-m1-s2']['save'], status['A-m3-s4']['save'])
            self.assertTrue(status['B-m1-s2']['smallBlockAllocator'])
            self.assertNotIn('smallBlockAllocator', status['A-m1-s2'])
            argv = json.loads((tmp / 'runs/q/B-m3-s4/argv.json').read_text(encoding='utf-8'))
            self.assertEqual(argv[argv.index('--reseed') + 1], '3:4')
            self.assertIn('--sba', argv)
            self.assertIn('sync-only: only one new seed', out.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            make_save(tmp / 'a.Civ5Save')
            with self.assertRaises(SystemExit) as caught:
                queue.expand({'jobs': [{'name': 'x', 'load': 'a.Civ5Save', 'noMods': True, 'seeds': {'map': 'one'}}]}, tmp / 'q.json')
            self.assertIn('job x: the map seed is a number', str(caught.exception))

    def test_small_block_allocator_and_known_hashes(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}):
            tmp = Path(tmp)
            cfg, save = self.game_kit(tmp)
            with mock.patch.object(run, 'host_supports', return_value=True):
                r = run.play(save, 7, cfg=cfg, run='sba', sba=True, no_profiles=True)
            self.assertTrue(r.ok, r.status.get('error'))
            argv = json.loads((r.dir / 'argv.json').read_text(encoding='utf-8'))[1:]
            self.assertIn('--sba', argv)
            self.assertTrue(r.status['smallBlockAllocator'])
            self.assertIn('small-block allocator', (r.dir / 'report.html').read_text(encoding='utf-8'))
            command = json.loads((r.dir / 'hh-command.json').read_text(encoding='utf-8'))
            known = [argv[i + 1] for i, a in enumerate(argv) if a == '--known-sha256']
            self.assertEqual(known, [f"{command['exe']}={command['exeSHA256']}", f"{command['dll']}={command['dllSHA256']}"])
            # off by default, and not for a host that cannot do them
            with mock.patch.object(run, 'host_supports', side_effect=lambda exe, option: option not in ('--sba', '--known-sha256')):
                plain = run.play(save, 7, cfg=cfg, run='plain', no_profiles=True, report=False)
                self.assertTrue(plain.ok, plain.status.get('error'))
                argv = json.loads((plain.dir / 'argv.json').read_text(encoding='utf-8'))[1:]
                self.assertFalse({'--sba', '--known-sha256'} & set(argv))
                self.assertNotIn('smallBlockAllocator', plain.status)
                old = run.play(save, 7, cfg=cfg, run='old', sba=True, no_profiles=True, report=False)
                self.assertFalse(old.ok)
                self.assertIn('no --sba', old.status['error'])

    def test_ai_logging(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}), \
                mock.patch.object(run, 'host_supports', return_value=True), contextlib.redirect_stdout(io.StringIO()):
            tmp = Path(tmp)
            cfg, save = self.game_kit(tmp)

            def flags(r):
                self.assertTrue(r.ok, r.status.get('error'))
                argv = json.loads((r.dir / 'argv.json').read_text(encoding='utf-8'))
                return [argv[i + 1] for i, a in enumerate(argv) if a == '--game-log-flags'], r.status['aiLogging'], r.status['gameLogFlags']

            def play(name, **kw):
                return run.play(save, 7, cfg=cfg, run=name, no_profiles=True, **kw)

            # off by default, which the status and the report record
            plain = play('plain')
            self.assertEqual(flags(plain), (['0'], False, 0))
            self.assertIn('AI logging off', (plain.dir / 'report.html').read_text(encoding='utf-8'))
            self.assertEqual(flags(play('off', ai_logging='off')), (['0'], False, 0))
            self.assertEqual(flags(play('false', ai_logging=False)), (['0'], False, 0))
            on = play('on', ai_logging='on')
            self.assertEqual(flags(on), (['15'], True, 15))
            self.assertIn('AI logging on', (on.dir / 'report.html').read_text(encoding='utf-8'))
            # a logging option in the host arguments wins, and the status says what the game played with
            self.assertEqual(flags(play('arg', ai_logging='off', host_args=['--game-log-flags', '5'])), (['5'], True, 5))
            self.assertEqual(flags(play('none', ai_logging='on', host_args=['--no-game-logging'])), ([], False, 0))
            self.assertEqual(flags(play('all', host_args=['--game-logging'])), ([], True, 15))
            # the command line
            self.assertEqual(run.main([str(save), '--turns', '7', '--run', 'cli', '--no-profiles', '--ai-logging', 'on'], cfg), 0)
            self.assertTrue(json.loads((tmp / 'runs/cli/status.json').read_text(encoding='utf-8'))['aiLogging'])
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                run.main([str(save), '--turns', '7', '--run', 'bad', '--ai-logging', 'maybe'], cfg)
            with self.assertRaisesRegex(ValueError, 'ai_logging is on or off'):
                play('bad', ai_logging='maybe')
            # the configuration, for games that do not choose
            cfg.ai_logging = 'on'
            self.assertEqual(flags(play('config')), (['15'], True, 15))
            self.assertEqual(flags(play('config-off', ai_logging='off')), (['0'], False, 0))
            cfg.ai_logging = 'off'
            self.assertEqual(flags(play('config-on', ai_logging='on')), (['15'], True, 15))
            cfg.ai_logging = None
            # --detail full without AI logs says so, unless the game chose that
            full = play('full', detail='full')
            self.assertEqual(flags(full), (['0'], False, 0))
            self.assertIn('--detail full keeps no AI logs', (full.dir / 'events.jsonl').read_text(encoding='utf-8'))
            for name, kw in (('full-on', dict(ai_logging='on')), ('full-off', dict(ai_logging='off'))):
                self.assertNotIn('keeps no AI logs', (play(name, detail='full', **kw).dir / 'events.jsonl').read_text(encoding='utf-8'))
            # a host without the option cannot turn it off, and plays with its own default unless asked to
            with mock.patch.object(run, 'host_supports', side_effect=lambda exe, option: option != '--game-log-flags'):
                old = play('old', ai_logging='off', report=False)
                self.assertFalse(old.ok)
                self.assertIn('no --game-log-flags', old.status['error'])
                self.assertEqual(flags(play('old-on', ai_logging='on', report=False)), ([], True, 15))
                self.assertEqual(flags(play('old-default', report=False)), ([], True, 15))

    def test_ai_logging_in_a_queue(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}), \
                mock.patch.object(run, 'host_supports', return_value=True), contextlib.redirect_stdout(io.StringIO()):
            tmp = Path(tmp)
            cfg, save = self.game_kit(tmp)
            qpath = tmp / 'q.json'
            qpath.write_text(json.dumps({
                'out': 'runs/q', 'workers': 2, 'affinity': 'none',
                'defaults': {'load': save.name, 'turns': 7, 'noProfiles': True, 'report': False},
                'jobs': [{'name': 'default'}, {'name': 'on', 'aiLogging': 'on'}, {'name': 'bool', 'aiLogging': True},
                         {'name': 'arg', 'hostArgs': ['--game-log-flags', '1']}]}), encoding='utf-8')
            self.assertEqual(queue.main(['run', str(qpath)], cfg), 0)
            status = {p.name: json.loads((p / 'status.json').read_text(encoding='utf-8')) for p in (tmp / 'runs/q').iterdir() if p.is_dir()}
            self.assertEqual({k: (v['aiLogging'], v['gameLogFlags']) for k, v in status.items()},
                             {'default': (False, 0), 'on': (True, 15), 'bool': (True, 15), 'arg': (True, 1)})
            with self.assertRaises(SystemExit) as caught:
                queue.expand({'jobs': [{'name': 'x', 'load': save.name, 'noMods': True, 'aiLogging': 'maybe'}]}, tmp / 'q.json', cfg)
            self.assertIn('job x: aiLogging is on or off', str(caught.exception))
            # queue compare says so when two games of a seed differ in it
            with contextlib.redirect_stdout(io.StringIO()) as out:
                queue.main(['compare', str(qpath)], cfg)
            self.assertIn('different AI logging: default off, on on', out.getvalue())
            compared = json.loads(qpath.with_suffix('.compare.json').read_text(encoding='utf-8'))
            self.assertEqual({r['b']: r['aiLogging'] for r in compared},
                             {'on': [False, True], 'bool': [False, True], 'arg': [False, True]})

    def test_game_logging_of_a_command(self):
        for cmd, want in ((['x'], (True, 15)), (['--game-log-flags', '0'], (False, 0)), (['--game-log-flags', '14'], (False, 14)),
                          (['--game-log-flags', '0', '--game-log-flags', '1'], (True, 1)), (['--game-log-flags', 'junk'], (False, 0)),
                          (['--no-game-logging'], (False, 0)), (['--no-game-logging', '--game-log-flags', '15'], (False, 0)),
                          (['--no-game-logging', '--game-logging'], (True, 15))):
            self.assertEqual(run.game_logging_of(cmd), want, cmd)


if __name__ == '__main__':
    unittest.main()
