"""The real-game automation (realgame/) without the game: the FireTuner protocol, config.ini seeding, mod selection,
the launcher's pieces, and RealGameSession driven end to end against a fake game behind a fake tuner (fake_tuner.py).

The Lua tests run tuner_result.lua and the mod Lua in Lua 5.1 through lupa: pip install lupa, or set C5H_LUPA_PATH to
a folder that holds it; they are skipped otherwise.
"""
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from civ5headless import config, run, savegame, spec as specmod
from civ5headless.realgame import dbcapture, launch, mods, probe, seeds, session, tuner

from .fake_tuner import FakeTuner, LuaError, unescape
from .test_savegame import make_save
from .test_spec import make_db


def lua_runtime():
    if os.environ.get('C5H_LUPA_PATH'):
        sys.path.insert(0, os.environ['C5H_LUPA_PATH'])
    try:
        from lupa.lua51 import LuaRuntime
    except ImportError:
        return None
    return LuaRuntime


def sleeper():
    return subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], stdin=subprocess.DEVNULL)


# ---- protocol ----

class ProtocolTest(unittest.TestCase):
    def test_frame_and_nul(self):
        data = tuner.encode_packet('LSQ:café', 73)
        self.assertEqual(struct.unpack('<II', data[:8]), (10, 73))
        self.assertEqual(data[8:], b'LSQ:caf\xc3\xa9\0')
        with self.assertRaises(tuner.TunerError):
            tuner.encode_packet('bad\0code', 1)

    def test_fragmented_frame(self):
        a, b = socket.socketpair()
        try:
            body = b'3\0InGame\0'
            data = struct.pack('<II', len(body), 9) + body
            worker = threading.Thread(target=lambda: [a.sendall(bytes([x])) for x in data])
            worker.start()
            self.assertEqual(tuner.recv_packet(b), (9, ['3', 'InGame']))
            worker.join()
        finally:
            a.close()
            b.close()

    def test_packet_bound(self):
        a, b = socket.socketpair()
        try:
            a.sendall(struct.pack('<II', tuner.MAX_PACKET + 1, 1))
            with self.assertRaises(tuner.TunerError):
                tuner.recv_packet(b)
        finally:
            a.close()
            b.close()

    def test_contexts(self):
        self.assertEqual(tuner.parse_states(['2', 'InGame', '4', 'LoadMenu']),
                         [{'id': 2, 'name': 'InGame'}, {'id': 4, 'name': 'LoadMenu'}])
        for data in (['3'], ['bad', 'InGame'], ['-1', 'InGame']):
            with self.assertRaises(tuner.TunerError):
                tuner.parse_states(data)

    def test_result_chunks(self):
        data = json.dumps({'ok': True, 'values': ['café' * 150]}, ensure_ascii=False).encode()
        chunks = [f'{i // 256 + 1}/{(len(data) + 255) // 256}:' + data[i:i + 256].hex() for i in range(0, len(data), 256)]
        self.assertEqual(tuner.decode_result(chunks)['values'], ['café' * 150])
        for bad in (chunks[:-1], chunks + [chunks[0]], ['0/1:aa'], ['1/1025:aa']):
            with self.assertRaises(tuner.TunerError):
                tuner.decode_result(bad)

    def test_reply_matching_and_output_bound(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        errors = []

        def engine():
            try:
                with listener.accept()[0] as sock:
                    for _ in range(2):
                        ident, command = tuner.recv_packet(sock)
                        self.assertEqual(command, ['LSQ:'])
                        sock.sendall(tuner.encode_packet('99', ident + 100))  # a stale reply must not answer
                        for _ in range(150):
                            body = b'O\0trace\0'
                            sock.sendall(struct.pack('<II', len(body), tuner.BROADCAST) + body)
                        body = b'2\0InGame\0'
                        sock.sendall(struct.pack('<II', len(body), ident) + body)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        worker = threading.Thread(target=engine)
        worker.start()
        client = tuner.TunerConnection(listener.getsockname()[1])
        try:
            for _ in range(2):
                response = client.request('LSQ:')
                self.assertEqual(response['reply'], ['2', 'InGame'])
                self.assertEqual(len(response['output']), 128)
                self.assertEqual(response['output_dropped'], 22)
        finally:
            client.close()
            worker.join()
            listener.close()
        self.assertEqual(errors, [])

    def test_timeout_never_retries(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        received = []

        def engine():
            with listener.accept()[0] as sock:
                received.append(tuner.recv_packet(sock))
                self.assertEqual(sock.recv(1), b'')
        worker = threading.Thread(target=engine)
        worker.start()
        client = tuner.TunerConnection(listener.getsockname()[1])
        try:
            with self.assertRaisesRegex(tuner.TunerError, 'outcome is unknown'):
                client.request('CMD:2:change()', timeout=.05)
            with self.assertRaises(tuner.TunerError):
                client.request('CMD:2:change()')
        finally:
            client.close()
            worker.join()
            listener.close()
        self.assertEqual(len(received), 1)


class ExecuteTest(unittest.TestCase):
    """TunerConnection.execute through the upload protocol, against the fake tuner."""

    def setUp(self):
        def handler(context, source):
            if 'fail' in source:
                raise LuaError('[string "Civ5Automation"]:1: fixture error')
            if 'big' in source:
                return ['x' * 5000]
            return [context, source, None, {'n': 1}]
        self.fake = FakeTuner(handler, ('MainMenu', 'InGame'))
        self.client = tuner.TunerConnection(self.fake.port)

    def tearDown(self):
        self.client.close()
        self.fake.close()

    def test_values_round_trip(self):
        source = 'return "café", ' + 'x' * 700  # several upload pieces
        result = self.client.execute('InGame', source)
        self.assertTrue(result['ok'])
        self.assertEqual(result['values'], ['InGame', source, None, {'n': 1}])
        self.assertEqual(result['context']['name'], 'InGame')
        self.client.execute('InGame', 'return 1')
        self.assertEqual(len(self.fake.commands), 2)  # the wrapper is uploaded once per context

    def test_lua_error_and_large_result(self):
        result = self.client.execute('MainMenu', 'fail()')
        self.assertFalse(result['ok'])
        self.assertIn('fixture error', result['error'])
        self.assertEqual(self.client.execute('MainMenu', 'big')['values'], ['x' * 5000])

    def test_unknown_context_and_limits(self):
        with self.assertRaisesRegex(tuner.TunerError, 'LoadScreen'):
            self.client.execute('LoadScreen', 'return 1')
        with self.assertRaises(tuner.TunerError):
            self.client.execute('InGame', 'x' * (tuner.MAX_SOURCE + 1))

    def test_lua_string(self):
        self.assertEqual(unescape(tuner.lua_string('a"\\\né')[1:-1]), 'a"\\\né'.encode('utf-8'))


class LuaTest(unittest.TestCase):
    """tuner_result.lua in Lua 5.1."""

    @classmethod
    def setUpClass(cls):
        cls.runtime = lua_runtime()
        if cls.runtime is None:
            raise unittest.SkipTest('lupa is not available (pip install lupa, or C5H_LUPA_PATH)')

    def run_lua(self, source):
        lua = self.runtime(unpack_returned_tuples=True)
        output = []
        lua.globals().print = lambda s: output.append(s)
        lua.execute(tuner.wrap_lua(source, 'RESULT:'))
        return tuner.decode_result([line.split('RESULT:', 1)[1] for line in output])

    def test_typed_results_and_nil_positions(self):
        self.assertEqual(self.run_lua("return 4,nil,false,{city='café',units={1,2}},'a\\n\\0'")['values'],
                         [4, None, False, {'city': 'café', 'units': [1, 2]}, 'a\n\0'])

    def test_errors(self):
        for source, message in [('this is not lua', ''), ("error('fixture error')", 'fixture error'),
                                ('local t={};t.self=t;return t', 'Circular'), ('return function()end', 'Unsupported'),
                                ('return {[2]=7}', 'Object keys'), ('return 0/0', 'Non-finite'),
                                ("return string.rep('x',262145)", '256 KiB')]:
            with self.subTest(source=source):
                result = self.run_lua(source)
                self.assertFalse(result['ok'])
                self.assertIn(message, result['error'])

    def test_session_lua_compiles(self):
        lua = self.runtime()
        compile_ = lua.eval('function(s) local f,e=loadstring(s);return e end')
        sources = dict(START=session.START, LOADED=session.LOADED, SHOWN=session.SHOWN, SETUP=session.SETUP,
                       ARM=session.ARM.format(turns=3), CONTINUE=session.CONTINUE, TURN=session.TURN,
                       VICTORY=session.VICTORY, HALT=session.HALT, MENU=session.MENU, QUIT=session.QUIT,
                       EXPORT=session.EXPORT % tuner.lua_string('x/y.db'), DISMISS=mods.DISMISS_LEGAL,
                       ACTIVATE=mods.ACTIVATE, REPLAY_OWNERS=session.REPLAY_OWNERS, REPLAY_PLAYER=session.REPLAY_PLAYER % 3,
                       LOAD=session.LOAD % tuner.lua_string('saves/Turn_0006 BC-3760.Civ5Save'),
                       SAVE=session.SAVE % tuner.lua_string('After 6'), DIGEST_ON=session.DIGEST_ON, FINAL=run.FINAL)
        for name, source in sources.items():
            with self.subTest(name=name):
                self.assertIsNone(compile_(source))

    def test_replay_player(self):
        fake = ("Players={[3]={GetReplayData=function(self) return {REPLAYDATASET_SCORE={[1]=5,[2]=7.5},"
                "REPLAYDATASET_NONE={}} end}}\n")
        values = self.run_lua(fake + session.REPLAY_PLAYER % 3)['values'][0]
        self.assertEqual(values['REPLAYDATASET_NONE'], '')
        self.assertEqual(sorted(values['REPLAYDATASET_SCORE'].split(',')), ['1=5', '2=7.5'])

    def test_set_enabled_mods(self):
        wanted = [('11111111-2222-3333-4444-555555555555', 1, 'A'), ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 2, 'B')]
        source = mods.set_enabled_lua(wanted)
        for initial, disabled, enabled in (([], 0, 2), ([('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 2)], 0, 1),
                                           ([('99999999-8888-7777-6666-55555555555f', 3),
                                             ('11111111-2222-3333-4444-555555555555', 9)], 2, 2)):
            with self.subTest(initial=initial):
                lua = self.runtime(unpack_returned_tuples=True)
                lua.execute('mods={}\nModding={GetEnabledModsByActivationOrder=function() return mods end,'
                            'EnableMod=function(id,v) mods[#mods+1]={ModID=id,Version=v} end,'
                            'DisableMod=function(id,v) for i,m in ipairs(mods) do if m.ModID==id and m.Version==v then '
                            'table.remove(mods,i) return end end end}')
                for mid, ver in initial:
                    lua.execute(f'mods[#mods+1]={{ModID="{mid.upper()}",Version={ver}}}')
                result = lua.execute(source)
                self.assertTrue(result['ok'])
                self.assertEqual((result['disabled'], result['enabled']), (disabled, enabled))
                self.assertEqual(sorted((m['ModID'].lower(), m['Version']) for m in lua.globals().mods.values()),
                                 sorted((i, v) for i, v, _ in wanted))


# ---- config.ini seeds ----

INI = ('[Debugging]\r\n\r\n; Set to 1 to enable the fire tuner to connect to the game.\r\nEnableTuner = 1\r\n\r\n'
       '[CONFIG]\r\n\r\n; Random seed for game sync, or \'0\' for default\r\nSyncRandSeed = 0\r\n\r\n'
       '; Random seed for map generation, or \'0\' for default\r\nMapRandSeed = 0\r\n\r\n'
       '[Debug]\r\nLoggingEnabled = 1\r\n')


class SeedsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ini = self.dir / 'config.ini'
        self.ini.write_bytes(INI.encode())
        self.marker = self.dir / 'ws/cache' / seeds.MARKER

    def tearDown(self):
        self.tmp.cleanup()

    def test_pin_and_restore(self):
        self.assertEqual(seeds.read(self.ini), {'MapRandSeed': 0, 'SyncRandSeed': 0})
        seeds.pin(self.ini, 4242, 2424, self.marker, self.dir / 'run/config.ini.backup')
        self.assertEqual(seeds.read(self.ini), {'MapRandSeed': 4242, 'SyncRandSeed': 2424})
        changed = self.ini.read_bytes().decode()
        self.assertEqual(changed, INI.replace('SyncRandSeed = 0', 'SyncRandSeed = 2424').replace('MapRandSeed = 0', 'MapRandSeed = 4242'))
        self.assertEqual((self.dir / 'run/config.ini.backup').read_bytes(), INI.encode())
        seeds.pin(self.ini, 7, None, self.marker)  # a second pin keeps the first original
        self.assertEqual(seeds.read(self.ini), {'MapRandSeed': 7, 'SyncRandSeed': 0})
        self.assertEqual(seeds.restore(self.marker), {'MapRandSeed': 0, 'SyncRandSeed': 0})
        self.assertEqual(self.ini.read_bytes(), INI.encode())
        self.assertFalse(self.marker.exists())
        self.assertIsNone(seeds.restore(self.marker))

    def test_values_and_missing_keys(self):
        self.assertEqual(seeds.ini_value(self.ini, 'EnableTuner'), '1')
        self.assertEqual(seeds.ini_value(self.ini, 'LoggingEnabled'), '1')
        self.assertIsNone(seeds.ini_value(self.ini, 'Nothing'))
        self.assertEqual(seeds.edit('[A]\nx=1\n[CONFIG]\nfoo = 1\n\n[B]\n', {'MapRandSeed': 1}),
                         '[A]\nx=1\n[CONFIG]\nfoo = 1\nMapRandSeed = 1\n\n[B]\n')
        self.assertEqual(seeds.edit('[A]\r\nx=1', {'MapRandSeed': 1}), '[A]\r\nx=1\r\n[CONFIG]\r\nMapRandSeed = 1\r\n')
        bom = self.dir / 'bom.ini'
        bom.write_bytes(b'\xef\xbb\xbf' + INI.encode())
        seeds.pin(bom, 5, 6, self.dir / 'other.json')
        self.assertTrue(bom.read_bytes().startswith(b'\xef\xbb\xbf[Debugging]'))
        self.assertEqual(seeds.read(bom), {'MapRandSeed': 5, 'SyncRandSeed': 6})

    def test_bad_seed(self):
        for bad in (0, -1, 2**31):
            with self.assertRaises(seeds.SeedError):
                seeds.pin(self.ini, bad, 1, self.marker)
        self.assertFalse(self.marker.exists())


# ---- mods and launcher pieces ----

class ModsTest(unittest.TestCase):
    def test_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods_dir = Path(tmp) / 'MODS'
            for folder, version in (('Mod (v 1)', 1), ('Mod (v 3)', 3)):
                (mods_dir / folder).mkdir(parents=True)
                (mods_dir / folder / 'mod.modinfo').write_text(
                    f'<Mod id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" version="{version}"><Properties><Name>Installed</Name>'
                    '</Properties></Mod>', encoding='utf-8')
            cfg = config.Config(mods_dir=mods_dir, active_mods=[config.Mod('11111111-2222-3333-4444-555555555555', 7, 'Active')])
            self.assertEqual(mods.resolve(cfg, ['{11111111-2222-3333-4444-555555555555}', 'AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE',
                                                '99999999-8888-7777-6666-555555555555:2']),
                             [('11111111-2222-3333-4444-555555555555', 7, 'Active'),
                              ('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 3, 'Installed'),
                              ('99999999-8888-7777-6666-555555555555', 2, '')])
            for bad in (['nope'], ['99999999-8888-7777-6666-000000000000'],
                        ['11111111-2222-3333-4444-555555555555', '11111111-2222-3333-4444-555555555555']):
                with self.assertRaises(mods.ModError):
                    mods.resolve(cfg, bad)
            self.assertEqual(mods.resolve(cfg, []), [])

    def test_lua_quotes_ids(self):
        source = mods.set_enabled_lua([('11111111-2222-3333-4444-555555555555', 7, 'x"y')])
        self.assertIn('Version=7}', source)
        self.assertNotIn('x"y', source)


class LaunchTest(unittest.TestCase):
    def test_steam_appid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'steam_appid.txt'
            with launch.steam_appid(tmp):
                self.assertEqual(path.read_text(), '8930')
            self.assertFalse(path.exists())
            path.write_text('8930')
            with launch.steam_appid(tmp):
                pass
            self.assertTrue(path.exists())  # not ours: left alone
            path.write_text('1234')
            with self.assertRaises(launch.LaunchError):
                with launch.steam_appid(tmp):
                    pass
            self.assertEqual(path.read_text(), '1234')

    def test_wait_for_port(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        child = subprocess.Popen([sys.executable, '-c', 'import socket,time\ns=socket.socket()\n'
                                  f's.bind(("127.0.0.1",{port}))\ntime.sleep(1)\ns.listen(1)\ntime.sleep(60)'])
        try:
            launch.wait_for_port(child, port, 30)
        finally:
            child.kill()
            child.wait()
        quitter = subprocess.Popen([sys.executable, '-c', 'pass'])
        with self.assertRaisesRegex(launch.LaunchError, 'exited'):
            launch.wait_for_port(quitter, port, 30)
        with self.assertRaisesRegex(launch.LaunchError, 'did not open'):
            child = sleeper()
            try:
                launch.wait_for_port(child, port, 1)
            finally:
                child.kill()
                child.wait()

    def test_core_temp_layout(self):
        buf = bytearray(launch.CORE_TEMP_BYTES)
        struct.pack_into('<II', buf, 1536, 2, 1)       # 2 cores, 1 CPU
        struct.pack_into('<I', buf, 1024, 100)         # TjMax
        struct.pack_into('<ff', buf, 1544, 60.5, 71.25)
        self.assertEqual(launch.parse_core_temp(bytes(buf))['coreTemperaturesC'], [60.5, 71.2])
        buf[2685] = 1                                  # values are distances to TjMax
        self.assertEqual(launch.parse_core_temp(bytes(buf))['maximumC'], 39.5)
        struct.pack_into('<II', buf, 1536, 0, 1)
        with self.assertRaises(ValueError):
            launch.parse_core_temp(bytes(buf))

    def test_cpu_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            child = sleeper()
            game = launch.Game(popen=child, pid=child.pid, exe='sleeper', port=0, seconds=0)
            temps = iter([99, 96, 80, 97, 98, 99, 99])
            guard = launch.CpuGuard(game, Path(tmp) / 'cpu.jsonl', limit=95, sustained=3, sample_seconds=0.01,
                                    reader=lambda: dict(maximumC=next(temps)))
            guard.start()
            guard.thread.join(10)
            child.wait(10)
            self.assertTrue(guard.tripped)
            events = [json.loads(line)['event'] for line in (Path(tmp) / 'cpu.jsonl').read_text().splitlines()]
            self.assertEqual(events, ['armed'] + ['sample'] * 6 + ['stopped'])

            child = sleeper()
            game = launch.Game(popen=child, pid=child.pid, exe='sleeper', port=0, seconds=0)

            def unavailable():
                raise ValueError('Core Temp is not running')
            guard = launch.CpuGuard(game, Path(tmp) / 'cpu2.jsonl', sample_seconds=0.01, reader=unavailable).start()
            time.sleep(0.2)
            guard.stop()
            self.assertIsNone(child.poll())
            child.kill()
            child.wait()
            events = [json.loads(line)['event'] for line in (Path(tmp) / 'cpu2.jsonl').read_text().splitlines()]
            self.assertEqual(events, ['armed', 'temperature_unavailable', 'disarmed'])


# ---- the session against a fake game ----

class FakeGame:
    """What the session sees of the game: front end, mod activation, a game that plays one turn per TURN poll.

    Like the real game it reads config.ini's seeds when it starts (its DLL loads) and logs them in HHSEEDS lines when
    a game starts (the digest additions)."""

    def __init__(self, cfg, victory_at=None):
        self.cfg, self.victory_at = cfg, victory_at
        self.fake = None
        self.processes = []
        self.enabled = []
        self.log = []          # what happened, in order

    def launcher(self, game_dir, port, timeout):
        values = seeds.read(self.cfg.config_ini)
        self.exe_seeds = (values['MapRandSeed'], values['SyncRandSeed'])
        if self.fake:
            self.fake.close()
        # As in the real game, the mods screens exist from the start, hidden; activation shows ModsMenu.
        self.fake = FakeTuner(self.handle, ('MainMenu', 'LegalScreen', 'ModsBrowser', 'ModsMenu', 'ModsSinglePlayer'))
        self.shown = {'MainMenu'}
        self.turn, self.autoplay, self.running, self.winner = 0, 0, False, -1
        (self.cfg.user_dir / 'Logs/Lua.log').write_text('started\n', encoding='utf-8')
        self.write_debug_db([(1,)])
        child = sleeper()
        self.processes.append(child)
        self.log.append(('launch', self.exe_seeds))
        return launch.Game(popen=child, pid=child.pid, exe='fake', port=self.fake.port, seconds=0.01)

    def handle(self, context, source):
        f = self.fake
        if 'DequeuePopup' in source:
            f.remove('LegalScreen')
            return [True]
        if 'Modding.GetEnabledModsByActivationOrder' in source:
            before = [dict(ModID=i, Version=v) for i, v in self.enabled]
            ids = [unescape(x).decode() for x in re.findall(r'ModID="((?:\\\d{3})*)"', source)]
            self.enabled = list(zip(ids, map(int, re.findall(r'Version=(\d+)', source))))
            self.log.append(('mods', [i for i, _ in self.enabled]))
            return [dict(ok=True, before=before or {}, after=[dict(ModID=i, Version=v) for i, v in self.enabled] or {},
                         disabled=len(before), enabled=len(self.enabled))]
        if source == mods.ACTIVATE:
            f.add('ModsSinglePlayer')
            self.shown = {'ModsMenu'}
            self.log.append(('activate',))
            self.write_debug_db([(1,), (2,)])
            return ['activated']
        if 'ATTACH DATABASE' in source:
            path = unescape(re.search(r'local path="((?:\\\d{3})*)"', source).group(1)).decode()
            con = sqlite3.connect(path)
            con.execute('CREATE TABLE T(x INTEGER)')
            con.executemany('INSERT INTO T VALUES(?)', self.debug_rows)
            con.commit()
            con.close()
            return [dict(tables=1, failed={}, activated={})]
        if source == session.SHOWN:
            return [context in self.shown]
        if 'PreGame.SetMapScript' in source:
            self.log.append(('pregame', context))
            return [{'map': 'Assets\\Maps\\Continents.lua'}]
        if source == session.START:
            f.remove('ModsSinglePlayer')
            request = self.cfg.cache_dir / 'HeadlessSeeds.txt'
            self.requested = request.read_text(encoding='ascii') if request.exists() else None
            f.add('InGame')
            f.add('LoadScreen')
            self.turn, self.autoplay, self.running, self.winner = 0, 0, False, -1
            with (self.cfg.user_dir / 'Logs/HeadlessDigest.log').open('a', encoding='utf-8') as log:
                log.write(f'HHSEEDS|point=gameInit|source=file|exeMap={self.exe_seeds[0]}|exeSync={self.exe_seeds[1]}\n')
            self.log.append(('start',))
            return ['start requested']
        if 'Events.PlayerChoseToLoadGame' in source:
            path = unescape(re.search(r'local path="((?:\\\d{3})*)"', source).group(1)).decode()
            f.remove('ModsSinglePlayer')
            f.add('InGame')
            f.add('LoadScreen')
            self.turn, self.autoplay, self.running, self.winner = savegame.read_header(path).turn, 0, False, -1
            self.log.append(('load', context, path))
            return ['load requested']
        if source == session.DIGEST_ON:
            self.log.append(('digest on',))
            return [True]
        if 'UI.SaveGame(' in source:
            name = unescape(re.search(r'UI.SaveGame\("((?:\\\d{3})*)"\)', source).group(1)).decode()
            folder = self.cfg.user_dir / 'ModdedSaves/single'
            folder.mkdir(parents=True, exist_ok=True)
            make_save(folder / f'{name}.Civ5Save', turn=self.turn)
            self.log.append(('save', name))
            return ['save requested']
        if source == session.LOADED:
            return [True]
        if source == session.SETUP:
            return [dict(turn=self.turn, world='WORLDSIZE_TINY', speed='GAMESPEED_STANDARD', players={})]
        if 'C5H_TEST_PROBE' in source:
            self.log.append(('probe', 'running' if self.running else 'paused'))
            return ['probe ran']
        if source == session.REPLAY_OWNERS:
            return [[0, 2]]
        if 'GetReplayData' in source:
            owner = int(re.search(r'Players\[(\d+)\]', source).group(1))
            return [{'REPLAYDATASET_SCORE': f'2=9,1={owner}', 'REPLAYDATASET_EMPTY': ''}]
        if 'C5HFP_EVERY' in source:
            self.log.append(('fingerprint settings', source.splitlines()[1:]))
            return [True]
        if 'Game.SetAIAutoPlay(' in source and 'QUICK_COMBAT' in source:
            self.autoplay = int(re.search(r'SetAIAutoPlay\((\d+),0\)', source).group(1))
            self.log.append(('arm', self.autoplay, 'running' if self.running else 'paused'))
            return [self.autoplay]
        if source == session.CONTINUE:
            self.running = True
            self.log.append(('continue',))
            return ['continued']
        if source == session.TURN:
            if self.running and self.autoplay > 0 and self.winner < 0:
                self.turn += 1
                self.autoplay -= 1
                if self.victory_at and self.turn >= self.victory_at:
                    self.winner = 0
            return [dict(turn=self.turn, autoplay=self.autoplay, pause=-1, winner=self.winner, state=2 if self.winner >= 0 else 0)]
        if source == session.VICTORY:
            return [dict(turn=self.turn, winner=self.winner, victory='VICTORY_DOMINATION', state=2)]
        if source == session.HALT:
            self.autoplay = 0
            return [0]
        if source == session.MENU:
            for name in ('InGame', 'LoadScreen'):
                f.remove(name)
            self.running = False
            self.shown = {'MainMenu'}
            self.log.append(('menu',))
            return ['menu requested']
        if source == session.QUIT:
            self.log.append(('quit', context))
            self.processes[-1].kill()
            return ['quit requested']
        raise LuaError(f'the fake game does not know this Lua: {source[:60]}')

    def write_debug_db(self, rows):
        """The game writes cache/Civ5DebugDatabase.db at start and again when mods load."""
        self.debug_rows = rows
        path = self.cfg.cache_dir / 'Civ5DebugDatabase.db'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE T(x INTEGER)')
        con.executemany('INSERT INTO T VALUES(?)', rows)
        con.commit()
        con.close()

    def close(self):
        if self.fake:
            self.fake.close()
        for p in self.processes:
            if p.poll() is None:
                p.kill()
            p.wait()


class SessionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        user = root / 'user'
        (user / 'Logs').mkdir(parents=True)
        (user / 'config.ini').write_bytes(INI.encode())
        db = root / 'game.db'
        make_db(db)
        self.cfg = config.Config(workspace=root / 'ws', game_dir=root / 'game', user_dir=user, config_ini=user / 'config.ini',
                                 cache_dir=user / 'cache', mods_dir=user / 'MODS', db=db,
                                 active_mods=[config.Mod('11111111-2222-3333-4444-555555555555', 7, 'Active')])
        self.spec = specmod.resolve({'map': {'script': 'Continents.lua', 'size': 'WORLDSIZE_TINY'},
                                     'seeds': {'map': 101, 'sync': 202}}, db)
        self.probe = root / 'probe.lua'
        self.probe.write_text('-- C5H_TEST_PROBE\nreturn 1\n', encoding='utf-8')
        self.game = FakeGame(self.cfg)

    def tearDown(self):
        self.game.close()
        self.tmp.cleanup()

    def session(self, **options):
        return session.RealGameSession(self.cfg, Path(self.tmp.name) / 'run', launcher=self.game.launcher, cpu_guard=False,
                                       poll=0.01, load_settle=0.05, **options)


class SessionTest(SessionCase):
    def test_full_session(self):
        s = self.session()
        info = s.launch(101, 202)
        self.assertEqual(info['seeds'], {'map': 101, 'sync': 202})
        self.assertEqual(seeds.read(self.cfg.config_ini), {'MapRandSeed': 101, 'SyncRandSeed': 202})
        self.assertNotIn('LegalScreen', s.states())
        m = s.set_mods(['11111111-2222-3333-4444-555555555555'])
        self.assertEqual(m['mods'], [dict(id='11111111-2222-3333-4444-555555555555', version=7, name='Active')])

        started = s.start_game(self.spec, 101, 202, lua_probes=[self.probe])
        self.assertFalse(started['relaunched'])
        # the digest DLL was asked for per-turn digests at the start; the request is gone afterwards
        self.assertEqual(self.game.requested, 'digest=1\nnormalise=1\n')
        self.assertFalse((self.cfg.cache_dir / 'HeadlessSeeds.txt').exists())
        self.assertTrue(started['seedsVerified'])
        self.assertEqual(started['probes'][0]['value'], 'probe ran')
        played = s.autoplay(3)
        self.assertEqual((played['startTurn'], played['endTurn'], played['endReason']), (0, 3, 'turns'))
        self.assertEqual(sorted(played['turnSeconds']), ['1', '2', '3'])
        # the host's order: probes at turn 0 while paused, then arm, then continue
        order = [e for e in self.game.log if e[0] in ('probe', 'arm', 'continue')]
        self.assertEqual(order, [('probe', 'paused'), ('arm', 3, 'paused'), ('continue',)])
        self.assertEqual(s.replay_data(), {'0': {'REPLAYDATASET_SCORE': [[1, 0], [2, 9]], 'REPLAYDATASET_EMPTY': []},
                                           '2': {'REPLAYDATASET_SCORE': [[1, 2], [2, 9]], 'REPLAYDATASET_EMPTY': []}})
        self.assertIn(session.MAJORS, run.FINAL)  # the same players as the host's replay-data.json

        s.to_main_menu()
        # other seeds: config.ini first, then a relaunch so the DLL reads them; the mods are activated again
        again = s.start_game(dict(self.spec, seeds={'map': 303, 'sync': 404}))
        self.assertTrue(again['relaunched'])
        self.assertTrue(again['seedsVerified'])
        self.assertEqual(again['seeds'], {'map': 303, 'sync': 404})
        self.assertEqual([e for e in self.game.log if e[0] == 'launch'], [('launch', (101, 202)), ('launch', (303, 404))])
        self.assertEqual(self.game.log[-4:], [('mods', ['11111111-2222-3333-4444-555555555555']), ('activate',),
                                              ('pregame', 'ModsSinglePlayer'), ('start',)])
        self.game.victory_at = 2
        self.assertEqual(s.autoplay(10)['endReason'], 'victory')

        logs = s.collect_logs(Path(self.tmp.name) / 'logs')
        self.assertEqual(sorted(logs), ['HeadlessDigest.log', 'Lua.log'])
        stats = s.process_stats()
        self.assertGreater(stats['peakWorkingSet'], 0)
        self.assertEqual(stats['launches'], 2)
        self.assertEqual(len(stats['earlier']), 1)
        result = s.quit()
        self.assertTrue(result['exited'])
        self.assertEqual(result['configIniRestored'], {'MapRandSeed': 0, 'SyncRandSeed': 0})
        self.assertEqual(self.cfg.config_ini.read_bytes(), INI.encode())
        self.assertIn(('quit', 'InGame'), self.game.log)
        events = [json.loads(line)['action'] for line in (Path(self.tmp.name) / 'run/session.jsonl').read_text().splitlines()]
        self.assertEqual(events[0], 'launched')
        self.assertEqual(events[-1], 'quit')
        self.assertEqual(s.quit()['exited'], True)  # twice is harmless

    def test_load_and_save(self):
        s = self.session()
        with self.assertRaises(session.SessionError):
            s.load_game(make_save(Path(self.tmp.name) / 'a.Civ5Save'))  # launch() first
        s.launch(101, 202)
        save = make_save(Path(self.tmp.name) / 'Turn 4.Civ5Save', turn=4,
                         mods=[('11111111-2222-3333-4444-555555555555', 7, 'Active')])
        loaded = s.load_game(save, lua_probes=[self.probe])
        self.assertEqual((loaded['turn'], loaded['setup']['turn'], loaded['probes'][0]['value']), (4, 4, 'probe ran'))
        # the save's mods activated (no set_mods given), then the load from the Mods menu's single-player screen
        self.assertEqual([e for e in self.game.log if e[0] in ('mods', 'load', 'activate', 'launch')],
                         [('launch', (101, 202)), ('mods', ['11111111-2222-3333-4444-555555555555']), ('activate',),
                          ('load', 'ModsSinglePlayer', save.as_posix())])
        self.assertFalse((self.cfg.cache_dir / 'HeadlessSeeds.txt').exists())
        self.assertIn(('digest on',), self.game.log)
        played = s.autoplay(2)
        self.assertEqual((played['startTurn'], played['endTurn']), (4, 6))
        saved = s.save_game('After 6')
        # moved from the game's folder into the run folder
        self.assertEqual(Path(saved['path']), Path(self.tmp.name) / 'run/saves/After 6.Civ5Save')
        self.assertFalse((self.cfg.user_dir / 'ModdedSaves/single/After 6.Civ5Save').exists())
        self.assertEqual(savegame.read_header(saved['path']).turn, 6)
        with self.assertRaises(session.SessionError):
            s.load_game(save)  # a game is running
        # after a game the mods are activated again before the next load
        s.to_main_menu()
        s.load_game(saved['path'])
        self.assertEqual([e[0] for e in self.game.log if e[0] in ('load', 'activate', 'launch')][-3:],
                         ['load', 'activate', 'load'])
        s.quit()

    def test_fingerprint_probe_files(self):
        self.cfg.probes = Path(self.tmp.name) / 'probes'
        self.cfg.probes.mkdir()
        (self.cfg.probes / 'fingerprint.lua').write_text('-- C5H_TEST_PROBE fingerprint\n', encoding='utf-8')
        with self.session() as s:
            s.launch(101, 202)
            s.set_mods([])
            started = s.start_game(self.spec, lua_probes=[self.probe], fingerprint=dict(every=5, turns=[1, 2]))
        self.assertEqual([Path(p['file']).name for p in started['probes']], ['fingerprint-settings.lua', 'fingerprint.lua', 'probe.lua'])
        self.assertTrue(all(p['ok'] for p in started['probes']), started['probes'])
        self.assertIn(('fingerprint settings', ['C5HFP_EVERY = 5', 'C5HFP_TURNS = {1, 2}']), self.game.log)

    def test_first_game_relaunches_without_launch_seeds(self):
        with self.session() as s:
            s.launch()
            s.set_mods([])
            self.assertTrue(s.start_game(self.spec)['relaunched'])
            self.assertEqual(self.game.log[-3][0], 'activate')
        self.assertEqual(seeds.read(self.cfg.config_ini), {'MapRandSeed': 0, 'SyncRandSeed': 0})

    def test_reload_mode_detects_seeds_not_taken(self):
        # the fake reads config.ini only at launch, like the verified game behaviour
        with self.session(reseed='reload') as s:
            s.launch()
            s.set_mods([])
            with self.assertRaisesRegex(session.SessionError, 'did not use the pinned seeds'):
                s.start_game(self.spec)

    def test_leftover_pin_restored_and_stop_reasons(self):
        seeds.pin(self.cfg.config_ini, 9, 9, self.cfg.workspace / 'cache' / seeds.MARKER)  # a session that died
        with self.session() as s:
            s.launch(101, 202)
            s.set_mods([])
            s.start_game(self.spec)
            result = s.autoplay(50, on_poll=lambda state: state['turn'] >= 2)
            self.assertEqual((result['endReason'], result['endTurn']), ('stopped', 2))
            self.assertEqual(self.game.autoplay, 0)  # halted
            self.assertEqual(s.autoplay(5000, max_seconds=0.05)['endReason'], 'max_seconds')
        self.assertEqual(self.cfg.config_ini.read_bytes(), INI.encode())

    def test_errors(self):
        s = self.session()
        self.assertTrue(s.quit()['exited'])  # never launched
        with self.assertRaises(session.SessionError):
            s.start_game(self.spec)
        self.cfg.config_ini.write_bytes(INI.replace('EnableTuner = 1', 'EnableTuner = 0').encode())
        with self.assertRaisesRegex(session.SessionError, 'EnableTuner'):
            s.launch()
        self.cfg.config_ini.write_bytes(INI.encode())
        s.launch()
        s.set_mods([])
        self.game.processes[-1].kill()
        self.game.processes[-1].wait()
        with self.assertRaisesRegex(session.SessionError, 'exited'):
            s.start_game(dict(self.spec, seeds={}), None, None)
        s.quit()


class ToolsTest(SessionCase):
    """probe.py and dbcapture.py on the session, against the fake game."""

    def patched(self, module):
        fake = self.game
        return mock.patch.object(module, 'RealGameSession', lambda cfg, run, **kw: session.RealGameSession(
            cfg, run, launcher=fake.launcher, cpu_guard=False, poll=0.01, log=kw.get('log')))

    def test_probe(self):
        root = Path(self.tmp.name)
        self.cfg.runs = root / 'runs'
        spec_file = root / 'spec.json'
        spec_file.write_text(json.dumps({'map': {'script': 'Continents.lua', 'size': 'WORLDSIZE_TINY'},
                                         'seeds': {'map': 11, 'sync': 22}}), encoding='utf-8')
        with self.patched(probe), mock.patch('sys.stdout'):
            code = probe.main(['--run', 'p1', '--spec', str(spec_file), '--turns', '3', '--probe', f'MainMenu={self.probe}',
                               '--probe', f'InGame@start={self.probe}', '--probe', f'InGame@end={self.probe}'], self.cfg)
        run = self.cfg.runs / 'p1'
        self.assertEqual(code, 0, (run / 'error.json').read_text() if (run / 'error.json').exists() else '')
        self.assertEqual([json.loads((run / f'probe-{n}-probe.json').read_text())['when'] for n in (1, 2, 3)],
                         ['setup', 'start', 'end'])
        self.assertEqual(json.loads((run / 'autoplay.json').read_text())['endTurn'], 3)
        self.assertTrue(json.loads((run / 'setup.json').read_text())['seedsVerified'])
        self.assertEqual(self.game.log[0], ('launch', (11, 22)))
        for name in ('victory.json', 'process.json', 'Lua.log', 'HeadlessDigest.log'):
            self.assertTrue((run / name).is_file(), name)
        self.assertFalse((self.cfg.cache_dir / 'HeadlessSeeds.txt').exists())
        self.assertEqual(self.cfg.config_ini.read_bytes(), INI.encode())

    def test_dbcapture(self):
        run = Path(self.tmp.name) / 'capture'
        s = session.RealGameSession(self.cfg, run, launcher=self.game.launcher, cpu_guard=False, poll=0.01)
        result = dbcapture.capture(self.cfg, run, session=s, settle=0.05)
        self.assertNotIn('error', result)
        self.assertEqual(result['mods'][0]['version'], 7)
        for phase in ('dlc', 'mods'):
            self.assertEqual(result[phase + 'Compare']['differing'], [])
        con = sqlite3.connect(run / 'mods-debug.db')
        self.assertEqual(con.execute('select count(*) from T').fetchone()[0], 2)
        con.close()
        self.assertTrue(result['quit']['exited'])


if __name__ == '__main__':
    unittest.main()
