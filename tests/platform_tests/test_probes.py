"""The probe files (probes.py): the fingerprint settings and files a host run and a real-game session inject, the
run settings for --lua-probe files, and the extras (--checkpoint-extra, --final-extra)."""
import json
from pathlib import Path
import tempfile
import unittest

from civ5headless import config, probes

REPO = Path(__file__).resolve().parents[2]


class FingerprintTest(unittest.TestCase):
    def test_options(self):
        self.assertIsNone(probes.fingerprint_options(None))
        self.assertIsNone(probes.fingerprint_options(False))
        self.assertEqual(probes.fingerprint_options(True), dict(every=None, turns=[], split=None))
        self.assertEqual(probes.fingerprint_options(None, every=4), dict(every=4, turns=[], split=None))
        self.assertEqual(probes.fingerprint_options(None, turns='1, 50,100'), dict(every=None, turns=[1, 50, 100], split=None))
        self.assertEqual(probes.fingerprint_options({'every': 2, 'split': 6}, turns=[7]), dict(every=2, turns=[7], split=6))
        with self.assertRaises(ValueError):
            probes.fingerprint_options({'evry': 2})
        with self.assertRaises(ValueError):
            probes.fingerprint_options(None, turns='1,x')

    def test_settings(self):
        self.assertEqual(probes.fingerprint_settings(), '')
        text = probes.fingerprint_settings(every=3, turns=[1, 2], split=8)
        self.assertEqual(text.splitlines()[1:], ['C5HFP_EVERY = 3', 'C5HFP_TURNS = {1, 2}', 'C5HFP_SPLIT = 8'])
        self.assertTrue(text.startswith('--'))
        for bad in (dict(every=0), dict(split=0)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probes.fingerprint_settings(**bad)

    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg = config.Config(probes=tmp / 'probes')
            with self.assertRaises(config.ConfigError):
                probes.fingerprint_probes(cfg, tmp / 'run')
            with self.assertRaises(config.ConfigError):
                probes.fingerprint_probes(config.Config(), tmp / 'run')
            (tmp / 'probes').mkdir()
            (tmp / 'probes/fingerprint.lua').write_text('', encoding='utf-8')
            self.assertEqual(probes.fingerprint_probes(cfg, tmp / 'run'), [tmp / 'probes/fingerprint.lua'])
            files = probes.fingerprint_probes(cfg, tmp / 'run', every=5)
            self.assertEqual(files, [tmp / 'run/fingerprint-settings.lua', tmp / 'probes/fingerprint.lua'])
            self.assertIn('C5HFP_EVERY = 5', files[0].read_text(encoding='utf-8'))

    def test_the_probe_reads_these_globals(self):
        probe = REPO / 'probes/fingerprint.lua'
        if not probe.is_file():
            self.skipTest('no probes/fingerprint.lua')
        text = probe.read_text(encoding='utf-8')
        for name in ('C5HFP_EVERY', 'C5HFP_TURNS', 'C5HFP_SPLIT'):
            self.assertIn(name, text)


class ExtrasTest(unittest.TestCase):
    """Your own Lua in the checkpoints and the final state (c5h run --checkpoint-extra / --final-extra)."""

    def test_long_string(self):
        self.assertEqual(probes.long_string('a'), '[[\na]]')
        self.assertEqual(probes.long_string('x]]y'), '[=[\nx]]y]=]')
        self.assertEqual(probes.long_string('a]'), '[=[\na]]=]')  # a closing bracket at the end needs a level too
        self.assertEqual(probes.long_string(']]]=]'), '[==[\n]]]=]]==]')

    def test_files_and_lua(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'a').mkdir()
            (tmp / 'stats.lua').write_text('﻿return {gold = 1}\n', encoding='utf-8')
            (tmp / 'a/Stats.lua').write_text('return 2', encoding='utf-8')
            self.assertEqual(probes.extra_files(None), [])
            files = probes.extra_files([tmp / 'stats.lua'])
            self.assertEqual(files, [('stats', (tmp / 'stats.lua').resolve())])
            with self.assertRaisesRegex(ValueError, 'not found'):
                probes.extra_files([tmp / 'none.lua'])
            with self.assertRaisesRegex(ValueError, 'two extra Lua files are named'):
                probes.extra_files([tmp / 'stats.lua', tmp / 'a/Stats.lua'])
            self.assertEqual(probes.extra_lua('checkpoint', [], tmp), '')
            with self.assertRaises(ValueError):
                probes.extra_lua('turn', files, tmp)
            lua = probes.extra_lua('final', files, tmp / 'run')
            # the source without its byte order mark, its path for error messages
            self.assertIn(' run([[\nstats]],[[\n' + str((tmp / 'stats.lua').resolve()) + ']],[[\nreturn {gold = 1}\n]])', lua)
            self.assertIn("local key='final:'..name", lua)
            # fresh globals at every call: the host keeps one thread for the checkpoint chunk
            self.assertIn('setfenv(f,setmetatable({},{__index=env}))', lua)
            self.assertIn('runDir=[[\n' + str((tmp / 'run').resolve()) + ']]', lua)
            self.assertNotIn('%(', lua)
            chunk = 'local out={}\nif x then return out end\nreturn out\n'
            self.assertEqual(probes.with_extra(chunk, ''), chunk)
            self.assertEqual(probes.with_extra(chunk, 'EXTRA\n'), 'local out={}\nif x then return out end\nEXTRA\nreturn out\n')

    def test_run_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = probes.run_settings(Path(tmp) / 'run')
            self.assertEqual(path, (Path(tmp) / 'run/probe-settings.lua').resolve())
            self.assertTrue((Path(tmp) / 'run/extra').is_dir())
            text = path.read_text(encoding='utf-8')
            self.assertIn('C5H_RUN_DIR = [[\n' + str((Path(tmp) / 'run').resolve()) + ']]', text)
            self.assertIn('C5H_EXTRA_DIR = [[\n' + str((Path(tmp) / 'run/extra').resolve()) + ']]', text)

    def test_collect(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            self.assertEqual(probes.collect_extras(run), [])
            self.assertFalse((run / 'extra').exists())
            (run / 'checkpoints').mkdir()
            points = {'turn-0010.json': dict(seconds=2.5, data=dict(turn=10, extra=dict(a=[1, 2]))),
                      'turn-0002.json': dict(seconds=0.5, data=dict(turn=2, extra=dict(), extraErrors=dict(a='a.lua:3: boom'))),
                      'turn-0010-final.json': dict(seconds=3.0, data=dict(turn=10, extra=dict(a={'x': None}))),
                      'turn-0005.json': dict(seconds=1.0, data=dict(turn=5, extra=dict())),  # the extra returned nil
                      'turn-0000.json': dict(data=dict(turn=0))}  # the start turn's, taken with the host's own Lua
            for name, value in points.items():
                (run / 'checkpoints' / name).write_text(json.dumps(value), encoding='utf-8')
            final = dict(extra=dict(end={'cities': 3}), extraErrors=dict(broken='broken.lua:1: nil value'))
            summaries = probes.collect_extras(run, ['a'], ['end', 'broken', 'missing'], final)
            rows = [json.loads(line) for line in (run / 'extra/a.jsonl').read_text(encoding='utf-8').splitlines()]
            self.assertEqual(rows, [dict(turn=2, final=False, seconds=0.5, error='a.lua:3: boom'),
                                    dict(turn=5, final=False, seconds=1.0, value=None),
                                    dict(turn=10, final=False, seconds=2.5, value=[1, 2]),
                                    dict(turn=10, final=True, seconds=3.0, value={'x': None})])

            def final_file(name):
                return json.loads((run / f'extra/{name}-final.json').read_text(encoding='utf-8'))

            self.assertEqual((final_file('end'), final_file('broken'), final_file('missing')),
                             ({'cities': 3}, {'error': 'broken.lua:1: nil value'}, None))
            self.assertEqual([(x['kind'], x['name'], x['rows'], x['errors'], x['firstError']) for x in summaries],
                             [('checkpoint', 'a', 4, 1, 'a.lua:3: boom'), ('final', 'end', 1, 0, None),
                              ('final', 'broken', 1, 1, 'broken.lua:1: nil value'), ('final', 'missing', 1, 0, None)])
            # the final chunk did not run (the host failed): the final files hold null
            self.assertEqual(probes.collect_extras(run, [], ['end'], None)[0]['rows'], 0)
            self.assertIsNone(final_file('end'))


if __name__ == '__main__':
    unittest.main()
