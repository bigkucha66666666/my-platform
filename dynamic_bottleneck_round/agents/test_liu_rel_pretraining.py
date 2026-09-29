import json
import hashlib
import math
import tempfile
import unittest
from pathlib import Path

from dynamic_bottleneck_round.agents.liu_rel_pretraining import (
    BANK_FILE,
    build_pretraining_bank,
    count_invalid_holdout_rows,
    load_pretraining_profile,
    simulate_market,
    validate_pretraining_bank,
)


class IndependentMarketPretrainingTests(unittest.TestCase):
    def test_invalid_market_count_counts_each_bad_market(self):
        rows = [
            {'formal_rounds_observed': 30, 'decision_sources': ['liu_rel_softmax_i0'],
             'mean_cost': 5.0},
            {'formal_rounds_observed': 29, 'decision_sources': ['liu_rel_softmax_i0'],
             'mean_cost': 5.0},
            {'formal_rounds_observed': 30, 'decision_sources': ['rl_fallback_error'],
             'mean_cost': 5.0},
        ]
        self.assertEqual(count_invalid_holdout_rows(rows), 2)

    def test_market_uses_full_batch_passage_and_formal_cost_weights(self):
        departures = [9] * 6 + [10] * 24
        costs = simulate_market(capacity=2.0, departure_slots=departures)
        # Slot 9 departs at 7:54; a batch of 6 takes 3 minutes to pass.
        # Arrival 8:03 => 2*3 queue + 3*3 late = 15.
        self.assertEqual(len(costs), 30)
        self.assertEqual(costs[:6], [15.0] * 6)
        self.assertTrue(all(math.isfinite(cost) for cost in costs))

    def test_bank_is_deterministic_and_separates_train_holdout(self):
        first = build_pretraining_bank()
        second = build_pretraining_bank()
        self.assertEqual(first, second)
        self.assertEqual(len(first['profiles']), 10)
        self.assertTrue(set(first['training_seeds']).isdisjoint(first['holdout_seeds']))
        self.assertEqual(first['rel_random_seed'], 2026090901)
        self.assertEqual(first['validation']['holdout_design'], 'joint_ten_rl')
        self.assertEqual(len(first['validation']['conditions']['I0']['per_seat']), 10)
        self.assertEqual(len(first['validation']['conditions']['I1']['per_seat']), 10)
        for history in first['profiles'].values():
            self.assertEqual(len(history), 256)
            self.assertGreater(len({round(item['actual_capacity'], 1) for item in history}), 10)
            self.assertEqual({item['departure_slot'] for item in history}, set(range(1, 17)))
        self.assertNotIn('S01', json.dumps(first))
        self.assertNotIn('S05', json.dumps(first))

    def test_checked_profile_is_identical_for_i0_and_i1(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / 'bank.json'
            # Test the frozen deployment artifact, independent of platform libm.
            path.write_bytes(BANK_FILE.read_bytes())
            i0 = load_pretraining_profile(1, path=path)
            i1 = load_pretraining_profile(1, path=path)
            self.assertEqual(i0, i1)
            self.assertEqual(i0['rounds_observed'], 0)
            self.assertEqual(len(i0['pretraining_experiences']), 256)
            bank = validate_pretraining_bank(path)
            self.assertTrue(bank['validation']['accepted'])
            tampered = json.loads(path.read_text())
            tampered['profiles']['RL_01'][0]['total_cost'] += 1
            path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError, 'hash|checksum|fingerprint'):
                load_pretraining_profile(1, path=path)
            payload = {key: value for key, value in tampered.items() if key != 'sha256'}
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False).encode('utf-8')
            tampered['sha256'] = hashlib.sha256(encoded).hexdigest()
            path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError, 'frozen|fingerprint'):
                load_pretraining_profile(1, path=path)


if __name__ == '__main__':
    unittest.main()
