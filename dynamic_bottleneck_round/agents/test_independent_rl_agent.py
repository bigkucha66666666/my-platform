import unittest

from dynamic_bottleneck_round.agents.independent_rl_agent import (
    INDEPENDENT_RL_POLICY_VERSION,
    choose_independent_rl_departure,
    initial_independent_rl_state,
)
from dynamic_bottleneck_round.agents.rl_fallback import (
    RL_POLICY_VERSION,
    valid_or_initial_state,
)


class IndependentRLAgentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.capacity_states = (
            {'capacity': 1, 'probability': 1 / 3},
            {'capacity': 2, 'probability': 1 / 3},
            {'capacity': 3, 'probability': 1 / 3},
        )
        self.slots = tuple(
            {'slot': slot, 'departure_minute': 472 + slot}
            for slot in range(1, 6)
        )
        self.costs = {
            'fixed_travel_time_cost': 6,
            'queue_cost_per_minute': 2,
            'early_cost_per_minute': 1,
            'late_cost_per_minute': 3,
            'preferred_arrival_minute': 480,
            'free_flow_travel_minutes': 6,
            'capacity_window_minutes': 1,
        }
        self.persona = {
            'traits': {
                'queue_aversion': 5,
                'early_arrival_aversion': 5,
                'late_arrival_aversion': 7,
                'toll_sensitivity': 5,
                'reward_sensitivity': 5,
                'capacity_risk_aversion': 5,
                'choice_inertia': 5,
                'adaptation_speed': 5,
            }
        }

    def test_choice_uses_independent_policy_identity(self):
        state = initial_independent_rl_state(self.capacity_states)

        choice = choose_independent_rl_departure(
            state=state,
            available_slots=self.slots,
            cost_parameters=self.costs,
            capacity_states=self.capacity_states,
            tolls=(),
            rewards=(),
            persona=self.persona,
        )

        self.assertEqual(choice['decision_source'], 'rl_policy')
        self.assertEqual(choice['policy_version'], INDEPENDENT_RL_POLICY_VERSION)

    def test_independent_state_is_not_accepted_as_fallback_state(self):
        independent = initial_independent_rl_state(self.capacity_states)

        fallback = valid_or_initial_state(independent, self.capacity_states)

        self.assertEqual(fallback['policy_version'], RL_POLICY_VERSION)
        self.assertEqual(fallback['rounds_observed'], 0)


if __name__ == '__main__':
    unittest.main()
