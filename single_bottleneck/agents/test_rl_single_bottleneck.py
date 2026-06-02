import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rl_single_bottleneck import (
    QLearningConfig,
    SingleBottleneckEnv,
    SingleBottleneckParams,
    choose_action_from_policy,
    settle_bottleneck_round,
    train_q_learning,
)


class SingleBottleneckRlTests(unittest.TestCase):
    def test_same_departure_time_uses_fifo_queue_positions(self):
        params = SingleBottleneckParams()
        free_flow_departure = params.preferred_arrival_minute - params.free_flow_travel_minutes
        actors = [
            {"actor_id": "A", "departure_slot": 11, "departure_minute": free_flow_departure},
            {"actor_id": "B", "departure_slot": 11, "departure_minute": free_flow_departure},
            {"actor_id": "C", "departure_slot": 11, "departure_minute": free_flow_departure},
        ]

        outcomes = settle_bottleneck_round(actors, params)

        for index, outcome in enumerate(outcomes):
            self.assertEqual(outcome.slot_load, 3)
            self.assertEqual(outcome.queue_delay_minutes, index * 2.0)
            self.assertEqual(outcome.arrival_minute, params.preferred_arrival_minute + index * 2.0)
        self.assertEqual([outcome.payoff for outcome in outcomes], [140.0, 130.0, 120.0])

    def test_q_learning_updates_q_values(self):
        params = SingleBottleneckParams(
            first_departure_minute=471,
            num_slots=7,
            background_count=4,
        )
        env = SingleBottleneckEnv(params=params, seed=7)
        config = QLearningConfig(episodes=25, rounds_per_episode=4, epsilon=0.35, seed=11)

        policy = train_q_learning(env, config)

        self.assertEqual(policy["policy_version"], "q_learning_v1")
        self.assertGreater(len(policy["q_table"]), 0)
        self.assertTrue(all(1 <= action <= params.num_slots for action in policy["best_action_by_state"].values()))

    def test_exported_policy_loads_and_selects_valid_action(self):
        params = SingleBottleneckParams(
            first_departure_minute=472,
            num_slots=5,
            background_count=3,
        )
        env = SingleBottleneckEnv(params=params, seed=5)
        policy = train_q_learning(
            env,
            QLearningConfig(episodes=20, rounds_per_episode=3, epsilon=0.25, seed=13),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "policy.json"
            path.write_text(json.dumps(policy), encoding="utf-8")
            loaded_policy = json.loads(path.read_text(encoding="utf-8"))

        state = env.reset()
        action = choose_action_from_policy(loaded_policy, state, params)

        self.assertIn(action, params.departure_slots())


if __name__ == "__main__":
    unittest.main()
