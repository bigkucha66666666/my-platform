"""Local shadow-learning fallback for dynamic bottleneck API Agents."""

from __future__ import annotations

from copy import deepcopy
from typing import Mapping, Sequence


RL_POLICY_VERSION = 'dynamic_rl_fallback_v1'


def public_feedback_observation(snapshot) -> dict:
    """Return the public fields used by RL after a completed round."""
    feedback = dict(snapshot or {})
    return {
        'revealed_capacity': feedback.get('dynamic_capacity'),
        'anonymous_slot_counts': {
            str(int(row['slot'])): int(row.get('participant_count', 0))
            for row in feedback.get('departure_outcomes', [])
        },
        'departure_average_costs': {
            str(int(row['slot'])): float(row['average_cost'])
            for row in feedback.get('departure_outcomes', [])
            if row.get('average_cost') is not None
        },
        'group_average_cost': float(feedback.get('group_average_cost', 0)),
    }


def initial_rl_state(
    capacity_states: Sequence[Mapping[str, object]],
    *,
    policy_version=RL_POLICY_VERSION,
) -> dict:
    values = [float(item['capacity']) for item in capacity_states]
    labels = [
        str(item.get('state', _capacity_key(item['capacity'])))
        for item in capacity_states
    ]
    probabilities = _normalized_probabilities(capacity_states)
    return {
        'policy_version': policy_version,
        'capacity_values': values,
        'capacity_labels': labels,
        'capacity_prior': probabilities,
        'observed_capacities': [],
        'q_values': {},
        'last_departure_slot': None,
        'last_total_cost': None,
        'last_anonymous_slot_counts': {},
        'last_departure_average_costs': {},
        'last_group_average_cost': None,
        'last_own_public_result': None,
        'rounds_observed': 0,
    }


def valid_or_initial_state(
    state,
    capacity_states,
    *,
    policy_version=RL_POLICY_VERSION,
) -> dict:
    expected_values = [float(item['capacity']) for item in capacity_states]
    expected_labels = [
        str(item.get('state', _capacity_key(item['capacity'])))
        for item in capacity_states
    ]
    if not isinstance(state, dict):
        return initial_rl_state(capacity_states, policy_version=policy_version)
    if state.get('policy_version') != policy_version:
        return initial_rl_state(capacity_states, policy_version=policy_version)
    if state.get('capacity_values') != expected_values:
        return initial_rl_state(capacity_states, policy_version=policy_version)
    if state.get('capacity_labels') != expected_labels:
        return initial_rl_state(capacity_states, policy_version=policy_version)
    return deepcopy(state)


def observe_rl_outcome(
    state,
    *,
    revealed_capacity,
    departure_slot,
    total_cost,
    anonymous_slot_counts,
    departure_average_costs=None,
    group_average_cost=None,
    own_public_result=None,
    persona,
) -> dict:
    updated = deepcopy(state)
    previous_state_key = rl_state_key(updated)
    try:
        capacity = float(revealed_capacity)
    except (TypeError, ValueError):
        return updated
    if capacity <= 0:
        return updated

    observed = [
        float(value) for value in updated.get('observed_capacities', [])
    ]
    observed.append(capacity)
    updated['observed_capacities'] = observed

    slot_key = str(int(departure_slot))
    q_values = updated.setdefault('q_values', {})
    state_actions = q_values.setdefault(previous_state_key, {})
    old_value = float(state_actions.get(slot_key, 0))
    learning_rate = _learning_rate(persona)
    reward = -float(total_cost)
    updated['last_departure_slot'] = int(departure_slot)
    updated['last_total_cost'] = round(float(total_cost), 4)
    updated['last_anonymous_slot_counts'] = {
        str(int(slot)): int(count)
        for slot, count in dict(anonymous_slot_counts or {}).items()
    }
    updated['last_departure_average_costs'] = {
        str(int(slot)): float(cost)
        for slot, cost in dict(departure_average_costs or {}).items()
    }
    updated['last_group_average_cost'] = (
        round(float(group_average_cost), 4)
        if group_average_cost is not None
        else None
    )
    updated['last_own_public_result'] = deepcopy(own_public_result)
    next_state_key = rl_state_key(updated)
    next_values = q_values.get(next_state_key, {})
    next_best = max((float(value) for value in next_values.values()), default=0.0)
    discount = 0.85
    state_actions[slot_key] = round(
        old_value + learning_rate * (reward + discount * next_best - old_value),
        6,
    )
    updated['rounds_observed'] = int(updated.get('rounds_observed', 0)) + 1
    return updated


def capacity_belief(state) -> dict[float, float]:
    values = [float(value) for value in state['capacity_values']]
    prior = [float(value) for value in state['capacity_prior']]
    return dict(zip(values, prior))


def rl_state_key(state) -> str:
    belief = capacity_belief(state)
    belief_bin = ','.join(
        f'{_capacity_key(capacity)}:{int(round(probability * 10))}'
        for capacity, probability in sorted(belief.items())
    )
    last_capacity = (
        state.get('observed_capacities', [])[-1]
        if state.get('observed_capacities')
        else 'none'
    )
    last_slot = state.get('last_departure_slot') or 'none'
    return f'last={last_capacity}|belief={belief_bin}|own={last_slot}'


def choose_rl_departure(
    *,
    state,
    available_slots,
    cost_parameters,
    capacity_states,
    tolls,
    rewards,
    persona,
    known_current_capacity=None,
    known_incident_status=None,
    policy_version=RL_POLICY_VERSION,
    decision_source='deepseek_fallback_rl',
) -> dict:
    current = valid_or_initial_state(
        state,
        capacity_states,
        policy_version=policy_version,
    )
    belief = capacity_belief(current)
    if known_current_capacity is not None:
        known = float(known_current_capacity)
        if known > 0:
            belief = {known: 1.0}
    elif known_incident_status is not None:
        desired_label = 'incident_expected' if known_incident_status else 'normal'
        matching = [
            capacity
            for label, capacity in zip(
                current['capacity_labels'],
                current['capacity_values'],
            )
            if label == desired_label
        ]
        if matching:
            belief = {float(matching[0]): 1.0}
    legal_slots = [dict(item) for item in available_slots]
    if not legal_slots:
        raise ValueError('RL fallback has no legal departure slots.')

    toll_by_slot = _slot_values(tolls, 'charge')
    reward_by_slot = _slot_values(rewards, 'bonus')
    previous_counts = {
        int(slot): int(count)
        for slot, count in current.get('last_anonymous_slot_counts', {}).items()
    }
    previous_own_slot = current.get('last_departure_slot')
    if previous_own_slot in previous_counts and previous_counts[previous_own_slot] > 0:
        previous_counts[previous_own_slot] -= 1
    state_key = rl_state_key(current)
    q_values = current.get('q_values', {}).get(state_key, {})
    last_slot = current.get('last_departure_slot')
    inertia = _trait(persona, 'choice_inertia', 5) / 10
    risk_aversion = _trait(persona, 'capacity_risk_aversion', 5) / 10

    ranked = []
    detail = {}
    for item in legal_slots:
        slot = int(item['slot'])
        costs_by_capacity = [
            _estimated_cost(
                selected_slot=slot,
                capacity=capacity,
                available_slots=legal_slots,
                previous_counts=previous_counts,
                cost_parameters=cost_parameters,
                toll_by_slot=toll_by_slot,
                reward_by_slot=reward_by_slot,
            )
            for capacity in belief
        ]
        expected_cost = sum(
            belief[capacity] * cost
            for capacity, cost in zip(belief, costs_by_capacity)
        )
        worst_cost = max(costs_by_capacity)
        risk_adjusted_cost = expected_cost + risk_aversion * (worst_cost - expected_cost)
        learned_value = float(q_values.get(str(slot), 0))
        learned_cost_correction = max(-20.0, min(20.0, -learned_value)) * 0.15
        inertia_bonus = 1.5 * inertia if last_slot == slot else 0
        score = risk_adjusted_cost + learned_cost_correction - inertia_bonus
        detail[str(slot)] = {
            'expected_cost': round(expected_cost, 4),
            'risk_adjusted_cost': round(risk_adjusted_cost, 4),
            'score': round(score, 4),
        }
        ranked.append((score, expected_cost, slot))

    selected_slot = min(ranked)[2]
    belief_json = {
        _capacity_key(capacity): round(probability, 6)
        for capacity, probability in belief.items()
    }
    return {
        'departure_slot': selected_slot,
        'decision_source': decision_source,
        'reason': 'Local shadow RL selected the lowest belief-adjusted cost action.',
        'belief': belief_json,
        'scores': detail,
        'policy_version': policy_version,
        'rounds_observed': int(current.get('rounds_observed', 0)),
    }


def _estimated_cost(
    *,
    selected_slot,
    capacity,
    available_slots,
    previous_counts,
    cost_parameters,
    toll_by_slot,
    reward_by_slot,
):
    fixed_cost = float(cost_parameters.get('fixed_travel_time_cost', 0))
    queue_rate = float(cost_parameters.get('queue_cost_per_minute', 0))
    early_rate = float(cost_parameters.get('early_cost_per_minute', 0))
    late_rate = float(cost_parameters.get('late_cost_per_minute', 0))
    preferred_arrival = float(cost_parameters.get('preferred_arrival_minute', 480))
    free_flow = float(cost_parameters.get('free_flow_travel_minutes', 0))
    service_window = float(cost_parameters.get('capacity_window_minutes', 1))
    minute_by_slot = {
        int(item['slot']): float(item['departure_minute'])
        for item in available_slots
    }
    first_minute = min(minute_by_slot.values())
    next_available = first_minute
    selected_cost = None

    for slot, departure_minute in sorted(minute_by_slot.items(), key=lambda pair: pair[1]):
        other_load = max(0, int(previous_counts.get(slot, 0)))
        load = other_load + (1 if slot == selected_slot else 0)
        if load <= 0:
            continue
        first_service_start = max(departure_minute, next_available)
        numeric_capacity = float(capacity)
        if numeric_capacity <= 0:
            raise ValueError('RL capacity must be positive.')
        inherited_wait = max(0.0, first_service_start - departure_minute)
        service_duration = load / numeric_capacity * service_window
        queue_delay = inherited_wait + max(0.0, service_duration - service_window)
        if slot == selected_slot:
            arrival = departure_minute + free_flow + queue_delay
            early = max(0.0, preferred_arrival - arrival)
            late = max(0.0, arrival - preferred_arrival)
            selected_cost = (
                fixed_cost
                + queue_rate * queue_delay
                + early_rate * early
                + late_rate * late
                + toll_by_slot.get(slot, 0)
                - reward_by_slot.get(slot, 0)
            )
        next_available = first_service_start + service_duration

    return float(selected_cost if selected_cost is not None else fixed_cost)


def _normalized_probabilities(capacity_states):
    probabilities = [max(0.0, float(item.get('probability', 0))) for item in capacity_states]
    total = sum(probabilities)
    if total <= 0:
        return [1 / len(probabilities) for _ in probabilities]
    return [value / total for value in probabilities]


def _capacity_key(value):
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return str(numeric)


def _slot_values(items, key):
    return {
        int(item['slot']): float(item.get(key, 0))
        for item in items
        if 'slot' in item
    }


def _trait(persona, name, default):
    try:
        return max(1, min(10, int(persona.get('traits', {}).get(name, default))))
    except (AttributeError, TypeError, ValueError):
        return default


def _learning_rate(persona):
    adaptation = _trait(persona, 'adaptation_speed', 5)
    return 0.1 + 0.05 * adaptation
