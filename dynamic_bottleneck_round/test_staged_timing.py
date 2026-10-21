"""Staged timing uses disposable fixtures, never an existing Session/database."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import dynamic_bottleneck_round as app
from .room_release import acknowledge_shell, expire_preparation, release_if_ready
from .sync_journal import SyncJournal
import tempfile
from pathlib import Path


RULE_VAR = 'dynamic_bottleneck_round_timing_v1'
EXPECTED = [(60, 50)] * 3 + [(60, 40)] * 10 + [(40, 30)] * 10 + [(30, 30)] * 10


def staged_session(**values):
    return SimpleNamespace(vars={RULE_VAR: {
        'version': 'staged_v1', 'rounds': [list(pair) for pair in EXPECTED],
    }}, **values)


class StagedTimingTests(unittest.TestCase):
    def test_new_rule_covers_all_33_rounds_and_has_independent_copies(self):
        factory = getattr(app, 'new_round_timing_rule', None)
        self.assertIsNotNone(factory, 'new Sessions need a saved timing rule')
        first, second = factory(), factory()
        self.assertEqual(first['rounds'], [list(pair) for pair in EXPECTED])
        first['rounds'][13][0] = 99
        self.assertEqual(second['rounds'][13], [40, 30])

    def test_results_timeout_follows_formal_rounds_after_three_warmups(self):
        for raw, (_, seconds) in enumerate(EXPECTED, 1):
            with self.subTest(raw_round=raw):
                player = SimpleNamespace(round_number=raw, session=staged_session(),
                                         participant=SimpleNamespace(vars={}))
                self.assertEqual(app.Results.get_timeout_seconds(player), seconds)

    def test_group_decision_deadline_uses_the_same_saved_rule(self):
        for raw, (seconds, _) in enumerate(EXPECTED, 1):
            with self.subTest(raw_round=raw):
                group = SimpleNamespace(session=staged_session(), round_number=raw,
                    round_started=False, round_start_deadline_ts=160,
                    decision_deadline_ts=0, get_players=lambda: [])
                self.assertTrue(app.maybe_start_round(group, now_ts=110))
                self.assertEqual(group.decision_deadline_ts, 110 + seconds)
                self.assertTrue(app.maybe_start_round(group, now_ts=115))
                self.assertEqual(group.decision_deadline_ts, 110 + seconds)

    def test_protocol1_ready_and_absence_paths_use_saved_duration(self):
        for raw in (13, 14, 23, 24, 33):
            for absent in (False, True):
                with self.subTest(raw_round=raw, absent=absent):
                    session = staged_session()
                    session.vars.update({app.ROOM_RELEASE_PROTOCOL_VAR: 1,
                                         app.ROOM_RELEASE_DRAIN_VAR: 10})
                    player = SimpleNamespace(session=session, round_number=raw,
                        participant=SimpleNamespace(code='p', vars={}),
                        group=SimpleNamespace(results_ready=False))
                    player.subsession = SimpleNamespace(get_players=lambda: [player])
                    state = app.room_release_state(player, now_ts=100)
                    if absent:
                        self.assertTrue(expire_preparation(state, now_ts=160, pending_codes=()))
                    else:
                        acknowledge_shell(state, 'p', now_ts=101)
                        self.assertTrue(release_if_ready(state, now_ts=101))
                    self.assertEqual(state['deadline_ts'] - state['release_ts'], EXPECTED[raw - 1][0])

    def test_existing_sessions_keep_60_second_decisions_and_50_second_results(self):
        for raw in (1, 14, 24, 33):
            player = SimpleNamespace(round_number=raw, session=SimpleNamespace(vars={}),
                                     participant=SimpleNamespace(vars={}))
            self.assertEqual(app.Results.get_timeout_seconds(player), 50)
        self.assertEqual(app.C.DECISION_TIMEOUT_SECONDS, 60)

    def test_saved_rule_is_authoritative_and_corruption_does_not_fall_back(self):
        session = staged_session()
        session.vars[RULE_VAR]['rounds'][23] = [35, 45]
        timing = app.round_timing_for_session(session, 24)
        self.assertEqual((timing['decision_seconds'], timing['results_seconds']), (35, 45))
        session.vars[RULE_VAR]['rounds'][23] = [0, 45]
        with self.assertRaises(ValueError):
            app.round_timing_for_session(session, 24)

    def test_legacy_round_state_without_duration_keeps_sixty_seconds(self):
        from .room_release import make_round_state
        state = make_round_state(round_number=24, now_ts=100, expected_codes=['p'])
        state.pop('decision_seconds')
        acknowledge_shell(state, 'p', now_ts=101)
        release_if_ready(state, now_ts=101)
        self.assertEqual(state['deadline_ts'] - state['release_ts'], 60)

    def test_unsettled_results_and_dropout_keep_existing_guards(self):
        session = staged_session()
        player = SimpleNamespace(round_number=24, session=session,
                                 participant=SimpleNamespace(vars={'dropout_active': True}))
        self.assertEqual(app.Results.get_timeout_seconds(player), 1)
        session.vars[app.ROOM_RELEASE_PROTOCOL_VAR] = 2
        with patch.object(app.sync_integration, 'results_available', return_value=False):
            self.assertIsNone(app.Results.get_timeout_seconds(player))


class StagedJournalTests(unittest.TestCase):
    def test_short_window_is_durable_and_rejects_at_the_exact_deadline(self):
        for raw, seconds in ((14, 40), (24, 30)):
            with self.subTest(raw_round=raw), tempfile.TemporaryDirectory() as directory:
                path = Path(directory, 'sync.sqlite3')
                journal = SyncJournal(path)
                journal.ensure_round('staged', raw, expected_codes=['a', 'b'], now_ts=100,
                    decision_page_index=9, drain_limit_seconds=10, information_condition='I0',
                    allowed_minutes=[474], decision_seconds=seconds)
                for code in ('a', 'b'):
                    token = code * 24
                    journal.register_shell('staged', raw, code, token, now_ts=101, page_index=9)
                    result = journal.exchange('staged', raw, code, token, kind='ack', now_ts=102)
                end = result['deadline_ts']
                self.assertEqual(end - result['release_ts'], seconds)
                reopened = SyncJournal(path)
                replay = reopened.ensure_round('staged', raw, expected_codes=['a', 'b'], now_ts=103,
                    decision_page_index=9, drain_limit_seconds=10, information_condition='I0',
                    allowed_minutes=[474], decision_seconds=60)
                self.assertEqual(replay['deadline_ts'], end)
                on_time = reopened.exchange('staged', raw, 'a', 'a' * 24, kind='decision',
                    now_ts=end - .001, departure_minute=474)
                late = reopened.exchange('staged', raw, 'b', 'b' * 24, kind='decision',
                    now_ts=end, departure_minute=474)
                self.assertEqual(on_time['decision_kind'], 'manual')
                self.assertEqual(late['decision_kind'], 'late')


def check_frozen_staged_timing(http_request, control_request):
    """Real ORM/HTTP boundary fixtures, explicitly not a 33-round full flow."""
    import asyncio
    import re
    import time
    from urllib.parse import urlencode
    from otree.main import setup
    setup()
    import project_asgi
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.models import Participant, Session
    from otree.session import create_session

    sessions = []
    for condition in ('I0', 'I1'):
        session = create_session('dynamic_bottleneck_round_demo', num_participants=1,
            modified_session_config_fields={'capacity_information_condition': condition,
                                            'api_agent_mode': 'off'})
        assert session.vars[RULE_VAR]['rounds'] == [list(pair) for pair in EXPECTED]
        sessions.append((session.code, session.get_participants()[0].code, condition))
    db.commit()

    async def exercise():
        for raw in (3, 4, 13, 14, 23, 24, 33):
            decision_seconds, results_seconds = EXPECTED[raw - 1]
            for session_code, participant_code, condition in sessions:
                db.new_session()
                participant = Participant.objects_get(code=participant_code)
                index = next(i for i, info in _get_session_lookups(session_code).items()
                    if info.round_number == raw and info.page_class.__name__ == 'Decision')
                participant._index_in_pages = index
                participant.visited = True
                participant.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
                participant._last_page_timestamp = int(time.time())
                db.commit()
                path = f'/p/{participant_code}/dynamic_bottleneck_round/Decision/{index}'
                status, content, error = await http_request(project_asgi.app, path)
                assert status == 200, (status, error)
                assert f'尚未开始 {decision_seconds} 秒决策计时' in content
                shell = re.search(r'data-shell-token="([0-9a-f]{24})"', content).group(1)
                token = re.search(r'data-control-token="([^"]+)"', content).group(1)
                status, armed = await control_request(project_asgi.app, token,
                    dict(kind='ack', shell_token=shell))
                assert status == 200, armed
                assert armed['deadline_ts'] - armed['release_ts'] == decision_seconds
                _, duplicate = await control_request(project_asgi.app, token,
                    dict(kind='ack', shell_token=shell))
                assert duplicate['deadline_ts'] == armed['deadline_ts']
                await asyncio.sleep(max(0, armed['release_ts'] - time.time() + .02))
                status, released = await control_request(project_asgi.app, token, dict(kind='status'))
                assert status == 200, released
                assert ('actual_capacity_display' in released) == (condition == 'I1')
                status, receipt = await control_request(project_asgi.app, token,
                    dict(kind='decision', departure_minute=474))
                assert status == 200 and receipt['decision_kind'] == 'manual', receipt
                status, _, error = await http_request(project_asgi.app, path, method='POST',
                    body=urlencode({'departure_minute': 474}).encode())
                assert status == 302, (status, error)
                sync_path = f'/p/{participant_code}/dynamic_bottleneck_round/ResultsSync/{index + 2}'
                for _ in range(20):
                    status, content, error = await http_request(project_asgi.app, sync_path)
                    assert status == 200, (status, error)
                    if 'data-ready="1"' in content:
                        break
                    await asyncio.sleep(.05)
                else:
                    raise AssertionError('staged timing fixture did not settle')
                status, _, error = await http_request(project_asgi.app, sync_path, method='POST')
                assert status == 302, (status, error)
                db.new_session()
                participant = Participant.objects_get(code=participant_code)
                info = _get_session_lookups(session_code)[participant._index_in_pages]
                assert info.page_class.__name__ == 'Results'
                result_path = participant._url_i_should_be_on()
                before = time.time()
                status, content, error = await http_request(project_asgi.app, result_path)
                after = time.time()
                assert status == 200 and '成本分解' in content, (status, error)
                db.new_session()
                participant = Participant.objects_get(code=participant_code)
                expires = participant._timeout_expiration_time
                assert before + results_seconds <= expires <= after + results_seconds
                status, _, error = await http_request(project_asgi.app, result_path)
                assert status == 200, (status, error)
                db.new_session()
                assert Participant.objects_get(code=participant_code)._timeout_expiration_time == expires
                # Participants can still leave Results before the maximum duration.
                status, _, error = await http_request(project_asgi.app, result_path, method='POST')
                assert status == 302, (status, error)
                print(f'STAGED_TIMING raw={raw} condition={condition} '
                      f'decision={decision_seconds} results={results_seconds} '
                      'GET/ACK/receipt/settlement/refresh/early-next OK', flush=True)
    asyncio.run(exercise())


if __name__ == '__main__':
    unittest.main()
