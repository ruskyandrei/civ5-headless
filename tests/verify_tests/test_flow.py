"""c5h verify end to end without the game: fake_host.cmd stands in for civ5headless.exe and FakeRealGameSession for
the real game, so the quick, full and host-only flows run, decide and write their report."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

from civ5headless import config
from civ5headless import run as host_run
from civ5headless.verify import capabilities, settings as settings_mod, suite

from fake_session import FakeRealGameSession
import fakegame

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


class FakeHazards:
    """civ5headless.hazards' scan_report as the check uses it: hits with reach and covered."""
    @staticmethod
    def scan_report(report_path):
        hit = dict(file='X.lua', line=1, call='Network.SendX', kind='writes', function='f', why='writes', triggers=[])
        return [dict(hit, reach='game', covered=False), dict(hit, reach='load', covered=False),
                dict(hit, reach='input', covered=False), dict(hit, reach='input', covered=False),
                dict(hit, reach='game', covered=True, coveredBy='profile')]

SCHEMA = [
    'CREATE TABLE Worlds(ID INTEGER, Type TEXT, DefaultPlayers INTEGER, DefaultMinorCivs INTEGER)',
    'CREATE TABLE MapScripts(FileName TEXT, SupportsSinglePlayer INTEGER, Hidden INTEGER)',
    'CREATE TABLE MapScriptOptions(FileName TEXT, OptionID TEXT, Name TEXT)',
    'CREATE TABLE MapScriptOptionPossibleValues(FileName TEXT, OptionID TEXT, Value INTEGER, Name TEXT)',
    'CREATE TABLE Map_Sizes(FileName TEXT, MapType TEXT, WorldSizeType TEXT)',
    'CREATE TABLE GameSpeeds(ID INTEGER, Type TEXT)',
    'CREATE TABLE Eras(ID INTEGER, Type TEXT)',
    'CREATE TABLE HandicapInfos(ID INTEGER, Type TEXT)',
    'CREATE TABLE Victories(ID INTEGER, Type TEXT)',
    'CREATE TABLE GameOptions(ID INTEGER, Type TEXT, SupportsSinglePlayer INTEGER)',
    'CREATE TABLE Civilizations(ID INTEGER, Type TEXT, Playable INTEGER)',
    'CREATE TABLE MinorCivilizations(ID INTEGER, Type TEXT)',
]
ROWS = {
    'Worlds': [(0, 'WORLDSIZE_DUEL', 2, 4), (1, 'WORLDSIZE_TINY', 4, 8), (2, 'WORLDSIZE_SMALL', 6, 12), (3, 'WORLDSIZE_STANDARD', 8, 16)],
    'MapScripts': [('Assets\\Maps\\Continents.lua', 1, 0), ('Assets\\Maps\\Pangaea.lua', 1, 0)],
    'MapScriptOptions': [], 'MapScriptOptionPossibleValues': [],
    'Map_Sizes': [('Assets\\Maps\\Earth_Small.Civ5Map', 'MAP_EARTH', 'WORLDSIZE_SMALL')],
    'GameSpeeds': [(2, 'GAMESPEED_STANDARD'), (3, 'GAMESPEED_QUICK')],
    'Eras': [(0, 'ERA_ANCIENT')],
    'HandicapInfos': [(5, 'HANDICAP_EMPEROR')],
    'Victories': [(0, 'VICTORY_TIME'), (2, 'VICTORY_DOMINATION')],
    'GameOptions': [(0, 'GAMEOPTION_RAGING_BARBARIANS', 1)],
    'Civilizations': [(0, 'CIVILIZATION_AMERICA', 1), (1, 'CIVILIZATION_GREECE', 1), (2, 'CIVILIZATION_EGYPT', 1),
                      (3, 'CIVILIZATION_JAPAN', 1), (4, 'CIVILIZATION_SWEDEN', 1), (5, 'CIVILIZATION_CHINA', 1),
                      (6, 'CIVILIZATION_BARBARIAN', 0)],
    'MinorCivilizations': [(i, t) for i, t in enumerate(fakegame.MINOR_POOL)],
}


def make_db(path):
    con = sqlite3.connect(path)
    for stmt in SCHEMA:
        con.execute(stmt)
    for table, rows in ROWS.items():
        if rows:
            con.executemany(f'INSERT INTO {table} VALUES({",".join("?" * len(rows[0]))})', rows)
    con.commit()
    con.close()


SMALL = dict(turns=12, ensemble=10, workers=4, reference_workers=2, min_members=6, sync_probe_turns=5, stats_from_turn=1,
             setups=[{'name': 'continents', 'map': {'script': 'Assets/Maps/Continents.lua', 'size': 'WORLDSIZE_TINY'},
                      'seeds': {'map': 11, 'sync': 12}},
                     {'name': 'earth', 'map': {'civ5map': 'Assets/Maps/Earth_Small.Civ5Map'}, 'seeds': {'map': 13, 'sync': 14}}],
             standard_spec={'map': {'script': 'Assets/Maps/Pangaea.lua', 'size': 'WORLDSIZE_STANDARD'}, 'seeds': {'map': 5, 'sync': 6}},
             affinity='none', reserve_seconds=5)


def _no_save(suite_, phase):
    """Suite.decide_reseed, with the game that saves having left no save."""
    for j in phase.jobs:
        if j.role == 'reseed-save':
            j.status = dict(j.status, saves=[])
    _DECIDE_RESEED(suite_, phase)


_DECIDE_RESEED = suite.Suite.decide_reseed


@unittest.skipUnless(os.name == 'nt', 'Windows only (job objects)')
class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = self.root = Path(self.tmp.name)
        db = tmp / 'game.db'
        make_db(db)
        (tmp / 'game').mkdir()
        for name in ('mods.db', 'loc.db', 'game.dll'):
            (tmp / name).write_bytes(b'x')
        self.cfg = config.Config(game_dir=tmp / 'game', mods_dir=tmp / 'MODS', mods_db=tmp / 'mods.db', loc_db=tmp / 'loc.db',
                                 dll=tmp / 'game.dll', exe=HERE / 'fake_host.cmd', db=db, runs=tmp / 'runs', db_cache=tmp / 'cache',
                                 probes=REPO / 'probes')
        self.env = mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable, 'C5H_FAKE_LUA_PROBE': '1'})
        self.env.start()
        for k in [k for k in os.environ if k.startswith('C5H_FAKE_') and k != 'C5H_FAKE_LUA_PROBE']:
            del os.environ[k]
        FakeRealGameSession.sync_matters = False
        FakeRealGameSession.diverge_at = None
        FakeRealGameSession.transient_at = None
        FakeRealGameSession.scale = 1.0
        FakeRealGameSession.fail_in = None
        FakeRealGameSession.with_extras = True
        FakeRealGameSession.exe_chooses = False
        FakeRealGameSession.instances = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_suite(self, mode, caps=None, **over):
        s = settings_mod.build(mode, dict(SMALL, **over.pop('table', {})), over)
        overrides = dict(session_class=FakeRealGameSession, hazards=None)
        overrides.update(caps or {})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            doc = suite.run(self.cfg, s, out_dir=self.root / f'out-{mode}-{len(list(self.root.glob("out-*")))}',
                            caps_overrides=overrides, log=lambda m: None)
        self.checks = {c['id']: c for c in doc['checks']}
        return doc

    def status(self, cid):
        return self.checks[cid]['status']

    def test_quick_pass(self):
        doc = self.run_suite('quick')
        self.assertEqual(doc['verdict'], 'pass', [c for c in doc['checks'] if c['status'] == 'fail'])
        self.assertEqual(doc['exitCode'], 0)
        for cid in ('setup.turn0', 'setup.pregame', 'setup.database', 'trajectory.aa', 'trajectory.frame-skew',
                    'trajectory.heap-perturb', 'trajectory.probe-neutral', 'trajectory.real', 'statistics.envelope'):
            self.assertEqual(self.status(cid), 'pass', (cid, self.checks[cid]['summary']))
        self.assertIn('matches the real game for 12 of 12 turns', doc['headline'])
        self.assertEqual(self.checks['trajectory.real']['details']['method'], 'digest')
        out = Path(doc['run'])
        html = (out / 'report.html').read_text(encoding='utf-8')
        self.assertIn('<svg', html)
        self.assertNotIn('<script', html)
        self.assertNotIn('http', html.split('</style>')[0])
        self.assertEqual(json.loads((out / 'verify.json').read_text(encoding='utf-8'))['verdict'], 'pass')
        # the host games read a copy of the localization database taken before the real game started (it rebuilds it)
        copy = out / 'loc' / 'loc.db'
        self.assertTrue(copy.is_file())
        command = json.loads(next((out / 'host').glob('*/hh-command.json')).read_text(encoding='utf-8'))['command']
        self.assertEqual(command[command.index('--loc-db') + 1], str(copy.resolve()))
        # the real side: one session, segments per game, the probes injected
        s = FakeRealGameSession.instances[0]
        self.assertEqual(s.calls.count('launch'), 1)
        self.assertEqual(s.calls.count('start_game'), 3)  # 2 setups and the trajectory game
        seg = (out / 'real/trajectory/Lua.segment.log').read_text(encoding='utf-8')
        setup = (out / 'real/setup-continents/Lua.segment.log').read_text(encoding='utf-8')
        self.assertIn('C5HFP|v=1|turn=11|', seg)
        self.assertNotIn('C5HFP|v=1|turn=11|', setup)
        self.assertIn('C5HFP|v=1|turn=0|', setup)
        self.assertEqual(seg.count('C5HFP|v=1|meta'), 1)
        # the trajectory game first (the launch seeds), then one relaunch per other seed pair
        self.assertEqual([g['name'] for g in doc['real']['games']], ['trajectory', 'setup-continents', 'setup-earth'])
        self.assertEqual(s.launches, 3)
        self.assertEqual(self.checks['trajectory.real']['details']['method'], 'digest')
        # random major civilizations are named
        spec = json.loads((out / 'specs/trajectory.json').read_text(encoding='utf-8'))
        self.assertEqual([x['civ'] for x in spec['slots'][:4]], ['CIVILIZATION_GREECE', 'CIVILIZATION_EGYPT',
                                                                 'CIVILIZATION_JAPAN', 'CIVILIZATION_SWEDEN'])
        self.assertEqual(self.status('setup.civ-choice'), 'n/a')  # no host-report.json from this fake host run
        # the fingerprint probe through run.play and start_game; the compared host games got the real city-states
        plan = json.loads((out / 'plan.json').read_text(encoding='utf-8'))
        self.assertTrue(plan['pinning'])
        self.assertEqual([p['name'] for p in plan['phases']], ['probe', 'ensemble', 'setups', 'single', 'references'])
        jobs = {j['name']: j for j in doc['jobs']}
        real = {g['name']: g for g in doc['real']['games']}
        self.assertEqual(jobs['trajectory']['pinned'], real['trajectory']['minorCivs']['types'])
        self.assertEqual(jobs['trajectory-aa']['pinned'], real['trajectory']['minorCivs']['types'])
        self.assertEqual(jobs['setup-earth-b']['pinned'], real['setup-earth']['minorCivs']['types'])
        self.assertIsNone(jobs['ensemble-01']['pinned'])
        self.assertIn('C5HFP|v=1|player|', (out / 'host/trajectory/Logs/Lua.log').read_text(encoding='utf-8'))
        self.assertIn('replay_data', s.calls)
        # the ensemble varied the map seed (the fake game ignores the sync seed)
        self.assertEqual(self.checks['statistics.ensemble']['details']['vary'], 'map')
        # history: one line; a second analysis does not add another
        hist = list((self.root / 'runs/verify-history').glob('*.jsonl'))
        self.assertEqual(len(hist), 1)
        suite.analyse(out, self.cfg, log=lambda m: None)
        self.assertEqual(len(hist[0].read_text(encoding='utf-8').splitlines()), 1)

    def reseed_suite(self, **env):
        with mock.patch.dict(os.environ, dict(C5H_FAKE_RESEED='1', **env)), mock.patch.object(host_run, 'host_supports', return_value=True):
            return self.run_suite('host-only', no_mods=True, dll=str(self.root / 'game.dll'), ensemble=0, setups=[], reseed_save_turn=4, reseed_turns=4)

    def test_reseed_reference(self):
        """A game saves at turn 4, its save plays on with seeds that differ in one seed each: the check says which matter."""
        doc = self.reseed_suite()
        c = self.checks['trajectory.reseed']
        self.assertEqual(c['status'], 'pass', c['summary'])
        self.assertEqual(c['details']['responds'], ['map'])  # the fake game, as VP, ignores the sync seed
        self.assertIn('respond to the map seed only', c['summary'])
        self.assertEqual(c['details']['matchedTurns'], 4)
        jobs = {j['name']: j for j in doc['jobs']}
        self.assertEqual(jobs['reseed-a']['seeds'], [81001, 82001])
        self.assertEqual(jobs['reseed-map']['seeds'], [81002, 82001])
        self.assertEqual(jobs['reseed-sync']['seeds'], [81001, 82002])
        status = json.loads((Path(doc['run']) / 'host/reseed-a/status.json').read_text(encoding='utf-8'))
        self.assertEqual((status['startTurn'], status['endTurn'], status['reseed']), (4, 8, {'map': 81001, 'sync': 82001}))
        self.assertIn('Turn_0004', status['loadedFrom'])

    def test_reseed_both_seeds_and_no_repeat(self):
        self.reseed_suite(C5H_FAKE_SYNC_MATTERS='1')
        self.assertEqual(self.checks['trajectory.reseed']['details']['responds'], ['map', 'sync'])
        self.reseed_suite(C5H_FAKE_NONDETERMINISTIC='5')
        c = self.checks['trajectory.reseed']
        self.assertEqual(c['status'], 'warn')
        self.assertIn('the same seeds differ from turn 5', c['summary'])

    def test_reseed_needs_the_host_option(self):
        self.run_suite('host-only', no_mods=True, dll=str(self.root / 'game.dll'), ensemble=0, setups=[])
        self.assertEqual(self.status('trajectory.reseed'), 'n/a')
        self.assertIn('no --reseed', self.checks['trajectory.reseed']['summary'])

    def test_reseed_without_a_save(self):
        """The game that saves left no save: the reseed games are skipped, and the check says so."""
        with mock.patch.dict(os.environ, dict(C5H_FAKE_RESEED='1')), mock.patch.object(host_run, 'host_supports', return_value=True), \
                mock.patch.object(suite.Suite, 'decide_reseed', autospec=True, side_effect=lambda self_, phase: _no_save(self_, phase)):
            self.run_suite('host-only', no_mods=True, dll=str(self.root / 'game.dll'), ensemble=0, setups=[], reseed_save_turn=4, reseed_turns=4)
        self.assertEqual(self.status('trajectory.reseed'), 'skipped')
        self.assertIn('left no save', self.checks['trajectory.reseed']['summary'])

    def host_logging(self, doc_out):
        """{(aiLogging, gameLogFlags)} of the host games of a suite run."""
        status = [json.loads(p.read_text(encoding='utf-8')) for p in (doc_out / 'host').glob('*/status.json')]
        self.assertTrue(status)
        return {(s['aiLogging'], s['gameLogFlags']) for s in status}

    def test_host_games_log_as_the_real_game_does(self):
        """Whatever ai_logging says: a real game and a host game with the same seed need the same setting."""
        ini = self.root / 'config.ini'
        ini.write_text('[Debug]\nLoggingEnabled = 1\nAILog = 0\nBuilderAILog = 1\n', encoding='utf-8')
        self.cfg.config_ini = ini
        self.cfg.ai_logging = 'on'
        self.run_suite('quick')
        out = max(self.root.glob('out-quick-*'))
        self.assertEqual(self.host_logging(out), {(False, 4)})
        plan = json.loads((out / 'plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['hostLogArgs'], ['--game-log-flags', '4'])
        self.assertEqual(plan['notes'], [])
        # host_args still wins, with a note
        self.run_suite('quick', host_args=['--game-log-flags', '15'])
        out = max(self.root.glob('out-quick-*'))
        self.assertEqual(self.host_logging(out), {(True, 15)})
        self.assertIn('host_args sets the logging', ' '.join(json.loads((out / 'plan.json').read_text(encoding='utf-8'))['notes']))

    def test_host_only_follows_ai_logging(self):
        ini = self.root / 'config.ini'
        ini.write_text('[Debug]\nLoggingEnabled = 1\nAILog = 1\n', encoding='utf-8')  # no real game: not read
        self.cfg.config_ini = ini
        with mock.patch.object(suite.host_run, 'host_supports', return_value=True):
            self.run_suite('host-only')
            self.assertEqual(self.host_logging(max(self.root.glob('out-host-only-*'))), {(False, 0)})
            self.cfg.ai_logging = 'on'
            self.run_suite('host-only')
            self.assertEqual(self.host_logging(max(self.root.glob('out-host-only-*'))), {(True, 15)})

    def test_real_divergence_fails(self):
        FakeRealGameSession.diverge_at = 7
        doc = self.run_suite('quick')
        self.assertEqual(self.status('trajectory.real'), 'fail')
        self.assertEqual(self.checks['trajectory.real']['details']['matchedTurns'], 7)
        self.assertEqual(doc['exitCode'], 1)
        self.assertIn('matches the real game for 7 of 12 turns', doc['headline'])

    def test_transient_is_reported_not_failed(self):
        FakeRealGameSession.transient_at = 5
        doc = self.run_suite('quick')
        self.assertEqual(self.status('trajectory.real'), 'pass')
        self.assertEqual(len(self.checks['trajectory.real']['details']['transients']), 1)
        self.assertEqual(doc['exitCode'], 0)

    def test_nondeterministic_dll_sets_the_bar(self):
        # every run differs from turn 4: the A/A warns, and the real game matching for 4 turns passes
        os.environ['C5H_FAKE_NONDETERMINISTIC'] = '4'
        FakeRealGameSession.diverge_at = 4
        doc = self.run_suite('quick')
        self.assertEqual(self.status('trajectory.aa'), 'warn')
        self.assertEqual(self.status('trajectory.real'), 'pass', self.checks['trajectory.real']['summary'])
        self.assertEqual(self.checks['trajectory.real']['details']['reproducible'], 4)
        self.assertNotEqual(doc['exitCode'], 3)

    def test_frame_skew_and_probe_dependence(self):
        os.environ['C5H_FAKE_SKEW_DIVERGE'] = '6'
        os.environ['C5H_FAKE_PROBE_DIVERGE'] = '3'
        self.run_suite('host-only')
        self.assertEqual(self.status('trajectory.frame-skew'), 'warn')
        self.assertEqual(self.status('trajectory.probe-neutral'), 'fail')
        self.assertEqual(self.status('trajectory.heap-perturb'), 'pass')

    def test_full_mode(self):
        os.environ['C5H_FAKE_VICTORY'] = '20'
        doc = self.run_suite('full', ensemble=8, victory_games=2, victory_max_turns=40, standard_turns=6)
        self.assertEqual(self.status('trajectory.real-aa'), 'pass')
        self.assertEqual(self.status('trajectory.standard'), 'pass')
        self.assertEqual(self.status('performance.standard'), 'info')
        self.assertEqual(self.status('health.victory-games'), 'pass', self.checks['health.victory-games']['summary'])
        self.assertEqual(doc['verdict'], 'pass', [c['summary'] for c in doc['checks'] if c['status'] == 'fail'])
        names = [g['name'] for g in doc['real']['games']]
        self.assertEqual(names, ['trajectory', 'trajectory-aa', 'setup-continents', 'setup-earth', 'standard'])  # by seeds

    def test_host_only(self):
        os.environ['C5H_FAKE_SYNC_MATTERS'] = '1'
        os.environ['C5H_FAKE_HOST_REPORT'] = '1'
        doc = self.run_suite('host-only')
        self.assertEqual(doc['mode'], 'host-only')
        self.assertTrue(doc['headline'].startswith('Host only'))
        for cid in ('setup.pregame', 'setup.database', 'trajectory.real'):
            self.assertEqual(self.status(cid), 'n/a')
        self.assertEqual(self.checks['statistics.ensemble']['details']['vary'], 'sync')
        self.assertEqual(self.status('health.stubs'), 'fail')  # host-report.json names one unknown stub
        self.assertEqual(self.checks['health.stubs']['details']['unknown'][0]['method'], 'ICvEngineUtility1::Unknown')
        self.assertEqual(doc['exitCode'], 1)
        tp = {r['workers'] for r in doc['performance']['throughput']}
        self.assertEqual(tp, {1, 2, 4})

    def test_stock_dll_gets_the_real_city_states(self):
        # the DLL leaves city-states to the exe: the real game's are a draw the host cannot make, so the compared
        # host games take them from the real game's turn-0 fingerprint
        os.environ['C5H_FAKE_HOST_REPORT'] = '1'
        os.environ['C5H_FAKE_HOST_CHOSE_CIVS'] = '1'
        FakeRealGameSession.exe_chooses = True
        doc = self.run_suite('quick')
        self.assertEqual(self.status('setup.civ-choice'), 'pass', self.checks['setup.civ-choice']['summary'])
        self.assertIn('the host applied them', self.checks['setup.civ-choice']['summary'])
        self.assertIn('other game(s)', self.checks['setup.civ-choice']['summary'])  # the unpinned ensemble
        for cid in ('setup.turn0', 'trajectory.real', 'trajectory.aa'):
            self.assertEqual(self.status(cid), 'pass', (cid, self.checks[cid]['summary']))
        failing = [c['id'] for c in doc['checks'] if c['status'] == 'fail']
        self.assertEqual(failing, ['health.stubs'])  # the fake host report names one unknown stub

    def test_stock_dll_without_pins_fails(self):
        os.environ['C5H_FAKE_HOST_REPORT'] = '1'
        os.environ['C5H_FAKE_HOST_CHOSE_CIVS'] = '1'
        FakeRealGameSession.exe_chooses = True
        doc = self.run_suite('quick', pin_minor_civs=False)
        self.assertEqual(self.status('setup.civ-choice'), 'fail')
        self.assertIn('drew', self.checks['setup.civ-choice']['summary'])
        self.assertEqual(self.status('setup.turn0'), 'fail')
        self.assertEqual(doc['real']['games'][0].get('minorCivs'), None)

    def test_dll_choice_other_than_the_real_game(self):
        # the DLL chooses city-states itself, but not the ones the real game has: pins compared, not applied
        os.environ['C5H_FAKE_HOST_REPORT'] = '1'
        FakeRealGameSession.exe_chooses = True
        self.run_suite('quick')
        self.assertEqual(self.status('setup.civ-choice'), 'fail')
        self.assertIn('other city-states than the real game', self.checks['setup.civ-choice']['summary'])

    def test_host_only_names_city_states(self):
        os.environ['C5H_FAKE_HOST_REPORT'] = '1'
        os.environ['C5H_FAKE_HOST_CHOSE_CIVS'] = '1'
        doc = self.run_suite('host-only')
        self.assertEqual(self.status('setup.civ-choice'), 'pass', self.checks['setup.civ-choice']['summary'])
        spec = json.loads((Path(doc['run']) / 'specs/trajectory.json').read_text(encoding='utf-8'))
        self.assertEqual([m['type'] for m in spec['minorCivs']][:2], ['MINOR_CIV_GENEVA', 'MINOR_CIV_BUDAPEST'])
        self.run_suite('host-only', pin_minor_civs=False)
        self.assertEqual(self.status('setup.civ-choice'), 'warn')

    def test_dll_without_digests(self):
        # the fingerprint compares; the probe-off reference takes sparse fingerprints against the dense ones
        os.environ['C5H_FAKE_NO_DIGEST'] = '1'
        os.environ['C5H_FAKE_SYNC_MATTERS'] = '1'
        doc = self.run_suite('host-only', sparse_every=4)
        self.assertEqual(self.status('trajectory.aa'), 'pass')
        self.assertEqual(self.checks['trajectory.aa']['details']['method'], 'fingerprint')
        self.assertIn('12 of 12 turns', self.checks['trajectory.aa']['summary'])  # 13 states, 12 turns
        self.assertEqual(self.status('trajectory.probe-neutral'), 'pass', self.checks['trajectory.probe-neutral']['summary'])
        self.assertIn('dense and sparse fingerprints (every 4 turns) agree at 4 sampled turns',
                      self.checks['trajectory.probe-neutral']['summary'])
        self.assertIn('12 (A/A)', doc['headline'])
        self.assertEqual(self.checks['statistics.ensemble']['details']['vary'], 'sync')
        os.environ['C5H_FAKE_PROBE_DIVERGE'] = '3'
        self.run_suite('host-only', sparse_every=4)
        self.assertEqual(self.status('trajectory.probe-neutral'), 'fail')
        self.assertIn('per-turn fingerprint probe changes the game', self.checks['trajectory.probe-neutral']['summary'])

    def test_ui_hazards_by_reach(self):
        os.environ['C5H_FAKE_HOST_REPORT'] = '1'
        self.run_suite('host-only', caps=dict(hazards=FakeHazards))
        c = self.checks['health.ui-hazards']
        self.assertEqual(c['status'], 'warn')
        self.assertEqual((c['details']['gameReachable'], c['details']['inputOnly']), (2, 2))
        self.assertIn('2 more reached only through player input', c['summary'])

    def test_shared_seeds_need_one_launch(self):
        setups = [dict(x) for x in SMALL['setups']]
        for x in setups:
            x.pop('seeds')
        doc = self.run_suite('quick', table=dict(setups=setups))
        self.assertEqual(FakeRealGameSession.instances[0].launches, 1)
        self.assertEqual(self.status('setup.turn0'), 'pass')
        self.assertEqual(doc['real']['games'][0]['name'], 'setup-continents')

    def test_lua_errors_only_in_the_host(self):
        os.environ['C5H_FAKE_LUA_ERROR'] = '1'
        self.run_suite('quick')
        self.assertEqual(self.status('health.lua-errors'), 'fail')
        self.assertIn('Thing.lua:12', self.checks['health.lua-errors']['details']['hostOnly'][0]['message'])

    def test_biased_real_game_is_flagged(self):
        FakeRealGameSession.scale = 1.6
        FakeRealGameSession.diverge_at = 1
        self.run_suite('quick', ensemble=16)
        self.assertIn(self.status('statistics.envelope'), ('warn', 'fail'))
        self.assertEqual(self.status('statistics.bias'), 'warn', self.checks['statistics.bias']['summary'])

    def test_no_session_is_incomplete(self):
        doc = self.run_suite('quick', caps=dict(session_class=None))
        self.assertEqual(doc['verdict'], 'incomplete')
        self.assertEqual(doc['exitCode'], 3)
        self.assertIn('the real trajectory game', doc['essentialMissing'])

    def test_old_host_without_probes(self):
        os.environ['C5H_FAKE_LUA_PROBE'] = '0'
        doc = self.run_suite('quick', caps=dict(fingerprint=None, fingerprint_probe=None))
        self.assertEqual(self.status('setup.pregame'), 'n/a')
        self.assertEqual(self.status('trajectory.probe-neutral'), 'n/a')
        self.assertEqual(self.status('trajectory.real'), 'pass')  # digests still compare
        self.assertFalse(doc['capabilities']['luaProbe'])

    def test_session_failure_is_reported(self):
        FakeRealGameSession.fail_in = 'autoplay'
        doc = self.run_suite('quick')
        self.assertEqual(doc['verdict'], 'fail')  # the real trajectory game failed
        self.assertTrue(any('fake failure' in (g.get('error') or '') for g in doc['real']['games']))

    def test_budget_trims_the_ensemble(self):
        # a 1.5-minute budget with a prior of 400 s per game: the ensemble is cut and the single phase dropped
        from civ5headless.verify import hostside
        with mock.patch.dict(hostside.PRIOR, startup=100.0):
            doc = self.run_suite('host-only', budget_minutes=1.5, ensemble=12)
        self.assertTrue(any('trimmed' in n for n in doc['notes']), doc['notes'])
        self.assertTrue(any('beside the references' in n for n in doc['notes']), doc['notes'])
        self.assertLessEqual(sum(1 for j in doc['jobs'] if j['kind'] == 'ensemble' and j['state'] == 'completed'), 6)


class CapabilityTest(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows only (.cmd)')
    def test_host_usage(self):
        with mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable, 'C5H_FAKE_LUA_PROBE': '1'}):
            opts, err = capabilities.host_options(HERE / 'fake_host.cmd')
        self.assertIsNone(err)
        self.assertIn('--lua-probe', opts)
        self.assertIn('--frame-skew', opts)
        with mock.patch.dict(os.environ, {'C5H_TEST_PYTHON': sys.executable, 'C5H_FAKE_LUA_PROBE': '0'}):
            opts, _ = capabilities.host_options(HERE / 'fake_host.cmd')
        self.assertNotIn('--lua-probe', opts)

    def test_missing_pieces_are_notes(self):
        cap = capabilities.detect(None, None, dict(host_options=['--frame-skew']))
        self.assertFalse(cap.lua_probe)
        self.assertIn('luaProbe', cap.notes)
        if cap.session_class is None:
            self.assertIn('realGameSession', cap.notes)
        d = cap.to_dict()
        self.assertEqual(d['hostOptions'], ['--frame-skew'])

    def test_unrunnable_host(self):
        opts, err = capabilities.host_options(Path(tempfile.gettempdir()) / 'no-such-host.exe')
        self.assertEqual(opts, set())
        self.assertIn('cannot run', err)


if __name__ == '__main__':
    unittest.main()
