"""Pure uniform-capacity-conditioned Liu-REL departure-choice mechanics."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import random
from typing import Iterable, Mapping, Sequence


LIU_REL_POLICY_VERSION = 'dynamic_liu_rel_uniform_capacity_v1'
INFORMATION_CONDITIONS = {'I0', 'I1'}
PHI_FLOOR = 1e-9
CAPACITY_KERNEL_BANDWIDTH = (4.00 - 1.33) / math.sqrt(12)
# Treat observations within two frozen bandwidths as effective kernel support.
CAPACITY_KERNEL_MIN_EFFECTIVE_WEIGHT = math.exp(-2)


class LiuRELAlgorithmError(ValueError):
    """Raised when valid Liu-REL probabilities cannot be produced."""


def initial_liu_rel_state() -> dict:
    return {
        'policy_version': LIU_REL_POLICY_VERSION,
        'rounds_observed': 0,
        'experiences': [],
        'last_departure_slot': None,
        'last_choice_probability': None,
        'last_context_level': None,
        'last_propensities': {},
        'last_choice_probabilities': {},
    }


def valid_or_initial_liu_rel_state(state) -> dict:
    if not isinstance(state, dict):
        return initial_liu_rel_state()
    if state.get('policy_version') != LIU_REL_POLICY_VERSION:
        return initial_liu_rel_state()
    experiences = state.get('experiences')
    if not isinstance(experiences, list):
        return initial_liu_rel_state()
    required = {
        'rounds_observed',
        'last_departure_slot',
        'last_choice_probability',
        'last_context_level',
        'last_propensities',
        'last_choice_probabilities',
    }
    if not required.issubset(state):
        return initial_liu_rel_state()
    if not isinstance(state.get('last_propensities'), dict):
        return initial_liu_rel_state()
    if not isinstance(state.get('last_choice_probabilities'), dict):
        return initial_liu_rel_state()
    try:
        rounds_observed = int(state.get('rounds_observed'))
    except (TypeError, ValueError):
        return initial_liu_rel_state()
    if rounds_observed < 0 or rounds_observed != len(experiences):
        return initial_liu_rel_state()
    seen_rounds = set()
    try:
        for item in experiences:
            if not isinstance(item, dict):
                return initial_liu_rel_state()
            round_number = _positive_int(
                item.get('formal_round_number'),
                'formal_round_number',
            )
            if round_number in seen_rounds:
                return initial_liu_rel_state()
            seen_rounds.add(round_number)
            _positive_int(item.get('departure_slot'), 'departure_slot')
            _finite_float(item.get('total_cost'), 'total_cost')
            _positive_float(item.get('actual_capacity'), 'actual_capacity')
    except LiuRELAlgorithmError:
        return initial_liu_rel_state()
    return deepcopy(state)


def append_liu_rel_experience(
    state,
    *,
    formal_round_number,
    departure_slot,
    total_cost,
    actual_capacity,
    decision_source='',
    warmup=False,
) -> dict:
    current = valid_or_initial_liu_rel_state(state)
    if warmup:
        return current
    round_number = _positive_int(formal_round_number, 'formal_round_number')
    slot = _positive_int(departure_slot, 'departure_slot')
    cost = _finite_float(total_cost, 'total_cost')
    capacity = _finite_float(actual_capacity, 'actual_capacity')
    if capacity <= 0:
        raise LiuRELAlgorithmError('actual_capacity must be positive.')
    experiences = current['experiences']
    if any(
        int(item.get('formal_round_number', 0)) == round_number
        for item in experiences
        if isinstance(item, dict)
    ):
        return current
    experiences.append(
        {
            'formal_round_number': round_number,
            'departure_slot': slot,
            'total_cost': cost,
            'actual_capacity': capacity,
            'decision_source': str(decision_source or ''),
        }
    )
    current['rounds_observed'] = len(experiences)
    current['last_departure_slot'] = slot
    return current


def select_information_conditioned_experiences(
    experiences: Sequence[Mapping[str, object]],
    *,
    information_condition,
    current_actual_capacity=None,
    capacity_kernel_bandwidth=CAPACITY_KERNEL_BANDWIDTH,
) -> dict:
    condition = str(information_condition or '').strip().upper()
    if condition not in INFORMATION_CONDITIONS:
        raise LiuRELAlgorithmError('information_condition must be I0 or I1.')
    history = [deepcopy(dict(item)) for item in experiences if isinstance(item, Mapping)]
    if condition == 'I0':
        return _selection(_unit_weighted(history), 'i0_all')

    if current_actual_capacity is None:
        raise LiuRELAlgorithmError(f'{condition} requires current actual capacity.')
    current_capacity = _positive_float(
        current_actual_capacity,
        'current actual capacity',
    )
    bandwidth = _positive_float(
        capacity_kernel_bandwidth,
        'capacity_kernel_bandwidth',
    )
    weighted = []
    for item in history:
        historical_capacity = _positive_float(
            item.get('actual_capacity'),
            'historical actual_capacity',
        )
        weight = math.exp(
            -((current_capacity - historical_capacity) ** 2)
            / (2 * bandwidth**2)
        )
        if weight >= CAPACITY_KERNEL_MIN_EFFECTIVE_WEIGHT:
            weighted.append({**item, 'weight': weight})

    if _distinct_slots(weighted) >= 2:
        return _selection(weighted, 'i1_capacity_kernel')
    if _distinct_slots(history) >= 2:
        return _selection(_unit_weighted(history), 'i1_backoff_i0')
    return _selection([], 'sparse')


def weighted_cost_statistics_by_slot(
    weighted_experiences: Sequence[Mapping[str, object]],
    *,
    rel_lambda,
) -> dict:
    lambda_value = _finite_float(rel_lambda, 'rel_lambda')
    if lambda_value < 0:
        raise LiuRELAlgorithmError('rel_lambda must be non-negative.')
    grouped = {}
    for item in weighted_experiences:
        slot = _positive_int(item.get('departure_slot'), 'departure_slot')
        cost = _finite_float(item.get('total_cost'), 'total_cost')
        weight = _finite_float(item.get('weight', 1), 'weight')
        if weight <= 0:
            continue
        grouped.setdefault(slot, []).append((cost, weight))

    statistics = {}
    for slot, observations in grouped.items():
        total_weight = sum(weight for _cost, weight in observations)
        mean = sum(cost * weight for cost, weight in observations) / total_weight
        variance = sum(
            weight * (cost - mean) ** 2
            for cost, weight in observations
        ) / total_weight
        standard_deviation = math.sqrt(max(0.0, variance))
        statistics[slot] = {
            'mean_cost': mean,
            'standard_deviation': standard_deviation,
            'propensity': mean - lambda_value * standard_deviation,
            'effective_observation_count': total_weight,
            'observation_count': len(observations),
        }
    return statistics


def interpolate_propensities(
    available_slot_numbers: Iterable[int],
    statistics_by_slot: Mapping[int, Mapping[str, object]],
) -> dict:
    slots = sorted({_positive_int(slot, 'slot') for slot in available_slot_numbers})
    anchors = sorted(
        (
            _positive_int(slot, 'experienced slot'),
            _finite_float(values['propensity'], 'propensity'),
        )
        for slot, values in statistics_by_slot.items()
        if int(slot) in slots
    )
    if len(anchors) < 2:
        raise LiuRELAlgorithmError('At least two experienced slots are required.')

    propensities = {slot: propensity for slot, propensity in anchors}
    for left, right in zip(anchors, anchors[1:]):
        left_slot, left_value = left
        right_slot, right_value = right
        slope = (right_value - left_value) / (right_slot - left_slot)
        for slot in slots:
            if left_slot < slot < right_slot:
                propensities[slot] = left_value + slope * (slot - left_slot)

    first_slot, first_value = anchors[0]
    second_slot, second_value = anchors[1]
    left_slope = (second_value - first_value) / (second_slot - first_slot)
    left_edge_slot = first_slot - 1
    left_edge_value = first_value - left_slope
    for slot in slots:
        if slot < first_slot:
            propensities[slot] = left_edge_value

    penultimate_slot, penultimate_value = anchors[-2]
    last_slot, last_value = anchors[-1]
    right_slope = (last_value - penultimate_value) / (
        last_slot - penultimate_slot
    )
    right_edge_slot = last_slot + 1
    right_edge_value = last_value + right_slope
    for slot in slots:
        if slot > last_slot:
            propensities[slot] = right_edge_value

    if left_edge_slot in slots:
        propensities[left_edge_slot] = left_edge_value
    if right_edge_slot in slots:
        propensities[right_edge_slot] = right_edge_value
    return {slot: propensities[slot] for slot in slots}


def liu_rel_choice_probabilities(
    propensities: Mapping[int, object],
    *,
    rel_eta,
    phi,
    phi_floor=PHI_FLOOR,
) -> dict:
    eta = _positive_float(rel_eta, 'rel_eta')
    scale = max(_positive_float(phi_floor, 'phi_floor'), abs(_finite_float(phi, 'phi')))
    clean = {
        _positive_int(slot, 'slot'): _finite_float(value, 'propensity')
        for slot, value in propensities.items()
    }
    if not clean:
        raise LiuRELAlgorithmError('At least one propensity is required.')
    logits = {slot: -eta * value / scale for slot, value in clean.items()}
    maximum = max(logits.values())
    exponentials = {slot: math.exp(value - maximum) for slot, value in logits.items()}
    total = sum(exponentials.values())
    if not math.isfinite(total) or total <= 0:
        raise LiuRELAlgorithmError('Liu-REL Softmax normalization failed.')
    probabilities = {slot: value / total for slot, value in exponentials.items()}
    if any(not math.isfinite(value) or value < 0 for value in probabilities.values()):
        raise LiuRELAlgorithmError('Liu-REL produced invalid probabilities.')
    probability_sum = sum(probabilities.values())
    if not math.isclose(probability_sum, 1.0, rel_tol=1e-12, abs_tol=1e-12):
        raise LiuRELAlgorithmError('Liu-REL probabilities do not sum to one.')
    return probabilities


def choose_liu_rel_departure(
    *,
    state,
    available_slots,
    formal_round_number,
    information_condition,
    rel_lambda,
    rel_eta,
    session_code,
    group_id,
    agent_id,
    rel_random_seed,
    rel_initial_uniform_rounds=2,
    warmup=False,
    current_actual_capacity=None,
    capacity_kernel_bandwidth=CAPACITY_KERNEL_BANDWIDTH,
) -> dict:
    current = valid_or_initial_liu_rel_state(state)
    legal_slots = sorted(
        {_positive_int(item['slot'], 'slot') for item in available_slots}
    )
    if not legal_slots:
        raise LiuRELAlgorithmError('Liu-REL has no legal departure slots.')
    if _explicit_int(
        rel_initial_uniform_rounds,
        'rel_initial_uniform_rounds',
    ) != 2:
        raise LiuRELAlgorithmError('rel_initial_uniform_rounds must equal 2.')
    lambda_value = _finite_float(rel_lambda, 'rel_lambda')
    if lambda_value < 0:
        raise LiuRELAlgorithmError('rel_lambda must be non-negative.')
    eta = _positive_float(rel_eta, 'rel_eta')
    random_seed = _explicit_int(rel_random_seed, 'rel_random_seed')
    decision_round = int(formal_round_number)
    seed_material = '|'.join(
        [
            str(session_code),
            str(_positive_int(group_id, 'group_id')),
            str(agent_id),
            str(decision_round),
            LIU_REL_POLICY_VERSION,
            str(random_seed),
        ]
    )
    digest = hashlib.sha256(seed_material.encode('utf-8')).hexdigest()
    rng = random.Random(int(digest, 16))

    uniform = {slot: 1 / len(legal_slots) for slot in legal_slots}
    propensities = {}
    context_level = 'warmup' if warmup else 'initial'
    effective_count = 0.0
    distinct_slots = 0
    if warmup:
        decision_source = 'liu_rel_uniform_warmup'
        probabilities = uniform
    elif decision_round <= 2:
        _positive_int(decision_round, 'formal_round_number')
        decision_source = 'liu_rel_uniform_initial'
        probabilities = uniform
    else:
        selection = select_information_conditioned_experiences(
            current['experiences'],
            information_condition=information_condition,
            current_actual_capacity=current_actual_capacity,
            capacity_kernel_bandwidth=capacity_kernel_bandwidth,
        )
        context_level = selection['context_level']
        weighted = selection['experiences']
        distinct_slots = _distinct_slots(weighted)
        effective_count = sum(float(item.get('weight', 1)) for item in weighted)
        if distinct_slots < 2:
            decision_source = 'liu_rel_uniform_sparse'
            probabilities = uniform
        else:
            statistics = weighted_cost_statistics_by_slot(
                weighted,
                rel_lambda=lambda_value,
            )
            propensities = interpolate_propensities(legal_slots, statistics)
            historical_costs = [
                _finite_float(item.get('total_cost'), 'historical total_cost')
                for item in current['experiences']
            ]
            phi = sum(historical_costs) / len(historical_costs)
            probabilities = liu_rel_choice_probabilities(
                propensities,
                rel_eta=eta,
                phi=phi,
            )
            decision_source = _softmax_source(context_level)

    departure_slot = _sample_slot(probabilities, rng)
    selected_probability = probabilities[departure_slot]
    return {
        'departure_slot': departure_slot,
        'decision_source': decision_source,
        'reason': 'Uniform-capacity-conditioned Liu-REL probability sample.',
        'policy_version': LIU_REL_POLICY_VERSION,
        'rounds_observed': int(current['rounds_observed']),
        'information_condition': str(information_condition).upper(),
        'context_level': context_level,
        'propensities': _string_keyed(propensities),
        'choice_probabilities': _string_keyed(probabilities),
        'selected_probability': selected_probability,
        'distinct_experienced_slots': distinct_slots,
        'effective_observation_count': effective_count,
        'rel_lambda': lambda_value,
        'rel_eta': eta,
        'capacity_kernel_bandwidth': float(capacity_kernel_bandwidth),
        'random_seed_fingerprint': digest[:12],
    }


def _softmax_source(context_level):
    return {
        'i0_all': 'liu_rel_softmax_i0',
        'i1_capacity_kernel': 'liu_rel_softmax_i1_capacity_kernel',
        'i1_backoff_i0': 'liu_rel_softmax_i1_backoff_i0',
    }[context_level]


def _selection(experiences, context_level):
    return {'experiences': experiences, 'context_level': context_level}


def _unit_weighted(experiences):
    return [{**item, 'weight': 1.0} for item in experiences]


def _distinct_slots(experiences):
    return len(
        {
            int(item['departure_slot'])
            for item in experiences
            if isinstance(item, Mapping) and 'departure_slot' in item
        }
    )


def _sample_slot(probabilities, rng):
    draw = rng.random()
    cumulative = 0.0
    last_slot = None
    for slot, probability in sorted(probabilities.items()):
        last_slot = slot
        cumulative += probability
        if draw < cumulative:
            return slot
    if last_slot is None:
        raise LiuRELAlgorithmError('Liu-REL has no probability mass.')
    return last_slot


def _string_keyed(values):
    return {str(slot): float(value) for slot, value in values.items()}


def _finite_float(value, field_name):
    if isinstance(value, bool):
        raise LiuRELAlgorithmError(f'{field_name} must be a finite number.')
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise LiuRELAlgorithmError(f'{field_name} must be a finite number.') from exc
    if not math.isfinite(parsed):
        raise LiuRELAlgorithmError(f'{field_name} must be a finite number.')
    return parsed


def _positive_float(value, field_name):
    parsed = _finite_float(value, field_name)
    if parsed <= 0:
        raise LiuRELAlgorithmError(f'{field_name} must be positive.')
    return parsed


def _explicit_int(value, field_name):
    if isinstance(value, bool):
        raise LiuRELAlgorithmError(f'{field_name} must be an integer.')
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LiuRELAlgorithmError(f'{field_name} must be an integer.') from exc
    if str(value).strip() != str(parsed):
        raise LiuRELAlgorithmError(f'{field_name} must be an integer.')
    return parsed


def _positive_int(value, field_name):
    parsed = _explicit_int(value, field_name)
    if parsed <= 0:
        raise LiuRELAlgorithmError(f'{field_name} must be positive.')
    return parsed
