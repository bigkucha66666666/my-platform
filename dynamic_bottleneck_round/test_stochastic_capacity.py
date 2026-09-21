import json
import math
from pathlib import Path
from statistics import pstdev
import tempfile
import unittest

from dynamic_bottleneck_round.stochastic_capacity import (
    APPROVED_SEQUENCE_IDS,
    CAPACITY_DISTRIBUTION,
    CAPACITY_MAX,
    CAPACITY_MIN,
    CAPACITY_MU,
    CAPACITY_SIGMA,
    FORMAL_ROUNDS,
    INFO_I0,
    INFO_I1,
    SEQUENCE_BANK_FILE,
    StochasticCapacityConfigError,
    capacity_level,
    generate_stratified_capacity_sequence,
    load_stochastic_capacity_sequence_bank,
    parse_stochastic_capacity_config,
    stochastic_capacity_public_context,
    truncated_normal_interval_mean,
    truncated_normal_quantile,
)


class StochasticCapacityConfigTests(unittest.TestCase):
    def test_approved_defaults_are_truncated_normal_i0(self):
        config = parse_stochastic_capacity_config({})
        self.assertEqual(config.distribution, 'truncated_normal')
        self.assertEqual(config.capacity_mu, 2.665)
        self.assertEqual(config.capacity_sigma, 0.80)
        self.assertEqual(config.information_condition, INFO_I0)
        self.assertAlmostEqual(config.truncated_mean, 2.665, places=12)
        self.assertAlmostEqual(config.truncated_standard_deviation, 0.6371680565, places=9)

    def test_rejects_nonapproved_distribution_parameters_and_information(self):
        invalid = (
            ({'capacity_distribution': 'uniform'}, 'capacity_distribution'),
            ({'capacity_mu': 2.66}, 'capacity_mu'),
            ({'capacity_sigma': 0.81}, 'capacity_sigma'),
            ({'capacity_min': 1.32}, 'capacity_min'),
            ({'capacity_max': 4.01}, 'capacity_max'),
            ({'capacity_information_condition': 'I2'}, 'capacity_information_condition'),
        )
        for raw, expected in invalid:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(StochasticCapacityConfigError, expected):
                    parse_stochastic_capacity_config(raw)

    def test_rejects_nonfinite_boolean_and_invalid_numeric_values(self):
        for field in ('capacity_mu', 'capacity_sigma', 'capacity_min', 'capacity_max'):
            for value in (True, False, float('nan'), float('inf'), 'not-a-number'):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(StochasticCapacityConfigError):
                        parse_stochastic_capacity_config({field: value})

    def test_rejects_invalid_bounds_before_approved_value_check(self):
        with self.assertRaisesRegex(StochasticCapacityConfigError, '小于'):
            parse_stochastic_capacity_config({'capacity_min': 4.0, 'capacity_max': 1.33})

    def test_rejects_old_accident_fields_instead_of_mapping_them(self):
        for field in (
            'incident_occurred', 'accident_probability', 'capacity_loss_ratio',
            'remaining_capacity_ratio', 'accident_normal_capacity',
            'accident_loss_alpha', 'accident_loss_beta',
            'accident_information_condition',
        ):
            with self.subTest(field=field):
                with self.assertRaisesRegex(StochasticCapacityConfigError, f'旧事故配置字段 {field}'):
                    parse_stochastic_capacity_config({field: 1})


class TruncatedNormalMathTests(unittest.TestCase):
    def test_quantiles_match_approved_tertiles_and_are_symmetric(self):
        lower = truncated_normal_quantile(1 / 3)
        median = truncated_normal_quantile(1 / 2)
        upper = truncated_normal_quantile(2 / 3)
        self.assertAlmostEqual(lower, 2.355, delta=0.002)
        self.assertAlmostEqual(median, 2.665, places=12)
        self.assertAlmostEqual(upper, 2.975, delta=0.002)
        self.assertAlmostEqual(lower + upper, 2 * CAPACITY_MU, places=12)

    def test_quantile_rejects_endpoints_nonfinite_and_boolean(self):
        for value in (0, 1, -0.1, 1.1, True, float('nan')):
            with self.subTest(value=value):
                with self.assertRaises(StochasticCapacityConfigError):
                    truncated_normal_quantile(value)

    def test_interval_conditional_means_match_symmetric_tertiles(self):
        lower_quantile = truncated_normal_quantile(1 / 3)
        upper_quantile = truncated_normal_quantile(2 / 3)
        means = (
            truncated_normal_interval_mean(CAPACITY_MIN, lower_quantile),
            truncated_normal_interval_mean(lower_quantile, upper_quantile),
            truncated_normal_interval_mean(upper_quantile, CAPACITY_MAX),
        )
        self.assertAlmostEqual(means[0], 1.9463110575, places=9)
        self.assertAlmostEqual(means[1], CAPACITY_MU, places=12)
        self.assertAlmostEqual(means[2], 3.3836889425, places=9)
        self.assertAlmostEqual(sum(means) / 3, CAPACITY_MU, places=12)

    def test_interval_conditional_mean_rejects_invalid_bounds(self):
        for bounds in ((1.32, 2.0), (2.0, 4.01), (2.0, 2.0), (3.0, 2.0)):
            with self.subTest(bounds=bounds):
                with self.assertRaises(StochasticCapacityConfigError):
                    truncated_normal_interval_mean(*bounds)


class CapacityLevelTests(unittest.TestCase):
    def test_capacity_levels_use_truncated_normal_tertiles(self):
        for capacity, expected in (
            (1.33, 'low'), (2.35, 'low'), (2.36, 'medium'),
            (2.97, 'medium'), (2.98, 'high'), (4.00, 'high'),
        ):
            with self.subTest(capacity=capacity):
                self.assertEqual(capacity_level(capacity), expected)

    def test_capacity_level_rejects_out_of_range_value(self):
        for value in (1.32, 4.01, float('nan')):
            with self.subTest(value=value):
                with self.assertRaises(StochasticCapacityConfigError):
                    capacity_level(value)


class StratifiedSequenceTests(unittest.TestCase):
    def test_seeded_sequence_is_reproducible_two_decimal_and_covers_all_strata(self):
        config = parse_stochastic_capacity_config({'capacity_sequence_seed': 2026091101})
        left = generate_stratified_capacity_sequence(config, rounds=FORMAL_ROUNDS, sequence_id='S01')
        right = generate_stratified_capacity_sequence(config, rounds=FORMAL_ROUNDS, sequence_id='S01')
        self.assertEqual(left, right)
        self.assertEqual(len(left), FORMAL_ROUNDS)
        self.assertEqual(
            {record['quantile_stratum_index'] for record in left},
            set(range(1, FORMAL_ROUNDS + 1)),
        )
        self.assertTrue(2.635 <= sum(r['actual_capacity'] for r in left) / 30 <= 2.695)
        for round_number, record in enumerate(left, start=1):
            self.assertEqual(record['formal_round_number'], round_number)
            self.assertGreaterEqual(record['actual_capacity'], CAPACITY_MIN)
            self.assertLessEqual(record['actual_capacity'], CAPACITY_MAX)
            self.assertEqual(record['actual_capacity'], round(record['actual_capacity'], 2))
            self.assertEqual(record['capacity_level'], capacity_level(record['actual_capacity']))
            self.assertEqual(record['distribution'], CAPACITY_DISTRIBUTION)
            self.assertEqual(record['capacity_mu'], CAPACITY_MU)
            self.assertEqual(record['capacity_sigma'], CAPACITY_SIGMA)

    def test_rejects_invalid_round_count(self):
        with self.assertRaisesRegex(StochasticCapacityConfigError, 'rounds'):
            generate_stratified_capacity_sequence(
                parse_stochastic_capacity_config({}), rounds=0, sequence_id='auto'
            )


class StochasticPublicContextTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            'formal_round_number': 1, 'actual_capacity': 2.37,
            'capacity_level': 'medium', 'sequence_id': 'S01',
            'sequence_seed': 2026091101, 'distribution': 'truncated_normal',
            'capacity_mu': CAPACITY_MU, 'capacity_sigma': CAPACITY_SIGMA,
            'capacity_min': CAPACITY_MIN, 'capacity_max': CAPACITY_MAX,
            'quantile_stratum_index': 12,
        }

    def context(self, condition, **kwargs):
        config = parse_stochastic_capacity_config({'capacity_information_condition': condition})
        return stochastic_capacity_public_context(config, self.record, **kwargs)

    def test_i0_omits_hidden_current_and_sequence_fields_before_decision(self):
        context = self.context(INFO_I0)
        self.assertFalse(context['current_capacity_revealed'])
        for field in ('actual_capacity', 'capacity_level', 'quantile_stratum_index', 'sequence_id', 'sequence_seed'):
            self.assertNotIn(field, context)
        self.assertEqual(context['capacity_distribution'], 'truncated_normal')
        self.assertEqual(context['capacity_mu'], CAPACITY_MU)
        self.assertEqual(context['capacity_sigma'], CAPACITY_SIGMA)
        self.assertAlmostEqual(context['capacity_truncated_mean'], 2.665)
        self.assertAlmostEqual(context['capacity_truncated_sd'], 0.6371680565, places=9)

    def test_i1_reveals_only_exact_current_capacity_and_level(self):
        i0 = self.context(INFO_I0)
        i1 = self.context(INFO_I1)
        self.assertTrue(i1['current_capacity_revealed'])
        self.assertEqual(i1['actual_capacity'], 2.37)
        self.assertEqual(i1['capacity_level'], 'medium')
        self.assertEqual(set(i1) - set(i0), {'actual_capacity', 'capacity_level'})

    def test_both_conditions_receive_same_realized_feedback(self):
        i0 = self.context(INFO_I0, after_decision=True)
        i1 = self.context(INFO_I1, after_decision=True)
        self.assertEqual(i0['actual_capacity'], i1['actual_capacity'])
        self.assertEqual(i0['capacity_level'], i1['capacity_level'])


class TruncatedNormalSequenceBankTests(unittest.TestCase):
    def test_checked_in_bank_has_five_complete_valid_sequences(self):
        bank = load_stochastic_capacity_sequence_bank()
        self.assertEqual(set(bank), APPROVED_SEQUENCE_IDS)
        signatures = set()
        for sequence_id, sequence in bank.items():
            with self.subTest(sequence_id=sequence_id):
                rounds = sequence['rounds']
                self.assertEqual(len(rounds), FORMAL_ROUNDS)
                self.assertEqual(
                    {record['quantile_stratum_index'] for record in rounds},
                    set(range(1, FORMAL_ROUNDS + 1)),
                )
                values = [record['actual_capacity'] for record in rounds]
                self.assertAlmostEqual(sequence['mean_actual_capacity'], sum(values) / 30)
                self.assertAlmostEqual(sequence['population_standard_deviation'], pstdev(values))
                signatures.add(tuple(values))
        self.assertEqual(len(signatures), 5)

    def test_loader_rejects_tampered_contract_or_record(self):
        payload = json.loads(Path(__file__).with_name(SEQUENCE_BANK_FILE).read_text(encoding='utf-8'))
        mutations = (
            lambda value: value.__setitem__('version', 1),
            lambda value: value.__setitem__('mechanism', 'uniform_stochastic_capacity_v1'),
            lambda value: value.__setitem__('distribution', 'uniform'),
            lambda value: value.__setitem__('capacity_mu', 2.66),
            lambda value: value.__setitem__('capacity_sigma', 0.81),
            lambda value: value.__setitem__('capacity_min', 1.32),
            lambda value: value.__setitem__('capacity_max', 4.01),
            lambda value: value['sequences'][0]['rounds'][0].__setitem__('capacity_level', 'wrong'),
            lambda value: value['sequences'][0]['rounds'][0].__setitem__('sequence_seed', -1),
            lambda value: value['sequences'][0]['rounds'][0].__setitem__('formal_round_number', 99),
            lambda value: value['sequences'][0]['rounds'][0].__setitem__('quantile_stratum_index', 99),
            lambda value: value['sequences'][0]['rounds'][0].__setitem__('actual_capacity', 4.00),
            lambda value: value['sequences'][0].__setitem__('mean_actual_capacity', 0),
            lambda value: value['sequences'][0].__setitem__('population_standard_deviation', 0),
            lambda value: value['sequences'].__setitem__(0, 'not-an-object'),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                candidate = json.loads(json.dumps(payload))
                mutate(candidate)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'bank.json'
                    path.write_text(json.dumps(candidate), encoding='utf-8')
                    with self.assertRaises(StochasticCapacityConfigError):
                        load_stochastic_capacity_sequence_bank(path)


if __name__ == '__main__':
    unittest.main()
