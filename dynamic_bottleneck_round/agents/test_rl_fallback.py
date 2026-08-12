import unittest

from dynamic_bottleneck_round.agents.rl_fallback import (
    choose_rl_departure,
    initial_rl_state,
    observe_rl_outcome,
    rl_state_key,
)


class DynamicRLFallbackPolicyTests(unittest.TestCase):
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

    def test_initial_state_uses_public_capacity_prior(self):
        state = initial_rl_state(self.capacity_states)

        self.assertEqual(state['capacity_values'], [1, 2, 3])
        self.assertEqual(state['capacity_prior'], [1 / 3, 1 / 3, 1 / 3])
        self.assertEqual(state['observed_capacities'], [])
        self.assertEqual(state['q_values'], {})

    def test_observations_learn_only_completed_capacity_transitions(self):
        state = initial_rl_state(self.capacity_states)
        state = observe_rl_outcome(
            state,
            revealed_capacity=3,
            departure_slot=3,
            total_cost=12,
            anonymous_slot_counts={'3': 2},
            persona=self.persona,
        )
        state = observe_rl_outcome(
            state,
            revealed_capacity=3,
            departure_slot=3,
            total_cost=8,
            anonymous_slot_counts={'3': 1},
            persona=self.persona,
        )

        self.assertEqual(state['observed_capacities'], [3, 3])
        self.assertEqual(state['transition_counts']['3']['3'], 1)
        learned_values = [
            actions.get('3')
            for actions in state['q_values'].values()
            if '3' in actions
        ]
        self.assertTrue(learned_values)
        self.assertLess(learned_values[-1], 0)
        self.assertEqual(state['rounds_observed'], 2)

    def test_q_values_are_scoped_to_observable_belief_state(self):
        state = initial_rl_state(self.capacity_states)
        updated = observe_rl_outcome(
            state,
            revealed_capacity=2,
            departure_slot=4,
            total_cost=10,
            anonymous_slot_counts={'4': 1},
            persona=self.persona,
        )

        self.assertEqual(len(updated['q_values']), 1)
        state_key, actions = next(iter(updated['q_values'].items()))
        self.assertIn('last=none', state_key)
        self.assertIn('belief=', state_key)
        self.assertIn('4', actions)
        self.assertIn('own=4', rl_state_key(updated))

    def test_choice_is_legal_and_does_not_require_current_capacity(self):
        state = initial_rl_state(self.capacity_states)

        choice = choose_rl_departure(
            state=state,
            available_slots=self.slots,
            cost_parameters=self.costs,
            capacity_states=self.capacity_states,
            tolls=(),
            rewards=(),
            persona=self.persona,
        )

        self.assertIn(choice['departure_slot'], {1, 2, 3, 4, 5})
        self.assertEqual(choice['decision_source'], 'deepseek_fallback_rl')
        self.assertIn('belief', choice)

    def test_known_current_capacity_overrides_belief_without_resetting_history(self):
        state = initial_rl_state(self.capacity_states)
        state['observed_capacities'] = [1, 2, 2]
        state['rounds_observed'] = 3

        choice = choose_rl_departure(
            state=state,
            available_slots=self.slots,
            cost_parameters=self.costs,
            capacity_states=self.capacity_states,
            tolls=(),
            rewards=(),
            persona=self.persona,
            known_current_capacity=3,
        )

        self.assertEqual(choice['belief'], {'1': 0.0, '2': 0.0, '3': 1.0})
        self.assertEqual(choice['rounds_observed'], 3)


if __name__ == '__main__':
    unittest.main()
