"""Compatibility wrapper for independent Liu-REL participants."""

from .liu_rel_agent import (
    LIU_REL_POLICY_VERSION,
    append_liu_rel_experience,
    choose_liu_rel_departure,
    initial_liu_rel_state,
    valid_or_initial_liu_rel_state,
)


INDEPENDENT_RL_POLICY_VERSION = LIU_REL_POLICY_VERSION


def initial_independent_rl_state(capacity_states=None):
    return initial_liu_rel_state()


def valid_or_initial_independent_rl_state(state, capacity_states=None):
    return valid_or_initial_liu_rel_state(state)


def choose_independent_rl_departure(**kwargs):
    return choose_liu_rel_departure(**kwargs)


def observe_independent_rl_outcome(state, **kwargs):
    return append_liu_rel_experience(state, **kwargs)
