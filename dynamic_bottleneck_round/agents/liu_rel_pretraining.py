"""Reproducible, independent-market pretraining for the main Liu-REL agents.

This module deliberately does not load or generate the formal S01--S05 bank.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
import random
from statistics import mean, pstdev

from ..stochastic_capacity import truncated_normal_quantile
from .liu_rel_agent import (
    CAPACITY_KERNEL_BANDWIDTH,
    LIU_REL_POLICY_VERSION,
    append_liu_rel_experience,
    choose_liu_rel_departure,
    initial_liu_rel_state,
)


BANK_ID = 'independent_market_liu_rel_v1'
BANK_SHA256 = '5b154ff70196a1da60ada0abfb29c7fb5a81eb1b5a68122cfdd6df25ee8246b5'
BANK_FILE = Path(__file__).with_name('liu_rel_pretraining_bank_v1.json')
TRAIN_MARKETS_PER_SEAT = 16
TRAIN_ROUNDS_PER_MARKET = 16
HOLDOUT_MARKETS_PER_SEAT = 20
HOLDOUT_ROUNDS_PER_MARKET = 30
SEAT_COUNT = 10
ACTOR_COUNT = 30
REL_LAMBDA = 0.25
REL_ETA = 14.7445
REL_RANDOM_SEED = 2026090901
TRAIN_SEED_BASE = 202609240000
HOLDOUT_SEED_BASE = 202609260000
SLOTS = tuple(range(1, 17))
SLOT_OPTIONS = tuple({'slot': slot} for slot in SLOTS)
OPPONENT_MODES = ('uniform', 'peak', 'early', 'late', 'habit')


def simulate_market(*, capacity: float, departure_slots) -> list[float]:
    """Settle one independent 30-actor market using production queue/cost rules."""
    if len(departure_slots) != ACTOR_COUNT:
        raise ValueError('simulated market requires exactly 30 actors')
    if not math.isfinite(float(capacity)) or float(capacity) <= 0:
        raise ValueError('capacity must be positive and finite')
    if any(slot not in SLOTS for slot in departure_slots):
        raise ValueError('departure slot must be between 1 and 16')
    by_slot = {}
    for index, slot in enumerate(departure_slots):
        by_slot.setdefault(slot, []).append(index)
    results = [0.0] * ACTOR_COUNT
    next_available = 466.0  # First departure is 7:46.
    for slot, indices in sorted(by_slot.items()):
        departure_minute = 465.0 + slot
        first_service = max(departure_minute, next_available)
        queue_delay = first_service - departure_minute + len(indices) / capacity
        arrival = departure_minute + 6.0 + queue_delay
        early = max(0.0, 480.0 - arrival)
        late = max(0.0, arrival - 480.0)
        cost = 2.0 * queue_delay + early + 3.0 * late
        for index in indices:
            results[index] = cost
        next_available = first_service + len(indices) / capacity
    return results


def _capacity(rng: random.Random) -> float:
    return truncated_normal_quantile(rng.random())


def _opponent_slot(rng: random.Random, mode: str, habit_slot: int) -> int:
    if mode == 'uniform':
        return rng.choice(SLOTS)
    if mode == 'peak':
        return min(16, max(1, round(rng.gauss(9, 1.8))))
    if mode == 'early':
        return rng.randint(2, 9)
    if mode == 'late':
        return rng.randint(9, 16)
    if mode == 'habit':
        return min(16, max(1, habit_slot + rng.choice((-2, -1, 0, 0, 0, 1, 2))))
    raise ValueError('unknown opponent mode')


def _opponents(rng: random.Random, market_index: int, habit_slots: list[int]) -> list[int]:
    choices = []
    for actor_index in range(len(habit_slots)):
        # Rotate dominant strategy across markets; retain individual heterogeneity.
        dominant = OPPONENT_MODES[market_index % len(OPPONENT_MODES)]
        mode = dominant if rng.random() < 0.55 else OPPONENT_MODES[actor_index % 5]
        slot = _opponent_slot(rng, mode, habit_slots[actor_index])
        choices.append(slot)
        if mode == 'habit':
            habit_slots[actor_index] = slot
    return choices


def _training_history(seat: int) -> tuple[list[dict], list[int]]:
    history = []
    seeds = []
    for market in range(TRAIN_MARKETS_PER_SEAT):
        seed = TRAIN_SEED_BASE + seat * 1000 + market
        seeds.append(seed)
        rng = random.Random(seed)
        exploration_order = list(SLOTS)
        rng.shuffle(exploration_order)
        habit_slots = [rng.choice(SLOTS) for _ in range(ACTOR_COUNT - 1)]
        for turn, slot in enumerate(exploration_order, start=1):
            capacity = _capacity(rng)
            opponents = _opponents(rng, market, habit_slots)
            own_cost = simulate_market(
                capacity=capacity, departure_slots=[slot, *opponents]
            )[0]
            history.append({
                'training_round_number': len(history) + 1,
                'departure_slot': slot,
                'total_cost': own_cost,
                'actual_capacity': capacity,
                'decision_source': 'independent_market_balanced_exploration',
            })
    return history, seeds


def _joint_holdout_result(profiles: dict, condition: str, market: int) -> list[dict]:
    """Run ten pretrained RL agents together with twenty mixed opponents."""
    seed = HOLDOUT_SEED_BASE + market
    rng = random.Random(seed)
    baseline_rng = random.Random(seed + 500000)
    habit_slots = [rng.choice(SLOTS) for _ in range(ACTOR_COUNT - SEAT_COUNT)]
    states = {}
    observations = {}
    for seat in range(1, SEAT_COUNT + 1):
        state = initial_liu_rel_state()
        state['pretraining_bank_id'] = BANK_ID
        state['pretraining_experiences'] = deepcopy(profiles[f'RL_{seat:02d}'])
        states[seat] = state
        observations[seat] = {
            'seed': seed, 'seat': seat, 'own_costs': [],
            'baseline_costs': [], 'chosen_slots': [], 'sources': set(),
        }
    for round_number in range(1, HOLDOUT_ROUNDS_PER_MARKET + 1):
        capacity = _capacity(rng)
        opponents = _opponents(rng, market, habit_slots)
        choices = {}
        for seat in states:
            choices[seat] = choose_liu_rel_departure(
                state=states[seat], available_slots=SLOT_OPTIONS,
                formal_round_number=round_number, information_condition=condition,
                current_actual_capacity=capacity if condition == 'I1' else None,
                rel_lambda=REL_LAMBDA, rel_eta=REL_ETA,
                session_code=f'joint_holdout_{market}', group_id=1,
                agent_id=f'RL_{seat:02d}', rel_random_seed=REL_RANDOM_SEED,
            )
        own_slots = [choices[seat]['departure_slot'] for seat in states]
        own_costs = simulate_market(
            capacity=capacity, departure_slots=[*own_slots, *opponents]
        )[:SEAT_COUNT]
        baseline_slots = [baseline_rng.choice(SLOTS) for _ in states]
        baseline_costs = simulate_market(
            capacity=capacity, departure_slots=[*baseline_slots, *opponents]
        )[:SEAT_COUNT]
        for index, seat in enumerate(states):
            choice = choices[seat]
            states[seat] = append_liu_rel_experience(
                states[seat], formal_round_number=round_number,
                departure_slot=own_slots[index], total_cost=own_costs[index],
                actual_capacity=capacity,
                decision_source=choice['decision_source'],
            )
            row = observations[seat]
            row['own_costs'].append(own_costs[index])
            row['baseline_costs'].append(baseline_costs[index])
            row['chosen_slots'].append(own_slots[index])
            row['sources'].add(choice['decision_source'])
    return [
        {
            'seed': seed,
            'seat': seat,
            'mean_cost': mean(row['own_costs']),
            'uniform_baseline_mean_cost': mean(row['baseline_costs']),
            'chosen_slots': row['chosen_slots'],
            'decision_sources': sorted(row['sources']),
            'formal_rounds_observed': states[seat]['rounds_observed'],
        }
        for seat, row in observations.items()
    ]


def count_invalid_holdout_rows(rows: list[dict]) -> int:
    return sum(
        row['formal_rounds_observed'] != HOLDOUT_ROUNDS_PER_MARKET
        or any(source.startswith('rl_fallback') for source in row['decision_sources'])
        or not math.isfinite(row['mean_cost'])
        for row in rows
    )


def _validation_report(profiles: dict) -> tuple[dict, list[int]]:
    results = {'I0': [], 'I1': []}
    seeds = []
    for market in range(HOLDOUT_MARKETS_PER_SEAT):
        seeds.append(HOLDOUT_SEED_BASE + market)
        for condition in ('I0', 'I1'):
            results[condition].extend(_joint_holdout_result(profiles, condition, market))
    summary = {}
    accepted = True
    for condition, rows in results.items():
        pooled_baseline_sd = pstdev(
            [row['uniform_baseline_mean_cost'] for row in rows]
        )
        per_seat = {}
        for seat in range(1, SEAT_COUNT + 1):
            seat_rows = [row for row in rows if row['seat'] == seat]
            seat_costs = [row['mean_cost'] for row in seat_rows]
            seat_baselines = [row['uniform_baseline_mean_cost'] for row in seat_rows]
            seat_invalid_count = count_invalid_holdout_rows(seat_rows)
            seat_mean = mean(seat_costs)
            seat_baseline = mean(seat_baselines)
            seat_sd = pstdev(seat_costs)
            seat_baseline_sd = pstdev(seat_baselines)
            seat_stable = (
                seat_invalid_count == 0
                and seat_mean <= seat_baseline * 1.20
                and seat_sd <= pooled_baseline_sd * 1.50
            )
            per_seat[f'RL_{seat:02d}'] = {
                'mean_market_cost': seat_mean,
                'sd_market_cost': seat_sd,
                'uniform_baseline_mean_cost': seat_baseline,
                'uniform_baseline_sd_market_cost': seat_baseline_sd,
                'stable': seat_stable,
                'invalid_market_count': seat_invalid_count,
            }
        market_means = [row['mean_cost'] for row in rows]
        baseline_means = [row['uniform_baseline_mean_cost'] for row in rows]
        slots = [slot for row in rows for slot in row['chosen_slots']]
        invalid_count = count_invalid_holdout_rows(rows)
        mean_cost = mean(market_means)
        baseline_mean = mean(baseline_means)
        cost_sd = pstdev(market_means)
        baseline_sd = pstdev(baseline_means)
        stable = invalid_count == 0 and all(item['stable'] for item in per_seat.values())
        accepted = accepted and stable
        summary[condition] = {
            'market_count': len(rows),
            'mean_market_cost': mean_cost,
            'sd_market_cost': cost_sd,
            'uniform_baseline_mean_cost': baseline_mean,
            'uniform_baseline_sd_market_cost': baseline_sd,
            'pooled_uniform_baseline_sd_market_cost': pooled_baseline_sd,
            'slot_counts': {str(slot): slots.count(slot) for slot in SLOTS},
            'per_seat': per_seat,
            'stable': stable,
            'invalid_market_count': invalid_count,
        }
    return {'accepted': accepted, 'holdout_design': 'joint_ten_rl', 'criteria': {
        'mean_cost_max_baseline_ratio': 1.20,
        'market_cost_sd_max_baseline_ratio': 1.50,
        'sd_reference': 'pooled_uniform_market_means',
        'invalid_market_count': 0,
    }, 'conditions': summary}, sorted(set(seeds))


@lru_cache(maxsize=1)
def _build_cached_bank() -> dict:
    profiles = {}
    training_seeds = []
    for seat in range(1, SEAT_COUNT + 1):
        history, seeds = _training_history(seat)
        profiles[f'RL_{seat:02d}'] = history
        training_seeds.extend(seeds)
    validation, holdout_seeds = _validation_report(profiles)
    return {
        'bank_id': BANK_ID,
        'policy_version': LIU_REL_POLICY_VERSION,
        'training_markets_per_seat': TRAIN_MARKETS_PER_SEAT,
        'training_rounds_per_market': TRAIN_ROUNDS_PER_MARKET,
        'holdout_markets_per_seat': HOLDOUT_MARKETS_PER_SEAT,
        'holdout_rounds_per_market': HOLDOUT_ROUNDS_PER_MARKET,
        'actor_count': ACTOR_COUNT,
        'opponent_modes': list(OPPONENT_MODES),
        'rel_lambda': REL_LAMBDA,
        'rel_eta': REL_ETA,
        'rel_random_seed': REL_RANDOM_SEED,
        'capacity_kernel_bandwidth': CAPACITY_KERNEL_BANDWIDTH,
        'training_seeds': training_seeds,
        'holdout_seeds': holdout_seeds,
        'profiles': profiles,
        'validation': validation,
    }


def build_pretraining_bank() -> dict:
    return deepcopy(_build_cached_bank())


def _payload_fingerprint(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def write_pretraining_bank(path: Path = BANK_FILE) -> dict:
    payload = build_pretraining_bank()
    if not payload['validation']['accepted']:
        raise ValueError('holdout stability validation failed; bank is not frozen')
    fingerprint = _payload_fingerprint(payload)
    if fingerprint != BANK_SHA256:
        raise ValueError('generated bank differs from frozen fingerprint')
    artifact = {**payload, 'sha256': fingerprint}
    path.write_text(json.dumps(artifact, ensure_ascii=False, sort_keys=True,
                               indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return artifact


def validate_pretraining_bank(path: Path = BANK_FILE) -> dict:
    try:
        artifact = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'pretraining bank unavailable or invalid: {exc}') from exc
    checksum = artifact.pop('sha256', None)
    if not isinstance(checksum, str) or checksum != _payload_fingerprint(artifact):
        raise ValueError('pretraining bank checksum/hash mismatch')
    if checksum != BANK_SHA256:
        raise ValueError('pretraining bank differs from frozen fingerprint')
    if artifact.get('bank_id') != BANK_ID or artifact.get('policy_version') != LIU_REL_POLICY_VERSION:
        raise ValueError('pretraining bank version mismatch')
    if artifact.get('rel_lambda') != REL_LAMBDA or artifact.get('rel_eta') != REL_ETA:
        raise ValueError('pretraining bank parameter mismatch')
    if artifact.get('rel_random_seed') != REL_RANDOM_SEED:
        raise ValueError('pretraining bank random-seed mismatch')
    if artifact.get('capacity_kernel_bandwidth') != CAPACITY_KERNEL_BANDWIDTH:
        raise ValueError('pretraining bank capacity-kernel mismatch')
    if not artifact.get('validation', {}).get('accepted'):
        raise ValueError('pretraining holdout validation was not accepted')
    if len(artifact.get('profiles', {})) != SEAT_COUNT:
        raise ValueError('pretraining bank has wrong seat count')
    for seat in range(1, SEAT_COUNT + 1):
        rows = artifact['profiles'].get(f'RL_{seat:02d}')
        if not isinstance(rows, list) or len(rows) != TRAIN_MARKETS_PER_SEAT * TRAIN_ROUNDS_PER_MARKET:
            raise ValueError('pretraining bank has wrong history length')
    artifact['sha256'] = checksum
    return artifact


@lru_cache(maxsize=1)
def _load_default_bank() -> dict:
    return validate_pretraining_bank(BANK_FILE)


def load_pretraining_profile(seat_index: int, *, path: Path = BANK_FILE) -> dict:
    if isinstance(seat_index, bool) or not 1 <= int(seat_index) <= SEAT_COUNT:
        raise ValueError('RL seat index must be 1 through 10')
    bank = _load_default_bank() if path == BANK_FILE else validate_pretraining_bank(path)
    state = initial_liu_rel_state()
    state['pretraining_bank_id'] = bank['bank_id']
    state['pretraining_experiences'] = deepcopy(bank['profiles'][f'RL_{seat_index:02d}'])
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.write:
        output = write_pretraining_bank()
    elif args.verify:
        output = validate_pretraining_bank()
    else:
        parser.error('choose --write or --verify')
    print(json.dumps({
        'bank_id': output['bank_id'],
        'sha256': output['sha256'],
        'validation': output['validation'],
    }, ensure_ascii=False, indent=2))
