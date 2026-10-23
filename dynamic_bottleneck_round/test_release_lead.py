"""Session-frozen release lead; disposable states and journals only."""
import inspect
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import dynamic_bottleneck_round as app
from . import room_release as release
from .sync_journal import SyncJournal

RULE_VAR = 'dynamic_bottleneck_release_timing_v1'
VERSION = 'ready_lead_v1'


class ReleaseLeadTests(unittest.TestCase):
    def rule(self):
        factory = getattr(release, 'new_release_timing_rule', None)
        self.assertIsNotNone(factory, 'new Sessions need a frozen 3-second lead')
        return factory()

    def lookup(self, session):
        function = getattr(release, 'release_lead_for_session', None)
        self.assertIsNotNone(function, 'old Sessions must retain the five-second fallback')
        return function(session)

    def make(self, *, lead=3, seconds=60, codes=('a',)):
        self.assertIn('release_lead_seconds', inspect.signature(release.make_round_state).parameters)
        return release.make_round_state(round_number=4, now_ts=100, expected_codes=codes,
            decision_seconds=seconds, release_lead_seconds=lead)

    def test_new_rule_is_three_seconds_and_copies_are_independent(self):
        first, second = self.rule(), self.rule()
        self.assertEqual(first, dict(version=VERSION, release_lead_seconds=3))
        first['release_lead_seconds'] = 9
        self.assertEqual(second['release_lead_seconds'], 3)

    def test_legacy_sessions_fallback_but_malformed_rules_are_rejected(self):
        self.assertEqual(self.lookup(SimpleNamespace(vars={})), 5)
        for rule in ({}, {'version': 'unknown', 'release_lead_seconds': 3},
                     {'version': VERSION, 'release_lead_seconds': True},
                     {'version': VERSION, 'release_lead_seconds': float('nan')},
                     {'version': VERSION, 'release_lead_seconds': 0},
                     {'version': VERSION, 'release_lead_seconds': 11}):
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                self.lookup(SimpleNamespace(vars={RULE_VAR: rule}))

    def test_ready_absence_and_staged_durations_use_the_frozen_lead(self):
        for lead in (3, 5):
            for seconds in (30, 40, 60):
                for absent in (True, False):
                    with self.subTest(lead=lead, seconds=seconds, absent=absent):
                        state = self.make(lead=lead, seconds=seconds, codes=('a', 'b'))
                        release.acknowledge_shell(state, 'a', now_ts=101)
                        if absent:
                            self.assertTrue(release.expire_preparation(state, now_ts=160, pending_codes=()))
                            at = 160
                            self.assertEqual(state['excluded_codes'], ['b'])
                        else:
                            self.assertFalse(release.release_if_ready(state, now_ts=101))
                            release.acknowledge_shell(state, 'b', now_ts=102)
                            self.assertTrue(release.release_if_ready(state, now_ts=102))
                            at = 102
                        self.assertEqual(state['release_ts'], at + lead)
                        self.assertEqual(state['deadline_ts'], at + lead + seconds)
                        self.assertEqual(release.admission_kind(state, 'a', at + lead - .001), 'early')
                        self.assertEqual(release.admission_kind(state, 'a', at + lead), 'manual')
                        self.assertEqual(release.admission_kind(state, 'a', at + lead + seconds), 'late')
                        original = state['release_ts'], state['deadline_ts']
                        release.release_if_ready(state, now_ts=200)
                        self.assertEqual((state['release_ts'], state['deadline_ts']), original)

    def test_old_state_without_lead_and_rendered_missing_ack_keep_existing_semantics(self):
        state = self.make()
        state.pop('release_lead_seconds')
        release.acknowledge_shell(state, 'a', now_ts=101)
        release.release_if_ready(state, now_ts=101)
        self.assertEqual(state['release_ts'], 106)
        broken = self.make(codes=('a', 'b'))
        release.acknowledge_shell(broken, 'a', now_ts=101)
        release.mark_shell_rendered(broken, 'b', now_ts=102)
        self.assertFalse(release.expire_preparation(broken, now_ts=160, pending_codes=()))
        self.assertEqual(broken['phase'], 'TECHNICAL_ANOMALY')
        self.assertIsNone(broken['release_ts'])

    def test_protocol_one_initializes_from_session_rule_without_retiming_existing_state(self):
        session = SimpleNamespace(vars={RULE_VAR: self.rule(), app.ROOM_RELEASE_PROTOCOL_VAR: 1,
                                        app.ROOM_RELEASE_DRAIN_VAR: 10})
        player = SimpleNamespace(session=session, round_number=4,
            participant=SimpleNamespace(code='a', vars={}), group=SimpleNamespace(results_ready=False))
        player.subsession = SimpleNamespace(get_players=lambda: [player])
        state = app.room_release_state(player, now_ts=100)
        release.acknowledge_shell(state, 'a', now_ts=101)
        release.release_if_ready(state, now_ts=101)
        self.assertEqual(state['release_ts'], 104)
        session.vars[RULE_VAR]['release_lead_seconds'] = 5
        self.assertEqual(app.room_release_state(player, now_ts=110)['release_ts'], 104)

    def test_journal_reopen_keeps_new_and_legacy_deadlines_and_i1_boundary(self):
        self.assertIn('release_lead_seconds', inspect.signature(SyncJournal.ensure_round).parameters)
        with tempfile.TemporaryDirectory(prefix='release-lead-') as folder:
            path = Path(folder) / 'journal.sqlite3'
            journal = SyncJournal(path)
            for code, lead in (('new', 3), ('old', 5)):
                kwargs = dict(expected_codes=['a'], now_ts=100, decision_page_index=9,
                    drain_limit_seconds=10, information_condition='I1', allowed_minutes=[474],
                    actual_capacity_display='2.67', capacity_level_label='medium',
                    decision_seconds=30, release_lead_seconds=lead)
                journal.ensure_round(code, 4, **kwargs)
                if code == 'old':
                    with journal._connection(write=True) as connection:
                        record, original = journal._load(connection, code, 4)
                        record['state'].pop('release_lead_seconds')
                        journal._save(connection, code, 4, record, original)
                token = ('a' if code == 'new' else 'b') * 24
                journal.register_shell(code, 4, 'a', token, now_ts=101, page_index=9)
                armed = journal.exchange(code, 4, 'a', token, kind='ack', now_ts=102)
                self.assertEqual(armed['release_ts'], 102 + lead)
                self.assertNotIn('actual_capacity_display', armed)
                reopened = SyncJournal(path)
                replay = reopened.ensure_round(code, 4, **dict(kwargs, release_lead_seconds=1))
                self.assertEqual(replay['release_ts'], 102 + lead)
                self.assertEqual(replay['deadline_ts'], 132 + lead)
                public = reopened.exchange(code, 4, 'a', token, kind='status', now_ts=102 + lead)
                self.assertEqual(public['actual_capacity_display'], '2.67')


def check_release_lead_http(http_request, control_request):
    import asyncio
    import re
    import time
    from otree.main import setup
    setup()
    import project_asgi
    from otree.database import db
    from otree.models import Participant, Session
    from otree.session import create_session
    from otree.lookup import _get_session_lookups
    fixtures = []
    for condition in ('I0', 'I1'):
        for legacy, lead in ((False, 3), (True, 5)):
            session = create_session('dynamic_bottleneck_round_demo', num_participants=1,
                modified_session_config_fields={'capacity_information_condition': condition,
                                                'api_agent_mode': 'off'})
            assert session.vars[RULE_VAR] == dict(version=VERSION, release_lead_seconds=3)
            if legacy:
                session.vars.pop(RULE_VAR)
            fixtures.append((session.code, session.get_participants()[0].code, condition, lead))
    db.commit()

    async def run():
        for session_code, code, condition, lead in fixtures:
            index = next(i for i, info in _get_session_lookups(session_code).items()
                         if info.round_number == 4 and info.page_class.__name__ == 'Decision')
            db.new_session()
            participant = Participant.objects_get(code=code)
            participant._index_in_pages = index
            participant.visited = True
            participant.vars['access_granted'] = True
            participant.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
            participant._last_page_timestamp = int(time.time())
            db.commit()
            path = f'/p/{code}/dynamic_bottleneck_round/Decision/{index}'
            status, html, error = await http_request(project_asgi.app, path)
            assert status == 200, (status, error)
            token = re.search(r'data-control-token="([^"]+)"', html).group(1)
            shell = re.search(r'data-shell-token="([0-9a-f]{24})"', html).group(1)
            status, armed = await control_request(project_asgi.app, token, dict(kind='ack', shell_token=shell))
            assert status == 200 and armed['acknowledged'], armed
            assert 'actual_capacity_display' not in armed
            assert armed['release_ts'] - armed['server_now_ts'] >= lead - .01
            db.new_session()
            saved_session = Session.objects_get(code=session_code)
            assert app.sync_runtime.get_journal().state(session_code, 4)['release_lead_seconds'] == lead
            status, duplicate = await control_request(project_asgi.app, token, dict(kind='ack', shell_token=shell))
            assert duplicate['release_ts'] == armed['release_ts']
            assert duplicate['deadline_ts'] == armed['deadline_ts']
            assert duplicate['deadline_ts'] - duplicate['release_ts'] == 60
            status, rejected = await control_request(project_asgi.app, token,
                dict(kind='decision', departure_minute=474))
            assert status == 400, (status, rejected)
            await asyncio.sleep(max(0, armed['release_ts'] - time.time() + .02))
            status, operative = await control_request(project_asgi.app, token, dict(kind='status'))
            assert status == 200 and not operative['technical_anomaly']
            assert ('actual_capacity_display' in operative) == (condition == 'I1')
            status, accepted = await control_request(project_asgi.app, token,
                dict(kind='decision', departure_minute=474))
            assert status == 200 and accepted['decision_kind'] == 'manual', accepted
            print(f'RELEASE_LEAD_HTTP {condition} lead={lead}; '
                  f'armed_delta={armed["release_ts"] - armed["server_now_ts"]:.4f}; '
                  'duplicate stable; early rejected; I1 hidden before T', flush=True)
    asyncio.run(run())
