"""Run reports (report.py) on fixture run folders built here, the shared HTML helpers, the detail levels (what a run
folder keeps, the host options that keep the host from writing the rest) and the DLL choice for explicit mod sets."""
from html import unescape
from html.parser import HTMLParser
import io
import contextlib
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from civ5headless import config, html, modsdb, queue, report, run
from civ5headless.compare import mixing

from .test_modsdb import ADDON_ID, BASE_ID, make_mods

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}


class Checker(HTMLParser):
    """Every element closed, in order (SVG elements included)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.errors, self.tags = [], [], {}

    def handle_starttag(self, tag, attrs):
        self.tags[tag] = self.tags.get(tag, 0) + 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags[tag] = self.tags.get(tag, 0) + 1

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f'</{tag}> closes <{self.stack[-1] if self.stack else None}>')
            if tag in self.stack:
                while self.stack and self.stack.pop() != tag:
                    pass
        else:
            self.stack.pop()


def well_formed(test, text):
    c = Checker()
    c.feed(text)
    c.close()
    test.assertEqual(c.errors, [])
    test.assertEqual(c.stack, [])
    test.assertNotRegex(text, r'(?:src|href)="(?:https?:)?//')  # nothing external
    return c.tags


def write(folder, name, value):
    p = Path(folder) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')


def make_run(folder, turns=20, final=True, replay=True, checkpoints=True, map0=True, host=True, winner=True):
    """A run folder as run.py leaves it: two majors (0 England, 1 Rome; Rome eliminated at turn 15 in the
    checkpoints), one city-state (22 Geneva), a 6x4 map."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    status = dict(state='completed', endTurn=turns, turns=turns, wallSeconds=12.5, secondsPerTurn=0.625, hostSeconds=15.0,
                  startedUTC='2026-10-26T10:00:00+00:00', process=dict(CPUSeconds=14.0, PeakWorkingSet=300 * 2 ** 20),
                  dll='game.dll', dllSHA256='AB' * 32, dllReason='--dll given', detail='standard', spec='spec.json',
                  background=dict(otherBusyCores=0.5, logicalCPUs=8))
    status.update(endReason='victory', winner=0, victory='VICTORY_DOMINATION') if winner else status.update(endReason='target_turn')
    write(folder, 'status.json', status)
    write(folder, 'setup.json', dict(world='WORLDSIZE_TINY', map='Assets\\Maps\\Continents.lua', players=[
        dict(owner=0, civilization='CIVILIZATION_ENGLAND', minor=False), dict(owner=1, civilization='CIVILIZATION_ROME', minor=False),
        dict(owner=22, civilization='CIVILIZATION_MINOR', minor=True)]))
    write(folder, 'spec-resolved.json', dict(specVersion=1, seeds=dict(map=1, sync=2), map=dict(kind='script', file='Assets\\Maps\\Continents.lua'),
                                             worldSize=dict(type='WORLDSIZE_TINY'), speed=dict(type='GAMESPEED_QUICK'),
                                             difficulty=dict(type='HANDICAP_PRINCE'), era=dict(type='ERA_ANCIENT'), majors=2, cityStates=1))
    write(folder, 'hh-command.json', dict(exe='civ5headless.exe', exeSHA256='CD' * 32, dll='game.dll', fingerprint=None, detail='standard'))
    write(folder, 'events.jsonl', json.dumps(dict(action='launched')) + '\n')
    write(folder, 'turn-times.jsonl', ''.join(json.dumps(dict(turn=t, seconds=round(t * 0.5 + t * t * 0.01, 3))) + '\n'
                                              for t in range(1, turns + 1)))
    if host:
        write(folder, 'host-report.json', {
            'format': 'civ5headless-host-report/1', 'status': 'passed', 'error': '',
            'host': dict(exe='civ5headless.exe', sha256='CD' * 32, gitHead='0123456789abcdef', gitDirty=False),
            'dll': dict(path='game.dll', sha256='AB' * 32, digestAdditions=True),
            'mods': [dict(id='m1', version=3, name='Some <Mod>')], 'options': dict(gameLogging=True),
            'profiles': dict(selected=[dict(name='base-demographics', hook='load-screen', reason='why')], loadErrors=[]),
            'addins': dict(run=2, failed=1, skipped=0, contexts=[dict(context='Panel', mod='Some Mod', status='failed', error='boom')]),
            'uiFiles': dict(unknown=[dict(name='Odd.lua')]),
            'stubCalls': dict(calls=[dict(method='ICvEngineUtility1::DoTurn', count=3, knownHarmless=True),
                                     dict(method='ICvEngineUtility1::Strange', count=2, firstPhase='autoplay', knownHarmless=False)]),
            'luaErrors': dict(failing=1, total=1, list=[dict(context='InGame', file='Assets\\UI\\Thing.lua', line=42,
                                                             message='attempt to index nil', phase='autoplay', counted=True)]),
            'turns': dict(secondsPerTurn={str(t): 0.4 + t * 0.02 for t in range(turns + 1)}),
            'timings': dict(load=0.5, autoplay=12.5, total=15.0), 'memory': dict(peakWorkingSetMB=300, peakPrivateMB=290, cpuSeconds=14)})
    if replay:
        write(folder, 'replay-data.json', {str(p): {'REPLAYDATASET_SCORE': [[t, 10 * t + p * 5] for t in range(1, (turns if p == 0 else 15) + 1)],
                                                    'REPLAYDATASET_CITYCOUNT': [[t, 1 + t // 8] for t in range(1, turns + 1)]}
                                           for p in (0, 1)})
        write(folder, 'replay-messages.json', [
            dict(Turn=0, Type=1, Player=0, Text='London is founded.'),
            dict(Turn=3, Type=0, Player=1, Text='Rome declares war on England!'),
            dict(Turn=8, Type=0, Player=0, Text='England completes [COLOR_POSITIVE_TEXT]Stonehenge[ENDCOLOR]!'),
            dict(Turn=9, Type=6, Player=1, Text='Rome has started worshipping a pantheon [ICON_PEACE].'),
            dict(Turn=12, Type=3, Player=0, Text='Rome was captured by England!!!'),
            dict(Turn=turns, Type=0, Player=0, Text='England has won a Domination Victory!!!')])
    if checkpoints:
        for t in range(turns + 1):
            ps = [dict(owner=0, team=0, civilization='CIVILIZATION_ENGLAND', minor=False, score=10 * t, cityCount=1, techs=t, era=0 if t < 10 else 1),
                  dict(owner=22, team=22, civilization='CIVILIZATION_MINOR', minor=True, score=0, cityCount=1)]
            if t < 15:
                ps.insert(1, dict(owner=1, team=1, civilization='CIVILIZATION_ROME', minor=False, score=5 * t, cityCount=1, techs=t // 2, era=0))
            write(folder, f'checkpoints/turn-{t:04d}.json', dict(data=dict(turn=t, players=ps)))
        write(folder, f'checkpoints/turn-{turns:04d}-final.json', dict(data=dict(turn=turns, players=[
            dict(owner=0, team=0, civilization='CIVILIZATION_ENGLAND', minor=False, score=10 * turns, cityCount=3, techs=turns)])))
    if map0:
        plots = [dict(plotType=3 if x in (0, 5) else (0 if (x, y) == (2, 2) else 2), terrain='TERRAIN_OCEAN' if x == 0 else
                      'TERRAIN_COAST' if x == 5 else 'TERRAIN_GRASS', feature='FEATURE_FOREST' if (x, y) == (3, 1) else None)
                 for y in range(4) for x in range(6)]
        write(folder, 't000-map.json', dict(format='civ5headless-t000-map/1', width=6, height=4, plots=plots, players=[
            dict(slot=0, civilization='CIVILIZATION_ENGLAND', leader='LEADER_ELIZABETH', team=0, minor=False, startX=1, startY=1),
            dict(slot=1, civilization='CIVILIZATION_ROME', leader='LEADER_AUGUSTUS', team=1, minor=False, startX=4, startY=2),
            dict(slot=22, civilization='CIVILIZATION_MINOR', minor=True, minorCiv='MINOR_CIV_GENEVA', team=22, startX=3, startY=3)]))
    if final:
        owners = [-1] * 24
        for i in (7, 8, 13, 14, 19):
            owners[i] = 0
        owners[21] = 22
        write(folder, 'final-state.json', dict(turn=turns, players=[
            dict(owner=0, team=0, name='England', leaderName='Elizabeth', civilization='CIVILIZATION_ENGLAND', minor=False, alive=True,
                 colors=['#ffffff', '#6d0200'], score=10 * turns, cities=3, population=9, techs=turns, policies=4, era=1,
                 eraType='ERA_CLASSICAL', wonders=1, religion='TXT_KEY_RELIGION_CHRISTIANITY'),
            dict(owner=1, team=1, name='Rome', leaderName='Augustus Caesar', civilization='CIVILIZATION_ROME', minor=False, alive=False,
                 colors=['#f0c800', '#460076'], score=0, cities=0),
            dict(owner=22, team=22, name='Geneva', civilization='CIVILIZATION_MINOR', minor=True, alive=True, colors=['#000000', '#ffff80'])],
            map=dict(width=6, height=4, owners=','.join(map(str, owners)), cities=[
                dict(plot=8, owner=0, name='London', population=6, capital=True), dict(plot=14, owner=0, name='Rome', population=3),
                dict(plot=21, owner=22, name='Geneva', population=2, capital=True)])))
    write(folder, 'Logs/Lua.log', 'InGame: hello\n')
    return folder


def make_vassal_run(folder, masters=None):
    """make_run with four more majors (2 France, 3 Egypt, 4 Greece, 5 Siam) and vassalage in the replay messages:
    France England's vassal (said twice); Greece France's, freed, England's, then freed again on one turn (the messages
    out of order, one of them twice); Egypt eliminated, then restored as England's vassal; Rome (dead at the end)
    England's; Siam France's, though at war with Geneva and France is not. masters: a master field in final-state.json."""
    folder = make_run(folder)
    final = json.loads((folder / 'final-state.json').read_text(encoding='utf-8'))
    for owner, name, leader, score in ((2, 'France', 'Napoleon', 150), (3, 'Egypt', 'Ramesses II', 140),
                                       (4, 'Greece', 'Alexander', 130), (5, 'Siam', 'Ramkhamhaeng', 120)):
        final['players'].append(dict(owner=owner, team=owner, name=name, leaderName=leader, civilization='CIVILIZATION_' + name.upper(),
                                     minor=False, alive=True, colors=['#ffffff', '#20%02x60' % (owner * 30)], score=score, cities=2))
    for p in final['players']:
        if masters is not None and not p['minor']:
            p['master'] = masters.get(p['owner'], -1)
    write(folder, 'final-state.json', final)
    messages = json.loads((folder / 'replay-messages.json').read_text(encoding='utf-8'))
    messages += [dict(Turn=5, Type=0, Player=1, Text='Augustus Caesar has become the vassal of Elizabeth!', index=20),
                 dict(Turn=6, Type=0, Player=2, Text='Napoleon has become the vassal of Elizabeth!', index=30),
                 dict(Turn=6, Type=0, Player=2, Text='Napoleon has become the vassal of Elizabeth!', index=31),
                 dict(Turn=7, Type=0, Player=4, Text='Alexander has become the vassal of Napoleon!', index=40),
                 dict(Turn=8, Type=0, Player=4, Text='Alexander is no longer the vassal of Elizabeth!', index=41),
                 dict(Turn=9, Type=0, Player=4, Text='Alexander is no longer the vassal of Napoleon!', index=42),
                 dict(Turn=10, Type=0, Player=4, Text='Alexander is no longer the vassal of Elizabeth!', index=52),
                 dict(Turn=10, Type=0, Player=4, Text='Alexander has become the vassal of Elizabeth!', index=53),
                 dict(Turn=10, Type=0, Player=4, Text='Alexander has become the vassal of Elizabeth!', index=51),
                 dict(Turn=11, Type=0, Player=3, Text='The Egyptian Civilization has been conquered!!!', index=60),
                 dict(Turn=13, Type=0, Player=0, Text='England has conquered Thebes and restored Egyptian sovereignty. The flame of '
                      'the Egyptian Empire, once extinguished, burns again. Egyptian Empire is now the vassal of England.', index=70),
                 dict(Turn=14, Type=0, Player=5, Text='Ramkhamhaeng has become the vassal of Napoleon!', index=80)]
    write(folder, 'replay-messages.json', messages)
    cp = folder / 'checkpoints' / 'turn-0020-final.json'
    data = json.loads(cp.read_text(encoding='utf-8'))
    data['data']['wars'] = [[5, 22]]
    write(folder, cp.relative_to(folder).as_posix(), data)
    return folder


def player_rows(text):
    """[(tr class, player id, its vassal tag's title or None)] of the players table, in order."""
    table = text.split('<h2 id="players">', 1)[1].split('</table>', 1)[0]
    out = []
    for row in table.split('<tr ')[1:]:  # the header row is a bare <tr>
        tag = re.search(r'<span class="vtag" title="([^"]*)">vassal</span>', row)
        out.append((re.match(r'class="([^"]*)"', row).group(1), int(re.search(r'var\(--p(\d+)\)', row).group(1)),
                    tag and unescape(tag.group(1))))
    return out


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_run(self):
        folder = make_run(self.dir / 'game')
        path = report.write(folder)
        text = path.read_text(encoding='utf-8')
        tags = well_formed(self, text)
        self.assertGreater(tags.get('svg', 0), 5)
        self.assertGreater(tags.get('use', 0), 10)                     # the hex maps
        for needle in ('England', 'Elizabeth', 'Domination victory, turn 20', 'Some &lt;Mod&gt;', 'game.dll', '--dll given',
                       'Christianity', 'Classical', 'Thing.lua:42', 'attempt to index nil', 'ICvEngineUtility1::Strange',
                       'Odd.lua', 'base-demographics', 'Panel', 'map 1, sync 2', 'Continents', 'Quick', 'Prince',
                       'Stonehenge!', 'Rome is eliminated', 'enters the Classical era', 'Geneva', 'detail standard',
                       'href="Logs/Lua.log"', 'href="status.json"', 'commit <span class="mono">0123456789'):
            self.assertIn(needle, text)
        self.assertNotIn('DoTurn', text)                                # harmless stubs are not listed
        self.assertNotIn('[COLOR_', text)
        self.assertIn('--p0:#6d0200', text)                             # England's banner colour, not its white icon
        self.assertIn('--p22:#', text)
        self.assertRegex(text, r'<tr class="winner"><td><span class="dot" style="background:var\(--p0\)"></span>England')
        self.assertIn('Lines end at their labels', text)
        self.assertLess(len(text), 200_000)

    def test_degrades(self):
        cases = {
            'crash': dict(final=False, replay=False, checkpoints=False, map0=False, host=False, winner=False),
            'stock': dict(final=False, checkpoints=False, winner=False),   # no final state, no checkpoints: replay data only
            'checkpoints': dict(final=False, replay=False, winner=False),
        }
        for name, kw in cases.items():
            with self.subTest(case=name):
                folder = make_run(self.dir / name, **kw)
                if name == 'crash':
                    st = json.loads((folder / 'status.json').read_text(encoding='utf-8'))
                    st.update(state='failed', endReason='killed', error='host exit 3', endTurn=7)
                    write(folder, 'status.json', st)
                text = report.write(folder).read_text(encoding='utf-8')
                well_formed(self, text)
                self.assertNotIn('could not be built', text)
                if name == 'crash':
                    self.assertIn('stopped at turn 7', text)
                    self.assertIn('host exit 3', text)
                    self.assertIn('No host report', text)
                    self.assertIn('No map', text)
                if name == 'stock':
                    self.assertIn('replay data: REPLAYDATASET_SCORE', text)
                    self.assertIn('No final territory', text)
                if name == 'checkpoints':
                    self.assertIn('checkpoints: score', text)
                    self.assertIn('Rome is eliminated', text)
        # nothing at all but a folder
        (self.dir / 'empty').mkdir()
        well_formed(self, report.write(self.dir / 'empty').read_text(encoding='utf-8'))

    def test_fingerprint_metrics(self):
        folder = make_run(self.dir / 'fp', final=False, replay=False, checkpoints=False)
        write(folder, 'Logs/Lua.log', ''.join(
            f'InGame: C5HM|v=1|turn={t}|player={p}|alive=1|score={t * (p + 1)}|cities=1|techs={t}\n' for t in range(5) for p in (0, 1)))
        text = report.write(folder).read_text(encoding='utf-8')
        self.assertIn('fingerprint: score', text)

    def test_summary_page_and_cli(self):
        a = make_run(self.dir / 'q/a')
        make_run(self.dir / 'q/b', winner=False)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = report.main([str(self.dir / 'q')])
        self.assertEqual(code, 0, err.getvalue())
        self.assertTrue((a / 'report.html').is_file())
        page = (self.dir / 'q/summary.html').read_text(encoding='utf-8')
        well_formed(self, page)
        self.assertIn('href="a/report.html"', page)
        self.assertIn('href="b/report.html"', page)
        self.assertIn('Score of the leader', page)
        with self.assertRaises(SystemExit):
            report.main([str(self.dir / 'nothing')])

    def test_events_and_colours(self):
        m = report.collect(make_run(self.dir / 'e'))
        ps = report.players(m)
        kinds = [e['kind'] for e in report.events(m, ps)]
        for k in ('city', 'war', 'wonder', 'pantheon', 'capture', 'victory', 'era', 'elimination'):
            self.assertIn(k, kinds)
        self.assertEqual(report.player_colour(['#ffffff', '#6d0200']), '#6d0200')  # a civilization: its banner colour
        self.assertEqual(report.player_colour(['#000000', '#ffff80']), '#ffff80')  # a city-state
        self.assertEqual(report.player_colour(['#dc0505', '#e6e6e6']), '#dc0505')  # a generic colour: the vivid one
        self.assertEqual(report.player_colour([None, 'x']), '')
        light, dark = report.theme_colours('#ffffff')
        self.assertNotEqual(light, '#ffffff')
        self.assertEqual(dark, '#ffffff')
        self.assertEqual(report.theme_colours('#000000')[1] != '#000000', True)

    def test_game_messages_win_over_synthesized_events(self):
        folder = make_run(self.dir / 'msg')
        messages = json.loads((folder / 'replay-messages.json').read_text(encoding='utf-8'))
        messages += [dict(Turn=15, Type=0, Player=1, Text='The Roman Empire has been conquered!!!'),
                     dict(Turn=12, Type=3, Player=0, Text=''),             # said by the text message of turn 12
                     dict(Turn=13, Type=3, Player=0, Text='')]             # said by nothing else: kept
        write(folder, 'replay-messages.json', messages)
        m = report.collect(folder)
        evs = report.events(m, report.players(m))
        gone = [e for e in evs if e['kind'] == 'elimination']
        self.assertEqual([(e['turn'], e['player'], e['text']) for e in gone], [(15, 1, 'The Roman Empire has been conquered!!!')])
        self.assertEqual([e['turn'] for e in evs if e['kind'] == 'capture'], [12, 13])

    def test_vassals_from_the_replay_messages(self):
        m = report.collect(make_vassal_run(self.dir / 'v'))
        self.assertEqual(report.vassal_masters(m, report.players(m)), ({2: 0, 3: 0}, 'the replay messages'))
        text = report.render(m)
        well_formed(self, text)
        self.assertEqual(player_rows(text), [('winner', 0, None), ('vassal vmore', 2, "France is England's vassal"),
                                             ('vassal', 3, "Egypt is England's vassal"), ('', 4, None), ('', 5, None),
                                             ('dead', 1, None)])
        self.assertIn('Vassals are listed under their master, as at the end of the game: from the replay messages', text)

    def test_ai_logging_of_older_runs(self):
        """status.json says; before it did, host-report.json."""
        self.assertIs(mixing.ai_logging({'aiLogging': False}, dict(gameLogging=True, gameLogFlags=15)), False)
        self.assertIs(mixing.ai_logging({}, dict(gameLogging=True, gameLogFlags=15)), True)
        self.assertIs(mixing.ai_logging({}, dict(gameLogging=True, gameLogFlags=14)), False)
        self.assertIs(mixing.ai_logging({}, dict(gameLogging=False, gameLogFlags=15)), False)
        self.assertIsNone(mixing.ai_logging({}, dict(gameLogging=True)))

    def test_vassals_from_the_final_state(self):
        m = report.collect(make_vassal_run(self.dir / 'v', masters={2: 0, 4: 2, 1: 0}))
        self.assertEqual(report.vassal_masters(m, report.players(m)), ({2: 0, 4: 2}, 'final-state.json'))  # Rome is dead
        text = report.render(m)
        well_formed(self, text)
        self.assertEqual(player_rows(text), [('winner', 0, None), ('vassal', 2, "France is England's vassal"),
                                             ('vassal', 4, "Greece is France's vassal"), ('', 3, None), ('', 5, None),
                                             ('dead', 1, None)])
        self.assertIn('<tr class="vassal" style="--d:2">', text)
        self.assertIn('as at the end of the game: from final-state.json.', text)

    def test_no_vassals(self):
        m = report.collect(make_run(self.dir / 'n'))
        self.assertEqual(report.vassal_masters(m, report.players(m)), ({}, ''))
        text = report.render(m)
        self.assertEqual(player_rows(text), [('winner', 0, None), ('dead', 1, None)])
        self.assertNotIn('Vassals are listed', text)
        rows = [dict(id=i) for i in range(4)]
        self.assertEqual([(p['id'], d, more) for p, d, more in report._vassal_rows(rows, {1: 9, 2: 3, 3: 2})],
                         [(0, 0, False), (1, 0, False), (2, 0, False), (3, 1, False)])  # master not shown; a loop

    def test_labels_never_overlap(self):
        ys = html.spread([50, 50.5, 51, 49, 120, 121], 11, 8, 190)
        order = sorted(ys)
        self.assertTrue(all(b - a >= 11 - 1e-9 for a, b in zip(order, order[1:])))
        self.assertTrue(all(8 <= y <= 190 for y in ys))
        ys = html.spread([5] * 30, 11, 8, 190)                           # more labels than fit: evenly packed
        self.assertTrue(all(8 - 1e-9 <= y <= 190 + 1e-9 for y in ys))
        svg = html.series_svg([('A', 'red', [(t, t) for t in range(300)], False), ('B', 'blue', [(t, t + 0.1) for t in range(300)], True)])
        well_formed(self, svg)
        texts = [float(y) for y in re.findall(r'<text x="[\d.]+" y="([\d.]+)" font-size="10" fill="(?:red|blue)"', svg)]
        self.assertEqual(len(texts), 2)
        self.assertGreaterEqual(abs(texts[0] - texts[1]), 11 - 1e-6)


class DetailTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def folder(self):
        f = make_run(self.dir / 'r')
        for name in ('db/game.db', 'dump-t000/map.bin', 'dump-t020/map.bin', 'Logs/HeadlessDigest.log', 'Logs/Database.log',
                     'Logs/TacticalAILog_England.csv', 'Logs/CitySites_English_001.txt', 'Logs/Stacking-a-r1-x.log',
                     'Logs/RandCalls.csv', 'Logs/HeadlessDumps/doTurnBegin-t003/game.bin', 'Logs/Headless.log', 'Logs/AStar_GC.log',
                     'lua-before-dump.json'):
            write(f, name, 'x')
        return f

    def test_tidy(self):
        for detail, kept, gone in (
                ('full', ['db', 'dump-t000', 'Logs/TacticalAILog_England.csv', 'checkpoints/turn-0005.json'], []),
                ('standard', ['Logs/HeadlessDigest.log', 'Logs/Database.log', 'Logs/Lua.log', 'Logs/Stacking-a-r1-x.log',
                              'Logs/RandCalls.csv', 'Logs/HeadlessDumps', 'checkpoints/turn-0005.json', 'lua-before-dump.json'],
                 ['db', 'dump-t000', 'dump-t020', 'Logs/TacticalAILog_England.csv', 'Logs/CitySites_English_001.txt',
                  'Logs/AStar_GC.log']),
                ('summary', ['Logs/Lua.log', 'Logs/Headless.log', 'Logs/Stacking-a-r1-x.log', 'checkpoints/turn-0020-final.json',
                             'final-state.json', 'replay-data.json', 't000-map.json'],
                 ['Logs/HeadlessDigest.log', 'Logs/Database.log', 'checkpoints/turn-0005.json', 'lua-before-dump.json', 'db'])):
            with self.subTest(detail=detail):
                f = self.folder()
                run.tidy(f, detail)
                for name in kept:
                    self.assertTrue((f / name).exists(), name)
                for name in gone:
                    self.assertFalse((f / name).exists(), name)
                import shutil
                shutil.rmtree(f)
        self.assertEqual(run.detail_of(None), 'standard')
        with self.assertRaises(ValueError):
            run.detail_of('verbose')

    def test_host_options(self):
        new, old = self.dir / 'new.exe', self.dir / 'old.exe'
        new.write_bytes(b'MZ...  --no-dumps  newgame: ... --keep-log <glob> ...')
        old.write_bytes(b'MZ... --dump-turns ...')
        self.assertEqual(run.detail_host_args('full', new), [])
        self.assertEqual(run.detail_host_args('summary', old), [])
        args = run.detail_host_args('summary', new)
        self.assertEqual(args[0], '--no-dumps')
        self.assertEqual([args[i + 1] for i, a in enumerate(args) if a == '--keep-log'],
                         ['Lua.log', 'Headless.log', 'Stacking-*.log', 'RandCalls.csv', 'HeadlessDumps'])
        self.assertIn('Headless*.log', run.detail_host_args('standard', new))
        self.assertNotIn('*.log', run.detail_host_args('standard', new))  # not every log: AStar_GC.log grows with the game

    def test_options_and_queue_keys(self):
        for argv, message in ((['--prune', '--detail', 'full'], '--prune is --detail standard'),
                              (['--detail', 'loud'], 'invalid choice')):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit):
                    run.main(['spec.json', '--turns', '3'] + argv, config.Config())
                self.assertIn(message, err.getvalue())
        self.assertEqual(queue.job_detail({}), 'standard')
        self.assertEqual(queue.job_detail({'prune': False}), 'full')
        self.assertEqual(queue.job_detail({'prune': True}), 'standard')
        self.assertEqual(queue.job_detail({'detail': 'summary', 'prune': False}), 'summary')
        with self.assertRaises(SystemExit):
            queue.expand({'jobs': [{'name': 'x', 'spec': 's', 'detail': 'loud'}]}, self.dir / 'q.json')


class DllChoiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.mods = make_mods(self.dir / 'MODS')
        self.game = self.dir / 'game'
        (self.game / 'Assets/DLC/Expansion2').mkdir(parents=True)
        self.own = self.game / 'Assets/DLC/Expansion2' / config.DLL_NAME
        self.own.write_bytes(b'x')
        self.found = self.dir / 'found.dll'
        self.cfg = config.Config(game_dir=self.game, mods_dir=self.mods, dll=self.found)
        self.cfg.sources['dll'] = config.Source('discovery', 'active mod Base Mod')

    def tearDown(self):
        self.tmp.cleanup()

    def test_rule(self):
        cfg = self.cfg
        self.assertEqual(modsdb.choose_dll(cfg, 'mine.dll', []), (Path('mine.dll').resolve(), '--dll given'))
        self.assertEqual(modsdb.choose_dll(cfg, None, None), (self.found.resolve(), 'discovery: active mod Base Mod'))
        dll, why = modsdb.choose_dll(cfg, None, [])
        self.assertEqual((dll, why), (self.own.resolve(), "no mods: the game's own DLL"))
        dll, why = modsdb.choose_dll(cfg, None, [ADDON_ID, BASE_ID])
        self.assertEqual(dll, self.own.resolve())
        self.assertIn("the game's own", why)
        (self.mods / 'Addon (v 1)' / config.DLL_NAME).write_bytes(b'a')
        dll, why = modsdb.choose_dll(cfg, None, [ADDON_ID, BASE_ID])
        self.assertEqual(dll, (self.mods / 'Addon (v 1)' / config.DLL_NAME).resolve())
        self.assertIn('Addon', why)
        (self.mods / 'Base Mod (v 3)' / config.DLL_NAME).write_bytes(b'b')  # first in activation order (Addon needs it)
        self.assertEqual(modsdb.choose_dll(cfg, None, [ADDON_ID, BASE_ID])[0], (self.mods / 'Base Mod (v 3)' / config.DLL_NAME).resolve())
        cfg.sources['dll'] = config.Source('file', '')
        cfg.file = self.dir / 'civ5headless.toml'
        self.assertEqual(modsdb.choose_dll(cfg, None, []), (self.found.resolve(), 'dll in civ5headless.toml'))
        cfg.sources['dll'] = config.Source('discovery', '')
        self.own.unlink()
        with self.assertRaises(config.ConfigError):
            modsdb.choose_dll(cfg, None, [])


if __name__ == '__main__':
    unittest.main()
