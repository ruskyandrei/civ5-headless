"""Games started together on an empty cache: winproc.file_lock, and one build of the database, the snapshot and the mods
registry however many ask for it."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from civ5headless import config, dbcache, modsdb, snapshot, winproc

from .test_modsdb import ADDON_ID, BASE_ID, make_mods

HOLDER = """
import sys
sys.path.insert(0, {package!r})
from civ5headless import winproc
with winproc.file_lock({path!r}):
    print('held', flush=True)
    sys.stdin.read()
"""


def together(n, call):
    """call(k) in n threads at once; returns what each returned or raised."""
    results = [None] * n
    start = threading.Barrier(n)

    def work(k):
        start.wait()
        try:
            results[k] = call(k)
        except Exception as error:  # noqa: BLE001 (the test looks at it)
            results[k] = error

    threads = [threading.Thread(target=work, args=(k,)) for k in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


class Builds:
    """Counts builds, and how many ran at once. Each takes a moment, so that callers overlap."""

    def __init__(self):
        self.lock = threading.Lock()
        self.count = self.running = self.most = 0

    def __enter__(self):
        with self.lock:
            self.count += 1
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(0.1)

    def __exit__(self, *_):
        with self.lock:
            self.running -= 1


class LockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'build.lock'

    def tearDown(self):
        self.tmp.cleanup()

    def holder(self):
        """A process that holds the lock until its stdin closes or it is killed."""
        package = str(Path(winproc.__file__).resolve().parents[1])
        p = subprocess.Popen([sys.executable, '-c', HOLDER.format(package=package, path=str(self.path))],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        self.addCleanup(p.stdout.close)
        self.addCleanup(p.stdin.close)
        self.assertEqual(p.stdout.readline().strip(), 'held')
        return p

    def test_excludes_threads(self):
        builds = Builds()

        def call(_):
            with winproc.file_lock(self.path):
                with builds:
                    pass

        self.assertEqual(together(4, call), [None] * 4)
        self.assertEqual((builds.count, builds.most), (4, 1))

    def test_excludes_another_process_and_says_so(self):
        p = self.holder()
        told = []
        with self.assertRaises(RuntimeError) as caught:
            with winproc.file_lock(self.path, waiting=lambda: told.append(1), timeout=0.3):
                self.fail('the lock was held')
        self.assertIn('another process holds it', str(caught.exception))
        self.assertEqual(told, [1])
        p.stdin.close()
        p.wait()
        with winproc.file_lock(self.path, timeout=5):
            pass

    def test_released_when_the_holder_dies(self):
        p = self.holder()
        p.kill()
        p.wait()
        with winproc.file_lock(self.path, timeout=5):
            pass

    def test_released_after_an_error(self):
        with self.assertRaises(ValueError):
            with winproc.file_lock(self.path):
                raise ValueError
        with winproc.file_lock(self.path, timeout=0):
            pass


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for name in ('mods.db', 'game.dll', 'exe', 'snapshot.db'):
            (self.dir / name).write_bytes(name.encode())
        (self.dir / 'MODS').mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def get_db(self, log=lambda m: None):
        d = self.dir
        return dbcache.get_db(d / 'MODS', d / 'mods.db', d / 'game.dll', d / 'exe', d / 'snapshot.db', d / 'cache', d, log=log)

    def test_one_database_build(self):
        builds = Builds()

        def build(cmd, **_):
            with builds:
                Path(cmd[cmd.index('--out') + 1]).write_bytes(b'db')
            return subprocess.CompletedProcess(cmd, 0, '', '')

        logs = []
        with mock.patch.object(dbcache.subprocess, 'run', build):
            results = together(6, lambda _: self.get_db(logs.append))
            self.assertEqual((builds.count, builds.most), (1, 1))
            self.assertEqual({r[0] for r in results}, {results[0][0]})
            self.assertEqual(results[0][0].read_bytes(), b'db')
            self.assertEqual(self.get_db()[0], results[0][0])
            self.assertEqual(builds.count, 1)
        self.assertEqual(len([m for m in logs if m.startswith('database built')]), 1)
        self.assertTrue([m for m in logs if m.startswith('another game is building')])

    def test_a_failed_build_is_tried_again(self):
        attempts = []

        def build(cmd, **_):
            attempts.append(1)
            if len(attempts) > 1:
                Path(cmd[cmd.index('--out') + 1]).write_bytes(b'db')
            return subprocess.CompletedProcess(cmd, 0 if len(attempts) > 1 else 3, '', '')

        with mock.patch.object(dbcache.subprocess, 'run', build):
            with self.assertRaisesRegex(RuntimeError, 'database build failed'):
                self.get_db()
            self.assertEqual(self.get_db()[0].read_bytes(), b'db')
        self.assertEqual(len(attempts), 2)

    def test_a_marked_entry_without_its_database_is_rebuilt_unmarked(self):
        """So nobody takes the half-written file for the finished one."""
        seen = []

        def build(cmd, **_):
            out = Path(cmd[cmd.index('--out') + 1])
            seen.append((out.parent / 'built.db.ok').exists())
            out.write_bytes(b'db')
            return subprocess.CompletedProcess(cmd, 0, '', '')

        with mock.patch.object(dbcache.subprocess, 'run', build):
            db = self.get_db()[0]
            db.unlink()
            self.get_db()
        self.assertEqual(seen, [False, False])

    def test_one_snapshot_build(self):
        cfg = config.Config(game_dir=self.dir, exe=self.dir / 'exe', db_cache=self.dir / 'cache', cache_dir=self.dir)
        (self.dir / snapshot.CORE_DB).write_bytes(b'core')
        builds = Builds()

        def build(game_dir, user_dir, core, exe, out, dll=None, log=print):
            with builds:
                Path(out).write_bytes(b'snapshot')
            return dict(status='built', seconds=0.1, run=str(out))

        with mock.patch.object(snapshot, 'build', build), mock.patch.object(snapshot, 'inputs', return_value=({}, 'KEY')), \
                mock.patch.object(snapshot.dbbuild, 'dlc_manifest', return_value={}):
            results = together(6, lambda _: snapshot.cached(cfg, log=lambda m: None))
            self.assertEqual((builds.count, builds.most), (1, 1))
            self.assertEqual({r[0] for r in results}, {cfg.db_cache / 'snapshot/KEY/snapshot.db'})
            snapshot.cached(cfg, log=lambda m: None, force=True)  # --force still builds
            self.assertEqual(builds.count, 2)

    def test_one_mods_registry(self):
        make_mods(self.dir / 'MODS')
        cfg = config.Config(mods_dir=self.dir / 'MODS', db_cache=self.dir / 'cache')
        logs = []
        results = together(6, lambda _: modsdb.cached(cfg, [BASE_ID, ADDON_ID], log=logs.append))
        self.assertEqual({r[0] for r in results if isinstance(r, tuple)}, {results[0][0]})
        self.assertEqual([r for r in results if not isinstance(r, tuple)], [])
        self.assertEqual(len(logs), 1)
        self.assertEqual(sorted(p.name for p in results[0][0].parent.iterdir() if '.tmp' in p.name), [])


if __name__ == '__main__':
    unittest.main()
