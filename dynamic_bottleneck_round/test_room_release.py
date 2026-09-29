"""Session-scoped decision release protocol; no production Session is created."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import dynamic_bottleneck_round as app

from dynamic_bottleneck_round.room_release import (
    acknowledge_shell,
    admission_kind,
    make_round_state,
    mark_shell_rendered,
    expire_preparation,
    release_if_ready,
)


class RoomReleaseStateTests(unittest.TestCase):
    def test_two_groups_share_one_release_but_another_room_is_independent(self):
        first = make_round_state(round_number=1, now_ts=100, expected_codes=[
            *(f'h{i}' for i in range(30)), *(f'ha{i}' for i in range(10)),
        ])
        second = make_round_state(round_number=1, now_ts=105, expected_codes=[
            *(f'other_h{i}' for i in range(30)),
            *(f'other_ha{i}' for i in range(10)),
        ])
        for code in [*(f'h{i}' for i in range(30)), *(f'ha{i}' for i in range(9))]:
            acknowledge_shell(first, code, now_ts=110)
        self.assertFalse(release_if_ready(first, now_ts=110))
        for code in second['expected_codes']:
            acknowledge_shell(second, code, now_ts=111)
        self.assertTrue(release_if_ready(second, now_ts=111))
        acknowledge_shell(first, 'ha9', now_ts=112)
        self.assertTrue(release_if_ready(first, now_ts=112))
        self.assertEqual(first['deadline_ts'] - first['release_ts'], 60)
        self.assertNotEqual(first['release_ts'], second['release_ts'])

    def test_duplicate_ack_and_poll_do_not_move_release(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['a'])
        acknowledge_shell(state, 'a', now_ts=101)
        self.assertTrue(release_if_ready(state, now_ts=101))
        original = (state['release_ts'], state['deadline_ts'])
        acknowledge_shell(state, 'a', now_ts=102)
        self.assertTrue(release_if_ready(state, now_ts=108))
        self.assertEqual((state['release_ts'], state['deadline_ts']), original)
        self.assertEqual(state['prepare_deadline_ts'], 160)

    def test_only_acknowledged_identity_can_enter_decision_window(self):
        state = make_round_state(round_number=1, now_ts=100, expected_codes=['a'])
        acknowledge_shell(state, 'a', now_ts=101)
        release_if_ready(state, now_ts=101)
        start, end = state['release_ts'], state['deadline_ts']
        self.assertEqual(admission_kind(state, 'a', start - 0.001), 'early')
        self.assertEqual(admission_kind(state, 'a', start), 'manual')
        self.assertEqual(admission_kind(state, 'a', end - 0.001), 'manual')
        self.assertEqual(admission_kind(state, 'a', end), 'late')
        self.assertEqual(admission_kind(state, 'stranger', start), 'unacknowledged')
        state['technical_anomaly'] = 'server_restart'
        self.assertEqual(admission_kind(state, 'a', end + 10), 'technical_anomaly')

    def test_preparation_deadline_distinguishes_absence_from_page_failure(self):
        absent = make_round_state(round_number=4, now_ts=100, expected_codes=['a', 'b'])
        acknowledge_shell(absent, 'a', now_ts=110)
        self.assertFalse(expire_preparation(absent, now_ts=159.999,
                                            pending_codes=()))
        self.assertTrue(expire_preparation(absent, now_ts=160,
                                           pending_codes=()))
        self.assertEqual(absent['excluded_codes'], ['b'])
        self.assertEqual(absent['phase'], 'ARMED')

        queued = make_round_state(round_number=4, now_ts=100, expected_codes=['a', 'b'])
        acknowledge_shell(queued, 'a', now_ts=110)
        self.assertFalse(expire_preparation(queued, now_ts=160,
                                            pending_codes=['b']))
        self.assertEqual(queued['phase'], 'TECHNICAL_ANOMALY')

        broken = make_round_state(round_number=4, now_ts=100, expected_codes=['a', 'b'])
        acknowledge_shell(broken, 'a', now_ts=110)
        mark_shell_rendered(broken, 'b', now_ts=112)
        self.assertFalse(expire_preparation(broken, now_ts=160,
                                            pending_codes=()))
        self.assertEqual(broken['phase'], 'TECHNICAL_ANOMALY')

    def test_ack_received_after_preparation_cutoff_cannot_requalify_excluded_human(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['a', 'b'])
        acknowledge_shell(state, 'a', now_ts=101)
        self.assertTrue(expire_preparation(state, now_ts=160, pending_codes=()))
        self.assertFalse(acknowledge_shell(state, 'b', now_ts=161))
        self.assertEqual(admission_kind(state, 'b', state['release_ts']), 'excluded')

    def test_predeadline_ack_processed_after_cutoff_is_still_admissible(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['a', 'b'])
        acknowledge_shell(state, 'a', now_ts=101)
        self.assertFalse(expire_preparation(state, now_ts=160, pending_codes=['b'],
                                            drain_limit_seconds=10))
        self.assertEqual(state['phase'], 'ACK_DRAINING')
        self.assertTrue(acknowledge_shell(state, 'b', now_ts=159.9))
        self.assertTrue(release_if_ready(state, now_ts=162))
        self.assertEqual(state['release_ts'], 167)

    def test_late_page_render_does_not_reclassify_absence_as_technical_error(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['a', 'b'])
        acknowledge_shell(state, 'a', now_ts=101)
        self.assertFalse(mark_shell_rendered(state, 'b', now_ts=160.1))
        self.assertTrue(expire_preparation(state, now_ts=160.1, pending_codes=()))
        self.assertEqual(state['excluded_codes'], ['b'])

    def test_shell_get_ingress_and_template_processing_have_distinct_times(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['a'])
        self.assertTrue(mark_shell_rendered(state, 'a', now_ts=161,
                                            ingress_ts=159.9))
        self.assertEqual(state['shell_get_ingress_ts_by_code']['a'], 159.9)
        self.assertEqual(state['shell_rendered_ts_by_code']['a'], 161)


class SessionIntegrationTests(unittest.TestCase):
    def make_player(self, session, code, group, subsession, round_number=4):
        player = SimpleNamespace(
            participant=SimpleNamespace(code=code, vars={}),
            session=session, group=group, subsession=subsession,
            round_number=round_number,
        )
        return player

    def test_two_groups_receive_identical_deadline_and_second_session_isolated(self):
        def room(session_code):
            session = SimpleNamespace(code=session_code, vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
            groups = [SimpleNamespace(round_started=False, round_started_at_ts=0,
                                      decision_deadline_ts=0,
                                      get_players=lambda: []) for _ in range(2)]
            subsession = SimpleNamespace(get_groups=lambda: groups)
            players = [
                self.make_player(session, 'h', groups[0], subsession),
                self.make_player(session, 'ha', groups[1], subsession),
            ]
            subsession.get_players = lambda: players
            return players, groups

        players, groups = room('room_one')
        other, other_groups = room('room_two')
        with (
            patch.object(app, 'participant_dropout_suspended', return_value=False),
            patch.object(app, 'fill_suspended_choices') as suspended,
        ):
            app.room_release_snapshot(players[0], kind='ack', now_ts=100)
            self.assertFalse(groups[0].round_started)
            app.room_release_snapshot(other[0], kind='ack', now_ts=101)
            self.assertFalse(other_groups[0].round_started)
            payload = app.room_release_snapshot(players[1], kind='ack', now_ts=102)
            self.assertEqual(payload['release_ts'], 107)
            self.assertEqual(groups[0].round_started_at_ts, 107)
            self.assertEqual(groups[1].round_started_at_ts, 107)
            self.assertEqual(groups[0].decision_deadline_ts, 167)
            self.assertEqual(groups[1].decision_deadline_ts, 167)
            self.assertFalse(other_groups[0].round_started)
            self.assertEqual(suspended.call_count, 2)
            self.assertEqual(
                app.room_release_snapshot(players[0], now_ts=108)['phase'],
                'RELEASED',
            )
            self.assertEqual(
                app.room_release_snapshot(players[0], now_ts=167)['phase'],
                'CLOSED',
            )

    def test_new_protocol_skips_ready_page_and_renders_untimed_shell(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(results_ready=False)
        player = SimpleNamespace(session=session, group=group, round_number=4)
        with (
            patch.object(app, 'access_allowed', return_value=True),
            patch.object(app, 'player_has_departure_choice', return_value=False),
            patch.object(app, 'room_release_state'),
            patch.object(app, 'mark_round_ready') as legacy_ready,
            patch.object(app, 'maybe_start_round') as legacy_start,
        ):
            self.assertFalse(app.RoundStartSync.is_displayed(player))
            self.assertTrue(app.Decision.is_displayed(player))
            self.assertIsNone(app.Decision.get_timeout_seconds(player))
        legacy_ready.assert_not_called()
        legacy_start.assert_not_called()

    def test_i1_shell_context_contains_no_actual_capacity(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        player = SimpleNamespace(
            session=session, group=SimpleNamespace(), round_number=4,
        )
        context = {
            'information_condition': 'I1', 'current_capacity_revealed': True,
            'actual_capacity': 3.14, 'capacity_level': 'high',
            'capacity_min': 1.33, 'capacity_max': 4,
        }
        schedule = {'first_departure_time': '07:50', 'last_departure_time': '08:05'}
        with (
            patch.object(app, 'public_capacity_context_for_group', return_value=context),
            patch.object(app, 'room_release_snapshot', return_value={
                'phase': 'SHELL_LOADING', 'release_ts': None,
                'deadline_ts': None, 'technical_anomaly': '',
                'server_now_ts': 100,
            }),
            patch.object(app, 'departure_schedule_for_player', return_value=schedule),
            patch.object(app, 'choice_preview', return_value=[]),
            patch.object(app, 'coarse_toll_description_for_player', return_value='无'),
        ):
            result = app.Decision.vars_for_template(player)
        self.assertFalse(result['current_capacity_revealed'])
        self.assertNotIn('actual_capacity', result)
        self.assertNotIn('capacity_level', result)
        self.assertTrue(result['room_release_shell'])
        from otree.templating.template import Template
        template = Template((Path(app.__file__).parent / 'Decision.html').read_text(
            encoding='utf-8'
        ))
        html = template.render(result)
        self.assertNotIn('3.14', html)
        self.assertIn('room_release.js', html)
        self.assertIn('id="decision-content" hidden', html)

    def test_form_admission_uses_trusted_ingress_not_processing_time(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        acknowledge_shell(state, 'p', now_ts=101)
        release_if_ready(state, now_ts=101)
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(
            session=session, participant=SimpleNamespace(code='p', vars={}),
            group=SimpleNamespace(results_ready=False), round_number=4,
        )
        with (
            patch.object(app, 'request_ingress_ts', return_value=state['deadline_ts'] - .1),
            patch.object(app, 'departure_schedule_for_player', return_value={}),
            patch.object(app, 'departure_slot_for_minute', return_value=1),
            patch.object(app.time, 'time', return_value=state['deadline_ts'] + 10),
        ):
            self.assertIsNone(app.Decision.error_message(player, {'departure_minute': 475}))

    def test_live_status_never_reveals_i1_capacity_before_release(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        player = SimpleNamespace(
            session=session, id_in_group=1,
            group=SimpleNamespace(), round_number=4,
            participant=SimpleNamespace(code='p', vars={}),
        )
        waiting = {'phase': 'SHELL_LOADING', 'release_ts': None,
                   'deadline_ts': None, 'server_now_ts': 100,
                   'technical_anomaly': ''}
        released = {**waiting, 'phase': 'ARMED', 'release_ts': 110,
                    'deadline_ts': 170, 'server_now_ts': 109}
        active = {**released, 'server_now_ts': 110}
        context = {'current_capacity_revealed': True, 'information_condition': 'I1',
                   'actual_capacity': 3.14, 'capacity_level': 'high'}
        with (
            patch.object(app, 'room_release_snapshot', side_effect=[waiting, released, active]),
            patch.object(app, 'public_capacity_context_for_group', return_value=context) as reveal,
        ):
            before = app.Decision.live_method(player, {'kind': 'status'})[1]
            armed = app.Decision.live_method(player, {'kind': 'status'})[1]
            after = app.Decision.live_method(player, {'kind': 'status'})[1]
        self.assertNotIn('actual_capacity_display', before)
        self.assertNotIn('actual_capacity_display', armed)
        self.assertEqual(after['actual_capacity_display'], '3.14')
        reveal.assert_called_once_with(player.group)

    def test_results_wait_for_predeadline_post_still_in_http_queue(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        acknowledge_shell(state, 'p', now_ts=101)
        release_if_ready(state, now_ts=101)
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(participant=SimpleNamespace(code='p'))
        group = SimpleNamespace(
            session=session, round_number=4, results_ready=False,
            round_started=True, decision_deadline_ts=state['deadline_ts'],
            get_players=lambda: [player],
        )
        with (
            patch.object(app, 'pending_decision_posts', return_value=(166.9,)),
            patch.object(app, 'player_has_departure_choice', return_value=False),
            patch.object(app, 'fill_missing_choices') as fill,
            patch.object(app, 'set_results') as settle,
            patch.object(app.time, 'time', return_value=170),
        ):
            app.maybe_prepare_results(group)
        fill.assert_not_called()
        settle.assert_not_called()

    def test_export_metadata_identifies_anomalous_round_and_exact_timing(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        acknowledge_shell(state, 'p', now_ts=101)
        mark_shell_rendered(state, 'p', now_ts=100.5)
        release_if_ready(state, now_ts=101)
        state['technical_anomaly'] = 'server_restart'
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(
            session=session, round_number=4,
            participant=SimpleNamespace(code='p', vars={
                f'{app.ROOM_RELEASE_ROUND_PREFIX}4:operative_ts': 106.2,
                f'{app.ROOM_RELEASE_ROUND_PREFIX}4:post_ingress_ts': 150.5,
                f'{app.ROOM_RELEASE_ROUND_PREFIX}4:shell_response_completed_ts': 100.9,
                f'{app.ROOM_RELEASE_ROUND_PREFIX}4:post_response_completed_ts': 151.1,
            }),
        )
        data = app.room_release_export_metadata(player)
        self.assertTrue(data['room_release_exclude_behavior'])
        self.assertEqual(data['room_release_technical_anomaly'], 'server_restart')
        self.assertEqual(data['room_release_ack_ts'], 101)
        self.assertEqual(data['room_release_operative_ts'], 106.2)
        self.assertEqual(data['room_release_shell_render_started_ts'], 100.5)
        self.assertNotIn('room_release_shell_rendered_ts', data)
        self.assertEqual(data['room_release_post_ingress_ts'], 150.5)
        self.assertEqual(data['room_release_shell_response_completed_ts'], 100.9)
        self.assertEqual(data['room_release_post_response_completed_ts'], 151.1)
        agent_data = app.room_release_agent_export_metadata(player)
        self.assertTrue(agent_data['room_release_exclude_behavior'])
        self.assertEqual(agent_data['room_release_technical_anomaly'], 'server_restart')
        self.assertNotIn('room_release_ack_ts', agent_data)

    def test_ack_persists_completed_shell_response_timestamp(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(round_started=False)
        subsession = SimpleNamespace(get_groups=lambda: [group])
        player = self.make_player(session, 'audit_ack', group, subsession)
        player.participant._index_in_pages = 9
        subsession.get_players = lambda: [player]
        with (
            patch.object(app, 'participant_dropout_suspended', return_value=False),
            patch.object(app, 'completed_shell_response_ts',
                         return_value=100.9, create=True),
            patch.object(app, 'fill_suspended_choices'),
        ):
            app.room_release_snapshot(player, kind='ack', now_ts=101,
                                      shell_token='a' * 24)
        self.assertEqual(player.participant.vars[
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4:shell_response_completed_ts'
        ], 100.9)

    def test_status_recovers_shell_completion_that_followed_early_ack(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(round_started=False)
        subsession = SimpleNamespace(get_groups=lambda: [group])
        player = self.make_player(session, 'early_ack', group, subsession)
        player.participant._index_in_pages = 9
        subsession.get_players = lambda: [player]
        with (
            patch.object(app, 'participant_dropout_suspended', return_value=False),
            patch.object(app, 'completed_shell_response_ts',
                         side_effect=[None, 100.9]),
            patch.object(app, 'fill_suspended_choices'),
        ):
            app.room_release_snapshot(player, kind='ack', now_ts=101,
                                      shell_token='a' * 24)
            app.room_release_snapshot(player, kind='status', now_ts=102)
        self.assertEqual(player.participant.vars[
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4:shell_response_completed_ts'
        ], 100.9)

    def test_ack_without_shell_token_does_not_guess_another_get_completion(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(round_started=False)
        subsession = SimpleNamespace(get_groups=lambda: [group])
        player = self.make_player(session, 'missing_token', group, subsession)
        subsession.get_players = lambda: [player]
        with (
            patch.object(app, 'participant_dropout_suspended', return_value=False),
            patch.object(app, 'completed_shell_response_ts') as completed,
            patch.object(app, 'fill_suspended_choices'),
        ):
            app.room_release_snapshot(player, kind='ack', now_ts=101)
        completed.assert_not_called()
        self.assertNotIn(
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4:shell_response_completed_ts',
            player.participant.vars,
        )

    def test_queued_ack_processed_after_drain_limit_is_anomaly(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(round_started=False, get_players=lambda: [])
        subsession = SimpleNamespace(get_groups=lambda: [group])
        players = [self.make_player(session, code, group, subsession)
                   for code in ('a', 'b')]
        subsession.get_players = lambda: players
        with patch.object(app, 'participant_dropout_suspended', return_value=False):
            app.room_release_snapshot(players[0], kind='ack', now_ts=101)
            state = session.vars[f'{app.ROOM_RELEASE_ROUND_PREFIX}4']
            state['drain_limit_seconds'] = 10
            with patch.object(app, 'shell_ack_ingress_ts', return_value=159.9):
                status = app.room_release_snapshot(players[1], kind='ack', now_ts=172)
        self.assertEqual(status['technical_anomaly'], 'shell_ack_queue_overrun')
        self.assertIsNone(status['release_ts'])

    def test_no_show_does_not_hide_server_late_prepare_poll(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(round_started=False, get_players=lambda: [])
        subsession = SimpleNamespace(get_groups=lambda: [group])
        players = [self.make_player(session, code, group, subsession)
                   for code in ('a', 'absent')]
        subsession.get_players = lambda: players
        with patch.object(app, 'participant_dropout_suspended', return_value=False):
            app.room_release_snapshot(players[0], kind='ack', now_ts=101)
            state = session.vars[f'{app.ROOM_RELEASE_ROUND_PREFIX}4']
            state['drain_limit_seconds'] = 10
            status = app.room_release_snapshot(players[0], kind='status', now_ts=200)
        self.assertEqual(status['technical_anomaly'], 'shell_ack_queue_overrun')
        self.assertIsNone(status['release_ts'])

    def test_results_sync_persists_completed_decision_post_response(self):
        state = make_round_state(round_number=4, now_ts=100,
                                 expected_codes=['audit_post'])
        state['decision_page_index'] = 9
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        group = SimpleNamespace(results_ready=False, decision_deadline_ts=170)
        player = SimpleNamespace(
            session=session, group=group, round_number=4,
            participant=SimpleNamespace(code='audit_post', vars={
                f'{app.ROOM_RELEASE_ROUND_PREFIX}4:post_ingress_ts': 150.5,
            }),
            subsession=SimpleNamespace(get_groups=lambda: [group]),
        )
        with (
            patch.object(app, 'maybe_prepare_results'),
            patch.object(app, 'completed_decision_response_ts',
                         return_value=151.1, create=True) as completion,
            patch.object(app.time, 'time', return_value=152),
        ):
            app.ResultsSync.vars_for_template(player)
        completion.assert_called_once_with(
            'audit_post', page_index=9, method='POST', ingress_ts=150.5,
        )
        self.assertEqual(player.participant.vars[
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4:post_response_completed_ts'
        ], 151.1)

    def test_results_sync_does_not_replace_accepted_post_completion(self):
        state = make_round_state(round_number=4, now_ts=100,
                                 expected_codes=['audit_post'])
        state['decision_page_index'] = 9
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(
            session=session, round_number=4,
            group=SimpleNamespace(decision_deadline_ts=170),
            participant=SimpleNamespace(code='audit_post', vars={
                f'{app.ROOM_RELEASE_ROUND_PREFIX}4:post_ingress_ts': 150.5,
                f'{app.ROOM_RELEASE_ROUND_PREFIX}4:post_response_completed_ts': 151.1,
            }),
            subsession=SimpleNamespace(get_groups=lambda: []),
        )
        with patch.object(app, 'completed_decision_response_ts',
                          return_value=199) as completion:
            app.ResultsSync.vars_for_template(player)
        completion.assert_not_called()
        self.assertEqual(player.participant.vars[
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4:post_response_completed_ts'
        ], 151.1)

    def test_restarted_worker_marks_unsettled_round_technical_anomaly(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        state['process_boot_id'] = 'previous-worker'
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(
            session=session, round_number=4,
            group=SimpleNamespace(results_ready=False),
        )
        self.assertEqual(app.room_release_state(player)['technical_anomaly'],
                         'worker_restarted_during_round')

    def test_results_poll_after_restart_cannot_auto_fill_lost_predeadline_post(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        acknowledge_shell(state, 'p', now_ts=101)
        release_if_ready(state, now_ts=101)
        state['process_boot_id'] = 'previous-worker'
        state['drain_limit_seconds'] = 10
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        group = SimpleNamespace(
            session=session, round_number=4, results_ready=False,
        )
        with (
            patch.object(app, 'fill_missing_choices') as fill,
            patch.object(app, 'set_results') as settle,
            patch.object(app.time, 'time', return_value=200),
        ):
            app.maybe_prepare_results(group)
        self.assertEqual(state['technical_anomaly'], 'worker_restarted_during_round')
        fill.assert_not_called()
        settle.assert_not_called()

    def test_pending_post_overrun_marks_technical_anomaly_without_auto_choice(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        acknowledge_shell(state, 'p', now_ts=101)
        release_if_ready(state, now_ts=101)
        state['drain_limit_seconds'] = 10
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(participant=SimpleNamespace(code='p', vars={}))
        group = SimpleNamespace(
            session=session, round_number=4, results_ready=False,
            get_players=lambda: [player],
        )
        with (
            patch.object(app, 'pending_decision_posts', return_value=(166.9,)),
            patch.object(app, 'player_has_departure_choice', return_value=False),
            patch.object(app, 'fill_missing_choices') as fill,
            patch.object(app, 'set_results') as settle,
            patch.object(app.time, 'time', return_value=181),
        ):
            app.maybe_prepare_results(group)
        self.assertEqual(state['technical_anomaly'], 'decision_post_queue_overrun')
        fill.assert_not_called()
        settle.assert_not_called()

    def test_completed_post_overrun_still_marks_anomaly_before_settlement(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        acknowledge_shell(state, 'p', now_ts=101)
        release_if_ready(state, now_ts=101)
        state['decision_page_index'] = 9
        state['drain_limit_seconds'] = 10
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(participant=SimpleNamespace(code='p', vars={
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4:post_ingress_ts': 166.9,
        }))
        group = SimpleNamespace(
            session=session, round_number=4, results_ready=False,
            get_players=lambda: [player],
        )
        with (
            patch.object(app, 'completed_decision_posts_overrun', return_value=True),
            patch.object(app, 'player_has_departure_choice', return_value=True),
            patch.object(app, 'set_results') as settle,
            patch.object(app.time, 'time', return_value=181),
        ):
            app.maybe_prepare_results(group)
        self.assertEqual(state['technical_anomaly'], 'decision_post_queue_overrun')
        settle.assert_not_called()

    def test_pending_post_blocks_settlement_even_if_choice_was_saved(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        acknowledge_shell(state, 'p', now_ts=101)
        release_if_ready(state, now_ts=101)
        state['drain_limit_seconds'] = 10
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(participant=SimpleNamespace(code='p', vars={}))
        group = SimpleNamespace(
            session=session, round_number=4, results_ready=False,
            get_players=lambda: [player],
        )
        with (
            patch.object(app, 'pending_decision_posts', return_value=(166.9,)),
            patch.object(app, 'player_has_departure_choice', return_value=True),
            patch.object(app, 'all_players_have_choice', return_value=True),
            patch.object(app, 'set_results') as settle,
            patch.object(app.time, 'time', return_value=178),
        ):
            app.maybe_prepare_results(group)
        settle.assert_not_called()

    def test_results_sync_exposes_technical_anomaly_instead_of_infinite_spinner(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        state['technical_anomaly'] = 'decision_post_queue_overrun'
        state['phase'] = 'TECHNICAL_ANOMALY'
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        group = SimpleNamespace(session=session, round_number=4,
                                results_ready=False, decision_deadline_ts=0)
        player = SimpleNamespace(group=group)
        context = app.ResultsSync.vars_for_template(player)
        self.assertEqual(context['technical_anomaly'], 'decision_post_queue_overrun')

    def test_failed_shell_http_response_is_not_called_absence(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(round_started=False)
        subsession = SimpleNamespace(get_groups=lambda: [group])
        players = [self.make_player(session, code, group, subsession)
                   for code in ('a', 'b')]
        subsession.get_players = lambda: players
        with (
            patch.object(app, 'participant_dropout_suspended', return_value=False),
            patch.object(app, 'failed_participant_requests',
                         side_effect=lambda code, **kwargs: (150,) if code == 'b' else ()),
        ):
            app.room_release_snapshot(players[0], kind='ack', now_ts=100)
            status = app.room_release_snapshot(players[0], now_ts=160)
        self.assertEqual(status['phase'], 'TECHNICAL_ANOMALY')
        self.assertIsNone(status['release_ts'])

    def test_failed_first_shell_get_survives_recreated_start_timestamp(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(round_started=False)
        subsession = SimpleNamespace(get_groups=lambda: [group])
        players = [self.make_player(session, code, group, subsession)
                   for code in ('a', 'b')]
        for player in players:
            player.participant._index_in_pages = 99
        subsession.get_players = lambda: players
        def failed(code, **kwargs):
            self.assertEqual(kwargs.get('page_index'), 99)
            self.assertNotIn('after_ts', kwargs)
            return (90,) if code == 'b' else ()
        with (
            patch.object(app, 'participant_dropout_suspended', return_value=False),
            patch.object(app, 'failed_participant_requests', side_effect=failed),
        ):
            status = app.room_release_snapshot(players[0], now_ts=100)
        self.assertEqual(status['phase'], 'TECHNICAL_ANOMALY')

    def test_prior_page_completed_after_prepare_cutoff_is_not_no_show(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(round_started=False)
        subsession = SimpleNamespace(get_groups=lambda: [group])
        players = [self.make_player(session, code, group, subsession)
                   for code in ('a', 'b')]
        subsession.get_players = lambda: players
        with (
            patch.object(app, 'participant_dropout_suspended', return_value=False),
            patch.object(app, 'completed_participant_requests_crossing',
                         side_effect=lambda code, **kwargs: ((101, 161),)
                         if code == 'b' else ()),
        ):
            app.room_release_snapshot(players[0], kind='ack', now_ts=100)
            status = app.room_release_snapshot(players[0], now_ts=160)
        self.assertEqual(status['phase'], 'TECHNICAL_ANOMALY')
        self.assertEqual(status['technical_anomaly'],
                         'participant_page_crossed_prepare_deadline')

    def test_excluded_late_post_cannot_populate_manual_choice(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p', 'q'])
        acknowledge_shell(state, 'q', now_ts=101)
        expire_preparation(state, now_ts=160, pending_codes=())
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(
            session=session, group=SimpleNamespace(results_ready=False),
            participant=SimpleNamespace(code='p', vars={}), round_number=4,
        )
        with (
            patch.object(app, 'player_has_departure_choice', return_value=False),
            patch.object(app, 'request_ingress_ts', return_value=170),
            patch.object(app, 'set_automatic_departure_choice', return_value='fallback') as auto,
            patch.object(app, 'mark_disconnect') as disconnected,
            patch.object(app, 'set_player_departure_choice') as manual,
        ):
            self.assertEqual(app.Decision.get_form_fields(player), [])
            self.assertIsNone(app.Decision.error_message(player, {}))
            app.Decision.before_next_page(player, timeout_happened=False)
        auto.assert_called_once_with(player, app.DECISION_SOURCE_DISCONNECT_AUTO)
        disconnected.assert_called_once_with(player, 'fallback')
        manual.assert_not_called()

    def test_stale_post_cannot_overwrite_choice_after_settlement(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        group = SimpleNamespace(results_ready=True)
        player = SimpleNamespace(session=session, group=group)
        with (
            patch.object(app, 'player_has_departure_choice', return_value=True),
            patch.object(app, 'set_automatic_departure_choice') as auto,
            patch.object(app, 'set_player_departure_choice') as manual,
        ):
            self.assertEqual(app.Decision.get_form_fields(player), [])
            self.assertIsNone(app.Decision.error_message(player, {}))
            app.Decision.before_next_page(player, timeout_happened=False)
        auto.assert_not_called()
        manual.assert_not_called()

    def test_form_population_does_not_make_valid_manual_post_look_preexisting(self):
        state = make_round_state(round_number=4, now_ts=100, expected_codes=['p'])
        acknowledge_shell(state, 'p', now_ts=101)
        release_if_ready(state, now_ts=101)
        session = SimpleNamespace(vars={
            app.ROOM_RELEASE_PROTOCOL_VAR: 1,
            f'{app.ROOM_RELEASE_ROUND_PREFIX}4': state,
        })
        player = SimpleNamespace(
            session=session, group=SimpleNamespace(results_ready=False),
            participant=SimpleNamespace(code='p'), round_number=4,
            field_maybe_none=lambda field: 475,
        )
        with (
            patch.object(app, 'player_has_departure_choice', side_effect=[False, True]),
            patch.object(app, 'request_ingress_ts', return_value=110),
            patch.object(app, 'set_player_departure_choice', return_value=True) as save,
            patch.object(app, 'record_manual_decision') as record,
        ):
            self.assertEqual(app.Decision.get_form_fields(player), ['departure_minute'])
            app.Decision.before_next_page(player, timeout_happened=False)
        save.assert_called_once()
        record.assert_called_once_with(player)

    def test_results_barrier_prevents_one_group_from_advancing_first(self):
        session = SimpleNamespace(vars={app.ROOM_RELEASE_PROTOCOL_VAR: 1})
        groups = [SimpleNamespace(results_ready=True, decision_deadline_ts=170),
                  SimpleNamespace(results_ready=False, decision_deadline_ts=170)]
        subsession = SimpleNamespace(get_groups=lambda: groups)
        player = SimpleNamespace(session=session, subsession=subsession,
                                 group=groups[0], round_number=4,
                                 participant=SimpleNamespace(code='barrier', vars={}))
        with patch.object(app, 'maybe_prepare_results'):
            context = app.ResultsSync.vars_for_template(player)
        self.assertFalse(context['results_ready'])
        self.assertIsNotNone(app.ResultsSync.error_message(player, {}))
        groups[1].results_ready = True
        with patch.object(app, 'maybe_prepare_results'):
            context = app.ResultsSync.vars_for_template(player)
        self.assertTrue(context['results_ready'])
        self.assertIsNone(app.ResultsSync.error_message(player, {}))


if __name__ == '__main__':
    unittest.main()
