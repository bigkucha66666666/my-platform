import json
import math
from pathlib import Path
import tempfile
import unittest

from dynamic_bottleneck_round.stochastic_capacity import (
    APPROVED_SEQUENCE_IDS,
    CAPACITY_MAX,
    CAPACITY_MIN,
    FORMAL_ROUNDS,
    INFO_I0,
    INFO_I1,
    StochasticCapacityConfigError,
    capacity_level,
    generate_stratified_capacity_sequence,
    load_uniform_capacity_sequence_bank,
    parse_stochastic_capacity_config,
    stochastic_capacity_public_context,
)


class StochasticCapacityConfigTests(unittest.TestCase):
    def test_approved_defaults_are_uniform_i0(self):
        config = parse_stochastic_capacity_config({})

        self.assertEqual(config.distribution, 'uniform')
        self.assertEqual(config.capacity_min, 1.33)
        self.assertEqual(config.capacity_max, 4.00)
        self.assertEqual(config.information_condition, INFO_I0)
        self.assertAlmostEqual(config.theoretical_mean, 2.665)
        self.assertAlmostEqual(
            config.theoretical_standard_deviation,
            (4.00 - 1.33) / math.sqrt(12),
        )

    def test_rejects_nonapproved_distribution_bounds_and_information(self):
        invalid = (
            ({'capacity_distribution': 'normal'}, 'capacity_distribution'),
            ({'capacity_min': 1.32}, 'capacity_min'),
            ({'capacity_max': 4.01}, 'capacity_max'),
            ({'capacity_information_condition': 'I2'}, 'capacity_information_condition'),
        )
        for raw, expected in invalid:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(StochasticCapacityConfigError, expected):
                    parse_stochastic_capacity_config(raw)

    def test_rejects_old_accident_fields_instead_of_mapping_them(self):
        old_fields = (
            'incident_occurred',
            'accident_probability',
            'capacity_loss_ratio',
            'remaining_capacity_ratio',
            'accident_normal_capacity',
            'accident_loss_alpha',
            'accident_loss_beta',
            'accident_information_condition',
        )
        for field in old_fields:
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    StochasticCapacityConfigError,
                    f'旧事故配置字段 {field}',
                ):
                    parse_stochastic_capacity_config({field: 1})


class CapacityLevelTests(unittest.TestCase):
    def test_capacity_levels_use_fixed_equal_probability_thresholds(self):
        cases = (
            (1.33, 'low'),
            (2.21, 'low'),
            (2.22, 'medium'),
            (3.10, 'medium'),
            (3.11, 'high'),
            (4.00, 'high'),
        )
        for capacity, expected in cases:
            with self.subTest(capacity=capacity):
                self.assertEqual(capacity_level(capacity), expected)

    def test_capacity_level_rejects_out_of_range_value(self):
        for value in (1.32, 4.01, float('nan')):
            with self.subTest(value=value):
                with self.assertRaises(StochasticCapacityConfigError):
                    capacity_level(value)


class StratifiedSequenceTests(unittest.TestCase):
    def test_seeded_sequence_is_reproducible_two_decimal_and_covers_all_strata(self):
        config = parse_stochastic_capacity_config(
            {'capacity_sequence_seed': 2026091101}
        )

        left = generate_stratified_capacity_sequence(
            config,
            rounds=FORMAL_ROUNDS,
            sequence_id='S01',
        )
        right = generate_stratified_capacity_sequence(
            config,
            rounds=FORMAL_ROUNDS,
            sequence_id='S01',
        )

        self.assertEqual(left, right)
        self.assertEqual(len(left), FORMAL_ROUNDS)
        self.assertEqual(
            {record['stratum_index'] for record in left},
            set(range(1, FORMAL_ROUNDS + 1)),
        )
        for round_number, record in enumerate(left, start=1):
            self.assertEqual(record['formal_round_number'], round_number)
            self.assertGreaterEqual(record['actual_capacity'], CAPACITY_MIN)
            self.assertLessEqual(record['actual_capacity'], CAPACITY_MAX)
            self.assertEqual(
                record['actual_capacity'],
                round(record['actual_capacity'], 2),
            )
            self.assertEqual(
                record['capacity_level'],
                capacity_level(record['actual_capacity']),
            )
            self.assertEqual(record['distribution'], 'uniform')
            self.assertEqual(record['capacity_min'], CAPACITY_MIN)
            self.assertEqual(record['capacity_max'], CAPACITY_MAX)

    def test_rejects_invalid_round_count(self):
        config = parse_stochastic_capacity_config({})
        with self.assertRaisesRegex(StochasticCapacityConfigError, 'rounds'):
            generate_stratified_capacity_sequence(
                config,
                rounds=0,
                sequence_id='auto',
            )


class StochasticPublicContextTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            'formal_round_number': 1,
            'actual_capacity': 2.37,
            'capacity_level': 'medium',
            'sequence_id': 'S01',
            'sequence_seed': 2026091101,
            'distribution': 'uniform',
            'capacity_min': CAPACITY_MIN,
            'capacity_max': CAPACITY_MAX,
            'stratum_index': 12,
        }

    def context(self, condition, **kwargs):
        config = parse_stochastic_capacity_config(
            {'capacity_information_condition': condition}
        )
        return stochastic_capacity_public_context(config, self.record, **kwargs)

    def test_i0_omits_current_capacity_before_decision(self):
        context = self.context(INFO_I0)

        self.assertFalse(context['current_capacity_revealed'])
        self.assertNotIn('actual_capacity', context)
        self.assertNotIn('capacity_level', context)
        self.assertEqual(context['capacity_distribution'], 'uniform')
        self.assertEqual(context['capacity_min'], CAPACITY_MIN)
        self.assertEqual(context['capacity_max'], CAPACITY_MAX)

    def test_i1_reveals_exact_current_capacity_before_decision(self):
        context = self.context(INFO_I1)

        self.assertTrue(context['current_capacity_revealed'])
        self.assertEqual(context['actual_capacity'], 2.37)
        self.assertEqual(context['capacity_level'], 'medium')

    def test_both_conditions_receive_same_realized_feedback(self):
        i0 = self.context(INFO_I0, after_decision=True)
        i1 = self.context(INFO_I1, after_decision=True)

        self.assertEqual(i0['actual_capacity'], i1['actual_capacity'])
        self.assertEqual(i0['capacity_level'], i1['capacity_level'])
        self.assertTrue(i0['current_capacity_revealed'])
        self.assertTrue(i1['current_capacity_revealed'])


class UniformSequenceBankTests(unittest.TestCase):
    def test_checked_in_bank_has_five_complete_valid_sequences(self):
        bank = load_uniform_capacity_sequence_bank()

        self.assertEqual(set(bank), APPROVED_SEQUENCE_IDS)
        for sequence_id, sequence in bank.items():
            with self.subTest(sequence_id=sequence_id):
                self.assertEqual(len(sequence['rounds']), FORMAL_ROUNDS)
                self.assertEqual(
                    {record['stratum_index'] for record in sequence['rounds']},
                    set(range(1, FORMAL_ROUNDS + 1)),
                )

    def test_loader_rejects_wrong_mechanism_bounds_and_derived_level(self):
        bank_path = Path(__file__).with_name('uniform_capacity_sequence_bank.json')
        payload = json.loads(bank_path.read_text(encoding='utf-8'))
        mutations = (
            ('mechanism', lambda value: value.__setitem__('mechanism', 'iid_uniform')),
            ('capacity_min', lambda value: value.__setitem__('capacity_min', 1.32)),
            (
                'capacity_level',
                lambda value: value['sequences'][0]['rounds'][0].__setitem__(
                    'capacity_level', 'wrong'
                ),
            ),
            (
                'sequence_seed',
                lambda value: value['sequences'][0]['rounds'][0].__setitem__(
                    'sequence_seed', -1
                ),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                candidate = json.loads(json.dumps(payload))
                mutate(candidate)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'bank.json'
                    path.write_text(
                        json.dumps(candidate, ensure_ascii=False),
                        encoding='utf-8',
                    )
                    with self.assertRaises(StochasticCapacityConfigError):
                        load_uniform_capacity_sequence_bank(path)


if __name__ == '__main__':
    unittest.main()
