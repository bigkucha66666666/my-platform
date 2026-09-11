from pathlib import Path
import unittest

from dynamic_bottleneck_round.agents.independent_rl_agent import (
    INDEPENDENT_RL_POLICY_VERSION,
    choose_independent_rl_departure,
    initial_independent_rl_state,
    observe_independent_rl_outcome,
    valid_or_initial_independent_rl_state,
)
from dynamic_bottleneck_round.agents.liu_rel_agent import LIU_REL_POLICY_VERSION


class IndependentRLAgentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.slots = tuple(
            {'slot': slot, 'departure_minute': 465 + slot}
            for slot in range(1, 17)
        )

    def test_wrapper_uses_liu_rel_policy_identity(self):
        self.assertEqual(INDEPENDENT_RL_POLICY_VERSION, LIU_REL_POLICY_VERSION)
        self.assertEqual(
            initial_independent_rl_state([])['policy_version'],
            LIU_REL_POLICY_VERSION,
        )

    def test_wrapper_no_longer_imports_llm_rl_fallback(self):
        source = Path(__file__).with_name('independent_rl_agent.py').read_text(
            encoding='utf-8'
        )

        self.assertNotIn('rl_fallback', source)
        self.assertIn('liu_rel_agent', source)

    def test_wrapper_choice_returns_liu_rel_audit(self):
        choice = choose_independent_rl_departure(
            state=initial_independent_rl_state([]),
            available_slots=self.slots,
            formal_round_number=1,
            information_condition='I0',
            rel_lambda=0.25,
            rel_eta=14.7445,
            session_code='SESSION01',
            group_id=1,
            agent_id='G01_RL_01',
            rel_random_seed=2026090901,
            rel_initial_uniform_rounds=2,
        )

        self.assertEqual(choice['decision_source'], 'liu_rel_uniform_initial')
        self.assertEqual(choice['policy_version'], LIU_REL_POLICY_VERSION)
        self.assertEqual(choice['policy_version'], 'dynamic_liu_rel_incident_v2')
        self.assertEqual(len(choice['choice_probabilities']), 16)
        self.assertNotIn('rel_capacity_bandwidth', choice)

    def test_wrapper_observation_appends_only_own_experience(self):
        state = observe_independent_rl_outcome(
            initial_independent_rl_state([]),
            formal_round_number=1,
            departure_slot=7,
            total_cost=12.5,
            incident_occurred=True,
            actual_capacity=1.5,
            decision_source='rl_fallback_lowest_schedule_cost',
        )

        self.assertEqual(state['rounds_observed'], 1)
        self.assertEqual(
            state['experiences'],
            [
                {
                    'formal_round_number': 1,
                    'departure_slot': 7,
                    'total_cost': 12.5,
                    'incident_occurred': True,
                    'actual_capacity': 1.5,
                    'decision_source': 'rl_fallback_lowest_schedule_cost',
                }
            ],
        )

    def test_wrapper_resets_old_independent_policy_state(self):
        old = {
            'policy_version': 'dynamic_independent_rl_v1',
            'rounds_observed': 20,
            'experiences': [],
        }

        current = valid_or_initial_independent_rl_state(old, [])

        self.assertEqual(current, initial_independent_rl_state([]))


if __name__ == '__main__':
    unittest.main()
