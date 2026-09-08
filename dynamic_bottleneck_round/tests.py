from collections import Counter
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from math import ceil
from otree.api import Bot, Submission, expect

import dynamic_bottleneck_round as dynamic_app

from . import (
    DynamicCapacityConfigError,
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
    build_capacity_round_records,
    capacity_reveal_description,
    comprehension_queue_example,
    decision_capacity_context,
    decision_submission_closed,
    generate_capacity_sequence,
    mark_round_ready,
    maybe_start_round,
    parse_dynamic_capacity_config,
    remaining_decision_seconds,
    round_start_wait_seconds,
    service_batch_wait_minutes,
    should_start_round,
)


class DynamicCapacityConfigTests(unittest.TestCase):
    def test_valid_config_is_parsed_with_matching_probabilities(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'dynamic_capacity_seed': 20260718,
                'dynamic_capacity_draw_mode': 'balanced_shuffle',
                'capacity_reveal_timing': 'before_decision',
            }
        )

        self.assertEqual(config.values, (1, 2, 3))
        self.assertEqual(config.probabilities, (0.3, 0.5, 0.2))
        self.assertEqual(config.seed, 20260718)
        self.assertEqual(config.draw_mode, 'balanced_shuffle')
        self.assertEqual(config.reveal_timing, 'before_decision')

    def test_probability_count_must_match_capacity_count(self):
        with self.assertRaisesRegex(
            DynamicCapacityConfigError,
            'dynamic_capacity_values.*dynamic_capacity_probabilities',
        ):
            parse_dynamic_capacity_config(
                {
                    'dynamic_capacity_values': '1,2,3',
                    'dynamic_capacity_probabilities': '0.5,0.5',
                }
            )

    def test_probabilities_must_sum_to_one(self):
        with self.assertRaisesRegex(DynamicCapacityConfigError, '概率之和必须为 1'):
            parse_dynamic_capacity_config(
                {
                    'dynamic_capacity_values': '1,2,3',
                    'dynamic_capacity_probabilities': '0.3,0.3,0.3',
                }
            )

    def test_probabilities_must_be_finite(self):
        with self.assertRaisesRegex(DynamicCapacityConfigError, '有限数'):
            parse_dynamic_capacity_config(
                {
                    'dynamic_capacity_values': '1,2,3',
                    'dynamic_capacity_probabilities': 'NaN,0.5,0.5',
                }
            )

    def test_capacity_values_must_be_positive_integers(self):
        with self.assertRaisesRegex(DynamicCapacityConfigError, '服务率必须为正整数'):
            parse_dynamic_capacity_config(
                {
                    'dynamic_capacity_values': '1,0,3',
                    'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                }
            )

    def test_phased_markov_config_parses_phase_and_transition_settings(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3333333333333333,0.3333333333333333,0.3333333333333334',
                'dynamic_capacity_draw_mode': 'phased_markov',
                'dynamic_capacity_random_rounds': 20,
                'dynamic_capacity_transition_matrix': (
                    '0.8,0.1,0.1;0.1,0.8,0.1;0.1,0.1,0.8'
                ),
            }
        )

        self.assertEqual(config.random_rounds, 20)
        self.assertEqual(
            config.transition_matrix,
            (
                (0.8, 0.1, 0.1),
                (0.1, 0.8, 0.1),
                (0.1, 0.1, 0.8),
            ),
        )

    def test_transition_matrix_rows_must_match_states_and_sum_to_one(self):
        with self.assertRaisesRegex(
            DynamicCapacityConfigError,
            'dynamic_capacity_transition_matrix',
        ):
            parse_dynamic_capacity_config(
                {
                    'dynamic_capacity_values': '1,2,3',
                    'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                    'dynamic_capacity_draw_mode': 'phased_markov',
                    'dynamic_capacity_transition_matrix': (
                        '0.8,0.2;0.1,0.8,0.1;0.1,0.1,0.8'
                    ),
                }
            )

    def test_named_sequence_preset_resolves_to_bank_manual_sequence(self):
        bank = json.loads(
            Path('dynamic_bottleneck_round/capacity_sequence_bank.json').read_text(
                encoding='utf-8'
            )
        )
        expected = next(
            record['manual_sequence_spec']
            for record in bank['sequences']
            if record['id'] == 'S01'
        )

        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': (
                    '0.3333333333333333,0.3333333333333333,0.3333333333333334'
                ),
                'dynamic_capacity_draw_mode': 'phased_markov',
                'dynamic_capacity_sequence_preset': 'S01',
            }
        )

        self.assertEqual(config.draw_mode, 'manual_sequence')
        self.assertEqual(config.manual_sequence, tuple(map(int, expected.split(','))))

    def test_unknown_sequence_preset_is_rejected(self):
        with self.assertRaisesRegex(
            DynamicCapacityConfigError,
            'dynamic_capacity_sequence_preset.*S99',
        ):
            parse_dynamic_capacity_config(
                {
                    'dynamic_capacity_values': '1,2,3',
                    'dynamic_capacity_probabilities': (
                        '0.3333333333333333,0.3333333333333333,0.3333333333333334'
                    ),
                    'dynamic_capacity_sequence_preset': 'S99',
                }
            )


class DynamicCapacitySequenceTests(unittest.TestCase):
    def setUp(self):
        self.config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'dynamic_capacity_seed': 20260718,
                'dynamic_capacity_draw_mode': 'balanced_shuffle',
                'capacity_reveal_timing': 'before_decision',
            }
        )

    def test_balanced_shuffle_matches_target_counts_for_ten_rounds(self):
        sequence = generate_capacity_sequence(self.config, rounds=10, group_id=1)

        self.assertEqual(Counter(sequence), Counter({1: 3, 2: 5, 3: 2}))

    def test_same_seed_and_group_generate_same_sequence(self):
        first = generate_capacity_sequence(self.config, rounds=10, group_id=2)
        second = generate_capacity_sequence(self.config, rounds=10, group_id=2)

        self.assertEqual(first, second)

    def test_sequence_only_contains_configured_capacities(self):
        sequence = generate_capacity_sequence(self.config, rounds=100, group_id=3)

        self.assertTrue(set(sequence).issubset(set(self.config.values)))

    def test_iid_mode_is_reproducible(self):
        iid_config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'dynamic_capacity_seed': 20260718,
                'dynamic_capacity_draw_mode': 'iid',
                'capacity_reveal_timing': 'after_decision',
            }
        )

        first = generate_capacity_sequence(iid_config, rounds=40, group_id=4)
        second = generate_capacity_sequence(iid_config, rounds=40, group_id=4)

        self.assertEqual(first, second)
        self.assertTrue(set(first).issubset(set(iid_config.values)))

    def test_round_records_include_previous_capacity_and_probability(self):
        records = build_capacity_round_records(self.config, rounds=10, group_id=1)

        self.assertEqual(len(records), 10)
        self.assertIsNone(records[0]['previous_capacity'])
        for index, record in enumerate(records):
            self.assertIn(record['capacity'], self.config.values)
            self.assertEqual(record['state'], f"capacity_{record['capacity']}")
            probability_index = self.config.values.index(record['capacity'])
            self.assertEqual(
                record['probability'],
                self.config.probabilities[probability_index],
            )
            if index:
                self.assertEqual(record['previous_capacity'], records[index - 1]['capacity'])

    def test_session_scope_uses_the_same_sequence_for_every_group(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'dynamic_capacity_seed': 20260718,
                'dynamic_capacity_draw_mode': 'balanced_shuffle',
                'dynamic_capacity_sequence_scope': 'session',
                'capacity_reveal_timing': 'before_decision',
            }
        )

        group_one = generate_capacity_sequence(config, rounds=10, group_id=1)
        group_two = generate_capacity_sequence(config, rounds=10, group_id=2)

        self.assertEqual(group_one, group_two)

    def test_invalid_capacity_sequence_scope_is_rejected(self):
        with self.assertRaisesRegex(
            DynamicCapacityConfigError,
            'dynamic_capacity_sequence_scope',
        ):
            parse_dynamic_capacity_config(
                {
                    'dynamic_capacity_values': '1,2,3',
                    'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                    'dynamic_capacity_sequence_scope': 'participant',
                }
            )
    def test_group_scope_keeps_independent_group_sequences(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'dynamic_capacity_seed': 20260718,
                'dynamic_capacity_draw_mode': 'balanced_shuffle',
                'dynamic_capacity_sequence_scope': 'group',
                'capacity_reveal_timing': 'before_decision',
            }
        )

        group_one = generate_capacity_sequence(config, rounds=10, group_id=1)
        group_two = generate_capacity_sequence(config, rounds=10, group_id=2)

        self.assertNotEqual(group_one, group_two)

    def test_phased_markov_switches_from_random_to_transition_rule_at_round_21(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '1,0,0',
                'dynamic_capacity_seed': 12,
                'dynamic_capacity_draw_mode': 'phased_markov',
                'dynamic_capacity_random_rounds': 20,
                'dynamic_capacity_transition_matrix': (
                    '0,1,0;0,0,1;0,0,1'
                ),
                'dynamic_capacity_sequence_scope': 'session',
                'capacity_reveal_timing': 'after_decision',
            }
        )

        sequence = generate_capacity_sequence(config, rounds=60, group_id=1)

        self.assertEqual(sequence[:20], [1] * 20)
        self.assertEqual(sequence[20], 2)
        self.assertEqual(sequence[21:], [3] * 39)

    def test_phased_markov_is_reproducible_and_shared_across_groups(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3333333333333333,0.3333333333333333,0.3333333333333334',
                'dynamic_capacity_seed': 20260718,
                'dynamic_capacity_draw_mode': 'phased_markov',
                'dynamic_capacity_random_rounds': 20,
                'dynamic_capacity_transition_matrix': (
                    '0.8,0.1,0.1;0.1,0.8,0.1;0.1,0.1,0.8'
                ),
                'dynamic_capacity_sequence_scope': 'session',
                'capacity_reveal_timing': 'after_decision',
            }
        )

        first = generate_capacity_sequence(config, rounds=60, group_id=1)
        repeated = generate_capacity_sequence(config, rounds=60, group_id=1)
        other_group = generate_capacity_sequence(config, rounds=60, group_id=2)

        self.assertEqual(len(first), 60)
        self.assertEqual(first, repeated)
        self.assertEqual(first, other_group)
        self.assertTrue(set(first).issubset({1, 2, 3}))

    def test_manual_sequence_mode_uses_exact_60_round_sequence(self):
        sequence = ([1, 2, 3] * 20)
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3333333333333333,0.3333333333333333,0.3333333333333334',
                'dynamic_capacity_draw_mode': 'manual_sequence',
                'dynamic_capacity_manual_sequence': ','.join(map(str, sequence)),
                'dynamic_capacity_sequence_scope': 'session',
                'capacity_reveal_timing': 'after_decision',
            }
        )

        self.assertEqual(
            generate_capacity_sequence(config, rounds=60, group_id=99),
            sequence,
        )

    def test_manual_sequence_rejects_wrong_length_and_unknown_capacity(self):
        wrong_length = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3333333333333333,0.3333333333333333,0.3333333333333334',
                'dynamic_capacity_draw_mode': 'manual_sequence',
                'dynamic_capacity_manual_sequence': '1,2,3',
            }
        )
        with self.assertRaisesRegex(DynamicCapacityConfigError, '恰好包含 60 个'):
            generate_capacity_sequence(wrong_length, rounds=60, group_id=1)

        unknown_capacity = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3333333333333333,0.3333333333333333,0.3333333333333334',
                'dynamic_capacity_draw_mode': 'manual_sequence',
                'dynamic_capacity_manual_sequence': ','.join(['4'] * 60),
            }
        )
        with self.assertRaisesRegex(DynamicCapacityConfigError, '候选集合'):
            generate_capacity_sequence(unknown_capacity, rounds=60, group_id=1)


class WarmupRoundPhaseTests(unittest.TestCase):
    def test_two_warmup_rounds_precede_sixty_formal_rounds(self):
        self.assertEqual(getattr(C, 'WARMUP_ROUNDS', None), 2)
        self.assertEqual(getattr(C, 'FORMAL_ROUNDS', None), 60)
        self.assertEqual(C.NUM_ROUNDS, 62)

    def test_raw_rounds_map_to_warmup_and_formal_round_numbers(self):
        is_warmup_round = getattr(dynamic_app, 'is_warmup_round', None)
        formal_round_number = getattr(dynamic_app, 'formal_round_number', None)

        self.assertIsNotNone(is_warmup_round)
        self.assertIsNotNone(formal_round_number)
        self.assertTrue(is_warmup_round(1))
        self.assertTrue(is_warmup_round(2))
        self.assertFalse(is_warmup_round(3))
        self.assertIsNone(formal_round_number(1))
        self.assertIsNone(formal_round_number(2))
        self.assertEqual(formal_round_number(3), 1)
        self.assertEqual(formal_round_number(62), 60)

    def test_warmup_capacity_must_be_a_configured_positive_state(self):
        parse_warmup_capacity = getattr(dynamic_app, 'parse_warmup_capacity', None)
        self.assertIsNotNone(parse_warmup_capacity)
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
            }
        )

        self.assertEqual(
            parse_warmup_capacity({'dynamic_warmup_capacity': 2}, config),
            2,
        )
        with self.assertRaisesRegex(DynamicCapacityConfigError, 'dynamic_warmup_capacity'):
            parse_warmup_capacity({'dynamic_warmup_capacity': 0}, config)
        with self.assertRaisesRegex(DynamicCapacityConfigError, '候选'):
            parse_warmup_capacity({'dynamic_warmup_capacity': 4}, config)

    def test_phase_context_uses_participant_facing_round_numbers(self):
        round_phase_context = getattr(dynamic_app, 'round_phase_context', None)
        self.assertIsNotNone(round_phase_context)

        self.assertEqual(
            round_phase_context(2),
            {
                'is_warmup': True,
                'phase_name': 'warmup',
                'display_round_number': 2,
                'display_total_rounds': 2,
                'round_label': '热身第 2 轮',
            },
        )
        self.assertEqual(
            round_phase_context(3),
            {
                'is_warmup': False,
                'phase_name': 'formal',
                'display_round_number': 1,
                'display_total_rounds': 60,
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
            self.assertTrue(formal_start.is_displayed(SimpleNamespace(round_number=3)))
            self.assertFalse(formal_start.is_displayed(SimpleNamespace(round_number=4)))

    def test_formal_payoff_total_excludes_warmup_rounds(self):
        formal_payoff_total = getattr(dynamic_app, 'formal_payoff_total', None)
        self.assertIsNotNone(formal_payoff_total)
        rounds = [
            SimpleNamespace(round_number=1, payoff=99),
            SimpleNamespace(round_number=2, payoff=98),
            SimpleNamespace(round_number=3, payoff=10),
            SimpleNamespace(round_number=4, payoff=20),
        ]
        player = SimpleNamespace(in_all_rounds=lambda: rounds)

        self.assertEqual(float(formal_payoff_total(player)), 30)

    def test_custom_export_omits_warmup_and_renumbers_formal_rounds(self):
        players = [SimpleNamespace(round_number=value) for value in (1, 2, 3, 4)]

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
            for round_number in (1, 2)
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
        self.assertEqual(fast_wait, 1)

    def test_queue_left_by_earlier_departures_is_included(self):
        wait = service_batch_wait_minutes(
            departure_minute=475,
            first_service_start_minute=478,
            load=3,
            capacity=2,
        )

        self.assertEqual(wait, 4)


class DynamicScheduleTests(unittest.TestCase):
    def test_schedule_uses_lowest_capacity_and_group_size(self):
        builder = getattr(dynamic_app, 'build_dynamic_departure_schedule', None)
        self.assertIsNotNone(builder)

        schedule = builder(
            players_count=60,
            capacity_values=(1, 2, 3),
            min_slots_each_side=10,
        )

        self.assertEqual(schedule['capacity_basis'], 1)
        self.assertEqual(schedule['slots_each_side'], 30)
        self.assertEqual(schedule['num_slots'], 61)

    def test_auto_toll_rejects_after_decision(self):
        validator = getattr(dynamic_app, 'validate_toll_reveal_compatibility', None)
        self.assertIsNotNone(validator)

        with self.assertRaisesRegex(ValueError, 'after_decision.*自动粗收费'):
            validator(
                {
                    'capacity_reveal_timing': 'after_decision',
                    'coarse_toll_auto_enabled': 1,
                }
            )


class DynamicTollCalibrationTests(unittest.TestCase):
    def calibration_module(self):
        spec = importlib.util.find_spec('dynamic_bottleneck_round.toll_calibration')
        self.assertIsNotNone(spec)
        from . import toll_calibration

        return toll_calibration

    def test_small_group_returns_symmetric_candidate(self):
        calibration = self.calibration_module()
        candidate = calibration.calibrate_best_candidate(
            players=5,
            capacity=2,
            valid_slots=tuple(range(1, 22)),
            first_departure_minute=464,
            slot_size_minutes=1,
            min_toll=0,
            max_toll=10,
            toll_step=1,
            calibration_mode='auto',
        )

        self.assertGreaterEqual(candidate.window_start, 1)
        self.assertLessEqual(candidate.window_end, 21)
        self.assertEqual(candidate.window_start + candidate.window_end, 22)
        self.assertGreaterEqual(candidate.toll, 0)
        self.assertEqual(sum(candidate.distribution), 5)

    def test_large_group_mode_returns_candidate(self):
        calibration = self.calibration_module()
        candidate = calibration.calibrate_best_candidate(
            players=60,
            capacity=1,
            valid_slots=tuple(range(1, 62)),
            first_departure_minute=444,
            slot_size_minutes=1,
            min_toll=0,
            max_toll=10,
            toll_step=1,
            calibration_mode='large-group',
            approx_refine_pool_size=4,
            approx_refine_iterations=80,
        )

        self.assertEqual(candidate.calibration_mode, 'large-group')
        self.assertEqual(sum(candidate.distribution), 60)

    def test_matching_cache_record_is_used_before_computation(self):
        schedule = {
            'num_slots': 21,
            'slot_size_minutes': 1,
            'first_departure_minute': 464,
            'last_departure_minute': 484,
        }
        settings = {
            'coarse_toll_auto_min_toll': 0,
            'coarse_toll_auto_max_toll': 10,
            'coarse_toll_auto_toll_step': 1,
            'coarse_toll_auto_mode': 'auto',
            'coarse_toll_auto_approx_refine_pool_size': 8,
            'coarse_toll_auto_approx_refine_iterations': 160,
        }
        distribution = [0] * 21
        distribution[8] = 2
        distribution[12] = 3
        cache_data = {
            'version': 1,
            'same_time_queue_rule': 'batch_max_wait',
            'toll_window_rule': 'symmetric_continuous',
            'records': [
                {
                    'players': 5,
                    'capacity': 2,
                    'num_slots': 21,
                    'first_departure_minute': 464,
                    'last_departure_minute': 484,
                    'slot_size_minutes': 1,
                    'min_toll': 0,
                    'max_toll': 10,
                    'toll_step': 1,
                    'requested_calibration_mode': 'auto',
                    'approx_refine_pool_size': 8,
                    'approx_refine_iterations': 160,
                    'window_start': 9,
                    'window_end': 13,
                    'toll': 3,
                    'cost_gap': 0,
                    'deviation_gap': 0,
                    'nash_count': 1,
                    'distribution': distribution,
                    'selected_costs': [[9, 8], [13, 8]],
                    'calibration_mode': 'large-group',
                }
            ],
        }

        candidate = dynamic_app.cached_toll_candidate(
            players_count=5,
            capacity=2,
            schedule=schedule,
            settings=settings,
            cache_data=cache_data,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.calibration_source, 'cache')
        self.assertEqual(candidate.window_spec, '9-13')

    def test_cache_record_with_different_schedule_is_ignored(self):
        schedule = {
            'num_slots': 21,
            'slot_size_minutes': 1,
            'first_departure_minute': 464,
            'last_departure_minute': 484,
        }
        settings = {
            'coarse_toll_auto_min_toll': 0,
            'coarse_toll_auto_max_toll': 10,
            'coarse_toll_auto_toll_step': 1,
            'coarse_toll_auto_mode': 'auto',
            'coarse_toll_auto_approx_refine_pool_size': 8,
            'coarse_toll_auto_approx_refine_iterations': 160,
        }
        cache_data = {
            'version': 1,
            'same_time_queue_rule': 'batch_max_wait',
            'toll_window_rule': 'symmetric_continuous',
            'records': [
                {
                    'players': 5,
                    'capacity': 2,
                    'num_slots': 21,
                    'first_departure_minute': 463,
                    'last_departure_minute': 483,
                    'slot_size_minutes': 1,
                    'min_toll': 0,
                    'max_toll': 10,
                    'toll_step': 1,
                    'requested_calibration_mode': 'auto',
                    'approx_refine_pool_size': 8,
                    'approx_refine_iterations': 160,
                    'window_start': 9,
                    'window_end': 13,
                    'toll': 3,
                    'distribution': [0] * 21,
                    'selected_costs': [],
                    'calibration_mode': 'large-group',
                }
            ],
        }

        candidate = dynamic_app.cached_toll_candidate(
            players_count=5,
            capacity=2,
            schedule=schedule,
            settings=settings,
            cache_data=cache_data,
        )

        self.assertIsNone(candidate)

    def test_group_calibration_returns_cached_source(self):
        schedule = {
            'num_slots': 3,
            'slot_size_minutes': 1,
            'first_departure_minute': 473,
            'last_departure_minute': 475,
        }
        settings = {
            'coarse_toll_auto_min_toll': 0,
            'coarse_toll_auto_max_toll': 10,
            'coarse_toll_auto_toll_step': 1,
            'coarse_toll_auto_mode': 'auto',
            'coarse_toll_auto_approx_refine_pool_size': 8,
            'coarse_toll_auto_approx_refine_iterations': 160,
        }
        cache_data = {
            'version': 1,
            'same_time_queue_rule': 'batch_max_wait',
            'toll_window_rule': 'symmetric_continuous',
            'records': [
                {
                    'players': 2,
                    'capacity': 1,
                    'num_slots': 3,
                    'first_departure_minute': 473,
                    'last_departure_minute': 475,
                    'slot_size_minutes': 1,
                    'min_toll': 0,
                    'max_toll': 10,
                    'toll_step': 1,
                    'requested_calibration_mode': 'auto',
                    'approx_refine_pool_size': 8,
                    'approx_refine_iterations': 160,
                    'window_start': 2,
                    'window_end': 2,
                    'toll': 2,
                    'cost_gap': 0,
                    'deviation_gap': 0,
                    'nash_count': 1,
                    'distribution': [1, 0, 1],
                    'selected_costs': [[1, 2], [3, 2]],
                    'calibration_mode': 'exact',
                }
            ],
        }

        results = dynamic_app.calibrate_tolls_for_group(
            players_count=2,
            capacities=(1,),
            schedule=schedule,
            settings=settings,
            cache_data=cache_data,
        )

        self.assertEqual(results['1']['calibration_source'], 'cache')
        self.assertEqual(results['1']['slot_spec'], '2')

    def test_cache_candidate_outside_toll_range_is_ignored(self):
        schedule = {
            'num_slots': 3,
            'slot_size_minutes': 1,
            'first_departure_minute': 473,
            'last_departure_minute': 475,
        }
        settings = {
            'coarse_toll_auto_min_toll': 0,
            'coarse_toll_auto_max_toll': 10,
            'coarse_toll_auto_toll_step': 1,
            'coarse_toll_auto_mode': 'auto',
            'coarse_toll_auto_approx_refine_pool_size': 8,
            'coarse_toll_auto_approx_refine_iterations': 160,
        }
        record = {
            'players': 2,
            'capacity': 1,
            'num_slots': 3,
            'first_departure_minute': 473,
            'last_departure_minute': 475,
            'slot_size_minutes': 1,
            'min_toll': 0,
            'max_toll': 10,
            'toll_step': 1,
            'requested_calibration_mode': 'auto',
            'approx_refine_pool_size': 8,
            'approx_refine_iterations': 160,
            'window_start': 2,
            'window_end': 2,
            'toll': 99,
            'distribution': [1, 0, 1],
            'selected_costs': [[1, 2], [3, 2]],
            'calibration_mode': 'exact',
        }
        cache_data = {
            'version': 1,
            'same_time_queue_rule': 'batch_max_wait',
            'toll_window_rule': 'symmetric_continuous',
            'records': [record],
        }

        candidate = dynamic_app.cached_toll_candidate(
            players_count=2,
            capacity=1,
            schedule=schedule,
            settings=settings,
            cache_data=cache_data,
        )

        self.assertIsNone(candidate)

    def test_cache_loader_rejects_invalid_json_with_clear_message(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / 'cache.json'
            cache_path.write_text('{invalid', encoding='utf-8')

            with self.assertRaisesRegex(ValueError, '粗收费缓存文件不是有效 JSON'):
                dynamic_app.load_toll_calibration_cache(cache_path)


class DynamicTollApplicationTests(unittest.TestCase):
    def setUp(self):
        self.schedule = {
            'enabled': True,
            'source': 'auto',
            'players_count': 5,
            'capacity_basis': 1,
            'slots_each_side': 10,
            'num_slots': 21,
            'slot_size_minutes': 1,
            'first_departure_minute': 464,
            'last_departure_minute': 484,
            'first_departure_time': '07:44',
            'last_departure_time': '08:04',
        }
        self.settings = {
            'coarse_toll_auto_min_toll': 0,
            'coarse_toll_auto_max_toll': 2,
            'coarse_toll_auto_toll_step': 1,
            'coarse_toll_auto_mode': 'large-group',
            'coarse_toll_auto_approx_refine_pool_size': 2,
            'coarse_toll_auto_approx_refine_iterations': 20,
        }

    def test_builds_one_result_per_capacity(self):
        calibrator = getattr(dynamic_app, 'calibrate_tolls_for_group', None)
        self.assertIsNotNone(calibrator)

        results = calibrator(
            players_count=5,
            capacities=(1, 2, 3),
            schedule=self.schedule,
            settings=self.settings,
        )

        self.assertEqual(set(results), {'1', '2', '3'})
        self.assertEqual(results['2']['capacity'], 2)
        self.assertEqual(results['2']['calibration_players'], 5)

    def test_round_selects_toll_matching_actual_capacity(self):
        apply_round_toll = getattr(dynamic_app, 'apply_round_toll', None)
        toll_var = getattr(dynamic_app, 'TOLL_BY_CAPACITY_VAR', None)
        self.assertIsNotNone(apply_round_toll)
        self.assertIsNotNone(toll_var)

        result = {
            'enabled': True,
            'source': 'auto',
            'capacity': 2,
            'calibration_players': 5,
            'calibration_mode': 'large-group',
            'calibration_source': 'computed',
            'slot_spec': '9-13',
            'time_window_spec': '07:52-07:56',
            'points': 3,
            'cost_gap': 0,
            'deviation_gap': 0,
            'nash_count': 1,
            'equilibrium_distribution': '07:50: 2人, 07:58: 3人',
            'equilibrium_costs': '07:50: 8, 07:58: 8',
        }
        participant_vars = {toll_var: {'2': result}}
        players = [
            SimpleNamespace(participant=SimpleNamespace(vars=participant_vars.copy()))
            for _ in range(3)
        ]
        group = SimpleNamespace(dynamic_capacity=2, get_players=lambda: players)

        apply_round_toll(group)

        for player in players:
            self.assertEqual(player.coarse_toll_calibration_capacity, 2)
            self.assertEqual(player.coarse_toll_time_window_spec, '07:52-07:56')
            self.assertEqual(float(player.coarse_toll_points), 3)

    def test_manual_time_window_is_converted_to_schedule_slots(self):
        slots = dynamic_app.parse_time_window_spec(
            '07:52-07:56',
            'coarse_toll_time_window_spec',
            self.schedule,
        )

        self.assertEqual(slots, {9, 10, 11, 12, 13})

    def test_manual_time_window_outside_schedule_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '可选出发时间范围'):
            dynamic_app.parse_time_window_spec(
                '07:30-07:40',
                'coarse_toll_time_window_spec',
                self.schedule,
            )


class DynamicCostExportTests(unittest.TestCase):
    def test_effective_toll_is_added_to_total_cost(self):
        calculator = getattr(dynamic_app, 'calculate_cost_components', None)
        self.assertIsNotNone(calculator)

        components = calculator(
            queue_delay=2,
            early_minutes=0,
            late_minutes=1,
            toll=3,
        )

        self.assertEqual(components['fixed_cost'], 12)
        self.assertEqual(components['queue_cost'], 4)
        self.assertEqual(components['late_cost'], 3)
        self.assertEqual(components['total_cost'], 22)

    def test_export_contains_effective_toll_and_schedule_metadata(self):
        required = {
            'coarse_toll_source',
            'coarse_toll_calibration_capacity',
            'coarse_toll_time_window_spec',
            'coarse_toll_points',
            'coarse_toll_charge',
            'departure_schedule_first_time',
            'departure_schedule_last_time',
            'departure_schedule_num_slots',
        }

        self.assertTrue(required.issubset(set(EXPORT_HEADERS)))


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


class CapacityRevealTests(unittest.TestCase):
    def test_after_decision_context_does_not_include_actual_capacity(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'capacity_reveal_timing': 'after_decision',
            }
        )

        context = decision_capacity_context(config, actual_capacity=3)

        self.assertFalse(context['capacity_revealed'])
        self.assertNotIn('actual_capacity', context)
        self.assertNotIn('3 人 / 1 分钟', str(context))

    def test_before_decision_context_includes_actual_capacity(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'capacity_reveal_timing': 'before_decision',
            }
        )

        context = decision_capacity_context(config, actual_capacity=2)

        self.assertTrue(context['capacity_revealed'])
        self.assertEqual(context['actual_capacity'], 2)


class DynamicPresentationContextTests(unittest.TestCase):
    def test_reveal_description_matches_before_decision_mode(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'capacity_reveal_timing': 'before_decision',
            }
        )

        self.assertEqual(
            capacity_reveal_description(config),
            '每轮真实服务率会在选择出发时间前公布。',
        )

    def test_reveal_description_matches_after_decision_mode(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '1,2,3',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
                'capacity_reveal_timing': 'after_decision',
            }
        )

        self.assertEqual(
            capacity_reveal_description(config),
            '每轮真实服务率会在提交出发时间后公布。',
        )

    def test_queue_example_keeps_all_arrival_choices_distinct_at_high_capacities(self):
        config = parse_dynamic_capacity_config(
            {
                'dynamic_capacity_values': '3,4,5',
                'dynamic_capacity_probabilities': '0.3,0.5,0.2',
            }
        )

        example = comprehension_queue_example(config)

        self.assertGreaterEqual(example['wait_minutes'], 2)
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
    def test_demo_and_prod_configs_use_20_plus_40_after_decision_design(self):
        import settings

        configs = {config['name']: config for config in settings.SESSION_CONFIGS}
        for name in ('dynamic_bottleneck_round_demo', 'dynamic_bottleneck_round_prod'):
            self.assertIn(name, configs)
            config = configs[name]
            self.assertEqual(config['dynamic_capacity_values'], '1,2,3')
            self.assertEqual(config['dynamic_warmup_capacity'], 2)
            probabilities = [
                float(item)
                for item in config['dynamic_capacity_probabilities'].split(',')
            ]
            self.assertAlmostEqual(probabilities[0], 1 / 3)
            self.assertAlmostEqual(probabilities[1], 1 / 3)
            self.assertAlmostEqual(probabilities[2], 1 / 3)
            self.assertEqual(config['dynamic_capacity_seed'], 20260718)
            self.assertEqual(config['dynamic_capacity_draw_mode'], 'phased_markov')
            self.assertEqual(config['dynamic_capacity_random_rounds'], 20)
            self.assertEqual(
                config['dynamic_capacity_transition_matrix'],
                '0.8,0.1,0.1;0.1,0.8,0.1;0.1,0.1,0.8',
            )
            self.assertEqual(config['dynamic_capacity_manual_sequence'], '')
            self.assertEqual(config['dynamic_capacity_sequence_preset'], 'auto')
            self.assertEqual(config['dynamic_capacity_sequence_scope'], 'session')
            self.assertEqual(config['capacity_reveal_timing'], 'after_decision')
            self.assertEqual(config['group_agent_spec'], '')
            self.assertEqual(config['api_agent_timeout_seconds'], 12)
            self.assertIn(
                str(config['api_agent_thinking_enabled']).lower(),
                {'0', 'false', 'off'},
            )
            self.assertEqual(config['api_agent_max_tokens'], 512)
            self.assertEqual(config['reward_treatment_enabled'], 0)
            self.assertEqual(config['departure_schedule_auto_enabled'], 1)
            self.assertEqual(config['departure_schedule_min_slots_each_side'], 10)
            self.assertEqual(config['coarse_toll_auto_enabled'], 0)
            self.assertEqual(config['coarse_toll_enabled'], 1)
            self.assertEqual(config['payoff_rounds'], 60)
        self.assertEqual(C.WARMUP_ROUNDS, 2)
        self.assertEqual(C.FORMAL_ROUNDS, 60)
        self.assertEqual(C.NUM_ROUNDS, 62)
        self.assertEqual(C.SYNC_POLL_INTERVAL_SECONDS, 1.5)

        for name in ('single_bottleneck_demo', 'single_bottleneck_prod'):
            self.assertEqual(configs[name]['api_agent_timeout_seconds'], 30)

    def test_export_headers_match_required_round_level_schema(self):
        required = {
            'session_code', 'participant_code', 'group_id', 'round_number',
            'dynamic_capacity', 'dynamic_capacity_state', 'previous_round_capacity',
            'capacity_probability', 'capacity_reveal_timing', 'dynamic_capacity_seed',
            'dynamic_capacity_draw_mode', 'departure_slot', 'departure_minute',
            'queue_delay_minutes', 'arrival_minute', 'early_minutes', 'late_minutes',
            'total_cost', 'payoff', 'decision_source', 'timeout_happened',
            'coarse_toll_source', 'coarse_toll_calibration_capacity',
            'coarse_toll_time_window_spec', 'coarse_toll_points', 'coarse_toll_charge',
            'departure_schedule_num_slots', 'departure_schedule_first_time',
            'departure_schedule_last_time',
        }
        self.assertTrue(required.issubset(set(EXPORT_HEADERS)))


class AccidentExperimentContractTests(unittest.TestCase):
    def test_dynamic_settings_use_accident_risk_contract(self):
        import settings

        configs = {config['name']: config for config in settings.SESSION_CONFIGS}
        prod = configs['dynamic_bottleneck_round_prod']
        demo = configs['dynamic_bottleneck_round_demo']
        for config in (prod, demo):
            self.assertEqual(config['accident_normal_capacity'], 4.0)
            self.assertEqual(config['accident_probability'], 0.20)
            self.assertEqual(config['accident_loss_alpha'], 6.83057)
            self.assertEqual(config['accident_loss_beta'], 4.05907)
            self.assertEqual(config['accident_information_condition'], 'I0')
            self.assertNotIn('dynamic_capacity_draw_mode', config)
            self.assertNotIn('dynamic_capacity_transition_matrix', config)
            self.assertNotIn('dynamic_capacity_values', config)
        self.assertEqual(prod['dynamic_capacity_sequence_preset'], 'S01')
        self.assertEqual(demo['dynamic_capacity_sequence_preset'], 'auto')

    def test_rounds_costs_and_fixed_action_space_match_approved_design(self):
        self.assertEqual(C.WARMUP_ROUNDS, 5)
        self.assertEqual(C.FORMAL_ROUNDS, 60)
        self.assertEqual(C.NUM_ROUNDS, 65)
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
                'api_agent_mode': api_mode,
                'api_agent_count_per_group': api_count,
                'rl_agent_enabled': rl_enabled,
                'rl_agent_count_per_group': rl_count,
                'group_agent_spec': '',
            },
            vars={},
        )

    def test_formal_human_only_composition_is_exactly_twenty_humans(self):
        session = self.make_session('dynamic_bottleneck_round_prod')

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object() for _ in range(20)]],
        )

        self.assertEqual(result, 'H')

    def test_formal_human_agent_composition_is_sixteen_plus_two_plus_two(self):
        session = self.make_session(
            'dynamic_bottleneck_round_prod',
            api_mode='active',
            api_count=2,
            rl_enabled='1',
            rl_count=2,
        )

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object() for _ in range(16)]],
        )

        self.assertEqual(result, 'HA')

    def test_formal_composition_rejects_any_other_actor_counts(self):
        invalid = (
            self.make_session('dynamic_bottleneck_round_prod'),
            self.make_session(
                'dynamic_bottleneck_round_prod',
                api_mode='active',
                api_count=2,
                rl_enabled='1',
                rl_count=2,
            ),
            self.make_session(
                'dynamic_bottleneck_round_prod',
                api_mode='active',
                api_count=2,
            ),
        )
        matrices = (
            [[object() for _ in range(19)]],
            [[object() for _ in range(20)]],
            [[object() for _ in range(18)]],
        )
        for session, matrix in zip(invalid, matrices):
            with self.subTest(config=session.config):
                with self.assertRaisesRegex(ValueError, '20 Human|16 Human'):
                    dynamic_app.validate_formal_actor_composition(session, matrix)

    def test_formal_composition_rejects_group_specific_agent_configuration(self):
        session = self.make_session('dynamic_bottleneck_round_prod')
        session.config['group_agent_spec'] = 'G01:api=0,rl=0'

        with self.assertRaisesRegex(ValueError, 'group_agent_spec'):
            dynamic_app.validate_formal_actor_composition(
                session,
                [[object() for _ in range(20)]],
            )

    def test_demo_allows_smaller_actor_count(self):
        session = self.make_session('dynamic_bottleneck_round_demo')

        result = dynamic_app.validate_formal_actor_composition(
            session,
            [[object() for _ in range(5)]],
        )

        self.assertEqual(result, 'demo')


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

        configured_values = {
            int(item)
            for item in self.session.config['dynamic_capacity_values'].split(',')
        }
        group_capacities = {player.dynamic_capacity for player in self.group.get_players()}
        expect(len(group_capacities), '==', 1)
        expect(self.player.dynamic_capacity, 'in', configured_values)
        if phase['is_warmup']:
            expect('本轮真实瓶颈服务率', 'in', self.html)
            expect(self.player.dynamic_capacity, '==', 2)
        elif self.session.config['capacity_reveal_timing'] == 'after_decision':
            expect('本轮服务率将在提交后公布', 'in', self.html)
            expect('本轮真实瓶颈服务率', 'not in', self.html)
        else:
            expect('本轮真实瓶颈服务率', 'in', self.html)
            expect(f'{self.player.dynamic_capacity} 人 / 1 分钟', 'in', self.html)
        expect('收费', 'in', self.html)

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
        expect(self.player.coarse_toll_calibration_capacity, '==', self.player.dynamic_capacity)
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
                    'rl_policy',
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
        expect(
            self.player.coarse_toll_calibration_players,
            '==',
            dynamic_app.effective_group_actor_count(
                self.session,
                len(self.group.get_players()),
                self.group.id_in_subsession,
            ),
        )

        expected_payoff = (
            0
            if phase['is_warmup']
            else max(
                0,
                round(
                    C.BASE_POINTS
                    - float(self.player.total_cost)
                    + float(self.player.reward_bonus),
                    2,
                ),
            )
        )
        expect(float(self.player.payoff), '==', expected_payoff)

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
                    float(round_player.payoff)
                    for round_player in self.player.in_all_rounds()
                    if not dynamic_app.is_warmup_round(round_player.round_number)
                ),
            )


if __name__ == '__main__':
    unittest.main()
