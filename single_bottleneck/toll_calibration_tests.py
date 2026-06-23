import unittest

from single_bottleneck import (
    C,
    build_departure_schedule_record,
    cached_toll_assumptions_match,
    departure_slots_from_schedule,
)
from single_bottleneck.toll_calibration import (
    CALIBRATION_MODE_AUTO,
    SAME_TIME_QUEUE_RULE,
    base_expected_costs_for_counts,
    build_departure_minute_map,
    calibrate_candidates_by_mode,
)


class TollCalibrationQueueRuleTests(unittest.TestCase):
    def test_same_time_cost_uses_batch_max_wait(self):
        valid_slots = tuple(range(1, 4))
        departure_minutes = build_departure_minute_map(
            valid_slots,
            first_departure_minute=C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES - 2,
            slot_size_minutes=C.SLOT_SIZE_MINUTES,
        )

        costs = base_expected_costs_for_counts(
            (4, 0, 0),
            valid_slots=valid_slots,
            capacity=2,
            departure_minutes=departure_minutes,
        )

        self.assertEqual(costs[0], C.QUEUE_COST_PER_MINUTE * 2)

    def test_cache_requires_queue_rule_marker(self):
        cache = {
            'calibration_assumptions': {
                'capacity': 2,
                'choice_slots': 21,
                'choice_step_minutes': 1,
                'min_toll': 0,
                'max_toll': 40,
                'toll_step': 1,
                'first_departure_time': '07:44',
                'last_departure_time': '08:04',
            }
        }
        schedule = build_departure_schedule_record(
            players_count=5,
            capacity=2,
            min_slots_each_side=10,
            auto_enabled=True,
        )

        self.assertFalse(
            cached_toll_assumptions_match(
                cache,
                capacity=2,
                min_toll=0,
                max_toll=40,
                toll_step=1,
                calibration_mode='exact',
                schedule=schedule,
            )
        )

        cache['calibration_assumptions']['same_time_queue_rule'] = SAME_TIME_QUEUE_RULE
        self.assertFalse(
            cached_toll_assumptions_match(
                cache,
                capacity=2,
                min_toll=0,
                max_toll=40,
                toll_step=1,
                calibration_mode='exact',
                schedule=schedule,
            )
        )

        cache['calibration_assumptions']['toll_window_rule'] = 'symmetric_around_free_flow_departure'
        self.assertTrue(
            cached_toll_assumptions_match(
                cache,
                capacity=2,
                min_toll=0,
                max_toll=40,
                toll_step=1,
                calibration_mode='exact',
                schedule=schedule,
            )
        )

    def test_auto_mode_returns_exact_candidate_for_default_group(self):
        schedule = build_departure_schedule_record(
            players_count=5,
            capacity=2,
            min_slots_each_side=10,
            auto_enabled=True,
        )
        candidates = calibrate_candidates_by_mode(
            players=5,
            capacity=2,
            max_toll=20,
            top_k=1,
            valid_slots=tuple(departure_slots_from_schedule(schedule)),
            first_departure_minute=schedule.get('first_departure_minute'),
            slot_size_minutes=schedule.get('slot_size_minutes', C.DEPARTURE_CHOICE_STEP_MINUTES),
            calibration_mode=CALIBRATION_MODE_AUTO,
        )

        self.assertTrue(candidates)
        self.assertEqual(candidates[0].calibration_mode, 'exact')

    def test_auto_mode_uses_symmetric_toll_window_around_free_flow_departure(self):
        schedule = build_departure_schedule_record(
            players_count=5,
            capacity=2,
            min_slots_each_side=10,
            auto_enabled=True,
        )
        candidates = calibrate_candidates_by_mode(
            players=5,
            capacity=2,
            max_toll=20,
            top_k=1,
            valid_slots=tuple(departure_slots_from_schedule(schedule)),
            first_departure_minute=schedule.get('first_departure_minute'),
            slot_size_minutes=schedule.get('slot_size_minutes', C.DEPARTURE_CHOICE_STEP_MINUTES),
            calibration_mode=CALIBRATION_MODE_AUTO,
        )

        self.assertTrue(candidates)
        free_flow_slot = 11
        self.assertEqual(
            candidates[0].window_start + candidates[0].window_end,
            free_flow_slot * 2,
        )
        self.assertLess(candidates[0].window_start, free_flow_slot)
        self.assertGreater(candidates[0].window_end, free_flow_slot)


if __name__ == '__main__':
    unittest.main()
