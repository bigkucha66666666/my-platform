import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from otree.api import Bot, Submission, expect

import dynamic_bottleneck_round as dynamic_app

from . import (
    C,
    COMPREHENSION_SEEN_VAR,
    DEPARTURE_SCHEDULE_VAR,
    ComprehensionCheck,
    Decision,
    EXPORT_HEADERS,
    FormalStart,
    Introduction,
    RecoveryGate,
    Results,
    ResultsSync,
    RoundStartSync,
    WarmupStart,
    capacity_reveal_description,
    comprehension_queue_example,
    decision_submission_closed,
    mark_round_ready,
    maybe_start_round,
    remaining_decision_seconds,
    round_start_wait_seconds,
    service_batch_wait_minutes,
    should_start_round,
)


class WarmupRoundPhaseTests(unittest.TestCase):
    def test_five_warmup_rounds_precede_thirty_formal_rounds(self):
        self.assertEqual(getattr(C, 'WARMUP_ROUNDS', None), 5)
        self.assertEqual(getattr(C, 'FORMAL_ROUNDS', None), 30)
        self.assertEqual(C.NUM_ROUNDS, 35)

    def test_raw_rounds_map_to_warmup_and_formal_round_numbers(self):
        is_warmup_round = getattr(dynamic_app, 'is_warmup_round', None)
        formal_round_number = getattr(dynamic_app, 'formal_round_number', None)

        self.assertIsNotNone(is_warmup_round)
        self.assertIsNotNone(formal_round_number)
        self.assertTrue(is_warmup_round(1))
        self.assertTrue(is_warmup_round(2))
        self.assertTrue(is_warmup_round(5))
        self.assertFalse(is_warmup_round(6))
        self.assertIsNone(formal_round_number(1))
        self.assertIsNone(formal_round_number(5))
        self.assertEqual(formal_round_number(6), 1)
        self.assertEqual(formal_round_number(35), 30)

    def test_warmup_capacity_is_the_normal_accident_capacity(self):
        parse_warmup_capacity = getattr(dynamic_app, 'parse_warmup_capacity', None)
        self.assertIsNotNone(parse_warmup_capacity)
        config = dynamic_app.parse_accident_risk_config(
            {'accident_normal_capacity': 4}
        )

        self.assertEqual(
            parse_warmup_capacity({}, config),
            4,
        )

    def test_phase_context_uses_participant_facing_round_numbers(self):
        round_phase_context = getattr(dynamic_app, 'round_phase_context', None)
        self.assertIsNotNone(round_phase_context)

        self.assertEqual(
            round_phase_context(2),
            {
                'is_warmup': True,
                'phase_name': 'warmup',
                'display_round_number': 2,
                'display_total_rounds': 5,
                'round_label': '热身第 2 轮',
            },
        )
        self.assertEqual(
            round_phase_context(6),
            {
                'is_warmup': False,
                'phase_name': 'formal',
                'display_round_number': 1,
                'display_total_rounds': 30,
                'round_label': '正式第 1 轮',
            },
        )

    def test_transition_pages_display_only_at_phase_boundaries(self):
        warmup_start = getattr(dynamic_app, 'WarmupStart', None)
        formal_start = getattr(dynamic_app, 'FormalStart', None)
        self.assertIsNotNone(warmup_start)
        self.assertIsNotNone(formal_start)

        with patch.object(dynamic_app, 'access_allowed', return_value=True):
            self.assertTrue(warmup_start.is_displayed(SimpleNamespace(round_number=1)))
            self.assertFalse(warmup_start.is_displayed(SimpleNamespace(round_number=2)))
            self.assertTrue(formal_start.is_displayed(SimpleNamespace(round_number=6)))
            self.assertFalse(formal_start.is_displayed(SimpleNamespace(round_number=7)))

    def test_formal_payoff_total_excludes_warmup_rounds(self):
        formal_payoff_total = getattr(dynamic_app, 'formal_payoff_total', None)
        self.assertIsNotNone(formal_payoff_total)
        rounds = [
            SimpleNamespace(round_number=1, payoff=99),
            SimpleNamespace(round_number=5, payoff=98),
            SimpleNamespace(round_number=6, payoff=10),
            SimpleNamespace(round_number=7, payoff=20),
        ]
        player = SimpleNamespace(in_all_rounds=lambda: rounds)

        self.assertEqual(float(formal_payoff_total(player)), 30)

    def test_formal_payoff_total_prefers_unrounded_payoff(self):
        rounds = [
            SimpleNamespace(round_number=6, payoff=10, payoff_unrounded=10.25),
            SimpleNamespace(round_number=7, payoff=20, payoff_unrounded=20.125),
        ]
        player = SimpleNamespace(in_all_rounds=lambda: rounds)

        self.assertAlmostEqual(
            dynamic_app.formal_payoff_total(player),
            30.375,
            places=12,
        )

    def test_custom_export_omits_warmup_and_renumbers_formal_rounds(self):
        players = [SimpleNamespace(round_number=value) for value in (1, 5, 6, 7)]

        with (
            patch.object(
                dynamic_app,
                'export_row_for_player',
                side_effect=lambda player: [
                    dynamic_app.formal_round_number(player.round_number)
                ],
            ),
            patch.object(dynamic_app, 'agent_decisions_for_players', return_value=[]),
        ):
            rows = list(dynamic_app.custom_export(players))

        self.assertEqual(rows, [EXPORT_HEADERS, [1], [2]])

    def test_admin_report_omits_warmup_only_input(self):
        session = SimpleNamespace(config={}, vars={})
        group = SimpleNamespace(id_in_subsession=1, session=session)
        players = [
            SimpleNamespace(round_number=round_number, group=group)
            for round_number in (1, 5)
        ]

        rows, states, _summary = dynamic_app.build_admin_report_rows(players)

        self.assertEqual(rows, [])
        self.assertEqual(states, [])


class GroupSpecificAgentConfigTests(unittest.TestCase):
    def make_session(self, **overrides):
        config = {
            'api_agent_mode': 'active',
            'api_agent_count_per_group': 1,
            'rl_agent_enabled': '0',
            'rl_agent_count_per_group': 1,
            'group_agent_spec': 'G01:api=0,rl=0;G02:api=5,rl=0',
        }
        config.update(overrides)
        return SimpleNamespace(config=config, vars={})

    def test_group_treatment_metadata_uses_actual_agent_counts(self):
        assign_treatment = getattr(
            dynamic_app,
            'assign_group_treatment_metadata',
            None,
        )
        self.assertIsNotNone(assign_treatment)
        session = self.make_session()
        human = SimpleNamespace(participant=SimpleNamespace(vars={}))
        human_agent = SimpleNamespace(participant=SimpleNamespace(vars={}))

        with (
            patch.object(
                dynamic_app,
                'api_agent_count_per_group',
                side_effect=lambda _session, group_id: 0 if group_id == 1 else 3,
            ),
            patch.object(
                dynamic_app,
                'rl_agent_count_per_group',
                return_value=0,
            ),
        ):
            assign_treatment(session, [[human], [human_agent]])

        self.assertEqual(
            human.participant.vars['dynamic_bottleneck_treatment_group'],
            'H',
        )
        self.assertEqual(
            human_agent.participant.vars['dynamic_bottleneck_treatment_group'],
            'HA',
        )
        self.assertEqual(
            human_agent.participant.vars['dynamic_bottleneck_api_agent_count'],
            3,
        )

    def test_group_spec_can_balance_human_and_agent_actor_counts(self):
        session = self.make_session()
        matrix = [[object() for _ in range(20)], [object() for _ in range(15)]]

        dynamic_app.validate_group_agent_configuration(session, matrix)

        self.assertEqual(dynamic_app.api_agent_count_per_group(session, 1), 0)
        self.assertEqual(dynamic_app.api_agent_count_per_group(session, 2), 5)
        self.assertEqual(dynamic_app.rl_agent_count_per_group(session, 1), 0)
        self.assertEqual(dynamic_app.rl_agent_count_per_group(session, 2), 0)
        self.assertEqual(dynamic_app.effective_group_actor_count(session, 20, 1), 20)
        self.assertEqual(dynamic_app.effective_group_actor_count(session, 15, 2), 20)

    def test_group_spec_must_cover_every_created_group(self):
        session = self.make_session(group_agent_spec='G02:api=5,rl=0')
        matrix = [[object()], [object()]]

        with self.assertRaisesRegex(ValueError, '必须覆盖全部实验组'):
            dynamic_app.validate_group_agent_configuration(session, matrix)

    def test_group_spec_rejects_agent_count_above_limit(self):
        session = self.make_session(
            group_agent_spec='G01:api=0,rl=0;G02:api=6,rl=0'
        )

        with self.assertRaisesRegex(ValueError, '0 到 5'):
            dynamic_app.validate_group_agent_configuration(
                session,
                [[object()], [object()]],
            )

    def test_group_spec_overrides_legacy_uniform_count_validation(self):
        session = self.make_session(
            api_agent_count_per_group=0,
            rl_agent_enabled='1',
            rl_agent_count_per_group=0,
            group_agent_spec='G01:api=0,rl=0;G02:api=4,rl=1',
        )

        self.assertEqual(dynamic_app.validate_api_agent_count(session), 0)
        self.assertEqual(dynamic_app.validate_rl_agent_count(session), 0)
        dynamic_app.validate_group_agent_configuration(
            session,
            [[object()], [object()]],
        )
        self.assertEqual(dynamic_app.api_agent_count_per_group(session, 2), 4)
        self.assertEqual(dynamic_app.rl_agent_count_per_group(session, 2), 1)

    def test_group_spec_authoritatively_enables_needed_agent_types(self):
        session = self.make_session(
            api_agent_mode='off',
            rl_agent_enabled='0',
            group_agent_spec='G01:api=0,rl=0;G02:api=4,rl=1',
        )

        dynamic_app.validate_api_agent_count(session)
        dynamic_app.validate_rl_agent_count(session)
        dynamic_app.validate_group_agent_configuration(
            session,
            [[object()], [object()]],
        )

        self.assertEqual(dynamic_app.api_agent_mode(session), 'active')
        self.assertTrue(dynamic_app.rl_agent_enabled(session))
        self.assertEqual(dynamic_app.api_agent_count_per_group(session, 1), 0)
        self.assertEqual(dynamic_app.api_agent_count_per_group(session, 2), 4)
        self.assertEqual(dynamic_app.rl_agent_count_per_group(session, 2), 1)

    def test_api_request_preparation_uses_each_groups_configured_count(self):
        session = self.make_session(api_agent_mode='off')
        dynamic_app.validate_api_agent_count(session)
        dynamic_app.validate_rl_agent_count(session)
        dynamic_app.validate_group_agent_configuration(
            session,
            [[object()], [object()]],
        )
        player_one = SimpleNamespace(
            participant=SimpleNamespace(vars={'assigned_group_label': 'G01'})
        )
        player_two = SimpleNamespace(
            participant=SimpleNamespace(vars={'assigned_group_label': 'G02'})
        )
        group_one = SimpleNamespace(
            session=session,
            id_in_subsession=1,
            get_players=lambda: [player_one],
        )
        group_two = SimpleNamespace(
            session=session,
            id_in_subsession=2,
            get_players=lambda: [player_two],
        )

        with (
            patch.object(dynamic_app, 'config_from_session', return_value='config'),
            patch.object(dynamic_app, 'get_or_create_api_agent_persona', return_value={}),
            patch.object(dynamic_app, 'api_agent_choice_set_for_group', return_value='choices'),
        ):
            _config_one, prepared_one = dynamic_app.prepare_api_agent_requests_for_group(
                group_one
            )
            _config_two, prepared_two = dynamic_app.prepare_api_agent_requests_for_group(
                group_two
            )

        self.assertEqual(prepared_one, [])
        self.assertEqual(len(prepared_two), 5)
        self.assertEqual(prepared_two[0][0], 'G02_API_01')
        self.assertEqual(prepared_two[-1][0], 'G02_API_05')


class DynamicCapacityQueueTests(unittest.TestCase):
    def test_current_capacity_changes_same_minute_batch_wait(self):
        slow_wait = service_batch_wait_minutes(
            departure_minute=474,
            first_service_start_minute=474,
            load=5,
            capacity=1,
        )
        fast_wait = service_batch_wait_minutes(
            departure_minute=474,
            first_service_start_minute=474,
            load=5,
            capacity=3,
        )

        self.assertEqual(slow_wait, 4)
        self.assertAlmostEqual(fast_wait, 2 / 3)

    def test_queue_left_by_earlier_departures_is_included(self):
        wait = service_batch_wait_minutes(
            departure_minute=475,
            first_service_start_minute=478,
            load=3,
            capacity=2,
        )

        self.assertEqual(wait, 3.5)


class DynamicCostExportTests(unittest.TestCase):
    def test_accident_cost_uses_only_queue_early_and_late_components(self):
        calculator = getattr(dynamic_app, 'calculate_cost_components', None)
        self.assertIsNotNone(calculator)

        components = calculator(
            queue_delay=2,
            early_minutes=0,
            late_minutes=1,
            toll=3,
        )

        self.assertEqual(components['fixed_cost'], 0)
        self.assertEqual(components['queue_cost'], 4)
        self.assertEqual(components['late_cost'], 5)
        self.assertEqual(components['toll_cost'], 0)
        self.assertEqual(components['total_cost'], 9)

    def test_export_contains_fixed_schedule_but_no_toll_metadata(self):
        required = {
            'departure_schedule_first_time',
            'departure_schedule_last_time',
            'departure_schedule_num_slots',
        }
        removed = {'coarse_toll_source', 'coarse_toll_points', 'coarse_toll_charge'}

        self.assertTrue(required.issubset(set(EXPORT_HEADERS)))
        self.assertTrue(removed.isdisjoint(EXPORT_HEADERS))


class PublicFeedbackSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.schedule = {
            'enabled': True,
            'num_slots': 3,
            'slot_size_minutes': 1,
            'first_departure_minute': 473,
            'last_departure_minute': 475,
        }
        self.participant = SimpleNamespace(vars={})
        self.players = [
            SimpleNamespace(
                participant=self.participant,
                departure_slot=1,
                departure_minute=473,
                total_cost=12,
            ),
            SimpleNamespace(
                participant=SimpleNamespace(vars={}),
                departure_slot=2,
                departure_minute=474,
                total_cost=18,
            ),
        ]
        for player in self.players:
            player.field_maybe_none = lambda field_name, player=player: getattr(
                player,
                field_name,
                None,
            )
        self.group = SimpleNamespace(
            round_number=2,
            dynamic_capacity=3,
            incident_occurred=False,
            capacity_loss_ratio=0,
            remaining_capacity_ratio=1,
            information_condition='I0',
            accident_sequence_id='warmup',
            get_players=lambda: self.players,
        )
        self.virtual_records = [
            {
                'actor_type': 'deepseek_api_agent',
                'agent_id': 'G01_API_01',
                'departure_slot': 1,
                'total_cost': 14,
            },
            {
                'actor_type': 'rl_agent',
                'agent_id': 'G01_RL_01',
                'departure_slot': 2,
                'total_cost': 10,
            },
        ]

    def test_public_feedback_snapshot_is_complete_and_anonymous(self):
        with (
            patch.object(
                dynamic_app,
                'departure_schedule_for_player',
                return_value=self.schedule,
            ),
            patch.object(
                dynamic_app,
                'active_virtual_decisions_for_group',
                return_value=self.virtual_records,
            ),
        ):
            snapshot = dynamic_app.public_feedback_snapshot_for_group(self.group)

        self.assertEqual(snapshot['round_number'], 2)
        self.assertEqual(snapshot['dynamic_capacity'], 3)
        self.assertEqual(
            sum(row['participant_count'] for row in snapshot['departure_outcomes']),
            4,
        )
        self.assertEqual(snapshot['group_average_cost'], 13.5)
        serialized = json.dumps(snapshot)
        self.assertNotIn('participant_code', serialized)
        self.assertNotIn('agent_id', serialized)
        self.assertNotIn('actor_type', serialized)

    def test_public_feedback_snapshot_save_is_idempotent(self):
        with (
            patch.object(
                dynamic_app,
                'departure_schedule_for_player',
                return_value=self.schedule,
            ),
            patch.object(
                dynamic_app,
                'active_virtual_decisions_for_group',
                return_value=self.virtual_records,
            ),
        ):
            first = dynamic_app.save_public_feedback_snapshot(self.group)
            second = dynamic_app.save_public_feedback_snapshot(self.group)

        stored = self.participant.vars[dynamic_app.PUBLIC_FEEDBACK_PARTICIPANT_VAR]
        self.assertEqual(first, second)
        self.assertEqual(list(stored), ['2'])
        self.assertEqual(stored['2'], first)

    def test_result_chart_uses_public_feedback_snapshot(self):
        public_snapshot = {
            'round_number': 2,
            'dynamic_capacity': 3,
            'departure_outcomes': [
                {
                    'slot': 1,
                    'departure_minute': 473,
                    'departure_time': '07:53',
                    'participant_count': 3,
                    'average_cost': 16,
                },
                {
                    'slot': 2,
                    'departure_minute': 474,
                    'departure_time': '07:54',
                    'participant_count': 1,
                    'average_cost': 8,
                },
                {
                    'slot': 3,
                    'departure_minute': 475,
                    'departure_time': '07:55',
                    'participant_count': 0,
                    'average_cost': None,
                },
            ],
            'group_average_cost': 14,
        }
        self.participant.vars[dynamic_app.PUBLIC_FEEDBACK_PARTICIPANT_VAR] = {
            '2': public_snapshot,
        }
        player = self.players[0]
        player.group = self.group

        with patch.object(
            dynamic_app,
            'departure_schedule_for_player',
            return_value=self.schedule,
        ):
            chart = dynamic_app.result_current_round_cost_snapshot(player)

        chart_by_slot = {row['slot']: row for row in chart['bars']}
        self.assertEqual(chart_by_slot[1]['participant_count'], 3)
        self.assertEqual(chart_by_slot[1]['average_cost_label'], '16')
        self.assertEqual(chart_by_slot[2]['participant_count'], 1)
        self.assertEqual(chart['average_cost_label'], '14')


class RoundStartSynchronizationTests(unittest.TestCase):
    def test_first_round_waits_longer_than_later_rounds(self):
        self.assertEqual(round_start_wait_seconds(1), 120)
        self.assertEqual(round_start_wait_seconds(2), 60)
        self.assertEqual(round_start_wait_seconds(C.NUM_ROUNDS), 60)

    def test_round_starts_immediately_when_everyone_is_ready(self):
        self.assertTrue(
            should_start_round(
                ready_count=5,
                group_size=5,
                now_ts=100,
                deadline_ts=200,
            )
        )

    def test_round_waits_when_people_are_missing_before_deadline(self):
        self.assertFalse(
            should_start_round(
                ready_count=4,
                group_size=5,
                now_ts=100,
                deadline_ts=200,
            )
        )

    def test_round_starts_after_sync_deadline_even_if_people_are_missing(self):
        self.assertTrue(
            should_start_round(
                ready_count=4,
                group_size=5,
                now_ts=200,
                deadline_ts=200,
            )
        )

    @staticmethod
    def make_group(size=2, round_number=1):
        group = SimpleNamespace(
            round_number=round_number,
            round_start_deadline_ts=0,
            round_started_at_ts=0,
            round_started=False,
            decision_deadline_ts=0,
        )
        players = [
            SimpleNamespace(
                group=group,
                round_start_ready=False,
                participant=SimpleNamespace(vars={}),
            )
            for _ in range(size)
        ]
        group.get_players = lambda: players
        return group, players

    def test_first_arrival_does_not_start_decision_clock(self):
        group, players = self.make_group()

        mark_round_ready(players[0], now_ts=100)

        self.assertTrue(players[0].round_start_ready)
        self.assertEqual(group.round_start_deadline_ts, 220)
        self.assertFalse(group.round_started)
        self.assertEqual(group.decision_deadline_ts, 0)

    def test_last_arrival_starts_one_shared_decision_clock(self):
        group, players = self.make_group()
        mark_round_ready(players[0], now_ts=100)

        mark_round_ready(players[1], now_ts=105)

        self.assertTrue(group.round_started)
        self.assertEqual(group.round_started_at_ts, 105)
        self.assertEqual(group.decision_deadline_ts, 105 + C.DECISION_TIMEOUT_SECONDS)

    def test_timeout_start_is_idempotent(self):
        group, players = self.make_group(round_number=2)
        mark_round_ready(players[0], now_ts=100)

        maybe_start_round(group, now_ts=160)
        maybe_start_round(group, now_ts=170)

        self.assertTrue(group.round_started)
        self.assertEqual(group.round_started_at_ts, 160)
        self.assertEqual(group.decision_deadline_ts, 160 + C.DECISION_TIMEOUT_SECONDS)

    def test_late_timeout_poll_anchors_start_to_sync_deadline(self):
        group, players = self.make_group(round_number=2)
        mark_round_ready(players[0], now_ts=100)

        maybe_start_round(group, now_ts=162.5)

        self.assertEqual(group.round_started_at_ts, 160)
        self.assertEqual(group.decision_deadline_ts, 160 + C.DECISION_TIMEOUT_SECONDS)

    def test_remaining_decision_time_keeps_fractional_precision(self):
        group, _ = self.make_group()
        group.round_started = True
        group.decision_deadline_ts = 160

        self.assertEqual(remaining_decision_seconds(group, now_ts=159.75), 0.25)
        self.assertEqual(remaining_decision_seconds(group, now_ts=160), 0)

    def test_submission_closes_at_shared_deadline_or_after_results(self):
        group, _ = self.make_group()
        group.round_started = True
        group.decision_deadline_ts = 160
        group.results_ready = False

        self.assertFalse(decision_submission_closed(group, now_ts=159.99))
        self.assertTrue(decision_submission_closed(group, now_ts=160))
        group.results_ready = True
        self.assertTrue(decision_submission_closed(group, now_ts=120))


class DropoutRecoveryTests(unittest.TestCase):
    @staticmethod
    def make_player(reason='timeout', round_number=3):
        participant = SimpleNamespace(
            vars={
                'is_dropout': True,
                'dropout_active': True,
                'dropout_reason': reason,
                'has_recovered_after_disconnect': False,
                'has_recovered_after_timeout': False,
            },
            is_dropout=True,
            dropout_active=True,
            dropout_reason=reason,
            has_recovered_after_disconnect=False,
            has_recovered_after_timeout=False,
        )
        return SimpleNamespace(
            participant=participant,
            session=SimpleNamespace(config={'name': 'dynamic_bottleneck_round_demo'}),
            round_number=round_number,
            departure_time_label='07:54',
            dropout_event=reason,
            recovered_this_round=False,
            recovery_reason='',
        )

    def test_viewing_recovery_gate_does_not_clear_timeout(self):
        recovery_page = getattr(dynamic_app, 'RecoveryGate', None)
        self.assertIsNotNone(recovery_page)
        player = self.make_player('timeout')

        self.assertTrue(recovery_page.is_displayed(player))

        self.assertTrue(player.participant.vars['dropout_active'])
        self.assertEqual(player.participant.vars['dropout_reason'], 'timeout')

    def test_explicit_confirmation_recovers_timeout_but_keeps_history(self):
        recover = getattr(dynamic_app, 'confirm_dropout_recovery', None)
        self.assertIsNotNone(recover)
        player = self.make_player('timeout', round_number=4)

        recovered = recover(player)

        self.assertTrue(recovered)
        self.assertFalse(player.participant.vars['dropout_active'])
        self.assertEqual(player.participant.vars['dropout_reason'], '')
        self.assertTrue(player.participant.vars['is_dropout'])
        self.assertTrue(player.participant.vars['has_recovered_after_timeout'])
        self.assertTrue(player.recovered_this_round)
        self.assertEqual(player.recovery_reason, 'timeout')

    def test_explicit_confirmation_records_disconnect_recovery(self):
        player = self.make_player('disconnect', round_number=5)

        dynamic_app.confirm_dropout_recovery(player)

        self.assertTrue(player.participant.vars['has_recovered_after_disconnect'])
        self.assertFalse(player.participant.vars['has_recovered_after_timeout'])
        self.assertEqual(player.recovery_reason, 'disconnect')

    def test_recovery_page_is_between_decision_and_results_sync(self):
        names = [page.__name__ for page in dynamic_app.page_sequence]

        self.assertEqual(
            names[names.index('Decision'):names.index('ResultsSync') + 1],
            ['Decision', 'RecoveryGate', 'ResultsSync'],
        )

    def test_recovery_template_requires_manual_confirmation(self):
        html = (Path(dynamic_app.__file__).resolve().parent / 'RecoveryGate.html').read_text(
            encoding='utf-8'
        )

        self.assertIn('重新加入实验', html)
        self.assertIn('本轮自动选择', html)
        self.assertNotIn('setTimeout', html)
        self.assertNotIn('window.location', html)

    def test_export_contains_dropout_and_recovery_metadata(self):
        for field in (
            'dropout_event',
            'recovered_this_round',
            'recovery_reason',
            'historical_dropout',
            'dropout_active_at_export',
            'consecutive_missed_decisions',
            'dropout_suspended',
            'automatic_choice_strategy',
        ):
            self.assertIn(field, EXPORT_HEADERS)


class PersistentDropoutSuspensionTests(unittest.TestCase):
    @staticmethod
    def make_player(round_number=3, participant_vars=None):
        participant = SimpleNamespace(vars=dict(participant_vars or {}))
        return SimpleNamespace(
            participant=participant,
            round_number=round_number,
            departure_minute=474,
            departure_time_label='07:54',
            recovered_this_round=False,
            recovery_reason='',
        )

    def test_second_consecutive_miss_suspends_participant_idempotently(self):
        record_missed = getattr(dynamic_app, 'record_missed_decision', None)
        self.assertIsNotNone(record_missed)
        player = self.make_player(round_number=3)

        record_missed(player, reason='disconnect')

        self.assertEqual(player.participant.vars['consecutive_missed_decisions'], 1)
        self.assertFalse(player.participant.vars['dropout_suspended'])

        player.round_number = 4
        record_missed(player, reason='disconnect')
        record_missed(player, reason='disconnect')

        self.assertEqual(player.participant.vars['consecutive_missed_decisions'], 2)
        self.assertTrue(player.participant.vars['dropout_suspended'])

    def test_manual_choice_resets_streak_and_becomes_proxy_baseline(self):
        record_manual = getattr(dynamic_app, 'record_manual_decision', None)
        self.assertIsNotNone(record_manual)
        player = self.make_player(
            participant_vars={
                'consecutive_missed_decisions': 2,
                'dropout_suspended': True,
            }
        )
        player.departure_minute = 472

        record_manual(player)

        self.assertEqual(player.participant.vars['consecutive_missed_decisions'], 0)
        self.assertFalse(player.participant.vars['dropout_suspended'])
        self.assertEqual(player.participant.vars['last_manual_departure_minute'], 472)

    def test_proxy_choice_uses_last_manual_choice_then_neutral_baseline(self):
        choose_automatic = getattr(dynamic_app, 'automatic_departure_for_player', None)
        self.assertIsNotNone(choose_automatic)
        schedule = dynamic_app.static_departure_schedule()
        player = self.make_player(
            participant_vars={
                DEPARTURE_SCHEDULE_VAR: schedule,
                'last_manual_departure_minute': 472,
            }
        )

        minute, strategy = choose_automatic(player)

        self.assertEqual(minute, 472)
        self.assertEqual(strategy, 'last_manual_choice')

        player.participant.vars.pop('last_manual_departure_minute')
        minute, strategy = choose_automatic(player)

        self.assertEqual(minute, C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES)
        self.assertEqual(strategy, 'neutral_baseline')

    def test_suspended_participant_does_not_delay_round_start(self):
        active_participant = SimpleNamespace(vars={'dropout_suspended': False})
        suspended_participant = SimpleNamespace(vars={'dropout_suspended': True})
        group = SimpleNamespace(
            round_number=4,
            round_start_deadline_ts=0,
            round_started_at_ts=0,
            round_started=False,
            decision_deadline_ts=0,
        )
        active = SimpleNamespace(
            group=group,
            participant=active_participant,
            round_start_ready=True,
        )
        suspended = SimpleNamespace(
            group=group,
            participant=suspended_participant,
            round_start_ready=False,
        )
        group.get_players = lambda: [active, suspended]

        with patch.object(dynamic_app, 'fill_suspended_choices', create=True):
            started = maybe_start_round(group, now_ts=100)

        self.assertTrue(started)
        self.assertEqual(group.round_started_at_ts, 100)

    def test_suspended_proxy_is_prefilled_and_audited(self):
        schedule = dynamic_app.static_departure_schedule()
        participant = SimpleNamespace(
            vars={
                DEPARTURE_SCHEDULE_VAR: schedule,
                dynamic_app.DROPOUT_AUDIT_PARTICIPANT_VAR: {},
                'dropout_active': True,
                'dropout_reason': 'disconnect',
                'dropout_suspended': True,
                'consecutive_missed_decisions': 2,
                'last_manual_departure_minute': 472,
            }
        )
        player = SimpleNamespace(
            participant=participant,
            round_number=5,
            departure_slot=None,
            departure_minute=None,
            departure_time_label='',
            decision_source='',
            dropout_event='',
        )
        player.field_maybe_none = lambda field_name: getattr(player, field_name, None)
        group = SimpleNamespace(get_players=lambda: [player])

        dynamic_app.fill_suspended_choices(group)

        self.assertEqual(player.departure_minute, 472)
        self.assertEqual(player.decision_source, 'suspended_auto')
        self.assertEqual(player.dropout_event, 'suspended_proxy')
        audit = dynamic_app.dropout_audit_for_player_round(player)
        self.assertEqual(audit['consecutive_missed_decisions'], 2)
        self.assertTrue(audit['dropout_suspended'])
        self.assertEqual(audit['automatic_choice_strategy'], 'last_manual_choice')


class DynamicPresentationContextTests(unittest.TestCase):
    def test_reveal_description_matches_i2(self):
        config = dynamic_app.parse_accident_risk_config(
            {'accident_information_condition': 'I2'}
        )

        self.assertIn('实际服务率', capacity_reveal_description(config))

    def test_reveal_description_matches_i0(self):
        config = dynamic_app.parse_accident_risk_config(
            {'accident_information_condition': 'I0'}
        )

        self.assertIn('长期分布', capacity_reveal_description(config))

    def test_queue_example_uses_expected_incident_capacity(self):
        config = dynamic_app.parse_accident_risk_config({})

        example = comprehension_queue_example(config)

        self.assertEqual(example['capacity'], config.expected_incident_capacity)
        self.assertGreater(example['wait_minutes'], 0)
        self.assertEqual(
            len(
                {
                    example['arrival_without_queue_minute'],
                    example['arrival_with_short_wait_minute'],
                    example['arrival_minute'],
                }
            ),
            3,
        )


class TemplateContractTests(unittest.TestCase):
    app_dir = Path(__file__).resolve().parent

    def template_text(self, name):
        return (self.app_dir / name).read_text(encoding='utf-8')

    def test_introduction_explains_round_level_capacity_draw(self):
        html = self.template_text('Introduction.html')

        self.assertIn('同一轮内保持不变', html)
        for text in (
            '最后提醒',
            '当前流程',
            '单瓶颈示意',
            '居住地',
            '工作地',
            '候选瓶颈服务率',
            '规则测试',
            '固定行驶成本',
            '排队成本',
            '早到成本',
            '晚到成本',
            '粗收费',
            '同一分钟',
            '瓶颈服务率',
        ):
            self.assertIn(text, html)
        self.assertIn('具体变化规律不会提前公布', html)
        self.assertIn('{{ for item in capacity_states }}', html)
        self.assertIn('{{ capacity_reveal_description }}', html)
        self.assertNotIn('目标比例', html)
        self.assertNotIn('每轮抽取概率', html)
        self.assertNotIn('{{ item.probability_label }}', html)
        self.assertIn(
            '.intro-hero,\n'
            '        .route-demo-card,\n'
            '        .process-card,\n'
            '        .confirm-card {',
            html,
        )

    def test_comprehension_check_tests_group_capacity_equality(self):
        html = self.template_text('ComprehensionCheck.html')

        self.assertIn('同一小组、同一轮', html)
        self.assertIn('排队成本', html)
        self.assertIn('早到成本', html)
        self.assertIn('最终选择成本', html)
        self.assertIn('class="scenario-box"', html)
        self.assertIn('class="answer-feedback"', html)
        self.assertIn('{{ example_arrival_time }}', html)
        self.assertEqual(html.count('class="question-card"'), 4)

    def test_decision_has_separate_reveal_messages(self):
        html = self.template_text('Decision.html')

        self.assertIn('{{ if capacity_revealed }}', html)
        self.assertIn('本轮真实瓶颈服务率', html)
        self.assertIn('本轮服务率将在提交后公布', html)
        self.assertIn('请根据已经公布的历史结果作出选择', html)
        self.assertNotIn('{{ item.probability_percent }}%', html)
        self.assertIn('class="time-wheel"', html)
        self.assertIn('收费 {{ item.toll_charge_label }}', html)
        self.assertIn('class="capacity-road-scene"', html)
        self.assertIn('class="commute-car capacity-car"', html)
        self.assertIn('class="picker-card commute-picker"', html)
        self.assertIn('class="wheel-arrow wheel-arrow-previous"', html)
        self.assertIn('class="wheel-arrow wheel-arrow-next"', html)
        self.assertIn('aria-hidden="true"', html)
        self.assertIn('grid-row: 1;', html)
        self.assertIn('grid-row: 2;', html)
        self.assertIn('@media (max-width: 760px)', html)
        self.assertIn('aria-label="出发时间选择"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertIn('id="departure-option-{{ item.minute }}"', html)
        self.assertIn("wheel.setAttribute('aria-activedescendant',row.id);", html)
        self.assertIn('role="option" tabindex="-1"', html)
        self.assertIn('wheel.focus({preventScroll:true});', html)

    def test_round_start_sync_shows_arrivals_and_uses_short_polling(self):
        html = self.template_text('RoundStartSync.html')

        self.assertIn('等待本轮参与者进入', html)
        self.assertIn('处于暂停状态的参与者由系统代理', html)
        self.assertIn('{{ ready_count }} / {{ group_size }}', html)
        self.assertIn('data-poll="{{ poll_interval_ms }}"', html)
        self.assertIn(
            'data-ready="{{ if round_started }}1{{ else }}0{{ endif }}"',
            html,
        )
        self.assertIn("panel.dataset.ready === '1'", html)

    def test_warmup_transition_templates_have_explicit_start_and_end_messages(self):
        warmup_path = self.app_dir / 'WarmupStart.html'
        formal_path = self.app_dir / 'FormalStart.html'

        self.assertTrue(warmup_path.exists())
        self.assertTrue(formal_path.exists())
        self.assertIn('热身环节开始', warmup_path.read_text(encoding='utf-8'))
        self.assertIn('热身已结束', formal_path.read_text(encoding='utf-8'))
        self.assertIn('正式实验共 60 轮', formal_path.read_text(encoding='utf-8'))

    def test_round_pages_use_phase_labels_instead_of_raw_round_numbers(self):
        round_sync = self.template_text('RoundStartSync.html')
        decision = self.template_text('Decision.html')

        self.assertIn('{{ round_label }}', round_sync)
        self.assertIn('R{{ display_round_number }} / {{ display_total_rounds }}', round_sync)
        self.assertIn('R{{ display_round_number }} / {{ display_total_rounds }}', decision)

    def test_results_sync_uses_shared_poll_interval(self):
        html = self.template_text('ResultsSync.html')

        self.assertIn('data-poll="{{ poll_interval_ms }}"', html)
        self.assertIn('Number(panel.dataset.poll)||1500', html)
        self.assertIn(
            'data-ready="{{ if results_ready }}1{{ else }}0{{ endif }}"',
            html,
        )
        self.assertIn("panel.dataset.ready==='1'", html)

    def test_results_is_cost_centered_with_combined_group_chart(self):
        html = self.template_text('Results.html')

        self.assertIn('本轮真实瓶颈服务率', html)
        self.assertIn('上一轮服务率', html)
        self.assertIn('本轮成本与用时', html)
        self.assertIn('所有参与者的成本分布', html)
        self.assertIn('粗收费', html)
        self.assertIn('participant_count', html)
        self.assertIn('class="journey-track"', html)
        self.assertIn('class="commute-car journey-car"', html)
        self.assertIn('class="journey-route"', html)
        self.assertNotIn('left: calc(50% - 18px);', html)
        self.assertIn('grid-template-rows: 32px 168px 30px 22px 18px;', html)
        self.assertIn('class="average-plot"', html)
        self.assertIn('--plot-height: 168px;', html)
        self.assertIn('--plot-offset: 70px;', html)
        self.assertIn('bottom: var(--plot-offset);', html)
        self.assertIn('height: var(--plot-height);', html)
        self.assertNotIn('最终收益</span>', html)
        self.assertNotIn('组内匿名出发时间分布', html)
        self.assertNotIn('bottleneck-route-visual', html)


class SettingsContractTests(unittest.TestCase):
    def test_dynamic_configs_no_longer_expose_rejected_markov_or_toll_rules(self):
        import settings

        configs = {config['name']: config for config in settings.SESSION_CONFIGS}
        dynamic_names = {
            'dynamic_bottleneck_round_demo',
            'dynamic_bottleneck_round_prod_h_i0',
            'dynamic_bottleneck_round_prod_h_i1',
            'dynamic_bottleneck_round_prod_ha_i0',
            'dynamic_bottleneck_round_prod_ha_i1',
            'dynamic_bottleneck_round_prod_custom',
        }
        for name in dynamic_names:
            self.assertIn(name, configs)
            config = configs[name]
            self.assertNotIn('dynamic_capacity_draw_mode', config)
            self.assertNotIn('dynamic_capacity_transition_matrix', config)
            self.assertNotIn('coarse_toll_enabled', config)
            self.assertNotIn('reward_treatment_enabled', config)
            self.assertEqual(config['payoff_rounds'], 30)
        self.assertEqual(C.WARMUP_ROUNDS, 5)
        self.assertEqual(C.FORMAL_ROUNDS, 30)
        self.assertEqual(C.NUM_ROUNDS, 35)
        self.assertEqual(C.SYNC_POLL_INTERVAL_SECONDS, 1.5)

        for name in ('single_bottleneck_demo', 'single_bottleneck_prod'):
            self.assertEqual(configs[name]['api_agent_timeout_seconds'], 30)

    def test_export_headers_match_required_round_level_schema(self):
        required = {
            'session_code', 'participant_code', 'group_id', 'round_number',
            'treatment_condition', 'actor_composition', 'information_condition',
            'incident_occurred', 'capacity_loss_ratio', 'remaining_capacity_ratio',
            'dynamic_capacity', 'dynamic_capacity_state', 'accident_sequence_id',
            'accident_sequence_seed', 'departure_slot', 'departure_minute',
            'queue_delay_minutes', 'arrival_minute', 'early_minutes', 'late_minutes',
            'total_cost', 'payoff', 'decision_source', 'timeout_happened',
            'departure_schedule_num_slots', 'departure_schedule_first_time',
            'departure_schedule_last_time',
        }
        self.assertTrue(required.issubset(set(EXPORT_HEADERS)))


class AccidentExperimentContractTests(unittest.TestCase):
    def test_dynamic_settings_use_accident_risk_contract(self):
        import settings

        configs = {config['name']: config for config in settings.SESSION_CONFIGS}
        formal_names = (
            'dynamic_bottleneck_round_prod_h_i0',
            'dynamic_bottleneck_round_prod_h_i1',
            'dynamic_bottleneck_round_prod_ha_i0',
            'dynamic_bottleneck_round_prod_ha_i1',
            'dynamic_bottleneck_round_prod_custom',
        )
        demo = configs['dynamic_bottleneck_round_demo']
        for config in [configs[name] for name in formal_names] + [demo]:
            self.assertEqual(config['accident_normal_capacity'], 4.0)
            self.assertEqual(config['accident_probability'], 0.20)
            self.assertEqual(config['accident_loss_alpha'], 6.83057)
            self.assertEqual(config['accident_loss_beta'], 4.05907)
            self.assertNotIn('dynamic_capacity_draw_mode', config)
            self.assertNotIn('dynamic_capacity_transition_matrix', config)
            self.assertNotIn('dynamic_capacity_values', config)
        for name in formal_names:
            self.assertEqual(configs[name]['dynamic_capacity_sequence_preset'], 'S01')
        self.assertEqual(demo['dynamic_capacity_sequence_preset'], 'auto')

    def test_formal_session_defaults_match_the_four_treatments(self):
        import settings

        configs = {config['name']: config for config in settings.SESSION_CONFIGS}
        expected = {
            'dynamic_bottleneck_round_prod_h_i0': (30, 0, 0, 'I0'),
            'dynamic_bottleneck_round_prod_h_i1': (30, 0, 0, 'I1'),
            'dynamic_bottleneck_round_prod_ha_i0': (10, 10, 10, 'I0'),
            'dynamic_bottleneck_round_prod_ha_i1': (10, 10, 10, 'I1'),
        }
        for name, (humans, llm, rl, condition) in expected.items():
            with self.subTest(name=name):
                config = configs[name]
                self.assertEqual(config['num_demo_participants'], humans)
                self.assertEqual(config['api_agent_count_per_group'], llm)
                self.assertEqual(config['rl_agent_count_per_group'], rl)
                self.assertEqual(config['accident_information_condition'], condition)
                self.assertEqual(config['payoff_rounds'], 30)
        custom = configs['dynamic_bottleneck_round_prod_custom']
        self.assertEqual(custom['group_treatment_spec'], 'G01:H-I0')
        self.assertEqual(custom['num_demo_participants'], 30)

    def test_rounds_costs_and_fixed_action_space_match_approved_design(self):
        self.assertEqual(C.WARMUP_ROUNDS, 5)
        self.assertEqual(C.FORMAL_ROUNDS, 30)
        self.assertEqual(C.NUM_ROUNDS, 35)
        self.assertEqual(C.NUM_DEPARTURE_SLOTS, 16)
        self.assertEqual(C.FIXED_TRAVEL_TIME_COST, 0)
        self.assertEqual(C.QUEUE_COST_PER_MINUTE, 2)
        self.assertEqual(C.EARLY_COST_PER_MINUTE, 1)
        self.assertEqual(C.LATE_COST_PER_MINUTE, 5)

        schedule = dynamic_app.static_departure_schedule()
        self.assertEqual(schedule['num_slots'], 16)
        self.assertEqual(schedule['first_departure_time'], '07:46')
        self.assertEqual(schedule['last_departure_time'], '08:01')

    @staticmethod
    def make_session(name, *, api_mode='off', api_count=0, rl_enabled='0', rl_count=0):
        return SimpleNamespace(
            config={
                'name': name,
                'accident_information_condition': (
                    'I1' if name.endswith('_i1') else 'I0'
                ),
                'api_agent_mode': api_mode,
                'api_agent_count_per_group': api_count,
                'rl_agent_enabled': rl_enabled,
                'rl_agent_count_per_group': rl_count,
                'group_agent_spec': '',
            },
            vars={},
        )

    def test_formal_human_only_composition_is_exactly_thirty_humans(self):
        session = self.make_session('dynamic_bottleneck_round_prod_h_i0')

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object() for _ in range(30)]],
        )

        self.assertEqual(result, 'H')

    def test_formal_human_agent_composition_is_ten_plus_ten_plus_ten(self):
        session = self.make_session(
            'dynamic_bottleneck_round_prod_ha_i1',
            api_mode='active',
            api_count=10,
            rl_enabled='1',
            rl_count=10,
        )

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object() for _ in range(10)]],
        )

        self.assertEqual(result, 'HA')

    def test_formal_composition_rejects_any_other_actor_counts(self):
        invalid = (
            self.make_session('dynamic_bottleneck_round_prod_h_i0'),
            self.make_session(
                'dynamic_bottleneck_round_prod_ha_i1',
                api_mode='active',
                api_count=2,
                rl_enabled='1',
                rl_count=2,
            ),
            self.make_session(
                'dynamic_bottleneck_round_prod_ha_i0',
                api_mode='active',
                api_count=2,
            ),
        )
        matrices = (
            [[object() for _ in range(20)]],
            [[object() for _ in range(16)]],
            [[object() for _ in range(30)]],
        )
        for session, matrix in zip(invalid, matrices):
            with self.subTest(config=session.config):
                with self.assertRaisesRegex(ValueError, '30 Human|10 Human'):
                    dynamic_app.validate_formal_actor_composition(session, matrix)

    def test_custom_treatment_spec_builds_variable_human_groups(self):
        session = self.make_session('dynamic_bottleneck_round_prod_custom')
        session.config['group_treatment_spec'] = (
            'G01:H-I0;G02:H-I1;G03:HA-I0;G04:HA-I1'
        )
        players = [object() for _ in range(80)]

        treatments = dynamic_app.configure_formal_treatments(session)
        matrix = dynamic_app.build_treatment_group_matrix(players, treatments)
        result = dynamic_app.validate_formal_actor_composition(session, matrix)

        self.assertEqual([len(group) for group in matrix], [30, 30, 10, 10])
        self.assertEqual(result, {'G01': 'H', 'G02': 'H', 'G03': 'HA', 'G04': 'HA'})
        self.assertEqual(dynamic_app.information_condition_for_group(session, 1), 'I0')
        self.assertEqual(dynamic_app.information_condition_for_group(session, 2), 'I1')
        self.assertEqual(dynamic_app.api_agent_count_per_group(session, 3), 10)
        self.assertEqual(dynamic_app.rl_agent_count_per_group(session, 4), 10)

    def test_custom_treatment_spec_rejects_non_contiguous_groups(self):
        session = self.make_session('dynamic_bottleneck_round_prod_custom')
        session.config['group_treatment_spec'] = 'G01:H-I0;G03:HA-I1'

        with self.assertRaisesRegex(ValueError, 'G01.*连续'):
            dynamic_app.configure_formal_treatments(session)

    def test_custom_treatment_matrix_rejects_wrong_human_total(self):
        session = self.make_session('dynamic_bottleneck_round_prod_custom')
        session.config['group_treatment_spec'] = 'G01:H-I0;G02:HA-I1'
        treatments = dynamic_app.configure_formal_treatments(session)

        with self.assertRaisesRegex(ValueError, '需要 40 名 Human'):
            dynamic_app.build_treatment_group_matrix(
                [object() for _ in range(39)],
                treatments,
            )

    def test_fixed_config_rejects_mismatched_condition(self):
        session = self.make_session(
            'dynamic_bottleneck_round_prod_ha_i1',
            api_mode='active',
            api_count=10,
            rl_enabled='1',
            rl_count=10,
        )
        session.config['accident_information_condition'] = 'I0'

        with self.assertRaisesRegex(ValueError, '配置名.*I1'):
            dynamic_app.validate_formal_actor_composition(
                session,
                [[object() for _ in range(10)]],
            )

    def test_demo_allows_smaller_actor_count(self):
        session = self.make_session('dynamic_bottleneck_round_demo')

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object() for _ in range(5)]],
        )

        self.assertEqual(result, 'demo')


class DynamicAccidentLifecycleTests(unittest.TestCase):
    @staticmethod
    def make_session(name='dynamic_bottleneck_round_demo', preset='auto'):
        return SimpleNamespace(
            config={
                'name': name,
                'accident_normal_capacity': 4.0,
                'accident_probability': 0.20,
                'accident_loss_alpha': 6.83057,
                'accident_loss_beta': 4.05907,
                'accident_sequence_seed': 2026090801,
                'accident_information_condition': 'I1',
                'dynamic_capacity_sequence_preset': preset,
            },
            vars={},
        )

    @staticmethod
    def make_player():
        return SimpleNamespace(participant=SimpleNamespace(vars={}))

    def test_production_rejects_auto_sequence(self):
        session = self.make_session('dynamic_bottleneck_round_prod_h_i0', 'auto')

        with self.assertRaisesRegex(ValueError, '正式.*S01-S05'):
            dynamic_app.accident_sequence_for_session(session)

    def test_named_sequence_loads_frozen_bank_records(self):
        session = self.make_session('dynamic_bottleneck_round_prod_h_i0', 'S01')

        records = dynamic_app.accident_sequence_for_session(session)

        self.assertEqual(len(records), 30)
        self.assertEqual(records[0]['sequence_id'], 'S01')
        self.assertEqual(records[-1]['formal_round_number'], 30)

    def test_initialization_stores_one_session_sequence_for_all_groups(self):
        session = self.make_session()
        group_one = SimpleNamespace(
            id_in_subsession=1,
            session=session,
            get_players=lambda: [self.make_player()],
        )
        group_two = SimpleNamespace(
            id_in_subsession=2,
            session=session,
            get_players=lambda: [self.make_player()],
        )
        subsession = SimpleNamespace(
            session=session,
            get_groups=lambda: [group_one, group_two],
        )

        dynamic_app.initialize_group_capacity_sequences(subsession)

        stored = session.vars[dynamic_app.ACCIDENT_SEQUENCE_SESSION_VAR]
        self.assertEqual(len(stored), 30)
        self.assertNotIn(
            'dynamic_bottleneck_round_capacity_sequence',
            group_one.get_players()[0].participant.vars,
        )
        self.assertNotIn(
            'dynamic_bottleneck_round_capacity_sequence',
            group_two.get_players()[0].participant.vars,
        )

    def test_warmup_uses_normal_capacity_without_consuming_formal_sequence(self):
        session = self.make_session()
        session.vars[dynamic_app.ACCIDENT_SEQUENCE_SESSION_VAR] = [
            {
                'formal_round_number': 1,
                'incident_occurred': True,
                'capacity_loss_ratio': 0.75,
                'remaining_capacity_ratio': 0.25,
                'actual_capacity': 1.0,
                'sequence_id': 'auto',
                'sequence_seed': 2026090801,
            }
        ] * 30
        player = self.make_player()
        group = SimpleNamespace(
            round_number=5,
            session=session,
            get_players=lambda: [player],
        )

        dynamic_app.apply_round_capacity(group)

        self.assertEqual(group.dynamic_capacity, 4.0)
        self.assertFalse(group.incident_occurred)
        self.assertEqual(group.capacity_loss_ratio, 0.0)
        self.assertEqual(group.accident_sequence_id, 'warmup')
        self.assertEqual(player.dynamic_capacity, 4.0)

    def test_formal_round_reads_corresponding_frozen_record(self):
        session = self.make_session('dynamic_bottleneck_round_prod_h_i1', 'S01')
        records = dynamic_app.accident_sequence_for_session(session)
        session.vars[dynamic_app.ACCIDENT_SEQUENCE_SESSION_VAR] = records
        player = self.make_player()
        group = SimpleNamespace(
            round_number=C.WARMUP_ROUNDS + 1,
            session=session,
            get_players=lambda: [player],
        )

        dynamic_app.apply_round_capacity(group)

        expected = records[0]
        self.assertEqual(group.dynamic_capacity, expected['actual_capacity'])
        self.assertEqual(group.incident_occurred, expected['incident_occurred'])
        self.assertEqual(group.capacity_loss_ratio, expected['capacity_loss_ratio'])
        self.assertEqual(group.accident_sequence_id, 'S01')
        self.assertEqual(player.information_condition, 'I1')

    def test_custom_groups_share_capacity_but_keep_distinct_information(self):
        session = self.make_session('dynamic_bottleneck_round_prod_custom', 'S01')
        session.config.update(
            {
                'group_treatment_spec': 'G01:H-I0;G02:H-I1',
                'api_agent_mode': 'off',
                'api_agent_count_per_group': 0,
                'rl_agent_enabled': '0',
                'rl_agent_count_per_group': 0,
            }
        )
        dynamic_app.configure_formal_treatments(session)
        records = dynamic_app.accident_sequence_for_session(session)
        session.vars[dynamic_app.ACCIDENT_SEQUENCE_SESSION_VAR] = records
        player_i0 = self.make_player()
        player_i1 = self.make_player()
        group_i0 = SimpleNamespace(
            id_in_subsession=1,
            round_number=C.WARMUP_ROUNDS + 1,
            session=session,
            get_players=lambda: [player_i0],
        )
        group_i1 = SimpleNamespace(
            id_in_subsession=2,
            round_number=C.WARMUP_ROUNDS + 1,
            session=session,
            get_players=lambda: [player_i1],
        )

        dynamic_app.apply_round_capacity(group_i0)
        dynamic_app.apply_round_capacity(group_i1)

        self.assertEqual(group_i0.dynamic_capacity, group_i1.dynamic_capacity)
        self.assertEqual(group_i0.incident_occurred, group_i1.incident_occurred)
        self.assertEqual(group_i0.capacity_loss_ratio, group_i1.capacity_loss_ratio)
        self.assertEqual(group_i0.information_condition, 'I0')
        self.assertEqual(group_i1.information_condition, 'I1')

    def test_capacity_model_fields_are_float_columns(self):
        for model in (dynamic_app.Group, dynamic_app.Player):
            column = model.__dict__['dynamic_capacity'].property.columns[0]
            self.assertEqual(str(column.type), 'FLOAT')

    def test_continuous_cost_and_payoff_have_unrounded_float_columns(self):
        for field_name in ('total_cost', 'payoff_unrounded'):
            column = dynamic_app.Player.__dict__[field_name].property.columns[0]
            self.assertEqual(str(column.type), 'FLOAT')


class DynamicContinuousQueueTests(unittest.TestCase):
    def test_float_capacity_uses_continuous_batch_duration(self):
        wait = dynamic_app.service_batch_wait_minutes(
            departure_minute=474,
            first_service_start_minute=474,
            load=5,
            capacity=1.5,
        )

        self.assertAlmostEqual(wait, 5 / 1.5 - 1, places=10)

    def test_clear_time_preserves_unrounded_fraction_for_next_batch(self):
        first_clear = dynamic_app.service_batch_clear_minute(474, 5, 1.5)
        second_wait = dynamic_app.service_batch_wait_minutes(
            departure_minute=476,
            first_service_start_minute=max(476, first_clear),
            load=2,
            capacity=1.5,
        )

        self.assertAlmostEqual(first_clear, 474 + 5 / 1.5, places=10)
        self.assertAlmostEqual(
            second_wait,
            (first_clear - 476) + (2 / 1.5 - 1),
            places=10,
        )

    def test_queue_helpers_reject_non_positive_capacity(self):
        for function, args in (
            (
                dynamic_app.service_batch_wait_minutes,
                dict(
                    departure_minute=474,
                    first_service_start_minute=474,
                    load=1,
                    capacity=0,
                ),
            ),
            (
                dynamic_app.service_batch_clear_minute,
                dict(first_service_start_minute=474, load=1, capacity=0),
            ),
        ):
            with self.subTest(function=function.__name__):
                with self.assertRaisesRegex(ValueError, 'capacity'):
                    function(**args)

    def test_cost_components_preserve_continuous_precision(self):
        components = dynamic_app.calculate_cost_components(
            queue_delay=1 / 3,
            early_minutes=0,
            late_minutes=0,
        )

        self.assertAlmostEqual(components['queue_cost'], 2 / 3, places=14)
        self.assertAlmostEqual(components['total_cost'], 2 / 3, places=14)
        self.assertNotEqual(components['total_cost'], 0.67)


class DynamicAccidentCostTests(unittest.TestCase):
    def test_cost_uses_only_queue_early_and_late_components(self):
        components = dynamic_app.calculate_cost_components(
            queue_delay=2,
            early_minutes=3,
            late_minutes=4,
        )

        self.assertEqual(
            components,
            {
                'fixed_cost': 0.0,
                'queue_cost': 4.0,
                'early_cost': 3.0,
                'late_cost': 20.0,
                'toll_cost': 0.0,
                'total_cost': 27.0,
            },
        )

    def test_legacy_toll_argument_cannot_change_accident_cost(self):
        without_toll = dynamic_app.calculate_cost_components(
            queue_delay=1,
            early_minutes=0,
            late_minutes=0,
        )
        with_toll = dynamic_app.calculate_cost_components(
            queue_delay=1,
            early_minutes=0,
            late_minutes=0,
            toll=99,
        )

        self.assertEqual(with_toll, without_toll)

    def test_legacy_reward_and_toll_settings_are_ignored(self):
        session = SimpleNamespace(
            config={
                'reward_treatment_enabled': 1,
                'rewarded_slot_spec': '1-16',
                'reward_bonus_points': 50,
                'coarse_toll_enabled': 1,
                'coarse_toll_slot_spec': '1-16',
                'coarse_toll_points': 50,
            }
        )

        self.assertEqual(
            dynamic_app.accident_incentives_for_slot(session, 8),
            {'reward_bonus': 0.0, 'coarse_toll_charge': 0.0},
        )


class DynamicAccidentExportTests(unittest.TestCase):
    @staticmethod
    def make_player_and_group():
        session = SimpleNamespace(code='SESSION01', config={}, vars={})
        participant = SimpleNamespace(vars={})
        player = SimpleNamespace(
            session=session,
            participant=participant,
            round_number=C.WARMUP_ROUNDS + 1,
            dynamic_capacity=1.50123456789,
            dynamic_capacity_state='incident',
            incident_occurred=True,
            capacity_loss_ratio=0.6246913580275,
            remaining_capacity_ratio=0.3753086419725,
            information_condition='I1',
            accident_sequence_id='S01',
            accident_sequence_seed=2026090801,
            actor_composition='H',
            departure_slot=1,
            departure_minute=466,
            queue_delay_minutes=1.23456789,
            arrival_minute=475.23456789,
            early_minutes=0,
            late_minutes=1.23456789,
            slot_load=7,
            total_cost=8.64197523,
            payoff=131.35802477,
        )
        player.field_maybe_none = lambda field_name: getattr(
            player,
            field_name,
            None,
        )
        group = SimpleNamespace(
            session=session,
            round_number=player.round_number,
            id_in_subsession=1,
            dynamic_capacity=player.dynamic_capacity,
            incident_occurred=True,
            capacity_loss_ratio=player.capacity_loss_ratio,
            remaining_capacity_ratio=player.remaining_capacity_ratio,
            information_condition='I1',
            accident_sequence_id='S01',
            accident_sequence_seed=2026090801,
            get_players=lambda: [player],
        )
        player.group = group
        return player, group

    def test_public_snapshot_contains_realized_accident_without_identity(self):
        player, group = self.make_player_and_group()

        snapshot = dynamic_app.public_feedback_snapshot_for_group(
            group,
            virtual_records=[],
        )

        self.assertTrue(snapshot['incident_occurred'])
        self.assertEqual(snapshot['capacity_loss_ratio'], player.capacity_loss_ratio)
        self.assertEqual(
            snapshot['remaining_capacity_ratio'],
            player.remaining_capacity_ratio,
        )
        self.assertEqual(snapshot['actual_capacity'], player.dynamic_capacity)
        self.assertEqual(snapshot['information_condition'], 'I1')
        self.assertEqual(snapshot['accident_sequence_id'], 'S01')
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn('participant_code', serialized)
        self.assertNotIn('actor_type', serialized)
        self.assertNotIn('agent_id', serialized)

    def test_export_schema_uses_accident_treatment_fields_not_markov_fields(self):
        required = {
            'treatment_condition',
            'actor_composition',
            'information_condition',
            'incident_occurred',
            'capacity_loss_ratio',
            'remaining_capacity_ratio',
            'dynamic_capacity',
            'accident_sequence_id',
            'accident_sequence_seed',
        }
        removed = {
            'previous_round_capacity',
            'capacity_probability',
            'capacity_reveal_timing',
            'dynamic_capacity_seed',
            'dynamic_capacity_draw_mode',
            'coarse_toll_calibration_capacity',
            'coarse_toll_points',
            'coarse_toll_charge',
        }

        self.assertTrue(required.issubset(EXPORT_HEADERS))
        self.assertTrue(removed.isdisjoint(EXPORT_HEADERS))

    def test_export_metadata_rounds_only_serialized_accident_values(self):
        player, _group = self.make_player_and_group()

        metadata = dynamic_app.accident_export_metadata(player)

        self.assertEqual(metadata['treatment_condition'], 'H-I1')
        self.assertEqual(metadata['capacity_loss_ratio'], 0.624691)
        self.assertEqual(metadata['remaining_capacity_ratio'], 0.375309)
        self.assertEqual(metadata['dynamic_capacity'], 1.501235)
        self.assertEqual(player.dynamic_capacity, 1.50123456789)

    def test_human_export_rounds_continuous_results_to_six_decimals(self):
        player, _group = self.make_player_and_group()

        exported = dict(zip(
            EXPORT_HEADERS,
            dynamic_app.export_row_for_player(player),
        ))

        self.assertEqual(exported['queue_delay_minutes'], 1.234568)
        self.assertEqual(exported['arrival_minute'], 475.234568)
        self.assertEqual(exported['late_minutes'], 1.234568)
        self.assertEqual(exported['total_cost'], 8.641975)
        self.assertEqual(exported['payoff'], 131.358025)

    def test_agent_export_uses_agent_batch_load_and_six_decimal_capacity(self):
        player, _group = self.make_player_and_group()
        record = {
            'group_id': 1,
            'dynamic_capacity': 1.50123456789,
            'dynamic_capacity_state': 'incident',
            'departure_slot': 3,
            'departure_minute': 468,
            'queue_delay_minutes': 2.34567891,
            'arrival_minute': 477.34567891,
            'early_minutes': 0,
            'late_minutes': 3.34567891,
            'slot_load': 3,
            'total_cost': 21.41975237,
            'payoff': 118.58024763,
            'actor_type': 'rl',
            'agent_id': 'rl-1',
        }

        exported = dict(zip(
            EXPORT_HEADERS,
            dynamic_app.export_row_for_agent_record(record, player),
        ))

        self.assertEqual(exported['slot_load'], 3)
        self.assertEqual(exported['dynamic_capacity'], 1.501235)
        self.assertEqual(exported['queue_delay_minutes'], 2.345679)
        self.assertEqual(exported['total_cost'], 21.419752)


class DynamicAccidentPresentationBackendTests(unittest.TestCase):
    def test_capacity_rows_describe_iid_normal_and_incident_distribution(self):
        config = dynamic_app.parse_accident_risk_config(
            {
                'normal_capacity': 4,
                'incident_probability': 0.2,
                'capacity_loss_alpha': 6.83057,
                'capacity_loss_beta': 4.05907,
                'information_condition': 'I1',
            }
        )

        rows = dynamic_app.capacity_state_rows(config)

        self.assertEqual([row['state'] for row in rows], ['normal', 'incident'])
        self.assertEqual([row['probability'] for row in rows], [0.8, 0.2])
        self.assertEqual(rows[0]['capacity'], 4.0)
        self.assertAlmostEqual(
            rows[1]['capacity'],
            config.expected_incident_capacity,
        )

    def test_information_descriptions_match_i0_i1_i2(self):
        descriptions = {
            condition: dynamic_app.capacity_reveal_description(
                dynamic_app.parse_accident_risk_config(
                    {'accident_information_condition': condition}
                )
            )
            for condition in ('I0', 'I1', 'I2')
        }

        self.assertIn('长期分布', descriptions['I0'])
        self.assertIn('事故是否发生', descriptions['I1'])
        self.assertIn('实际服务率', descriptions['I2'])

    def test_warmup_capacity_is_normal_capacity(self):
        config = dynamic_app.parse_accident_risk_config({'normal_capacity': 4.0})

        self.assertEqual(dynamic_app.parse_warmup_capacity({}, config), 4.0)


class DynamicLegacyBackendRemovalTests(unittest.TestCase):
    def test_rejected_capacity_engine_is_absent_from_active_backend(self):
        source = Path(dynamic_app.__file__).read_text(encoding='utf-8')
        rejected = {
            'phased_markov',
            'balanced_shuffle',
            'dynamic_capacity_transition_matrix',
            'dynamic_capacity_random_rounds',
            'manual_sequence_spec',
            'build_dynamic_departure_schedule',
            'DynamicCapacityConfig',
        }

        for token in rejected:
            with self.subTest(token=token):
                self.assertNotIn(token, source)

    def test_accident_bank_contains_no_legacy_sequence_schema(self):
        bank_text = Path(
            dynamic_app.__file__
        ).with_name('capacity_sequence_bank.json').read_text(encoding='utf-8')

        self.assertNotIn('manual_sequence_spec', bank_text)
        self.assertNotIn('phased_markov', bank_text)


class PlayerBot(Bot):
    cases = ['staggered', 'same_time', 'timeout_recovery']

    def play_round(self):
        if self.round_number == 1:
            expect('开始前最后提醒', 'in', self.html)
            expect('同一轮内保持不变', 'in', self.html)
            expect('固定行驶成本', 'in', self.html)
            expect('具体变化规律不会提前公布', 'in', self.html)
            yield Submission(Introduction, check_html=False)
            expect('同一小组、同一轮', 'in', self.html)
            yield Submission(ComprehensionCheck, check_html=False)
            expect(self.participant.vars.get(COMPREHENSION_SEEN_VAR), '==', True)
            expect('热身环节开始', 'in', self.html)
            expect('不计入正式实验数据', 'in', self.html)
            yield Submission(WarmupStart, check_html=False)

        if self.round_number == C.WARMUP_ROUNDS + 1:
            expect('热身已结束', 'in', self.html)
            expect('正式实验共 60 轮', 'in', self.html)
            yield Submission(FormalStart, check_html=False)

        expect('等待本轮参与者进入', 'in', self.html)
        phase = dynamic_app.round_phase_context(self.round_number)
        expect(phase['round_label'], 'in', self.html)
        expect('data-poll="1500"', 'in', self.html)
        yield Submission(RoundStartSync, check_html=False)

        accident_config = dynamic_app.parse_accident_risk_config(self.session.config)
        group_capacities = {player.dynamic_capacity for player in self.group.get_players()}
        expect(len(group_capacities), '==', 1)
        expect(self.player.dynamic_capacity, '>', 0)
        expect(self.player.dynamic_capacity, '<=', accident_config.normal_capacity)
        if phase['is_warmup']:
            expect('本轮真实瓶颈服务率', 'in', self.html)
            expect(self.player.dynamic_capacity, '==', accident_config.normal_capacity)
        elif accident_config.information_condition == 'I2':
            expect('本轮真实瓶颈服务率', 'in', self.html)
            expect(f'{self.player.dynamic_capacity} 人 / 1 分钟', 'in', self.html)

        if self.case == 'same_time':
            chosen_minute = 474
        else:
            schedule = self.participant.vars[DEPARTURE_SCHEDULE_VAR]
            chosen_minute = schedule['first_departure_minute'] + (
                self.player.id_in_group + self.round_number - 2
            ) % schedule['num_slots']
        if self.case == 'timeout_recovery':
            yield Submission(
                Decision,
                timeout_happened=True,
                check_html=False,
            )
            expect('重新加入实验', 'in', self.html)
            expect(self.participant.vars.get('dropout_active'), '==', True)
            expect(self.player.decision_source, '==', 'timeout_auto')
            yield Submission(RecoveryGate, check_html=False)
            expect(self.participant.vars.get('dropout_active'), '==', False)
            expect(self.player.recovered_this_round, '==', True)
        else:
            yield Submission(Decision, {'departure_minute': chosen_minute}, check_html=False)
        yield Submission(ResultsSync, check_html=False)

        feedback_store = self.group.get_players()[0].participant.vars.get(
            dynamic_app.PUBLIC_FEEDBACK_PARTICIPANT_VAR,
            {},
        )
        feedback = feedback_store.get(str(self.round_number), {})
        expect(feedback.get('round_number'), '==', phase['display_round_number'])
        expect(feedback.get('dynamic_capacity'), '==', self.player.dynamic_capacity)
        expect(
            sum(
                row['participant_count']
                for row in feedback.get('departure_outcomes', [])
            ),
            '==',
            dynamic_app.effective_group_actor_count(
                self.session,
                len(self.group.get_players()),
                self.group.id_in_subsession,
            ),
        )
        expect('上一轮服务率', 'in', self.html)
        expect('本轮真实瓶颈服务率', 'in', self.html)
        expect(f'{self.player.dynamic_capacity} 人 / 1 分钟', 'in', self.html)
        expect('本轮成本与用时', 'in', self.html)
        expect('所有参与者的成本分布', 'in', self.html)
        expect('粗收费', 'in', self.html)
        if self.case == 'timeout_recovery':
            expect(self.player.decision_source, '==', 'timeout_auto')
            expect(self.player.timeout_happened, '==', True)
            expect(self.player.recovery_reason, '==', 'timeout')
        else:
            expect(self.player.decision_source, '==', 'manual')
            expect(self.player.timeout_happened, '==', False)
        expect(self.player.coarse_toll_calibration_capacity, '==', 0)
        if dynamic_app.api_agent_mode(self.session) == 'active':
            agent_records = dynamic_app.active_agent_decisions_for_group(self.group)
            group_api_count = dynamic_app.api_agent_count_per_group(
                self.session,
                self.group.id_in_subsession,
            )
            expect(
                len(agent_records),
                '==',
                group_api_count,
            )
            if group_api_count:
                expect(agent_records[0]['dynamic_capacity'], '==', self.player.dynamic_capacity)
                expect(agent_records[0]['total_cost'], '>=', 0)
                if dynamic_app.rl_fallback_enabled(self.session) and not phase['is_warmup']:
                    expect(agent_records[0]['decision_source'], '==', 'deepseek_fallback_rl')
                    rl_states = self.group.get_players()[0].participant.vars.get(
                        dynamic_app.RL_AGENT_STATE_PARTICIPANT_VAR,
                        {},
                    )
                    rl_state = rl_states.get(agent_records[0]['agent_id'], {})
                    expect(
                        rl_state.get('rounds_observed'),
                        '==',
                        phase['display_round_number'],
                    )
        if dynamic_app.rl_agent_enabled(self.session):
            rl_records = dynamic_app.independent_rl_records_for_group(self.group)
            group_rl_count = dynamic_app.rl_agent_count_per_group(
                self.session,
                self.group.id_in_subsession,
            )
            expect(
                len(rl_records),
                '==',
                group_rl_count,
            )
            if group_rl_count and not phase['is_warmup']:
                expect(rl_records[0]['decision_source'], 'in', {
                    'liu_rel_uniform_initial',
                    'liu_rel_uniform_sparse',
                    'liu_rel_softmax_i0',
                    'liu_rel_softmax_i1',
                    'liu_rel_softmax_i1_backoff_i0',
                    'liu_rel_softmax_i2_kernel',
                    'liu_rel_softmax_i2_backoff_i1',
                    'liu_rel_softmax_i2_backoff_i0',
                    'rl_fallback_lowest_schedule_cost',
                })
                rl_states = self.group.get_players()[0].participant.vars.get(
                    dynamic_app.INDEPENDENT_RL_STATE_PARTICIPANT_VAR,
                    {},
                )
                expect(
                    rl_states[rl_records[0]['agent_id']]['rounds_observed'],
                    '==',
                    phase['display_round_number'],
                )
        expect(self.player.coarse_toll_calibration_players, '==', 0)

        expected_payoff = (
            0.0
            if phase['is_warmup']
            else max(
                0.0,
                C.BASE_POINTS
                - float(self.player.total_cost)
                + float(self.player.reward_bonus),
            )
        )
        expect(self.player.payoff_unrounded, '==', expected_payoff)
        expect(
            float(self.player.payoff),
            '==',
            float(dynamic_app.cu(expected_payoff)),
        )

        if self.case == 'same_time':
            actors_by_minute = {chosen_minute: len(self.group.get_players())}
            for record in dynamic_app.active_virtual_decisions_for_group(self.group):
                minute = record['departure_minute']
                actors_by_minute[minute] = actors_by_minute.get(minute, 0) + 1
            next_available = self.participant.vars[DEPARTURE_SCHEDULE_VAR][
                'first_departure_minute'
            ]
            expected_wait = None
            for minute in sorted(actors_by_minute):
                first_service_start = max(minute, next_available)
                wait = dynamic_app.service_batch_wait_minutes(
                    departure_minute=minute,
                    first_service_start_minute=first_service_start,
                    load=actors_by_minute[minute],
                    capacity=self.player.dynamic_capacity,
                    capacity_window_minutes=C.CAPACITY_WINDOW_MINUTES,
                )
                if minute == chosen_minute:
                    expected_wait = wait
                next_available = dynamic_app.service_batch_clear_minute(
                    first_service_start,
                    actors_by_minute[minute],
                    self.player.dynamic_capacity,
                )
            assert expected_wait is not None
            for group_player in self.group.get_players():
                expect(group_player.dynamic_capacity, '==', self.player.dynamic_capacity)
                expect(group_player.queue_delay_minutes, '==', expected_wait)
                expect(group_player.arrival_minute, '==', self.player.arrival_minute)
                expect(group_player.total_cost, '==', self.player.total_cost)

        yield Submission(Results, check_html=False)

        if self.round_number == C.NUM_ROUNDS:
            capacities = [
                round_player.dynamic_capacity
                for round_player in self.player.in_all_rounds()
                if not dynamic_app.is_warmup_round(round_player.round_number)
            ]
            expect(len(set(capacities)), '>', 1)
            expect(
                float(self.participant.vars[dynamic_app.TOTAL_PAYOFF_VAR]),
                '==',
                sum(
                    round_player.payoff_unrounded
                    for round_player in self.player.in_all_rounds()
                    if not dynamic_app.is_warmup_round(round_player.round_number)
                ),
            )


if __name__ == '__main__':
    unittest.main()
