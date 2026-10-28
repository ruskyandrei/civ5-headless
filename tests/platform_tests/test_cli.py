"""The c5h command line, module imports, run.play against a fake host, queue expansion, and the no-machine-paths rule."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from civ5headless import cli, config, queue, run, winproc

from .test_modsdb import ADDON_ID, BASE_ID, make_mods
from .test_spec import make_db

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class ImportTest(unittest.TestCase):
    def test_every_module_imports(self):
        for path in sorted((REPO / 'python/civ5headless').rglob('*.py')):
            if path.name == '__main__.py':
                continue
            parts = path.relative_to(REPO / 'python').with_suffix('').parts
            name = '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_cli_commands_exist(self):
        for name, module, function, _ in cli.COMMANDS:
            if module == 'civ5headless.verify':
                continue  # the verification workstream's package
            with self.subTest(command=name):
                self.assertTrue(callable(getattr(importlib.import_module(module), function)))

    def test_pyproject(self):
        import tomllib
        project = tomllib.loads((REPO / 'pyproject.toml').read_text(encoding='utf-8'))
        module, function = project['project']['scripts']['c5h'].split(':')
        self.assertIs(getattr(importlib.import_module(module), function), cli.main)
        self.assertEqual(project['project']['requires-python'], '>=3.11')
        self.assertEqual(project['project']['dependencies'], [])
        self.assertEqual(project['tool']['setuptools']['package-dir'], {'': 'python'})
        self.assertEqual(project['tool']['setuptools']['package-data'], {'civ5headless': ['data/*.json'],
                                                                         'civ5headless.realgame': ['*.lua']})


class CliTest(unittest.TestCase):
    def test_help_and_errors(self):
        code, out, _ = call(['--help'])
        self.assertEqual(code, 0)
        for name, _, _, _ in cli.COMMANDS:
            self.assertIn(name, out)
        self.assertEqual(call([])[0], 2)
        code, _, err = call(['nosuchcommand'])
        self.assertEqual(code, 2)
        self.assertIn('unknown command', err)
        code, _, err = call(['--config', str(REPO / 'missing.toml'), 'compare', 'a', 'b'])
        self.assertEqual(code, 2)
        self.assertIn('configuration error', err)

    def test_verify_hook(self):
        try:
            importlib.import_module('civ5headless.verify')
        except ModuleNotFoundError:
            code, _, err = call(['verify'])
            self.assertEqual(code, 2)
            self.assertIn('civ5headless.verify', err)
        else:
            self.skipTest('civ5headless.verify is present')

    def test_compare_through_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('a', 'b'):
                p = Path(tmp) / name / 'Logs/HeadlessDigest.log'
                p.parent.mkdir(parents=True)
                p.write_text('HHDIGEST|turn=0|point=doTurnBegin|total=0000000000000001|game=0000000000000001\n', encoding='utf-8')
            code, out, _ = call(['compare', str(Path(tmp) / 'a'), str(Path(tmp) / 'b')])
            self.assertEqual(code, 0)
            self.assertIn('identical on 1', out)

    def test_doctor_runs(self):
        code, out, _ = call(['doctor'])
        self.assertIn(code, (0, 1))
        self.assertIn('host games:', out)
        code, out, _ = call(['doctor', '--json'])
        self.assertEqual(set(json.loads(out)['values']), set(config.KEYS))


@unittest.skipUnless(os.name == 'nt', 'Windows only (job objects)')
class PlayTest(unittest.TestCase):
    """run.play end to end with fake_host.cmd standing in for civ5headless.exe."""

    def test_play_builds_the_host_command(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}):
            tmp = Path(tmp)
            db = tmp / 'game.db'
            make_db(db)
            game = tmp / 'game folder'
            game.mkdir()
            probe = tmp / 'probe.lua'
            probe.write_text('print(1)', encoding='utf-8')
            for name in ('stats', 'bad-one', 'summary'):
                (tmp / f'{name}.lua').write_text(f'return "{name}" -- ]]', encoding='utf-8')
            for name in ('mods.db', 'loc.db', 'game.dll'):
                (tmp / name).write_bytes(b'x')
            (tmp / 'profiles').mkdir()
            cfg = config.Config(game_dir=game, mods_dir=tmp / 'MODS', mods_db=tmp / 'mods.db', loc_db=tmp / 'loc.db',
                                dll=tmp / 'game.dll', exe=HERE / 'fake_host.cmd', db=db, runs=tmp / 'runs',
                                profiles=tmp / 'profiles')
            spec = {'map': {'script': 'Continents.lua', 'size': 'WORLDSIZE_TINY'}, 'seeds': {'map': 1, 'sync': 2}}
            r = run.play(spec, 4, run='p1', cfg=cfg, lua_probes=[probe], heap_perturb=5, host_args=['--no-ui-frame'],
                         seeds={'sync': 3}, checkpoint_extras=[tmp / 'stats.lua', tmp / 'bad-one.lua'],
                         final_extras=[tmp / 'summary.lua'])
            self.assertTrue(r.ok, r.status.get('error'))
            self.assertEqual(r.dir, (tmp / 'runs/p1').resolve())
            self.assertEqual((r.status['endTurn'], r.status['endReason'], r.status['wallSeconds']), (4, 'target_turn', 1.0))
            self.assertEqual(r.report['status'], 'passed')
            argv = json.loads((r.dir / 'argv.json').read_text(encoding='utf-8'))[1:]
            self.assertEqual(argv[0], 'newgame')

            def value(opt):
                return [argv[i + 1] for i, a in enumerate(argv) if a == opt]

            self.assertEqual(value('--turns'), ['4'])
            self.assertEqual(value('--db'), [str(db.resolve())])
            self.assertEqual(value('--dll'), [str((tmp / 'game.dll').resolve())])
            self.assertEqual(value('--game-dir'), [str(game)])
            self.assertEqual(value('--mods-db'), [str((tmp / 'mods.db').resolve())])
            self.assertEqual(value('--loc-db'), [str((tmp / 'loc.db').resolve())])
            # the user's probes get the run folder from a generated settings file before them
            self.assertEqual(value('--lua-probe'), [str(r.dir / 'probe-settings.lua'), str(probe.resolve())])
            settings = (r.dir / 'probe-settings.lua').read_text(encoding='utf-8')
            self.assertIn(f'C5H_RUN_DIR = [[\n{r.dir}]]', settings)
            self.assertIn(f"C5H_EXTRA_DIR = [[\n{r.dir / 'extra'}]]", settings)
            # extras: run at the end of c5h's checkpoint and final chunks, collected into extra/ after the game
            checkpoint_lua = Path(value('--checkpoint-lua')[0]).read_text(encoding='utf-8')
            self.assertIn(' run([[\nstats]],', checkpoint_lua)
            self.assertIn('[=[\nreturn "bad-one" -- ]]]=]', checkpoint_lua)
            self.assertTrue(checkpoint_lua.rstrip().endswith('return out'))
            self.assertNotIn('summary', checkpoint_lua)
            self.assertIn(' run([[\nsummary]],', Path(value('--lua-before-dump')[0]).read_text(encoding='utf-8'))
            rows = [json.loads(l) for l in (r.dir / 'extra/stats.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([(x['turn'], x['final'], x['value']) for x in rows],
                             [(t, False, {'turn': t}) for t in range(1, 5)] + [(4, True, {'turn': 4})])
            self.assertEqual(rows[0]['seconds'], 0.25)
            rows = [json.loads(l) for l in (r.dir / 'extra/bad-one.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual({x['error'] for x in rows}, {'bad-one.lua:1: failed'})
            self.assertFalse(any('value' in x for x in rows))
            self.assertEqual(json.loads((r.dir / 'extra/summary-final.json').read_text(encoding='utf-8')), 'final')
            self.assertEqual([(x['kind'], x['name'], x['rows'], x['errors']) for x in r.status['extras']],
                             [('checkpoint', 'stats', 5, 0), ('checkpoint', 'bad-one', 5, 5), ('final', 'summary', 1, 0)])
            self.assertEqual(r.status['extras'][1]['firstError'], 'bad-one.lua:1: failed')
            events = [json.loads(l) for l in (r.dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual([e['name'] for e in events if e['action'] == 'extra'], ['stats', 'bad-one', 'summary'])
            command = json.loads((r.dir / 'hh-command.json').read_text(encoding='utf-8'))
            self.assertEqual([x['name'] for x in command['extras']['checkpoint']], ['stats', 'bad-one'])
            self.assertEqual(command['extras']['final'][0]['path'], str((tmp / 'summary.lua').resolve()))
            self.assertEqual(value('--heap-perturb'), ['5'])
            self.assertEqual(value('--profiles'), [str((tmp / 'profiles').resolve())])
            self.assertIn('--no-ui-frame', argv)
            self.assertFalse({'--no-mods', '--no-profiles', '--no-addins', '--skip-addin'} & set(argv))
            self.assertEqual(r.report['format'], 'civ5headless-host-report/1')  # host-report.json before newgame-report.json
            # the default detail (standard) and the report; a host without --no-dumps/--keep-log gets neither
            self.assertEqual((r.status['detail'], r.status['dllReason']), ('standard', 'configured'))
            self.assertFalse({'--no-dumps', '--keep-log'} & set(argv))
            self.assertTrue((r.dir / 'report.html').is_file())
            self.assertIn('"action": "report"', (r.dir / 'events.jsonl').read_text(encoding='utf-8'))
            resolved = json.loads((r.dir / 'spec-resolved.json').read_text(encoding='utf-8'))
            self.assertEqual(resolved['seeds'], {'map': 1, 'sync': 3})
            self.assertEqual(json.loads((r.dir / 'spec.json').read_text(encoding='utf-8')), spec)
            self.assertEqual((r.status['cpuLimit'], r.status['efficiencyMode']), (None, False))
            # a second game into the same folder is refused
            with self.assertRaises(FileExistsError):
                run.play(r.dir / 'spec.json', 4, run_dir=r.dir, cfg=cfg)
            # a capped game in efficiency mode: recorded in status.json, the launch event and the report
            r = run.play(spec, 4, run='cap', cfg=cfg, cpu_limit=50, efficiency_mode=True)
            self.assertTrue(r.ok, r.status.get('error'))
            self.assertEqual((r.status['cpuLimit'], r.status['efficiencyMode']), (50, True))
            launched = [json.loads(l) for l in (r.dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()
                        if '"action": "launched"' in l][0]
            self.assertEqual((launched['cpuLimit'], launched['efficiencyMode']), (50, True))
            self.assertIn('capped at 50% of a core', (r.dir / 'report.html').read_text(encoding='utf-8'))
            with self.assertRaises(ValueError):
                run.play(spec, 4, run='cap0', cfg=cfg, cpu_limit=0)

    def test_play_mod_set_ui_options_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}):
            tmp = Path(tmp)
            db = tmp / 'game.db'
            make_db(db)
            (tmp / 'game.dll').write_bytes(b'x')
            own = tmp / 'Assets/DLC/Expansion2/CvGameCore_Expansion2.dll'  # the game's own DLL
            own.parent.mkdir(parents=True)
            own.write_bytes(b'x')
            (tmp / 'probes').mkdir()
            (tmp / 'probes/fingerprint.lua').write_text('-- fingerprint\n', encoding='utf-8')
            probe = tmp / 'probe.lua'
            probe.write_text('print(1)', encoding='utf-8')
            mods_dir = make_mods(tmp / 'MODS')
            cfg = config.Config(game_dir=tmp, mods_dir=mods_dir, mods_db=tmp / 'game-registry.db', dll=tmp / 'game.dll',
                                exe=HERE / 'fake_host.cmd', db=db, runs=tmp / 'runs', db_cache=tmp / 'cache',
                                probes=tmp / 'probes')
            spec = {'map': {'script': 'Continents.lua', 'size': 'WORLDSIZE_TINY'}, 'seeds': {'map': 1, 'sync': 2}}
            with mock.patch.object(run, 'host_supports', return_value=True):
                r = run.play(spec, 2, run='m', cfg=cfg, mods=[ADDON_ID, BASE_ID], no_profiles=True,
                             skip_addins=['Panel.lua', 'Other.xml'], no_addins=True, fingerprint=dict(every=5, turns=[3]),
                             lua_probes=[probe], detail='summary', report=False)
            self.assertTrue(r.ok, r.status.get('error'))
            argv = json.loads((r.dir / 'argv.json').read_text(encoding='utf-8'))[1:]

            def value(opt):
                return [argv[i + 1] for i, a in enumerate(argv) if a == opt]

            # an explicit mod set whose mods ship no DLL plays the game's own; events.jsonl says why
            self.assertEqual(value('--dll'), [str(own.resolve())])
            dll_event = [json.loads(l) for l in (r.dir / 'events.jsonl').read_text(encoding='utf-8').splitlines()
                         if '"action": "dll"' in l][0]
            self.assertEqual(dll_event['reason'], "no mod of the set ships the DLL: the game's own")
            # summary detail: the host is told to write no dumps and only these logs; the rest is tidied away
            self.assertIn('--no-dumps', argv)
            self.assertEqual(value('--keep-log')[:2], ['Lua.log', 'Headless.log'])
            self.assertFalse((r.dir / 'Logs/HeadlessDigest.log').exists())
            self.assertFalse((r.dir / 'report.html').exists())
            self.assertEqual(json.loads((r.dir / 'hh-command.json').read_text(encoding='utf-8'))['detail'], 'summary')

            registry = Path(value('--mods-db')[0])
            self.assertEqual(registry.parent.parent, tmp / 'cache/modsdb')
            con = sqlite3.connect(registry)
            self.assertEqual([r[0] for r in con.execute('SELECT ModID FROM Mods ORDER BY RowID')], [BASE_ID, ADDON_ID])
            con.close()
            self.assertEqual(value('--mods-dir'), [str(mods_dir.resolve())])
            self.assertEqual(value('--skip-addin'), ['Panel.lua', 'Other.xml'])
            self.assertTrue({'--no-profiles', '--no-addins'} <= set(argv))
            self.assertEqual(value('--profiles'), [])
            self.assertEqual(value('--lua-probe'), [str(r.dir / 'fingerprint-settings.lua'), str(tmp / 'probes/fingerprint.lua'),
                                                     str(r.dir / 'probe-settings.lua'), str(probe.resolve())])
            # no extras: c5h's checkpoint chunk as it was, and no extras in status.json
            self.assertNotIn('c5h: --checkpoint-extra', Path(value('--checkpoint-lua')[0]).read_text(encoding='utf-8'))
            self.assertNotIn('extras', r.status)
            self.assertIn('C5HFP_EVERY = 5\nC5HFP_TURNS = {3}\n', (r.dir / 'fingerprint-settings.lua').read_text(encoding='utf-8'))
            command = json.loads((r.dir / 'hh-command.json').read_text(encoding='utf-8'))
            self.assertEqual(command['fingerprint'], dict(every=5, turns=[3], split=None))
            # without mods: --no-mods and neither a MODS folder nor a registry for the host
            r = run.play(spec, 2, run='n', cfg=cfg, no_mods=True, fingerprint=True, profiles=tmp / 'probes')
            self.assertTrue(r.ok, r.status.get('error'))
            argv = json.loads((r.dir / 'argv.json').read_text(encoding='utf-8'))[1:]
            self.assertIn('--no-mods', argv)
            self.assertEqual((value('--dll'), r.status['dllReason']), ([str(own.resolve())], "no mods: the game's own DLL"))
            r2 = run.play(spec, 2, run='n2', cfg=cfg, no_mods=True, dll=tmp / 'game.dll')  # --dll wins
            self.assertEqual(r2.status['dllReason'], '--dll given')
            self.assertFalse({'--mods-db', '--mods-dir', '--no-profiles'} & set(argv))
            self.assertEqual(value('--lua-probe'), [str(tmp / 'probes/fingerprint.lua')])
            self.assertEqual(value('--profiles'), [str((tmp / 'probes').resolve())])
            # errors end the run with the reason
            r = run.play(spec, 2, run='e', cfg=cfg, mods=[ADDON_ID])
            self.assertFalse(r.ok)
            self.assertIn('not in the set', r.status['error'])

    def test_run_option_checks(self):
        for argv, message in ((['--mod', 'x', '--no-mods'], 'exclude each other'),
                              (['--mod', 'x', '--mods-db', 'r.db'], 'exclude --mods-db'),
                              (['--profiles', 'p', '--no-profiles'], 'exclude each other'),
                              (['--fingerprint-turns', '1,x'], 'turn numbers'),
                              (['--checkpoint-extra', 'no-such-file.lua'], 'not found'),
                              (['--final-extra', __file__, '--final-extra', __file__], 'two extra Lua files'),
                              (['--cpu-limit', '0'], '1 to 100'), (['--cpu-limit', '101'], '1 to 100')):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as ctx:
                    run.main(['spec.json', '--turns', '3'] + argv, config.Config())
                self.assertEqual(ctx.exception.code, 2)
                self.assertIn(message, err.getvalue())


class QueueTest(unittest.TestCase):
    def test_example_queues_expand(self):
        for p in sorted((REPO / 'examples/queues').glob('*.json')):
            with self.subTest(queue=p.name):
                q = json.loads(p.read_text(encoding='utf-8'))
                jobs = queue.expand(q, p)
                self.assertEqual(len(jobs), len(q['seeds']) * len(q['variants']))
                for job in jobs:
                    self.assertTrue(Path(job['spec']).is_file(), job['spec'])
                    self.assertRegex(job['name'], r'^\w+-m\d+-s\d+$')

    def test_paths_relative_to_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / 'q' / 'queue.json'
            qpath.parent.mkdir()
            q = {'defaults': {'spec': 'spec.json', 'turns': 5, 'luaProbes': ['p.lua'], 'checkpointExtras': ['x/c.lua'],
                              'finalExtras': ['f.lua']},
                 'variants': {'A': {'dll': 'a/x.dll'}}, 'seeds': [[1, 2], {'map': 3, 'sync': 4}],
                 'jobs': [{'name': 'extra', 'turns': 9, 'db': 'built.db'}]}
            jobs = {j['name']: j for j in queue.expand(q, qpath)}
            self.assertEqual(set(jobs), {'A-m1-s2', 'A-m3-s4', 'extra'})
            self.assertEqual(Path(jobs['A-m1-s2']['dll']), (qpath.parent / 'a/x.dll').resolve())
            self.assertEqual(Path(jobs['A-m1-s2']['spec']), (qpath.parent / 'spec.json').resolve())
            self.assertEqual(jobs['A-m3-s4']['seeds'], {'map': 3, 'sync': 4})
            self.assertEqual(Path(jobs['extra']['db']), (qpath.parent / 'built.db').resolve())
            self.assertEqual([Path(p) for p in jobs['extra']['luaProbes']], [(qpath.parent / 'p.lua').resolve()])
            self.assertEqual([Path(p) for p in jobs['extra']['checkpointExtras']], [(qpath.parent / 'x/c.lua').resolve()])
            a = queue.job_args(jobs['extra'], Path(tmp) / 'runs/extra', dict(db=None, modsDir=None, modsDb=None), None)
            self.assertEqual((a.checkpoint_extra, a.final_extra), (jobs['extra']['checkpointExtras'], [str((qpath.parent / 'f.lua').resolve())]))
            self.assertEqual(jobs['extra']['turns'], 9)
            qpath.write_text(json.dumps(q), encoding='utf-8')
            cfg = config.Config(runs=Path(tmp) / 'runs')
            self.assertEqual(queue.Queue(qpath, cfg).out, Path(tmp) / 'runs/queue')
            with self.assertRaises(SystemExit):
                queue.expand({'jobs': [{'name': 'x'}, {'name': 'x', 'spec': 's'}]}, qpath)

    def test_save_retries_a_denied_replace(self):
        """A state save survives a passing sharing violation (Defender scanning the file) and raises a lasting one."""
        with tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / 'queue.json'
            qpath.write_text(json.dumps({'jobs': [{'name': 'x', 'spec': 's.json'}]}), encoding='utf-8')
            q = queue.Queue(qpath, config.Config(runs=Path(tmp) / 'runs'))
            q.state['jobs']['x'] = {'status': 'running'}
            real, calls = os.replace, []

            def flaky(src, dst):
                calls.append(dst)
                if len(calls) <= 2:
                    raise PermissionError(5, 'Access is denied')
                real(src, dst)
            with mock.patch.object(winproc.os, 'replace', flaky), mock.patch.object(winproc.time, 'sleep') as sleep:
                q.save()
            self.assertEqual(len(calls), 3)
            self.assertEqual([c.args[0] for c in sleep.call_args_list], [0.05, 0.1])
            self.assertEqual(json.loads(q.state_path.read_text(encoding='utf-8'))['jobs'], {'x': {'status': 'running'}})
            self.assertFalse(q.state_path.with_suffix('.tmp').exists())
            denied = mock.Mock(side_effect=PermissionError(5, 'Access is denied'))
            with mock.patch.object(winproc.os, 'replace', denied), mock.patch.object(winproc.time, 'sleep'):
                with self.assertRaises(PermissionError):
                    q.save()
            self.assertEqual(denied.call_count, 5)

    def test_mod_sets_and_host_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            qpath = tmp / 'queue.json'
            mods_dir = make_mods(tmp / 'MODS')
            q = {'defaults': {'spec': 'spec.json', 'turns': 5, 'profiles': 'profiles', 'skipAddins': ['A.lua'],
                              'fingerprint': {'every': 10}},
                 'jobs': [{'name': 'set', 'mods': [BASE_ID, ADDON_ID]}, {'name': 'none', 'noMods': True, 'noProfiles': True},
                          {'name': 'game', 'noAddins': True}]}
            jobs = {j['name']: j for j in queue.expand(q, qpath)}
            self.assertEqual(Path(jobs['set']['profiles']), (tmp / 'profiles').resolve())
            own = tmp / 'game/Assets/DLC/Expansion2/CvGameCore_Expansion2.dll'
            own.parent.mkdir(parents=True)
            own.write_bytes(b'x')
            cfg = config.Config(mods_dir=mods_dir, mods_db=tmp / 'game.db', db=tmp / 'built.db', db_cache=tmp / 'cache',
                                game_dir=tmp / 'game', dll=tmp / 'active.dll')
            logs = []
            q = SimpleNamespace(cfg=cfg, log=logs.append)
            inputs = queue.databases(q, list(jobs.values()))
            # the DLL: the game's own for noMods and for a mod set without one, the configured one otherwise
            self.assertEqual((inputs['none']['dll'], inputs['set']['dll']), (str(own.resolve()), str(own.resolve())))
            self.assertEqual(inputs['game']['dll'], str((tmp / 'active.dll').resolve()))
            self.assertEqual(queue.job_args(jobs['none'], tmp / 'runs/none', inputs['none'], None).dll_reason,
                             "no mods: the game's own DLL")
            self.assertEqual(inputs['set']['db'], str((tmp / 'built.db').resolve()))
            self.assertEqual(Path(inputs['set']['modsDb']).name, 'Civ5ModsDatabase.db')
            self.assertEqual(Path(inputs['set']['modsDir']), mods_dir.resolve())
            self.assertEqual((inputs['none']['modsDir'], inputs['none']['modsDb']), (None, None))
            self.assertEqual(Path(inputs['game']['modsDb']), tmp / 'game.db')
            a = queue.job_args(jobs['set'], tmp / 'runs/set', inputs['set'], None)
            self.assertEqual((a.mods_db, a.mod, a.no_mods, a.skip_addin, a.no_addins, a.no_profiles),
                             (inputs['set']['modsDb'], None, False, ['A.lua'], False, False))
            self.assertEqual(run.fingerprint_options(a), dict(every=10, turns=[], split=None))
            a = queue.job_args(jobs['none'], tmp / 'runs/none', inputs['none'], None)
            self.assertEqual((a.no_mods, a.no_profiles, a.mods_db), (True, True, None))
            with self.assertRaises(SystemExit):
                queue.expand({'jobs': [{'name': 'x', 'spec': 's', 'mods': [], 'modsDb': 'r.db'}]}, qpath)

    def test_background_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            qpath = Path(tmp) / 'queue.json'
            q = {'defaults': {'spec': 'spec.json', 'cpuLimit': 50},
                 'jobs': [{'name': 'a'}, {'name': 'b', 'cpuLimit': None, 'efficiencyMode': True}]}
            jobs = {j['name']: j for j in queue.expand(q, qpath)}
            inputs = dict(db='built.db', modsDir=None, modsDb=None)
            a = queue.job_args(jobs['a'], Path(tmp) / 'runs/a', inputs, None)
            self.assertEqual((a.cpu_limit, a.efficiency_mode, a.low_priority), (50, False, True))
            b = queue.job_args(jobs['b'], Path(tmp) / 'runs/b', inputs, None)
            self.assertEqual((b.cpu_limit, b.efficiency_mode), (None, True))
            for bad in (0, 101, '50'):
                with self.subTest(cpuLimit=bad), self.assertRaises(SystemExit):
                    queue.expand({'jobs': [{'name': 'x', 'spec': 's', 'cpuLimit': bad}]}, qpath)
            for argv, message in ((['--cpu-limit', '0'], '1 to 100'), (['--total-cpu-limit', '0'], 'percent of one core')):
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
                    queue.main(['run', str(qpath)] + argv, config.Config())
                self.assertIn(message, err.getvalue())

    def test_total_cap_run(self):
        """queue run --total-cpu-limit with fake_host.cmd: every game nests in the queue's capped job."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable}):
            tmp = Path(tmp)
            db = tmp / 'game.db'
            make_db(db)
            for name in ('mods.db', 'loc.db', 'game.dll'):
                (tmp / name).write_bytes(b'x')
            (tmp / 'game').mkdir()
            spec = {'map': {'script': 'Continents.lua', 'size': 'WORLDSIZE_TINY'}, 'seeds': {'map': 1, 'sync': 2}}
            (tmp / 'spec.json').write_text(json.dumps(spec), encoding='utf-8')
            qpath = tmp / 'q.json'
            qpath.write_text(json.dumps({'totalCpuLimit': 300, 'defaults': {'spec': 'spec.json', 'turns': 3, 'report': False},
                                         'jobs': [{'name': 'a'}, {'name': 'b', 'cpuLimit': 50}]}), encoding='utf-8')
            cfg = config.Config(game_dir=tmp / 'game', mods_dir=tmp / 'MODS', mods_db=tmp / 'mods.db', loc_db=tmp / 'loc.db',
                                dll=tmp / 'game.dll', exe=HERE / 'fake_host.cmd', db=db, runs=tmp / 'runs')
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = queue.main(['run', str(qpath), '--workers', '2', '--total-cpu-limit', '100'], cfg)  # the option wins
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn('all games together at 100% of a core', out.getvalue())
            statuses = {n: json.loads((tmp / 'runs/q' / n / 'status.json').read_text(encoding='utf-8')) for n in 'ab'}
            self.assertEqual({n: (s['state'], s['totalCpuLimit'], s['cpuLimit']) for n, s in statuses.items()},
                             {'a': ('completed', 100, None), 'b': ('completed', 100, 50)})
            state = json.loads(qpath.with_suffix('.state.json').read_text(encoding='utf-8'))
            self.assertEqual(state['queue']['totalCpuLimit'], 100)


class WinprocTest(unittest.TestCase):
    def test_cpu_rate(self):
        """Percent of one logical processor as a job CpuRate, in 1/10,000 of the whole machine."""
        with mock.patch.object(winproc, 'logical_cpus', return_value=32):
            self.assertEqual([winproc.cpu_rate(p) for p in (100, 50, 1)], [312, 156, 3])
        with mock.patch.object(winproc, 'logical_cpus', return_value=1):
            self.assertEqual(winproc.cpu_rate(100), 10000)
        # nested in a job capped at within percent of a core: counted against that cap
        self.assertEqual([winproc.cpu_rate(50, within=100), winproc.cpu_rate(25, within=200), winproc.cpu_rate(50, within=25)],
                         [5000, 1250, 10000])

    def test_total_cap_over_nested_jobs(self):
        """Three busy loops, each in its own job capped at 50% of a core, nested in one capped at 50% in all: together
        they get about half a core (a nested rate counted against the machine instead would give them almost none)."""
        parent = winproc.Job(cpu_limit=50)
        jobs, procs = [], []
        try:
            t = time.monotonic()
            for _ in range(3):
                jobs.append(winproc.Job(cpu_limit=50, within=parent))
                procs.append(subprocess.Popen([sys.executable, '-c', 'import time\nt = time.time()\nwhile time.time() - t < 3: pass']))
                parent.assign(procs[-1])
                jobs[-1].assign(procs[-1])
            for p in procs:
                p.wait()
            total = sum(winproc.process_stats(p)['CPUSeconds'] for p in procs) / (time.monotonic() - t)
            self.assertGreater(total, 0.3)
            self.assertLess(total, 0.75)
        finally:
            for job in jobs + [parent]:
                job.close()

    def test_cap_and_efficiency_mode_on_a_busy_process(self):
        """A busy loop capped at 25% of a core uses about a quarter of one; efficiency mode reads back as set."""
        job = winproc.Job(cpu_limit=25)
        try:
            t = time.monotonic()
            p = subprocess.Popen([sys.executable, '-c', 'import time\nt = time.time()\nwhile time.time() - t < 3: pass'])
            job.assign(p)
            eco = winproc.set_efficiency_mode(p)
            if eco:
                self.assertEqual(winproc.efficiency_mode(p), (True, winproc.IDLE_PRIORITY_CLASS))
            p.wait()
            self.assertLess(winproc.process_stats(p)['CPUSeconds'] / (time.monotonic() - t), 0.4)
        finally:
            job.close()
        if not eco:
            self.skipTest('this Windows has no EcoQoS')


class CommandTest(unittest.TestCase):
    """Commands whose module main takes no configuration, and the defaults c5h adds."""

    def test_fingerprint_show(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'Lua.log'
            log.write_text('InGame: C5HFP|v=1|meta|turn=0|plots=10|width=5|height=2|maxMajors=2|maxCivs=3|every=1\n'
                           'InGame: C5HFP|v=1|turn=0|point=inject|part=1/1|total=0000000A|game=0000000B\n', encoding='utf-8')
            code, out, _ = call(['fingerprint', 'show', str(log)])
            self.assertEqual(code, 0)
            self.assertIn('0', out)
            code, out, _ = call(['fingerprint', 'compare', str(log), str(log)])
            self.assertEqual(code, 0)
            self.assertIn('identical', out)

    def test_hazards_gets_the_configured_profiles(self):
        seen = []
        cfg = config.Config(profiles=Path('profiles-folder'))
        with mock.patch('civ5headless.hazards.main', side_effect=lambda argv: seen.append(argv) or 0):
            cli.hazards_main(['scan', 'ui.lua'], cfg)
            cli.hazards_main(['scan', '--profiles=mine'], cfg)
            cli.hazards_main(['known', '--report', 'r.json', '--note', 'n'], cfg)
            cli.hazards_main(['methods', '--dll-source', 'src'], cfg)
        self.assertEqual(seen, [['scan', 'ui.lua', '--profiles', 'profiles-folder'], ['scan', '--profiles=mine'],
                                ['known', '--report', 'r.json', '--note', 'n', '--known', str(Path('profiles-folder/known-ui-files.json'))],
                                ['methods', '--dll-source', 'src']])


class NoMachinePathsTest(unittest.TestCase):
    """Committed code, docs, examples and tests of this workstream name no drive paths, user folders or user name."""
    FILES = ['README.md', 'docs/user-guide.md', 'docs/database-builder.md', 'pyproject.toml', 'civ5headless.example.toml',
             'c5h.cmd', 'tools/phase_speed.py', 'tools/dbbuild_test_edits.py', 'tools/get_toolchain.py']
    TREES = ['python/civ5headless', 'examples', 'tests/platform_tests']

    def files(self):
        out = [REPO / f for f in self.FILES if (REPO / f).is_file()]
        for tree in self.TREES:
            out += [p for p in (REPO / tree).rglob('*') if p.is_file() and p.suffix in ('.py', '.json', '.md', '.toml', '.cmd', '.lua')]
        return out

    def test_no_machine_paths(self):
        drive = re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]')
        users = re.compile(r'[\\/]Users[\\/]', re.I)
        names = {n for n in (os.environ.get('USERNAME'), os.environ.get('USER')) if n and len(n) >= 4}
        found = []
        for p in self.files():
            text = p.read_text(encoding='utf-8', errors='replace')
            for n, line in enumerate(text.splitlines(), 1):
                if drive.search(line) or users.search(line) or any(name.lower() in line.lower() for name in names):
                    found.append(f'{p.relative_to(REPO)}:{n}: {line.strip()[:100]}')
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
