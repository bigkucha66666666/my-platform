import math
import unittest

from dynamic_bottleneck_round.agents.liu_rel_agent import (
    CAPACITY_KERNEL_BANDWIDTH,
    LIU_REL_POLICY_VERSION,
    append_liu_rel_experience,
    choose_liu_rel_departure,
    initial_liu_rel_state,
    interpolate_propensities,
    liu_rel_choice_probabilities,
    select_information_conditioned_experiences,
    valid_or_initial_liu_rel_state,
    weighted_cost_statistics_by_slot,
)


def experience(round_number, slot, cost, capacity):
    return {
        'formal_round_number': round_number,
        'departure_slot': slot,
        'total_cost': cost,
        'actual_capacity': capacity,
        'decision_source': 'liu_rel_softmax_i0',
    }


class LiuRELStateTests(unittest.TestCase):
    def test_initial_state_has_required_serializable_structure(self):
        state = initial_liu_rel_state()

        self.assertEqual(
            state,
            {
                'policy_version': LIU_REL_POLICY_VERSION,
                'rounds_observed': 0,
                'experiences': [],
                'last_departure_slot': None,
                'last_choice_probability': None,
                'last_context_level': None,
                'last_propensities': {},
                'last_choice_probabilities': {},
            },
        )

    def test_version_mismatch_reinitializes_state(self):
        stale = initial_liu_rel_state()
        stale['policy_version'] = 'old'
        stale['experiences'] = [experience(1, 4, 10, 4)]

        self.assertEqual(valid_or_initial_liu_rel_state(stale), initial_liu_rel_state())

    def test_malformed_experience_reinitializes_state(self):
        damaged = initial_liu_rel_state()
        damaged['rounds_observed'] = 1
        damaged['experiences'] = [
            {
                'formal_round_number': 1,
                'departure_slot': 4,
                'total_cost': 'not-a-number',
                'actual_capacity': 4,
            }
        ]

        self.assertEqual(
            valid_or_initial_liu_rel_state(damaged),
            initial_liu_rel_state(),
        )

    def test_duplicate_formal_round_is_not_appended_twice(self):
        state = append_liu_rel_experience(
            initial_liu_rel_state(),
            formal_round_number=1,
            departure_slot=4,
            total_cost=10,
            actual_capacity=4,
            decision_source='liu_rel_uniform_initial',
        )

        repeated = append_liu_rel_experience(
            state,
            formal_round_number=1,
            departure_slot=9,
            total_cost=99,
            actual_capacity=1.5,
            decision_source='rl_fallback_lowest_schedule_cost',
        )

        self.assertEqual(repeated, state)
        self.assertEqual(repeated['rounds_observed'], 1)

    def test_warmup_outcome_does_not_change_state(self):
        initial = initial_liu_rel_state()

        updated = append_liu_rel_experience(
            initial,
            formal_round_number=0,
            departure_slot=3,
            total_cost=8,
            actual_capacity=4,
            decision_source='liu_rel_uniform_warmup',
            warmup=True,
        )

        self.assertEqual(updated, initial)


class LiuRELStatisticsTests(unittest.TestCase):
    def test_single_experience_has_zero_population_standard_deviation(self):
        weighted = [{**experience(1, 3, 12, 4), 'weight': 0.4}]

        statistics = weighted_cost_statistics_by_slot(weighted, rel_lambda=0.25)

        self.assertAlmostEqual(statistics[3]['mean_cost'], 12)
        self.assertAlmostEqual(statistics[3]['standard_deviation'], 0)
        self.assertAlmostEqual(statistics[3]['propensity'], 12)

    def test_weighted_mean_population_deviation_and_minus_lambda_propensity(self):
        weighted = [
            {**experience(1, 3, 10, 4), 'weight': 1},
            {**experience(2, 3, 14, 2), 'weight': 3},
        ]

        statistics = weighted_cost_statistics_by_slot(weighted, rel_lambda=0.25)

        self.assertAlmostEqual(statistics[3]['mean_cost'], 13)
        self.assertAlmostEqual(statistics[3]['standard_deviation'], math.sqrt(3))
        self.assertAlmostEqual(
            statistics[3]['propensity'],
            13 - 0.25 * math.sqrt(3),
        )
        self.assertAlmostEqual(statistics[3]['effective_observation_count'], 4)


class LiuRELConditioningTests(unittest.TestCase):
    def setUp(self):
        self.history = [
            experience(1, 2, 11, 1.40),
            experience(2, 5, 15, 2.20),
            experience(3, 3, 20, 3.10),
            experience(4, 6, 16, 4.00),
        ]

    def test_i0_uses_all_history_and_ignores_current_realization(self):
        first = select_information_conditioned_experiences(
            self.history,
            information_condition='I0',
            current_actual_capacity=1.50,
        )
        second = select_information_conditioned_experiences(
            self.history,
            information_condition='I0',
            current_actual_capacity=3.90,
        )

        self.assertEqual(first, second)
        self.assertEqual(first['context_level'], 'i0_all')
        self.assertTrue(all(item['weight'] == 1 for item in first['experiences']))

    def test_i1_weights_all_history_by_gaussian_capacity_distance(self):
        current_capacity = 2.50
        selected = select_information_conditioned_experiences(
            self.history,
            information_condition='I1',
            current_actual_capacity=current_capacity,
        )

        self.assertEqual(selected['context_level'], 'i1_capacity_kernel')
        self.assertEqual(
            [item['formal_round_number'] for item in selected['experiences']],
            [1, 2, 3, 4],
        )
        for source, weighted in zip(self.history, selected['experiences']):
            expected = math.exp(
                -((current_capacity - source['actual_capacity']) ** 2)
                / (2 * CAPACITY_KERNEL_BANDWIDTH**2)
            )
            self.assertAlmostEqual(weighted['weight'], expected)

    def test_i1_requires_current_actual_capacity(self):
        with self.assertRaisesRegex(ValueError, 'current actual capacity'):
            select_information_conditioned_experiences(
                self.history,
                information_condition='I1',
            )

    def test_i1_falls_back_to_i0_when_kernel_has_fewer_than_two_weighted_slots(self):
        history = [
            experience(1, 2, 11, 3.00),
            experience(2, 5, 15, 4.00),
        ]
        selected = select_information_conditioned_experiences(
            history,
            information_condition='I1',
            current_actual_capacity=1.33,
        )

        self.assertEqual(selected['context_level'], 'i1_backoff_i0')
        self.assertEqual(len(selected['experiences']), 2)

    def test_i2_is_rejected_by_the_two_condition_policy(self):
        with self.assertRaisesRegex(ValueError, 'I0 or I1'):
            select_information_conditioned_experiences(
                self.history,
                information_condition='I2',
                current_actual_capacity=2.50,
            )


class LiuRELInterpolationAndProbabilityTests(unittest.TestCase):
    def test_interpolates_middle_and_extrapolates_one_step_with_constant_tails(self):
        propensities = interpolate_propensities(
            range(1, 9),
            {
                3: {'propensity': 10.0},
                6: {'propensity': 16.0},
            },
        )

        self.assertEqual(
            propensities,
            {1: 8.0, 2: 8.0, 3: 10.0, 4: 12.0, 5: 14.0, 6: 16.0, 7: 18.0, 8: 18.0},
        )

    def test_softmax_probabilities_are_finite_normalized_and_favor_low_cost(self):
        probabilities = liu_rel_choice_probabilities(
            {1: 8, 2: 10, 3: 12},
            rel_eta=14.7445,
            phi=10,
        )

        self.assertTrue(all(math.isfinite(value) for value in probabilities.values()))
        self.assertTrue(all(value >= 0 for value in probabilities.values()))
        self.assertAlmostEqual(sum(probabilities.values()), 1, places=14)
        self.assertGreater(probabilities[1], probabilities[2])
        self.assertGreater(probabilities[2], probabilities[3])


class LiuRELChoiceTests(unittest.TestCase):
    def setUp(self):
        self.available_slots = tuple(
            {'slot': slot, 'departure_minute': 465 + slot}
            for slot in range(1, 17)
        )
        self.base = {
            'available_slots': self.available_slots,
            'information_condition': 'I0',
            'rel_lambda': 0.25,
            'rel_eta': 14.7445,
            'session_code': 'SESSION01',
            'group_id': 1,
            'agent_id': 'G01_RL_01',
            'rel_random_seed': 2026090901,
            'rel_initial_uniform_rounds': 2,
        }

    def test_first_two_formal_rounds_are_uniform(self):
        for round_number in (1, 2):
            with self.subTest(round_number=round_number):
                choice = choose_liu_rel_departure(
                    state=initial_liu_rel_state(),
                    formal_round_number=round_number,
                    **self.base,
                )
                self.assertEqual(choice['decision_source'], 'liu_rel_uniform_initial')
                self.assertEqual(set(choice['choice_probabilities'].values()), {1 / 16})

    def test_same_stable_identity_repeats_choice(self):
        left = choose_liu_rel_departure(
            state=initial_liu_rel_state(),
            formal_round_number=1,
            **self.base,
        )
        right = choose_liu_rel_departure(
            state=initial_liu_rel_state(),
            formal_round_number=1,
            **self.base,
        )

        self.assertEqual(left['departure_slot'], right['departure_slot'])
        self.assertEqual(left['random_seed_fingerprint'], right['random_seed_fingerprint'])

    def test_agent_identity_produces_independent_random_fingerprint(self):
        first = choose_liu_rel_departure(
            state=initial_liu_rel_state(),
            formal_round_number=1,
            **self.base,
        )
        second = choose_liu_rel_departure(
            state=initial_liu_rel_state(),
            formal_round_number=1,
            **{**self.base, 'agent_id': 'G01_RL_02'},
        )

        self.assertNotEqual(
            first['random_seed_fingerprint'],
            second['random_seed_fingerprint'],
        )

    def test_sparse_formal_history_uses_normal_uniform_policy(self):
        state = initial_liu_rel_state()
        state['experiences'] = [experience(1, 4, 10, 4)]
        state['rounds_observed'] = 1

        choice = choose_liu_rel_departure(
            state=state,
            formal_round_number=3,
            **self.base,
        )

        self.assertEqual(choice['decision_source'], 'liu_rel_uniform_sparse')
        self.assertEqual(set(choice['choice_probabilities'].values()), {1 / 16})

    def test_warmup_is_uniform_and_does_not_report_formal_learning(self):
        choice = choose_liu_rel_departure(
            state=initial_liu_rel_state(),
            formal_round_number=0,
            warmup=True,
            **self.base,
        )

        self.assertEqual(choice['decision_source'], 'liu_rel_uniform_warmup')
        self.assertEqual(choice['rounds_observed'], 0)

    def test_initial_uniform_round_count_rejects_non_integer_two(self):
        with self.assertRaisesRegex(ValueError, 'rel_initial_uniform_rounds'):
            choose_liu_rel_departure(
                state=initial_liu_rel_state(),
                formal_round_number=1,
                **{**self.base, 'rel_initial_uniform_rounds': 2.5},
            )

    def test_softmax_choice_returns_complete_audit_without_full_seed(self):
        state = initial_liu_rel_state()
        state['experiences'] = [
            experience(1, 4, 10, 4),
            experience(2, 12, 20, 1.5),
        ]
        state['rounds_observed'] = 2

        choice = choose_liu_rel_departure(
            state=state,
            formal_round_number=3,
            **self.base,
        )

        self.assertEqual(choice['decision_source'], 'liu_rel_softmax_i0')
        self.assertEqual(choice['policy_version'], LIU_REL_POLICY_VERSION)
        self.assertEqual(choice['policy_version'], 'dynamic_liu_rel_uniform_capacity_v1')
        self.assertEqual(choice['context_level'], 'i0_all')
        self.assertEqual(len(choice['propensities']), 16)
        self.assertEqual(len(choice['choice_probabilities']), 16)
        self.assertIn(str(choice['departure_slot']), choice['choice_probabilities'])
        self.assertNotIn(str(self.base['rel_random_seed']), str(choice))
        self.assertAlmostEqual(
            choice['capacity_kernel_bandwidth'],
            CAPACITY_KERNEL_BANDWIDTH,
        )

    def test_i1_softmax_uses_current_capacity_kernel(self):
        state = initial_liu_rel_state()
        state['experiences'] = [
            experience(1, 4, 10, 1.50),
            experience(2, 12, 20, 3.50),
        ]
        state['rounds_observed'] = 2

        choice = choose_liu_rel_departure(
            state=state,
            formal_round_number=3,
            current_actual_capacity=2.50,
            **{**self.base, 'information_condition': 'I1'},
        )

        self.assertEqual(choice['context_level'], 'i1_capacity_kernel')
        self.assertEqual(choice['decision_source'], 'liu_rel_softmax_i1_capacity_kernel')


if __name__ == '__main__':
    unittest.main()
