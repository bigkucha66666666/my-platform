import unittest

from dynamic_bottleneck_round.accident_capacity import (
    INFO_I0,
    INFO_I2,
    AccidentRiskConfigError,
    generate_accident_sequence,
    parse_accident_risk_config,
)


class AccidentRiskConfigTests(unittest.TestCase):
    def test_parses_approved_default_parameters(self):
        config = parse_accident_risk_config({})

        self.assertEqual(config.normal_capacity, 4.0)
        self.assertEqual(config.incident_probability, 0.20)
        self.assertEqual(config.loss_alpha, 6.83057)
        self.assertEqual(config.loss_beta, 4.05907)
        self.assertEqual(config.information_condition, INFO_I0)

    def test_rejects_invalid_information_condition(self):
        with self.assertRaisesRegex(AccidentRiskConfigError, 'I0、I1 或 I2'):
            parse_accident_risk_config(
                {'accident_information_condition': 'I3'}
            )

    def test_rejects_non_finite_or_non_positive_parameters(self):
        invalid_cases = (
            ({'accident_normal_capacity': 0}, 'normal_capacity'),
            ({'accident_probability': float('nan')}, 'incident_probability'),
            ({'accident_probability': 1.1}, 'incident_probability'),
            ({'accident_loss_alpha': 0}, 'loss_alpha'),
            ({'accident_loss_beta': float('inf')}, 'loss_beta'),
        )
        for raw_config, field_name in invalid_cases:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    AccidentRiskConfigError,
                    field_name,
                ):
                    parse_accident_risk_config(raw_config)

    def test_rejects_boolean_numeric_parameters(self):
        with self.assertRaisesRegex(AccidentRiskConfigError, 'normal_capacity'):
            parse_accident_risk_config({'accident_normal_capacity': True})

    def test_seeded_sequence_is_reproducible_and_uses_float_capacity(self):
        config = parse_accident_risk_config(
            {
                'accident_sequence_seed': 2026090801,
                'accident_information_condition': INFO_I2,
            }
        )

        left = generate_accident_sequence(
            config,
            rounds=60,
            sequence_id='auto',
        )
        right = generate_accident_sequence(
            config,
            rounds=60,
            sequence_id='auto',
        )

        self.assertEqual(left, right)
        self.assertEqual(len(left), 60)
        self.assertTrue(any(record['incident_occurred'] for record in left))
        for index, record in enumerate(left, start=1):
            self.assertEqual(record['formal_round_number'], index)
            self.assertGreater(record['actual_capacity'], 0)
            if record['incident_occurred']:
                self.assertGreater(record['capacity_loss_ratio'], 0)
                self.assertLess(record['capacity_loss_ratio'], 1)
                self.assertAlmostEqual(
                    record['remaining_capacity_ratio'],
                    1 - record['capacity_loss_ratio'],
                    places=10,
                )
                self.assertAlmostEqual(
                    record['actual_capacity'],
                    4 * (1 - record['capacity_loss_ratio']),
                    places=10,
                )
            else:
                self.assertEqual(record['capacity_loss_ratio'], 0.0)
                self.assertEqual(record['remaining_capacity_ratio'], 1.0)
                self.assertEqual(record['actual_capacity'], 4.0)

    def test_rejects_non_positive_round_count(self):
        config = parse_accident_risk_config({})

        with self.assertRaisesRegex(AccidentRiskConfigError, 'rounds'):
            generate_accident_sequence(config, rounds=0, sequence_id='auto')


if __name__ == '__main__':
    unittest.main()
