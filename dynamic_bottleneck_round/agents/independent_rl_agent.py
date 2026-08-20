"""Independent local RL participants for the dynamic bottleneck app."""

from .rl_fallback import (
    choose_rl_departure,
    initial_rl_state,
    observe_rl_outcome,
    valid_or_initial_state,
)


INDEPENDENT_RL_POLICY_VERSION = 'dynamic_independent_rl_v1'


def initial_independent_rl_state(capacity_states):
    return initial_rl_state(
        capacity_states,
        policy_version=INDEPENDENT_RL_POLICY_VERSION,
    )


def valid_or_initial_independent_rl_state(state, capacity_states):
    return valid_or_initial_state(
        state,
        capacity_states,
        policy_version=INDEPENDENT_RL_POLICY_VERSION,
    )


def choose_independent_rl_departure(**kwargs):
    return choose_rl_departure(
        **kwargs,
        policy_version=INDEPENDENT_RL_POLICY_VERSION,
        decision_source='rl_policy',
    )


def observe_independent_rl_outcome(state, **kwargs):
    return observe_rl_outcome(state, **kwargs)
