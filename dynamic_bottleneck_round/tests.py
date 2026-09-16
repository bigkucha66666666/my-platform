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
    def test_three_warmup_rounds_precede_thirty_formal_rounds(self):
        self.assertEqual(getattr(C, 'WARMUP_ROUNDS', None), 3)
        self.assertEqual(getattr(C, 'FORMAL_ROUNDS', None), 30)
        self.assertEqual(C.NUM_ROUNDS, 33)

    def test_raw_rounds_map_to_warmup_and_formal_round_numbers(self):
        is_warmup_round = getattr(dynamic_app, 'is_warmup_round', None)
        formal_round_number = getattr(dynamic_app, 'formal_round_number', None)

        self.assertIsNotNone(is_warmup_round)
        self.assertIsNotNone(formal_round_number)
        self.assertTrue(is_warmup_round(1))
        self.assertTrue(is_warmup_round(2))
        self.assertTrue(is_warmup_round(3))
        self.assertFalse(is_warmup_round(4))
        self.assertIsNone(formal_round_number(1))
        self.assertIsNone(formal_round_number(3))
        self.assertEqual(formal_round_number(4), 1)
        self.assertEqual(formal_round_number(33), 30)

    def test_warmup_capacities_are_fixed_and_span_the_distribution(self):
        parse_warmup_capacity = getattr(dynamic_app, 'parse_warmup_capacity', None)
        self.assertIsNotNone(parse_warmup_capacity)
        config = dynamic_app.parse_stochastic_capacity_config({})

        self.assertEqual(
            [parse_warmup_capacity({}, config, round_number) for round_number in range(1, 4)],
            [1.33, 2.67, 4.00],
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
                'display_total_rounds': 3,
                'round_label': '热身第 2 轮',
            },
        )
        self.assertEqual(
            round_phase_context(4),
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
            self.assertTrue(formal_start.is_displayed(SimpleNamespace(round_number=4)))
            self.assertFalse(formal_start.is_displayed(SimpleNamespace(round_number=5)))

    def test_formal_payoff_total_excludes_warmup_rounds(self):
        formal_payoff_total = getattr(dynamic_app, 'formal_payoff_total', None)
        self.assertIsNotNone(formal_payoff_total)
        rounds = [
            SimpleNamespace(round_number=1, payoff=99),
            SimpleNamespace(round_number=3, payoff=98),
            SimpleNamespace(round_number=4, payoff=10),
            SimpleNamespace(round_number=5, payoff=20),
        ]
        player = SimpleNamespace(in_all_rounds=lambda: rounds)

        self.assertEqual(float(formal_payoff_total(player)), 30)

    def test_formal_payoff_total_prefers_unrounded_payoff(self):
        rounds = [
            SimpleNamespace(round_number=4, payoff=10, payoff_unrounded=10.25),
            SimpleNamespace(round_number=5, payoff=20, payoff_unrounded=20.125),
        ]
        player = SimpleNamespace(in_all_rounds=lambda: rounds)

        self.assertAlmostEqual(
            dynamic_app.formal_payoff_total(player),
            30.375,
            places=12,
        )

    def test_custom_export_omits_warmup_and_renumbers_formal_rounds(self):
        players = [SimpleNamespace(round_number=value) for value in (1, 3, 4, 5)]

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
            for round_number in (1, 3)
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
            group_agent_spec='G01:api=0,rl=0;G02:api=11,rl=0'
        )

        with self.assertRaisesRegex(ValueError, '0 到 10'):
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

        self.assertEqual(slow_wait, 5)
        self.assertAlmostEqual(fast_wait, 5 / 3)

    def test_queue_left_by_earlier_departures_is_included(self):
        wait = service_batch_wait_minutes(
            departure_minute=475,
            first_service_start_minute=478,
            load=3,
            capacity=2,
        )

        self.assertEqual(wait, 4.5)

    def test_under_capacity_still_counts_full_service_time(self):
        self.assertEqual(service_batch_wait_minutes(
            departure_minute=474,
            first_service_start_minute=474,
            load=1,
            capacity=2,
        ), 0.5)

    def test_empty_batch_has_no_extra_travel_time(self):
        self.assertEqual(service_batch_wait_minutes(
            departure_minute=474,
            first_service_start_minute=478,
            load=0,
            capacity=2,
        ), 0)


class DynamicCostExportTests(unittest.TestCase):
    def test_uniform_capacity_cost_uses_only_queue_early_and_late_components(self):
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
        self.assertEqual(components['late_cost'], 3)
        self.assertEqual(components['toll_cost'], 0)
        self.assertEqual(components['total_cost'], 7)

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
            capacity_level='medium',
            information_condition='I0',
            capacity_sequence_id='warmup',
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
    def test_reveal_description_matches_i1(self):
        config = dynamic_app.parse_stochastic_capacity_config(
            {'capacity_information_condition': 'I1'}
        )

        self.assertIn('精确服务率', capacity_reveal_description(config))

    def test_reveal_description_matches_i0(self):
        config = dynamic_app.parse_stochastic_capacity_config(
            {'capacity_information_condition': 'I0'}
        )

        self.assertIn('均匀分布', capacity_reveal_description(config))

    def test_queue_example_uses_integer_teaching_values(self):
        config = dynamic_app.parse_stochastic_capacity_config({})

        example = comprehension_queue_example(config)

        self.assertEqual(example['capacity'], 2)
        self.assertEqual(example['people'], 6)
        self.assertEqual(example['departure_minute'], 474)
        self.assertEqual(example['wait_minutes'], 3)
        self.assertEqual(example['arrival_without_queue_minute'], 480)
        self.assertEqual(example['arrival_with_short_wait_minute'], 481)
        self.assertEqual(example['arrival_minute'], 483)

    def test_comprehension_answer_key_depends_on_information_condition(self):
        answer_key = getattr(dynamic_app, 'comprehension_answer_key', None)
        self.assertIsNotNone(answer_key)
        i0_config = dynamic_app.parse_stochastic_capacity_config(
            {'capacity_information_condition': 'I0'}
        )
        i1_config = dynamic_app.parse_stochastic_capacity_config(
            {'capacity_information_condition': 'I1'}
        )

        self.assertEqual(
            answer_key(i0_config),
            {
                'comprehension_q1': 'b',
                'comprehension_q2': 'a',
                'comprehension_q3': 'c',
                'comprehension_q4': 'a',
            },
        )
        self.assertEqual(answer_key(i1_config)['comprehension_q4'], 'b')

    def test_comprehension_server_validation_allows_wrong_completed_answers(self):
        player = SimpleNamespace()
        i0_config = dynamic_app.parse_stochastic_capacity_config(
            {'capacity_information_condition': 'I0'}
        )
        correct = {
            'comprehension_q1': 'b',
            'comprehension_q2': 'a',
            'comprehension_q3': 'c',
            'comprehension_q4': 'a',
            'comprehension_attempts': 2,
        }

        with patch.object(
            dynamic_app,
            'capacity_config_for_player',
            return_value=i0_config,
        ):
            self.assertIsNone(ComprehensionCheck.error_message(player, correct))
            wrong = {**correct, 'comprehension_q4': 'b'}
            self.assertIsNone(ComprehensionCheck.error_message(player, wrong))
            incomplete = {**correct, 'comprehension_q3': ''}
            self.assertIn(
                '完成全部 4 道题',
                ComprehensionCheck.error_message(player, incomplete),
            )
            unchecked = {**correct, 'comprehension_attempts': 0}
            self.assertIn(
                '检查答案',
                ComprehensionCheck.error_message(player, unchecked),
            )

    def test_comprehension_page_persists_validated_score_and_attempts(self):
        i1_config = dynamic_app.parse_stochastic_capacity_config(
            {'capacity_information_condition': 'I1'}
        )
        player = SimpleNamespace(
            comprehension_q1='b',
            comprehension_q2='a',
            comprehension_q3='c',
            comprehension_q4='b',
            comprehension_score=0,
            comprehension_attempts=3,
            participant=SimpleNamespace(vars={}),
        )

        with patch.object(
            dynamic_app,
            'capacity_config_for_player',
            return_value=i1_config,
        ):
            ComprehensionCheck.before_next_page(player, False)

        self.assertEqual(player.comprehension_score, 4)
        self.assertEqual(player.comprehension_attempts, 3)
        self.assertTrue(player.participant.vars[COMPREHENSION_SEEN_VAR])
        self.assertEqual(
            player.participant.vars[dynamic_app.COMPREHENSION_SCORE_VAR],
            4,
        )
        self.assertEqual(
            player.participant.vars[dynamic_app.COMPREHENSION_ATTEMPTS_VAR],
            3,
        )

    def test_comprehension_fields_and_export_metadata_are_declared(self):
        self.assertEqual(
            ComprehensionCheck.form_fields,
            [
                'comprehension_q1',
                'comprehension_q2',
                'comprehension_q3',
                'comprehension_q4',
                'comprehension_attempts',
            ],
        )
        self.assertIn('comprehension_score', EXPORT_HEADERS)
        self.assertIn('comprehension_attempts', EXPORT_HEADERS)


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
            '随机瓶颈服务率',
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
        self.assertIn('1.33–4.00 主体/分钟的均匀分布', html)
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

    def test_comprehension_check_tests_condition_specific_information_boundary(self):
        html = self.template_text('ComprehensionCheck.html')

        self.assertIn('同一小组、同一轮', html)
        self.assertIn('排队成本', html)
        self.assertIn('早到成本', html)
        self.assertIn('决策前', html)
        self.assertIn('均匀分布范围', html)
        self.assertIn('本轮精确服务率和容量等级', html)
        self.assertIn('{{ comprehension_q4_correct }}', html)
        self.assertNotIn('粗收费', html)
        self.assertIn('class="scenario-box"', html)
        self.assertIn('class="answer-feedback"', html)
        self.assertIn('{{ example_arrival_time }}', html)
        self.assertEqual(html.count('class="question-card"'), 4)
        self.assertIn('name="comprehension_q1"', html)
        self.assertIn('name="comprehension_q4"', html)
        self.assertIn('name="comprehension_attempts"', html)
        self.assertIn('进入练习阶段', html)
        self.assertIn(
            'nextButton.disabled = answeredCount !== cards.length;',
            html,
        )

    def test_decision_has_i0_and_i1_capacity_messages(self):
        html = self.template_text('Decision.html')

        self.assertIn('{{ if current_capacity_revealed }}', html)
        self.assertIn('本轮瓶颈服务率', html)
        self.assertIn('{{ actual_capacity_display }} 主体 / 分钟', html)
        self.assertIn('通行能力等级：{{ capacity_level_label }}', html)
        self.assertIn('本轮服务率将在提交后公布', html)
        self.assertNotIn('事故', html)
        self.assertNotIn('incident', html)
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
        self.assertIn('正式实验共 30 轮', formal_path.read_text(encoding='utf-8'))

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
            'dynamic_bottleneck_round_prod',
        }
        for name in dynamic_names:
            self.assertIn(name, configs)
            config = configs[name]
            self.assertNotIn('dynamic_capacity_draw_mode', config)
            self.assertNotIn('dynamic_capacity_transition_matrix', config)
            self.assertNotIn('coarse_toll_enabled', config)
            self.assertNotIn('reward_treatment_enabled', config)
            self.assertEqual(config['payoff_rounds'], 30)
        self.assertEqual(C.WARMUP_ROUNDS, 3)
        self.assertEqual(C.FORMAL_ROUNDS, 30)
        self.assertEqual(C.NUM_ROUNDS, 33)
        self.assertEqual(C.SYNC_POLL_INTERVAL_SECONDS, 1.5)

        for name in ('single_bottleneck_demo', 'single_bottleneck_prod'):
            self.assertEqual(configs[name]['api_agent_timeout_seconds'], 30)

    def test_export_headers_match_required_round_level_schema(self):
        required = {
            'session_code', 'participant_code', 'group_id', 'round_number',
            'treatment_condition', 'actor_composition', 'information_condition',
            'dynamic_capacity', 'capacity_level', 'capacity_distribution',
            'capacity_min', 'capacity_max', 'capacity_sequence_id',
            'capacity_sequence_seed', 'departure_slot', 'departure_minute',
            'formal_round_number', 'actual_capacity',
            'capacity_revealed_before_decision', 'departure_time',
            'queue_delay', 'arrival_time', 'schedule_early', 'schedule_late',
            'agent_policy_version', 'agent_fallback_reason',
            'queue_delay_minutes', 'arrival_minute', 'early_minutes', 'late_minutes',
            'total_cost', 'payoff', 'decision_source', 'timeout_happened',
            'departure_schedule_num_slots', 'departure_schedule_first_time',
            'departure_schedule_last_time',
        }
        self.assertTrue(required.issubset(set(EXPORT_HEADERS)))


class UniformCapacityExperimentContractTests(unittest.TestCase):
    def test_dynamic_settings_use_uniform_capacity_contract(self):
        import settings

        configs = {config['name']: config for config in settings.SESSION_CONFIGS}
        formal_names = ('dynamic_bottleneck_round_prod',)
        demo = configs['dynamic_bottleneck_round_demo']
        for config in [configs[name] for name in formal_names] + [demo]:
            self.assertEqual(config['capacity_distribution'], 'uniform')
            self.assertEqual(config['capacity_min'], 1.33)
            self.assertEqual(config['capacity_max'], 4.00)
            self.assertEqual(config['capacity_sequence_seed'], 2026091101)
            self.assertNotIn('accident_probability', config)
            self.assertNotIn('accident_loss_alpha', config)
            self.assertNotIn('accident_loss_beta', config)
            self.assertNotIn('dynamic_capacity_draw_mode', config)
            self.assertNotIn('dynamic_capacity_transition_matrix', config)
            self.assertNotIn('dynamic_capacity_values', config)
        for name in formal_names:
            self.assertEqual(configs[name]['capacity_sequence_id'], 'S01')
        self.assertEqual(demo['capacity_sequence_id'], 'auto')

    def test_one_formal_scenario_contains_session_level_treatment_defaults(self):
        import settings

        configs = {config['name']: config for config in settings.SESSION_CONFIGS}
        formal = configs['dynamic_bottleneck_round_prod']
        self.assertEqual(formal['display_name'], '正式实验 · 随机服务率动态瓶颈')
        self.assertEqual(
            formal['group_treatment_spec'],
            'G01:H-I0;G02:HA-I0',
        )
        self.assertEqual(formal['num_demo_participants'], 40)
        self.assertEqual(formal['participant_label_file'], '_rooms/econ101.txt')
        self.assertEqual(formal['participant_label_assignment'], 'sequential')
        self.assertEqual(formal['api_agent_count_per_group'], 0)
        self.assertEqual(formal['rl_agent_count_per_group'], 0)
        self.assertEqual(formal['capacity_information_condition'], 'I0')
        self.assertEqual(formal['flow_preview_enabled'], 0)
        self.assertEqual(formal['payoff_rounds'], 30)
        for retired in (
            'dynamic_bottleneck_round_prod_h_i0',
            'dynamic_bottleneck_round_prod_h_i1',
            'dynamic_bottleneck_round_prod_ha_i0',
            'dynamic_bottleneck_round_prod_ha_i1',
            'dynamic_bottleneck_round_prod_custom',
        ):
            self.assertNotIn(retired, configs)

    def test_rounds_costs_and_fixed_action_space_match_approved_design(self):
        self.assertEqual(C.WARMUP_ROUNDS, 3)
        self.assertEqual(C.FORMAL_ROUNDS, 30)
        self.assertEqual(C.NUM_ROUNDS, 33)
        self.assertEqual(C.NUM_DEPARTURE_SLOTS, 16)
        self.assertEqual(C.FIXED_TRAVEL_TIME_COST, 0)
        self.assertEqual(C.QUEUE_COST_PER_MINUTE, 2)
        self.assertEqual(C.EARLY_COST_PER_MINUTE, 1)
        self.assertEqual(C.LATE_COST_PER_MINUTE, 3)

        schedule = dynamic_app.static_departure_schedule()
        self.assertEqual(schedule['num_slots'], 16)
        self.assertEqual(schedule['first_departure_time'], '07:46')
        self.assertEqual(schedule['last_departure_time'], '08:01')

    @staticmethod
    def make_session(
        name,
        *,
        treatment='H-I0',
        api_mode='off',
        api_count=0,
        rl_enabled='0',
        rl_count=0,
    ):
        return SimpleNamespace(
            config={
                'name': name,
                'capacity_information_condition': (
                    'I1' if treatment.endswith('I1') else 'I0'
                ),
                'capacity_distribution': 'uniform',
                'capacity_min': 1.33,
                'capacity_max': 4.00,
                'capacity_sequence_seed': 2026091101,
                'api_agent_mode': api_mode,
                'api_agent_count_per_group': api_count,
                'rl_agent_enabled': rl_enabled,
                'rl_agent_count_per_group': rl_count,
                'group_agent_spec': '',
                'group_treatment_spec': f'G01:{treatment}',
            },
            vars={},
        )

    def test_formal_human_only_composition_is_exactly_thirty_humans(self):
        session = self.make_session('dynamic_bottleneck_round_prod')

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object() for _ in range(30)]],
        )

        self.assertEqual(result, 'H')

    def test_formal_human_agent_composition_is_ten_plus_ten_plus_ten(self):
        session = self.make_session(
            'dynamic_bottleneck_round_prod',
            treatment='HA-I1',
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
            self.make_session('dynamic_bottleneck_round_prod'),
            self.make_session(
                'dynamic_bottleneck_round_prod',
                treatment='HA-I1',
                api_mode='active',
                api_count=2,
                rl_enabled='1',
                rl_count=2,
            ),
            self.make_session(
                'dynamic_bottleneck_round_prod',
                treatment='HA-I0',
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
        session = self.make_session('dynamic_bottleneck_round_prod')
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
        session = self.make_session('dynamic_bottleneck_round_prod')
        session.config['group_treatment_spec'] = 'G01:H-I0;G03:HA-I1'

        with self.assertRaisesRegex(ValueError, 'G01.*连续'):
            dynamic_app.configure_formal_treatments(session)

    def test_custom_treatment_matrix_rejects_wrong_human_total(self):
        session = self.make_session('dynamic_bottleneck_round_prod')
        session.config['group_treatment_spec'] = 'G01:H-I0;G02:HA-I1'
        treatments = dynamic_app.configure_formal_treatments(session)

        with self.assertRaisesRegex(ValueError, '需要 40 名 Human'):
            dynamic_app.build_treatment_group_matrix(
                [object() for _ in range(39)],
                treatments,
            )

    def test_formal_scenario_derives_runtime_condition_from_selected_treatment(self):
        session = self.make_session(
            'dynamic_bottleneck_round_prod',
            treatment='HA-I1',
            api_mode='active',
            api_count=10,
            rl_enabled='1',
            rl_count=10,
        )
        session.config['capacity_information_condition'] = 'I0'

        dynamic_app.configure_formal_treatments(session)

        self.assertEqual(session.config['capacity_information_condition'], 'I1')
        self.assertEqual(dynamic_app.information_condition_for_group(session, 1), 'I1')

    def test_demo_allows_smaller_actor_count(self):
        session = self.make_session('dynamic_bottleneck_round_demo')

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object() for _ in range(5)]],
        )

        self.assertEqual(result, 'demo')

    def test_formal_preview_allows_one_human_and_keeps_selected_information(self):
        session = self.make_session(
            'dynamic_bottleneck_round_prod',
            treatment='H-I1',
        )
        session.config['flow_preview_enabled'] = 1

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object()]],
        )

        self.assertEqual(result, 'preview')
        self.assertEqual(dynamic_app.information_condition_for_group(session, 1), 'I1')

    def test_formal_preview_rejects_agent_treatment(self):
        session = self.make_session(
            'dynamic_bottleneck_round_prod',
            treatment='HA-I0',
            api_mode='active',
            api_count=10,
            rl_enabled='1',
            rl_count=10,
        )
        session.config['flow_preview_enabled'] = 1

        with self.assertRaisesRegex(ValueError, 'Human-only'):
            dynamic_app.validate_formal_actor_composition(
                session,
                [[object()]],
            )

    def test_formal_preview_rejects_multiple_groups(self):
        session = self.make_session('dynamic_bottleneck_round_prod')
        session.config.update(
            {
                'flow_preview_enabled': 1,
                'group_treatment_spec': 'G01:H-I0;G02:H-I1',
            }
        )

        with self.assertRaisesRegex(ValueError, '只允许一个'):
            dynamic_app.validate_formal_actor_composition(
                session,
                [[object()], [object()]],
            )

    def test_formal_preview_group_matrix_skips_room_label_binding(self):
        session = self.make_session(
            'dynamic_bottleneck_round_prod',
            treatment='H-I0',
        )
        session.config.update(
            {
                'flow_preview_enabled': 1,
                'participant_label_assignment': 'sequential',
            }
        )
        players = [object()]

        matrix, label_plan = dynamic_app.prepare_formal_group_matrix(
            session,
            players,
        )

        self.assertEqual(matrix, [players])
        self.assertEqual(label_plan, {})
        self.assertEqual(session.config['participant_label_assignment'], '')


class DynamicCapacityLifecycleTests(unittest.TestCase):
    @staticmethod
    def make_session(name='dynamic_bottleneck_round_demo', preset='auto'):
        return SimpleNamespace(
            config={
                'name': name,
                'capacity_distribution': 'uniform',
                'capacity_min': 1.33,
                'capacity_max': 4.00,
                'capacity_sequence_seed': 2026091101,
                'capacity_information_condition': 'I1',
                'capacity_sequence_id': preset,
            },
            vars={},
        )

    @staticmethod
    def make_player():
        return SimpleNamespace(participant=SimpleNamespace(vars={}))

    def test_production_rejects_auto_sequence(self):
        session = self.make_session('dynamic_bottleneck_round_prod', 'auto')

        with self.assertRaisesRegex(ValueError, '正式.*S01-S05'):
            dynamic_app.capacity_sequence_for_session(session)

    def test_named_sequence_loads_frozen_bank_records(self):
        session = self.make_session('dynamic_bottleneck_round_prod', 'S01')

        records = dynamic_app.capacity_sequence_for_session(session)

        self.assertEqual(len(records), 30)
        self.assertEqual(records[0]['sequence_id'], 'S01')
        self.assertEqual(records[-1]['formal_round_number'], 30)

    def test_named_sequence_rejects_mismatched_config_seed(self):
        session = self.make_session('dynamic_bottleneck_round_prod', 'S02')

        with self.assertRaisesRegex(ValueError, 'capacity_sequence_seed.*S02'):
            dynamic_app.capacity_sequence_for_session(session)

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

        dynamic_app.initialize_capacity_sequence(subsession)

        stored = session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR]
        self.assertEqual(len(stored), 30)
        self.assertNotIn(
            'dynamic_bottleneck_round_capacity_sequence',
            group_one.get_players()[0].participant.vars,
        )
        self.assertNotIn(
            'dynamic_bottleneck_round_capacity_sequence',
            group_two.get_players()[0].participant.vars,
        )

    def test_warmup_uses_fixed_capacity_without_consuming_formal_sequence(self):
        session = self.make_session()
        formal_records = [
            {
                'formal_round_number': 1,
                'actual_capacity': 1.33,
                'capacity_level': 'low',
                'sequence_id': 'auto',
                'sequence_seed': 2026091101,
                'distribution': 'uniform',
                'capacity_min': 1.33,
                'capacity_max': 4.0,
                'stratum_index': 1,
            }
        ] * 30
        session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR] = formal_records
        player = self.make_player()
        group = SimpleNamespace(
            round_number=3,
            session=session,
            get_players=lambda: [player],
        )

        dynamic_app.apply_round_capacity(group)

        self.assertEqual(group.dynamic_capacity, 4.0)
        self.assertEqual(group.capacity_level, 'high')
        self.assertEqual(group.capacity_sequence_id, 'warmup')
        self.assertEqual(player.dynamic_capacity, 4.0)
        self.assertEqual(
            session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR],
            formal_records,
        )

    def test_formal_round_reads_corresponding_frozen_record(self):
        session = self.make_session('dynamic_bottleneck_round_prod', 'S01')
        session.config['group_treatment_spec'] = 'G01:H-I1'
        dynamic_app.configure_formal_treatments(session)
        records = dynamic_app.capacity_sequence_for_session(session)
        session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR] = records
        player = self.make_player()
        group = SimpleNamespace(
            round_number=C.WARMUP_ROUNDS + 1,
            session=session,
            get_players=lambda: [player],
        )

        dynamic_app.apply_round_capacity(group)

        expected = records[0]
        self.assertEqual(group.dynamic_capacity, expected['actual_capacity'])
        self.assertEqual(group.capacity_level, expected['capacity_level'])
        self.assertEqual(group.capacity_sequence_id, 'S01')
        self.assertEqual(player.information_condition, 'I1')

    def test_custom_groups_share_capacity_but_keep_distinct_information(self):
        session = self.make_session('dynamic_bottleneck_round_prod', 'S01')
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
        records = dynamic_app.capacity_sequence_for_session(session)
        session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR] = records
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
        self.assertEqual(group_i0.capacity_level, group_i1.capacity_level)
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


class UniformCapacityLifecycleContractTests(unittest.TestCase):
    @staticmethod
    def make_session(name='dynamic_bottleneck_round_demo', sequence_id='auto'):
        return SimpleNamespace(
            config={
                'name': name,
                'capacity_distribution': 'uniform',
                'capacity_min': 1.33,
                'capacity_max': 4.00,
                'capacity_sequence_seed': 2026091101,
                'capacity_information_condition': 'I1',
                'capacity_sequence_id': sequence_id,
            },
            vars={},
        )

    @staticmethod
    def make_player():
        return SimpleNamespace(participant=SimpleNamespace(vars={}))

    def test_formal_session_rejects_auto_capacity_sequence(self):
        session = self.make_session('dynamic_bottleneck_round_prod', 'auto')

        with self.assertRaisesRegex(ValueError, '正式.*S01-S05'):
            dynamic_app.capacity_sequence_for_session(session)

    def test_named_sequence_loads_frozen_uniform_records(self):
        session = self.make_session('dynamic_bottleneck_round_prod', 'S01')

        records = dynamic_app.capacity_sequence_for_session(session)

        self.assertEqual(len(records), 30)
        self.assertEqual(records[0]['sequence_id'], 'S01')
        self.assertEqual(records[-1]['formal_round_number'], 30)
        self.assertTrue(all('incident_occurred' not in record for record in records))
        self.assertTrue(
            all(record['actual_capacity'] == round(record['actual_capacity'], 2)
                for record in records)
        )

    def test_initialization_stores_one_session_sequence_not_player_copies(self):
        session = self.make_session()
        player_one = self.make_player()
        player_two = self.make_player()
        subsession = SimpleNamespace(
            session=session,
            get_groups=lambda: [
                SimpleNamespace(get_players=lambda: [player_one]),
                SimpleNamespace(get_players=lambda: [player_two]),
            ],
        )

        dynamic_app.initialize_capacity_sequence(subsession)

        stored = session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR]
        self.assertEqual(len(stored), 30)
        self.assertNotIn(dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR, player_one.participant.vars)
        self.assertNotIn(dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR, player_two.participant.vars)

    def test_warmup_uses_fixed_public_sequence_without_consuming_formal_records(self):
        session = self.make_session()
        formal_records = dynamic_app.capacity_sequence_for_session(session)
        session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR] = formal_records
        player = self.make_player()
        group = SimpleNamespace(
            id_in_subsession=1,
            round_number=3,
            session=session,
            get_players=lambda: [player],
        )

        dynamic_app.apply_round_capacity(group)

        self.assertEqual(group.dynamic_capacity, dynamic_app.WARMUP_CAPACITIES[2])
        self.assertEqual(group.capacity_level, 'high')
        self.assertEqual(group.capacity_sequence_id, 'warmup')
        self.assertEqual(session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR], formal_records)
        self.assertEqual(player.dynamic_capacity, group.dynamic_capacity)
        self.assertTrue(player.capacity_revealed_before_decision)

    def test_formal_groups_share_capacity_and_keep_distinct_information(self):
        session = self.make_session('dynamic_bottleneck_round_prod', 'S01')
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
        records = dynamic_app.capacity_sequence_for_session(session)
        session.vars[dynamic_app.CAPACITY_SEQUENCE_SESSION_VAR] = records
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
        self.assertEqual(group_i0.capacity_level, group_i1.capacity_level)
        self.assertEqual(group_i0.information_condition, 'I0')
        self.assertEqual(group_i1.information_condition, 'I1')
        self.assertFalse(player_i0.capacity_revealed_before_decision)
        self.assertTrue(player_i1.capacity_revealed_before_decision)


class DynamicContinuousQueueTests(unittest.TestCase):
    def test_float_capacity_uses_continuous_batch_duration(self):
        wait = dynamic_app.service_batch_wait_minutes(
            departure_minute=474,
            first_service_start_minute=474,
            load=5,
            capacity=1.5,
        )

        self.assertAlmostEqual(wait, 5 / 1.5, places=10)

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
            (first_clear - 476) + 2 / 1.5,
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


class DynamicUniformCapacityCostTests(unittest.TestCase):
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
                'late_cost': 12.0,
                'toll_cost': 0.0,
                'total_cost': 19.0,
            },
        )

    def test_legacy_toll_argument_cannot_change_capacity_cost(self):
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
            dynamic_app.capacity_incentives_for_slot(session, 8),
            {'reward_bonus': 0.0, 'coarse_toll_charge': 0.0},
        )


class DynamicUniformCapacityExportTests(unittest.TestCase):
    @staticmethod
    def make_player_and_group():
        session = SimpleNamespace(code='SESSION01', config={}, vars={})
        participant = SimpleNamespace(vars={})
        player = SimpleNamespace(
            session=session,
            participant=participant,
            round_number=C.WARMUP_ROUNDS + 1,
            dynamic_capacity=1.50123456789,
            capacity_level='low',
            capacity_distribution='uniform',
            capacity_min=1.33,
            capacity_max=4.00,
            information_condition='I1',
            capacity_sequence_id='S01',
            capacity_sequence_seed=2026091101,
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
            capacity_level='low',
            information_condition='I1',
            capacity_sequence_id='S01',
            capacity_sequence_seed=2026091101,
            get_players=lambda: [player],
        )
        player.group = group
        return player, group

    def test_public_snapshot_contains_realized_capacity_without_identity(self):
        player, group = self.make_player_and_group()

        snapshot = dynamic_app.public_feedback_snapshot_for_group(
            group,
            virtual_records=[],
        )

        self.assertEqual(snapshot['actual_capacity'], player.dynamic_capacity)
        self.assertEqual(snapshot['capacity_level'], 'low')
        self.assertEqual(snapshot['capacity_distribution'], 'uniform')
        self.assertEqual(snapshot['information_condition'], 'I1')
        self.assertNotIn('capacity_sequence_id', snapshot)
        self.assertNotIn('capacity_sequence_seed', snapshot)
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn('participant_code', serialized)
        self.assertNotIn('actor_type', serialized)
        self.assertNotIn('agent_id', serialized)

    def test_export_schema_uses_uniform_capacity_fields_not_legacy_fields(self):
        required = {
            'treatment_condition',
            'actor_composition',
            'information_condition',
            'flow_preview_enabled',
            'dynamic_capacity',
            'capacity_level',
            'capacity_distribution',
            'capacity_min',
            'capacity_max',
            'capacity_sequence_id',
            'capacity_sequence_seed',
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
            'incident_occurred',
            'capacity_loss_ratio',
            'remaining_capacity_ratio',
            'accident_sequence_id',
            'accident_sequence_seed',
        }

        self.assertTrue(required.issubset(EXPORT_HEADERS))
        self.assertTrue(removed.isdisjoint(EXPORT_HEADERS))

    def test_export_metadata_rounds_only_serialized_capacity_values(self):
        player, _group = self.make_player_and_group()

        metadata = dynamic_app.capacity_export_metadata(player)

        self.assertEqual(metadata['treatment_condition'], 'H-I1')
        self.assertEqual(metadata['dynamic_capacity'], 1.501235)
        self.assertEqual(metadata['capacity_level'], 'low')
        self.assertEqual(player.dynamic_capacity, 1.50123456789)

    def test_human_export_rounds_continuous_results_to_six_decimals(self):
        player, _group = self.make_player_and_group()
        player.participant.vars.update(
            {
                dynamic_app.COMPREHENSION_SCORE_VAR: 3,
                dynamic_app.COMPREHENSION_ATTEMPTS_VAR: 2,
            }
        )

        exported = dict(zip(
            EXPORT_HEADERS,
            dynamic_app.export_row_for_player(player),
        ))

        self.assertEqual(exported['queue_delay_minutes'], 1.234568)
        self.assertEqual(exported['queue_delay'], 1.234568)
        self.assertEqual(exported['arrival_minute'], 475.234568)
        self.assertEqual(exported['late_minutes'], 1.234568)
        self.assertEqual(exported['total_cost'], 8.641975)
        self.assertEqual(exported['payoff'], 131.358025)
        self.assertEqual(exported['comprehension_score'], 3)
        self.assertEqual(exported['comprehension_attempts'], 2)

    def test_custom_export_marks_formal_flow_preview_rows(self):
        player, _group = self.make_player_and_group()
        player.session.config.update(
            {
                'name': 'dynamic_bottleneck_round_prod',
                'flow_preview_enabled': 1,
            }
        )

        exported = dict(zip(
            EXPORT_HEADERS,
            dynamic_app.export_row_for_player(player),
        ))

        self.assertIs(exported['flow_preview_enabled'], True)

    def test_agent_export_uses_agent_batch_load_and_six_decimal_capacity(self):
        player, _group = self.make_player_and_group()
        player.participant.vars.update(
            {
                dynamic_app.COMPREHENSION_SCORE_VAR: 4,
                dynamic_app.COMPREHENSION_ATTEMPTS_VAR: 2,
            }
        )
        record = {
            'group_id': 1,
            'dynamic_capacity': 1.50123456789,
            'capacity_level': 'low',
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
        self.assertEqual(exported['queue_delay'], 2.345679)
        self.assertEqual(exported['total_cost'], 21.419752)
        self.assertEqual(exported['comprehension_score'], '')
        self.assertEqual(exported['comprehension_attempts'], '')


class DynamicCapacityPresentationBackendTests(unittest.TestCase):
    @staticmethod
    def decision_context(information_condition, actual_capacity=2.37):
        group = SimpleNamespace(
            round_number=C.WARMUP_ROUNDS + 1,
            id_in_subsession=1,
            dynamic_capacity=actual_capacity,
            capacity_level=dynamic_app.capacity_level(actual_capacity),
            capacity_sequence_id='S01',
            capacity_sequence_seed=2026091101,
            session=SimpleNamespace(
                config={
                    'capacity_distribution': 'uniform',
                    'capacity_min': 1.33,
                    'capacity_max': 4.0,
                    'capacity_information_condition': information_condition,
                },
                vars={},
            ),
        )
        player = SimpleNamespace(
            group=group,
            round_number=group.round_number,
        )
        with (
            patch.object(dynamic_app, 'departure_schedule_for_player', return_value={
                'first_departure_time': '07:40',
                'last_departure_time': '08:00',
            }),
            patch.object(dynamic_app, 'choice_preview', return_value=[]),
            patch.object(dynamic_app.Decision, 'get_timeout_seconds', return_value=90),
            patch.object(
                dynamic_app,
                'coarse_toll_description_for_player',
                return_value='当前未开启粗收费。',
            ),
        ):
            return dynamic_app.Decision.vars_for_template(player)

    def test_i1_decision_context_discloses_exact_capacity_and_level(self):
        context = self.decision_context('I1', 2.37)

        self.assertTrue(context['current_capacity_revealed'])
        self.assertEqual(context['actual_capacity'], 2.37)
        self.assertEqual(context['actual_capacity_display'], '2.37')
        self.assertEqual(context['capacity_level'], 'medium')
        self.assertEqual(context['capacity_level_label'], '中')
        self.assertNotIn('incident_occurred', context)
        self.assertNotIn('capacity_loss_ratio', context)

    def test_i0_decision_context_does_not_disclose_capacity_or_level(self):
        context = self.decision_context('I0', 2.37)

        self.assertFalse(context['current_capacity_revealed'])
        self.assertNotIn('actual_capacity', context)
        self.assertNotIn('actual_capacity_display', context)
        self.assertNotIn('capacity_level', context)
        self.assertNotIn('capacity_level_label', context)

    def test_capacity_rows_describe_three_equal_probability_ranges(self):
        config = dynamic_app.parse_stochastic_capacity_config({})

        rows = dynamic_app.capacity_state_rows(config)

        self.assertEqual([row['state'] for row in rows], ['low', 'medium', 'high'])
        self.assertEqual([row['label'] for row in rows], ['低', '中', '高'])
        self.assertTrue(all(row['probability'] == 1 / 3 for row in rows))
        self.assertEqual(rows[0]['range_label'], '1.33–2.21')
        self.assertEqual(rows[-1]['range_label'], '3.11–4.00')

    def test_information_descriptions_match_i0_i1(self):
        descriptions = {
            condition: dynamic_app.capacity_reveal_description(
                dynamic_app.parse_stochastic_capacity_config(
                    {'capacity_information_condition': condition}
                )
            )
            for condition in ('I0', 'I1')
        }

        self.assertIn('均匀分布', descriptions['I0'])
        self.assertIn('精确服务率', descriptions['I1'])

    def test_warmup_capacity_sequence_is_predeclared(self):
        config = dynamic_app.parse_stochastic_capacity_config({})

        self.assertEqual(
            [dynamic_app.parse_warmup_capacity({}, config, round_number)
             for round_number in range(1, 4)],
            [1.33, 2.67, 4.00],
        )


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

    def test_uniform_bank_contains_no_legacy_sequence_schema(self):
        bank_text = Path(
            dynamic_app.__file__
        ).with_name('uniform_capacity_sequence_bank.json').read_text(encoding='utf-8')

        self.assertNotIn('manual_sequence_spec', bank_text)
        self.assertNotIn('phased_markov', bank_text)
        self.assertNotIn('incident_occurred', bank_text)
        self.assertNotIn('capacity_loss_ratio', bank_text)


class PlayerBot(Bot):
    cases = ['staggered', 'same_time', 'timeout_recovery']

    def play_round(self):
        if self.round_number == 1:
            expect('开始前最后提醒', 'in', self.html)
            expect('同一轮内保持不变', 'in', self.html)
            expect('固定行驶成本', 'in', self.html)
            expect('均匀分布', 'in', self.html)
            yield Submission(Introduction, check_html=False)
            expect('同一小组、同一轮', 'in', self.html)
            capacity_config = dynamic_app.capacity_config_for_player(self.player)
            comprehension_answers = dynamic_app.comprehension_answer_key(
                capacity_config
            )
            expected_comprehension_score = 4
            if self.case == 'same_time':
                comprehension_answers['comprehension_q1'] = 'a'
                expected_comprehension_score = 3
            comprehension_answers['comprehension_attempts'] = 1
            yield Submission(
                ComprehensionCheck,
                comprehension_answers,
                check_html=False,
            )
            expect(self.participant.vars.get(COMPREHENSION_SEEN_VAR), '==', True)
            expect(
                self.player.comprehension_score,
                '==',
                expected_comprehension_score,
            )
            expect(self.player.comprehension_attempts, '==', 1)
            expect('热身环节开始', 'in', self.html)
            expect('不计入正式实验数据', 'in', self.html)
            yield Submission(WarmupStart, check_html=False)

        if self.round_number == C.WARMUP_ROUNDS + 1:
            expect('热身已结束', 'in', self.html)
            expect('正式实验共 30 轮', 'in', self.html)
            yield Submission(FormalStart, check_html=False)

        expect('等待本轮参与者进入', 'in', self.html)
        phase = dynamic_app.round_phase_context(self.round_number)
        expect(phase['round_label'], 'in', self.html)
        expect('data-poll="1500"', 'in', self.html)
        yield Submission(RoundStartSync, check_html=False)

        capacity_config = dynamic_app.parse_stochastic_capacity_config(self.session.config)
        group_capacities = {player.dynamic_capacity for player in self.group.get_players()}
        expect(len(group_capacities), '==', 1)
        expect(self.player.dynamic_capacity, '>', 0)
        expect(self.player.dynamic_capacity, '<=', capacity_config.capacity_max)
        if phase['is_warmup']:
            expect('本轮瓶颈服务率', 'in', self.html)
        elif capacity_config.information_condition == 'I1':
            expect('本轮精确服务率', 'in', self.html)
            expect(f'{self.player.dynamic_capacity:.2f}', 'in', self.html)

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
        expect(f'{self.player.dynamic_capacity:.2f} 主体 / 分钟', 'in', self.html)
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
                # Independent expected value: all bottleneck passage time is
                # additional, including the first service window.
                wait = (
                    first_service_start - minute
                    + actors_by_minute[minute] / self.player.dynamic_capacity
                    * C.CAPACITY_WINDOW_MINUTES
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
