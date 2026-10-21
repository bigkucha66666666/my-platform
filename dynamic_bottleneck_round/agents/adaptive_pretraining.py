"""Deterministic simulated experience for a supplied adaptive departure window.

This module has no bank-file, formal-sequence, database, or network I/O. The
choice window is supplied in departure minutes directly; free-flow travel is
added only when settling arrivals. These artifacts make no holdout-performance
claim, and do not replace or validate the historical frozen experience bank.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
import random

from ..stochastic_capacity import StochasticCapacityConfig, truncated_normal_quantile
from .liu_rel_agent import (
    CAPACITY_KERNEL_BANDWIDTH,
    LIU_REL_POLICY_VERSION,
    initial_liu_rel_state,
)


BANK_VERSION = 'adaptive_independent_market_liu_rel_v1'
TRAIN_MARKETS_PER_SEAT = 16
TRAIN_ROUNDS_PER_MARKET = 16
EXPERIENCES_PER_SEAT = TRAIN_MARKETS_PER_SEAT * TRAIN_ROUNDS_PER_MARKET
SEAT_COUNT = 10
REL_LAMBDA = 0.25
REL_ETA = 14.7445
REL_RANDOM_SEED = 2026090901
TRAIN_SEED_BASE = 202610200000
OPPONENT_MODES = ('uniform', 'peak', 'early', 'late', 'habit')


def _integer(value, name, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be an integer')
    if minimum is not None and value < minimum:
        raise ValueError(f'{name} must be at least {minimum}')
    if maximum is not None and value > maximum:
        raise ValueError(f'{name} must be at most {maximum}')
    return value


def _finite(value, name, *, positive=False):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be finite')
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f'{name} must be finite') from exc
    if not math.isfinite(result) or (result <= 0 if positive else result < 0):
        raise ValueError(f'{name} must be finite and {"positive" if positive else "nonnegative"}')
    return result


def _design(actor_count, first_departure_minute, num_slots, late_cost_per_minute):
    return (
        _integer(actor_count, 'actor_count', minimum=1),
        _integer(first_departure_minute, 'first_departure_minute'),
        _integer(num_slots, 'num_slots', minimum=1, maximum=EXPERIENCES_PER_SEAT),
        _finite(late_cost_per_minute, 'late_cost_per_minute'),
    )


def _fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def simulate_adaptive_market(*, capacity, departure_slots, actor_count,
                             first_departure_minute, num_slots,
                             late_cost_per_minute=4):
    """Settle N actors with inherited backlog plus full same-time batch time."""
    n, first, slots, late_rate = _design(
        actor_count, first_departure_minute, num_slots, late_cost_per_minute)
    capacity = _finite(capacity, 'capacity', positive=True)
    departures = list(departure_slots)
    if len(departures) != n:
        raise ValueError('departure_slots must contain exactly actor_count actions')
    by_slot = {}
    for index, value in enumerate(departures):
        slot = _integer(value, 'departure_slot', minimum=1, maximum=slots)
        by_slot.setdefault(slot, []).append(index)
    costs = [0.0] * n
    next_available = float(first)
    for slot, indices in sorted(by_slot.items()):
        departure = first + slot - 1
        first_service = max(departure, next_available)
        queue_delay = first_service - departure + len(indices) / capacity
        arrival = departure + 6.0 + queue_delay
        cost = (2.0 * queue_delay + max(0.0, 480.0 - arrival)
                + late_rate * max(0.0, arrival - 480.0))
        if not math.isfinite(cost):
            raise ValueError('simulated cost must be finite')
        for index in indices:
            costs[index] = cost
        next_available = first_service + len(indices) / capacity
    return costs


def _opponent_slot(rng, mode, habit_slot, num_slots):
    # Scale the historical 1..16 behavioral landmarks onto this legal window.
    peak = 1 + (num_slots - 1) * 8 / 15
    if mode == 'uniform':
        return rng.randint(1, num_slots)
    if mode == 'peak':
        value = round(rng.gauss(peak, max(0.25, (num_slots - 1) * 1.8 / 15)))
    elif mode == 'early':
        value = rng.randint(1, max(1, round(peak)))
    elif mode == 'late':
        value = rng.randint(max(1, round(peak)), num_slots)
    elif mode == 'habit':
        value = habit_slot + rng.choice((-2, -1, 0, 0, 0, 1, 2))
    else:
        raise ValueError('unknown opponent mode')
    return min(num_slots, max(1, value))


def _training_history(seat, *, n, first, slots, late_rate, design_seed):
    # One globally balanced deck per seat, split into 16 independent markets.
    # Truncating a sequence of complete shuffled decks gives counts differing
    # by at most one even when the window size does not divide 256.
    exploration_rng = random.Random(design_seed + seat * 1000 + 999)
    exploration = []
    while len(exploration) < EXPERIENCES_PER_SEAT:
        deck = list(range(1, slots + 1))
        exploration_rng.shuffle(deck)
        exploration.extend(deck)
    history, seeds = [], []
    for market in range(TRAIN_MARKETS_PER_SEAT):
        seed = design_seed + seat * 1000 + market
        seeds.append(seed)
        rng = random.Random(seed)
        habits = [rng.randint(1, slots) for _ in range(n - 1)]
        for turn in range(TRAIN_ROUNDS_PER_MARKET):
            focal_slot = exploration[market * TRAIN_ROUNDS_PER_MARKET + turn]
            capacity = truncated_normal_quantile(rng.random())
            opponents = []
            for index, habit in enumerate(habits):
                dominant = OPPONENT_MODES[market % len(OPPONENT_MODES)]
                mode = dominant if rng.random() < .55 else OPPONENT_MODES[index % 5]
                chosen = _opponent_slot(rng, mode, habit, slots)
                opponents.append(chosen)
                if mode == 'habit':
                    habits[index] = chosen
            cost = simulate_adaptive_market(
                capacity=capacity, departure_slots=[focal_slot, *opponents],
                actor_count=n, first_departure_minute=first,
                num_slots=slots, late_cost_per_minute=late_rate,
            )[0]
            history.append({
                'training_round_number': len(history) + 1,
                'departure_slot': focal_slot,
                'total_cost': cost,
                'actual_capacity': capacity,
                'decision_source': 'adaptive_independent_market_balanced_exploration',
            })
    return history, seeds


@lru_cache(maxsize=32)
def _build_cached_bank(actor_count, first_departure_minute, num_slots,
                       late_cost_per_minute):
    config = StochasticCapacityConfig()
    design = {
        'bank_version': BANK_VERSION,
        'actor_count': actor_count,
        'first_departure_minute': first_departure_minute,
        'num_slots': num_slots,
        'late_cost_per_minute': late_cost_per_minute,
    }
    design_hash = _fingerprint(design)
    design_seed = TRAIN_SEED_BASE + int(design_hash, 16) * 100000
    profiles, training_seeds = {}, []
    for seat in range(1, SEAT_COUNT + 1):
        rows, seeds = _training_history(
            seat, n=actor_count, first=first_departure_minute,
            slots=num_slots, late_rate=late_cost_per_minute,
            design_seed=design_seed,
        )
        profiles[f'RL_{seat:02d}'] = rows
        training_seeds.extend(seeds)
    rows = [row for history in profiles.values() for row in history]
    payload = {
        **design,
        'bank_id': f'{BANK_VERSION}_{design_hash}',
        'policy_version': LIU_REL_POLICY_VERSION,
        'last_departure_minute': first_departure_minute + num_slots - 1,
        'choice_window_basis': 'supplied_departure_minutes_no_free_flow_shift',
        'free_flow_minutes': 6,
        'preferred_arrival_minute': 480,
        'queue_cost_per_minute': 2,
        'early_cost_per_minute': 1,
        'batch_wait_rule': 'inherited_backlog_plus_full_same_time_batch_over_capacity',
        'training_markets_per_seat': TRAIN_MARKETS_PER_SEAT,
        'training_rounds_per_market': TRAIN_ROUNDS_PER_MARKET,
        'experiences_per_seat': EXPERIENCES_PER_SEAT,
        'opponents_per_market': actor_count - 1,
        'opponent_modes': list(OPPONENT_MODES),
        'exploration_design': 'shuffled_complete_slot_decks_truncated_to_256',
        'rel_lambda': REL_LAMBDA,
        'rel_eta': REL_ETA,
        'rel_random_seed': REL_RANDOM_SEED,
        'capacity_kernel_bandwidth': CAPACITY_KERNEL_BANDWIDTH,
        'capacity_distribution': {
            'distribution': config.distribution,
            'mu': config.capacity_mu, 'sigma': config.capacity_sigma,
            'minimum': config.capacity_min, 'maximum': config.capacity_max,
            'source': 'independent_inverse_cdf_draws_no_formal_sequences',
        },
        'seed_design': {
            'base': TRAIN_SEED_BASE,
            'configuration_sha256': design_hash,
            'market_seed_rule': 'base + int(configuration_sha256,16)*100000 + seat*1000 + market',
            'exploration_seed_rule': 'design_seed + seat*1000 + 999',
        },
        'training_seeds': training_seeds,
        'profiles': profiles,
        'validation': {
            'finite_costs': all(math.isfinite(row['total_cost']) for row in rows),
            'legal_actions': all(1 <= row['departure_slot'] <= num_slots for row in rows),
            'every_slot_represented_per_seat': all(
                {row['departure_slot'] for row in history} == set(range(1, num_slots + 1))
                for history in profiles.values()),
            'deterministic_reproducibility': 'fixed_configuration_and_local_random_seeds',
            'performance_validated': False,
            'legacy_holdout_transferred': False,
            'holdout_markets': 0,
            'scope': 'mechanical checks only; no stability or relative-performance promise',
        },
    }
    return {**payload, 'sha256': _fingerprint(payload)}


def build_adaptive_bank(*, actor_count, first_departure_minute, num_slots,
                        late_cost_per_minute=4):
    """Return a fresh safe copy of the cached, configuration-specific artifact."""
    design = _design(actor_count, first_departure_minute, num_slots,
                     late_cost_per_minute)
    return deepcopy(_build_cached_bank(*design))


def load_adaptive_profile(seat_index, *, actor_count, first_departure_minute,
                          num_slots, late_cost_per_minute=4):
    """Initialize one RL seat without counting simulated history as formal rounds."""
    seat = _integer(seat_index, 'seat_index', minimum=1, maximum=SEAT_COUNT)
    design = _design(actor_count, first_departure_minute, num_slots,
                     late_cost_per_minute)
    bank = _build_cached_bank(*design)
    state = initial_liu_rel_state()
    state['pretraining_bank_id'] = bank['bank_id']
    state['pretraining_experiences'] = deepcopy(bank['profiles'][f'RL_{seat:02d}'])
    return state
