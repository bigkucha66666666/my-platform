"""Pure adaptive-bank regressions; run with the isolated project runner."""

from collections import Counter
import hashlib
import importlib
import json
import math
import time
import unittest
from unittest.mock import patch


MODULE = 'dynamic_bottleneck_round.agents.adaptive_pretraining'


class AdaptivePretrainingTests(unittest.TestCase):
    def setUp(self):
        try:
            self.module = importlib.import_module(MODULE)
        except ModuleNotFoundError as exc:
            self.fail(f'adaptive simulated experience implementation is missing: {exc}')

    def bank(self, n=9, first=471, slots=5, late=4):
        return self.module.build_adaptive_bank(
            actor_count=n, first_departure_minute=first,
            num_slots=slots, late_cost_per_minute=late,
        )

    def test_batch_settlement_matches_independent_formula(self):
        # Same-time actors all receive full batch passage time; inherited
        # backlog persists into the next occupied departure minute.
        for departures, first, capacity in [([1, 1, 2], 477, 1.33),
                                            ([1, 3, 3], 470, 2.665),
                                            ([1], 480, 4.0)]:
            result = self.module.simulate_adaptive_market(
                capacity=capacity, departure_slots=departures,
                actor_count=len(departures), first_departure_minute=first,
                num_slots=3, late_cost_per_minute=4,
            )
            queue_end = float(first)
            expected = [None] * len(departures)
            for slot in sorted(set(departures)):
                indices = [i for i, value in enumerate(departures) if value == slot]
                minute = first + slot - 1
                wait = max(0, queue_end - minute) + len(indices) / capacity
                arrival = minute + 6 + wait
                cost = 2 * wait + max(480 - arrival, 0) + 4 * max(arrival - 480, 0)
                for i in indices:
                    expected[i] = cost
                queue_end = minute + wait
            for actual, wanted in zip(result, expected):
                self.assertAlmostEqual(actual, wanted, places=12)

    def test_designs_cover_every_legal_slot_in_256_experiences_per_seat(self):
        for n, first, slots in [(1, 473, 3), (2, 473, 3),
                                (9, 471, 5), (30, 464, 14), (40, 461, 18)]:
            with self.subTest(n=n):
                artifact = self.bank(n, first, slots)
                self.assertEqual(artifact['actor_count'], n)
                self.assertEqual(artifact['first_departure_minute'], first)
                self.assertEqual(artifact['num_slots'], slots)
                self.assertEqual(set(artifact['profiles']),
                                 {f'RL_{seat:02d}' for seat in range(1, 11)})
                for rows in artifact['profiles'].values():
                    self.assertEqual(len(rows), 256)
                    self.assertEqual([row['training_round_number'] for row in rows],
                                     list(range(1, 257)))
                    counts = Counter(row['departure_slot'] for row in rows)
                    self.assertEqual(set(counts), set(range(1, slots + 1)))
                    self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
                    self.assertTrue(all(math.isfinite(row['total_cost']) for row in rows))
                    self.assertTrue(all(1.33 <= row['actual_capacity'] <= 4 for row in rows))
                self.assertEqual(artifact['rel_lambda'], .25)
                self.assertEqual(artifact['rel_eta'], 14.7445)
                self.assertEqual(artifact['rel_random_seed'], 2026090901)
                self.assertFalse(artifact['validation']['performance_validated'])
                self.assertFalse(artifact['validation']['legacy_holdout_transferred'])

    def test_supplied_shifted_window_uses_six_minute_arrival_once(self):
        # Parent owns the -6 window shift. Last N30 departure is 07:57;
        # a lone batch at capacity2 arrives 08:03:30 and costs 1 + 4*3.5.
        costs = self.module.simulate_adaptive_market(
            capacity=2, departure_slots=[14], actor_count=1,
            first_departure_minute=464, num_slots=14)
        self.assertEqual(costs, [15.0])

    def test_identity_changes_with_every_design_input(self):
        artifacts = [self.bank(), self.bank(n=8), self.bank(first=476),
                     self.bank(slots=6), self.bank(late=3)]
        self.assertEqual(len({item['bank_id'] for item in artifacts}), len(artifacts))
        self.assertEqual(len({item['sha256'] for item in artifacts}), len(artifacts))

    def test_reproducibility_and_payload_hash(self):
        self.module._build_cached_bank.cache_clear()
        first = self.bank()
        self.module._build_cached_bank.cache_clear()
        self.assertEqual(first, self.bank())
        payload = {key: value for key, value in first.items() if key != 'sha256'}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
        self.assertEqual(first['sha256'], hashlib.sha256(encoded).hexdigest())
        seeds = first['training_seeds']
        self.assertEqual(len(seeds), 160)
        self.assertEqual(len(set(seeds)), len(seeds))
        self.assertNotEqual(first['profiles']['RL_01'], first['profiles']['RL_02'])

    def test_cached_build_and_public_copies_are_isolated(self):
        self.module._build_cached_bank.cache_clear()
        original = self.bank()
        with patch.object(self.module, 'simulate_adaptive_market',
                          side_effect=AssertionError('cache was not used')):
            cached = self.bank()
        self.assertEqual(original, cached)
        cached['profiles']['RL_01'][0]['total_cost'] = -100
        cached['training_seeds'].clear()
        self.assertEqual(original, self.bank())

    def test_loaded_profile_is_fresh_and_has_zero_formal_rounds(self):
        state = self.module.load_adaptive_profile(
            2, actor_count=9, first_departure_minute=471, num_slots=5)
        self.assertEqual(state['rounds_observed'], 0)
        self.assertEqual(state['experiences'], [])
        self.assertEqual(state['pretraining_bank_id'], self.bank()['bank_id'])
        self.assertEqual(state['pretraining_experiences'], self.bank()['profiles']['RL_02'])
        state['pretraining_experiences'][0]['departure_slot'] = -1
        self.assertGreater(self.bank()['profiles']['RL_02'][0]['departure_slot'], 0)

    def test_in_memory_generation_and_timing_evidence(self):
        self.module._build_cached_bank.cache_clear()
        measurements = []
        # This also catches accidental bank or formal-sequence file access.
        with patch('builtins.open', side_effect=AssertionError('runtime file I/O')):
            for n, first, slots in [(2, 473, 3), (9, 471, 5), (30, 464, 14),
                                    (40, 461, 18)]:
                started = time.perf_counter()
                cold = self.bank(n, first, slots)
                cold_seconds = time.perf_counter() - started
                started = time.perf_counter()
                for _ in range(10):
                    warm = self.bank(n, first, slots)
                warm_seconds = (time.perf_counter() - started) / 10
                started = time.perf_counter()
                for _ in range(10):
                    self.module.load_adaptive_profile(
                        1, actor_count=n, first_departure_minute=first, num_slots=slots)
                profile_seconds = (time.perf_counter() - started) / 10
                self.assertEqual(cold, warm)
                measurements.append(dict(actor_count=n, cold_seconds=cold_seconds,
                                         warm_copy_seconds=warm_seconds,
                                         profile_copy_seconds=profile_seconds))
        print('ADAPTIVE_BANK_TIMING ' + json.dumps(measurements, sort_keys=True))

    def test_invalid_designs_and_markets_are_rejected(self):
        for overrides in [dict(actor_count=0),
                          dict(actor_count=True), dict(actor_count=2.5),
                          dict(num_slots=0), dict(num_slots=257),
                          dict(first_departure_minute=477.5),
                          dict(late_cost_per_minute=float('nan')),
                          dict(late_cost_per_minute=-1)]:
            args = dict(actor_count=9, first_departure_minute=477, num_slots=5)
            args.update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.module.build_adaptive_bank(**args)
        for seat in [0, 11, True, 1.5, '2']:
            with self.subTest(seat=seat), self.assertRaises(ValueError):
                self.module.load_adaptive_profile(
                    seat, actor_count=9, first_departure_minute=477, num_slots=5)
        for capacity, departures in [(0, [1, 1]), (float('inf'), [1, 1]),
                                     (2, [1]), (2, [1, 4]), (2, [True, 1])]:
            with self.subTest(capacity=capacity, departures=departures), self.assertRaises(ValueError):
                self.module.simulate_adaptive_market(
                    capacity=capacity, departure_slots=departures,
                    actor_count=2, first_departure_minute=479, num_slots=3)


if __name__ == '__main__':
    unittest.main()
