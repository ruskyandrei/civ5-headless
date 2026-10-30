"""Digest comparison (compare/digest.py): xxHash64, the digest total, compare() with transients, savedb and the CLI."""
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

from civ5headless.compare import digest

SECTIONS = ('pregame', 'game', 'map', 'p00', 'p01', 'savedb')


def line(turn, values, point='doTurnBegin'):
    """An HHDIGEST line with a correct total (savedb outside it)."""
    order = list(values)
    fields = {k: '%016x' % v for k, v in values.items()}
    total = digest.total_of(fields, order)
    return f'HHDIGEST|turn={turn}|point={point}|total={total:016x}|' + '|'.join(f'{k}={fields[k]}' for k in order)


def game(turns, changes=None):
    """Digest log text for turns 0..turns-1; changes: {turn: {section: value}} on top of the base values."""
    out = ['HHSEEDS|point=gameInit|map=1|sync=2']
    for t in range(turns):
        values = {s: (t + 1) * 1000 + i for i, s in enumerate(SECTIONS)}
        values.update((changes or {}).get(t, {}))
        out.append(line(t, values))
        out.append(f'HHDIGESTSIZE|turn={t}|point=doTurnBegin|map=100')
    return '\n'.join(out) + '\n'


class DigestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def log(self, name, text):
        p = self.dir / name / 'Logs/HeadlessDigest.log'
        p.parent.mkdir(parents=True)
        p.write_text(text, encoding='utf-8')
        return p

    def test_xxh64_vectors(self):
        self.assertEqual(digest.xxh64(b''), 0xEF46DB3751D8E999)
        self.assertEqual(digest.xxh64(b'a'), 0xD24EC4F1A98C6E5B)
        self.assertEqual(digest.xxh64(b'abc'), 0x44BC2CF5AD770999)
        self.assertEqual(digest.xxh64(b'Nobody inspects the spammish repetition'), 0xFBCEA83C8A378BF1)

    def test_total_excludes_savedb(self):
        a = {'game': '%016x' % 1, 'savedb': '%016x' % 2}
        b = {'game': '%016x' % 1, 'savedb': '%016x' % 3}
        self.assertEqual(digest.total_of(a, ['game', 'savedb']), digest.total_of(b, ['game', 'savedb']))
        self.assertEqual(digest.total_of(a, ['game']), digest.xxh64(b'game\0' + struct.pack('<Q', 1)))

    def test_identical(self):
        a, b = self.log('a', game(10)), self.log('b', game(12))
        r = digest.compare(a, b)
        self.assertTrue(r['identical'])
        self.assertEqual((r['common'], r['onlyA'], r['onlyB'], r['lastTurnA'], r['lastTurnB']), (10, 0, 2, 9, 11))
        self.assertIsNone(r['first'])
        self.assertIsNone(r['divergence'])
        self.assertTrue(r['lastPointIdentical'])

    def test_transient_and_divergence(self):
        changes = {3: {'p01': 1}, 4: {'p01': 2, 'map': 3},  # differs at 3-4, equal again at 5: transient
                   7: {'game': 9}, 8: {'game': 9}, 9: {'game': 9}}  # differs from 7 to the end: divergence
        r = digest.compare(self.log('a', game(10)), self.log('b', game(10, changes)))
        self.assertFalse(r['identical'])
        self.assertEqual(r['first'], {'turn': 3, 'point': 'doTurnBegin', 'sections': ['p01']})
        self.assertEqual(r['differing'], {3: ['p01'], 4: ['map', 'p01'], 7: ['game'], 8: ['game'], 9: ['game']})
        self.assertEqual(r['transients'], [{'turn': 3, 'point': 'doTurnBegin', 'until': 5, 'sections': ['p01', 'map']}])
        self.assertEqual(r['divergence'], {'turn': 7, 'point': 'doTurnBegin', 'until': None, 'sections': ['game']})
        self.assertFalse(r['lastPointIdentical'])
        self.assertIn('diverges at turn 7', digest.summary(r))

    def test_heal_turns_boundary(self):
        b = self.log('b', game(10, {4: {'p00': 1}, 5: {'p00': 1}}))  # differs at 4 and 5, equal at 6
        a = self.log('a', game(10))
        self.assertEqual(digest.compare(a, b, heal_turns=2)['divergence'], None)
        r = digest.compare(a, b, heal_turns=1)
        self.assertEqual(r['divergence']['turn'], 4)
        self.assertEqual(r['divergence']['until'], 6)
        self.assertEqual(r['transients'], [])

    def test_savedb_only_is_not_a_difference(self):
        r = digest.compare(self.log('a', game(5)), self.log('b', game(5, {0: {'savedb': 7}, 2: {'savedb': 7}})))
        self.assertTrue(r['identical'])
        self.assertEqual(r['savedbFirstTurn'], 0)

    def test_total_mismatch_without_section_difference(self):
        lines = game(3).splitlines()  # change only the total of turn 1
        i = next(k for k, l in enumerate(lines) if l.startswith('HHDIGEST|turn=1|'))
        fields = lines[i].split('|')
        fields[3] = 'total=0000000000000000'
        lines[i] = '|'.join(fields)
        r = digest.compare(self.log('a', game(3)), self.log('b', '\n'.join(lines) + '\n'))
        self.assertEqual(r['first'], {'turn': 1, 'point': 'doTurnBegin', 'sections': []})

    def test_extra_section(self):
        b_lines = [l + '|p63=0000000000000001' if l.startswith('HHDIGEST|turn=2|') else l for l in game(4).splitlines()]
        r = digest.compare(self.log('a', game(4)), self.log('b', '\n'.join(b_lines) + '\n'))
        self.assertEqual(r['differing'], {2: ['p63']})

    def test_log_path_and_cli(self):
        a, b = self.log('a', game(4)), self.log('b', game(4, {2: {'map': 1}}))
        self.assertEqual(digest.log_path(self.dir / 'a'), a)
        self.assertEqual(digest.log_path(a), a)
        with self.assertRaises(FileNotFoundError):
            digest.log_path(self.dir)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(digest.compare_main([str(self.dir / 'a'), str(self.dir / 'a')]), 0)
            code = digest.compare_main([str(self.dir / 'a'), str(b), '--json', str(self.dir / 'r.json')])
        self.assertEqual(code, 1)
        self.assertIn('identical on 4 common digest points', out.getvalue())
        self.assertIn('first difference: turn 2', out.getvalue())
        saved = json.loads((self.dir / 'r.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['differing'], {'2': ['map']})

    def test_warns_when_ai_logging_differs(self):
        def run(name, status, host=None):
            log = self.log(name, game(4))
            (log.parents[1] / 'status.json').write_text(json.dumps(status), encoding='utf-8')
            if host is not None:
                (log.parents[1] / 'host-report.json').write_text(json.dumps(dict(options=host)), encoding='utf-8')
            return log.parents[1]

        def compare(a, b):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = digest.compare_main([str(a), str(b), '--json', str(self.dir / 'r.json')])
            self.assertEqual(code, 0)  # the warning changes nothing else
            return out.getvalue(), json.loads((self.dir / 'r.json').read_text(encoding='utf-8'))['aiLogging']

        on, off = run('on', dict(aiLogging=True)), run('off', dict(aiLogging=False))
        text, settings = compare(on, off)
        self.assertIn(f'different AI logging: {on} on, {off} off', text)
        self.assertEqual(settings, [True, False])
        self.assertIn('different AI logging', compare(on / 'Logs', off / 'Logs/HeadlessDigest.log')[0])
        self.assertNotIn('warning', compare(off, run('off2', dict(aiLogging=False)))[0])
        # an older run: its host report says
        old = run('old', dict(state='completed'), dict(gameLogging=True, gameLogFlags=15))
        self.assertIn(f'{old} on', compare(old, off)[0])
        self.assertNotIn('warning', compare(old, on)[0])
        # nothing known about one side: no warning
        bare = self.dir / 'bare.log'
        bare.write_text(game(4), encoding='utf-8')
        for other in (bare, run('unsaid', dict(state='completed')), self.log('nostatus', game(4)).parents[1]):
            text, settings = compare(off, other)
            self.assertNotIn('warning', text)
            self.assertEqual(settings, [False, None])

    def test_verify_dump(self):
        dump = self.dir / 'dump-t000'
        dump.mkdir()
        data = {'game': b'game state', 'map': bytes(range(200)), 'savedb': b'ui'}
        values = {}
        for name, blob in data.items():
            (dump / f'{name}.bin').write_bytes(blob)
            values[name] = digest.xxh64(blob)
        (dump / 'digest.txt').write_text(line(0, values) + '\n', encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(digest.cmd_verify(dump), 0)
            (dump / 'map.bin').write_bytes(b'changed')
            self.assertEqual(digest.cmd_verify(dump), 1)


if __name__ == '__main__':
    unittest.main()
