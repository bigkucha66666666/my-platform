"""Tabular Q-learning for the current single_bottleneck scenario.

This module is intentionally independent from oTree so it can be trained and
tested without importing ``single_bottleneck.__init__``. Defaults mirror the
current experiment constants: 08:00 preferred arrival, 6-minute free-flow
travel time, 1-minute choice grid, 2-minute bottleneck service window, and
payoff = 140 - queue/early/late costs - toll + reward.

Example:
    python my_platform/single_bottleneck/agents/rl_single_bottleneck.py \
        --episodes 1000 \
        --rounds-per-episode 10 \
        --output my_platform/single_bottleneck/agents/policies/q_policy_v1.json
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from math import ceil
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple


@dataclass(frozen=True)
class SingleBottleneckParams:
    preferred_arrival_minute: float = 8 * 60
    free_flow_travel_minutes: float = 6
    first_departure_minute: float = 8 * 60 - 6 - 10
    choice_step_minutes: float = 1
    num_slots: int = 21
    service_window_minutes: float = 2
    capacity_per_window: int = 4
    base_points: float = 140
    queue_cost_per_minute: float = 2
    early_cost_per_minute: float = 1
    late_cost_per_minute: float = 3
    background_count: int = 4
    toll_by_slot: Mapping[int, float] = field(default_factory=dict)
    reward_by_slot: Mapping[int, float] = field(default_factory=dict)

    def departure_slots(self) -> List[int]:
        return list(range(1, self.num_slots + 1))

    def departure_minute_for_slot(self, slot: int) -> float:
        if slot < 1 or slot > self.num_slots:
            raise ValueError(f"slot must be in 1..{self.num_slots}, got {slot}")
        return self.first_departure_minute + (slot - 1) * self.choice_step_minutes

    def slot_for_departure_minute(self, minute: float) -> int:
        offset = round((minute - self.first_departure_minute) / self.choice_step_minutes)
        slot = int(offset) + 1
        if slot < 1 or slot > self.num_slots:
            raise ValueError(f"minute {minute} is outside the departure grid")
        snapped = self.departure_minute_for_slot(slot)
        if abs(snapped - minute) > 1e-6:
            raise ValueError(f"minute {minute} is not on the departure grid")
        return slot

    def free_flow_slot(self) -> int:
        minute = self.preferred_arrival_minute - self.free_flow_travel_minutes
        return self.slot_for_departure_minute(minute)

    def service_batches_needed(self, load: int) -> int:
        if load <= 0:
            return 0
        return ceil(load / max(1, self.capacity_per_window))

    def to_policy_dict(self) -> Dict[str, object]:
        data = asdict(self)
        data["toll_by_slot"] = {str(k): v for k, v in self.toll_by_slot.items()}
        data["reward_by_slot"] = {str(k): v for k, v in self.reward_by_slot.items()}
        return data


@dataclass(frozen=True)
class BottleneckOutcome:
    actor_id: str
    departure_slot: int
    departure_minute: float
    slot_load: int
    queue_delay_minutes: float
    arrival_minute: float
    schedule_early_minutes: float
    schedule_late_minutes: float
    generalized_cost: float
    toll_charge: float
    reward_bonus: float
    payoff: float


@dataclass(frozen=True)
class QLearningConfig:
    episodes: int = 1000
    rounds_per_episode: int = 10
    learning_rate: float = 0.18
    discount_factor: float = 0.92
    epsilon: float = 0.25
    epsilon_decay: float = 0.997
    min_epsilon: float = 0.04
    seed: int = 20260519


def _actor_value(actor: Mapping[str, object], key: str):
    if key not in actor:
        raise ValueError(f"actor is missing required key: {key}")
    return actor[key]


def settle_bottleneck_round(
    actors: Sequence[Mapping[str, object]],
    params: SingleBottleneckParams,
) -> List[BottleneckOutcome]:
    """Settle one round using the point-bottleneck batch-window queue rule."""
    actors_by_minute: Dict[float, List[Mapping[str, object]]] = {}
    for actor in actors:
        departure_minute = float(_actor_value(actor, "departure_minute"))
        params.slot_for_departure_minute(departure_minute)
        actors_by_minute.setdefault(departure_minute, []).append(actor)

    outcomes_by_id: Dict[str, BottleneckOutcome] = {}
    next_available_minute = params.first_departure_minute

    for departure_minute in sorted(actors_by_minute):
        same_time_actors = actors_by_minute[departure_minute]
        slot_load = len(same_time_actors)
        first_service_start = max(departure_minute, next_available_minute)
        service_batches = params.service_batches_needed(slot_load)
        queue_delay = max(
            0.0,
            first_service_start + (service_batches - 1) * params.service_window_minutes - departure_minute,
        )
        arrival_minute = departure_minute + params.free_flow_travel_minutes + queue_delay
        early_minutes = max(0.0, params.preferred_arrival_minute - arrival_minute)
        late_minutes = max(0.0, arrival_minute - params.preferred_arrival_minute)
        generalized_cost = (
            params.queue_cost_per_minute * queue_delay
            + params.early_cost_per_minute * early_minutes
            + params.late_cost_per_minute * late_minutes
        )

        for actor in same_time_actors:
            actor_id = str(_actor_value(actor, "actor_id"))
            departure_slot = int(_actor_value(actor, "departure_slot"))
            toll_charge = float(params.toll_by_slot.get(departure_slot, 0))
            reward_bonus = float(params.reward_by_slot.get(departure_slot, 0))
            payoff = max(0.0, round(params.base_points - generalized_cost - toll_charge + reward_bonus, 2))
            outcomes_by_id[actor_id] = BottleneckOutcome(
                actor_id=actor_id,
                departure_slot=departure_slot,
                departure_minute=round(departure_minute, 2),
                slot_load=slot_load,
                queue_delay_minutes=round(queue_delay, 2),
                arrival_minute=round(arrival_minute, 2),
                schedule_early_minutes=round(early_minutes, 2),
                schedule_late_minutes=round(late_minutes, 2),
                generalized_cost=round(generalized_cost, 2),
                toll_charge=round(toll_charge, 2),
                reward_bonus=round(reward_bonus, 2),
                payoff=payoff,
            )

        next_available_minute = first_service_start + service_batches * params.service_window_minutes

    return [outcomes_by_id[str(_actor_value(actor, "actor_id"))] for actor in actors]


class SingleBottleneckEnv:
    """A small training environment for one RL agent in a bottleneck group."""

    rl_actor_id = "rl_agent"

    def __init__(self, params: SingleBottleneckParams | None = None, seed: int | None = None):
        self.params = params or SingleBottleneckParams()
        self.rng = random.Random(seed)
        self.round_number = 1
        self.last_own_slot = self.params.free_flow_slot()
        self.last_payoff = self.params.base_points
        self.last_slot_counts = tuple(0 for _ in self.params.departure_slots())

    def reset(self) -> str:
        self.round_number = 1
        self.last_own_slot = self.params.free_flow_slot()
        self.last_payoff = self.params.base_points
        self.last_slot_counts = tuple(0 for _ in self.params.departure_slots())
        return self.state_key()

    def step(self, action_slot: int) -> Tuple[str, float, bool, Dict[str, object]]:
        if action_slot not in self.params.departure_slots():
            raise ValueError(f"action_slot must be valid, got {action_slot}")

        actors = [self._actor(self.rl_actor_id, action_slot)]
        for index, slot in enumerate(self._background_slots(), start=1):
            actors.append(self._actor(f"background_{index}", slot))

        outcomes = settle_bottleneck_round(actors, self.params)
        rl_outcome = next(item for item in outcomes if item.actor_id == self.rl_actor_id)
        self.last_own_slot = action_slot
        self.last_payoff = rl_outcome.payoff
        self.last_slot_counts = self._slot_counts(outcomes)
        self.round_number += 1
        next_state = self.state_key()
        reward = rl_outcome.payoff
        done = False
        return next_state, reward, done, {"outcomes": outcomes, "rl_outcome": rl_outcome}

    def state_key(self) -> str:
        peak_slot = self._peak_slot_from_counts(self.last_slot_counts)
        payoff_bin = int(self.last_payoff // 10) * 10
        round_bin = min(9, self.round_number)
        return f"round={round_bin}|own={self.last_own_slot}|peak={peak_slot}|payoff={payoff_bin}"

    def _actor(self, actor_id: str, slot: int) -> Dict[str, object]:
        return {
            "actor_id": actor_id,
            "departure_slot": slot,
            "departure_minute": self.params.departure_minute_for_slot(slot),
        }

    def _background_slots(self) -> List[int]:
        free_flow_slot = self.params.free_flow_slot()
        peak_slot = self._peak_slot_from_counts(self.last_slot_counts)
        if peak_slot == 0:
            peak_slot = free_flow_slot

        slots = []
        for _ in range(self.params.background_count):
            if self.rng.random() < 0.65:
                center = free_flow_slot
            else:
                center = peak_slot
            slot = center + self.rng.choice([-2, -1, 0, 0, 1, 2])
            slots.append(_clamp(slot, 1, self.params.num_slots))
        return slots

    def _slot_counts(self, outcomes: Iterable[BottleneckOutcome]) -> Tuple[int, ...]:
        counts = [0 for _ in self.params.departure_slots()]
        for outcome in outcomes:
            counts[outcome.departure_slot - 1] += 1
        return tuple(counts)

    @staticmethod
    def _peak_slot_from_counts(counts: Sequence[int]) -> int:
        if not counts or max(counts) <= 0:
            return 0
        return counts.index(max(counts)) + 1


def train_q_learning(env: SingleBottleneckEnv, config: QLearningConfig) -> Dict[str, object]:
    rng = random.Random(config.seed)
    q_table: Dict[str, Dict[str, float]] = {}
    epsilon = config.epsilon
    actions = env.params.departure_slots()

    for _episode in range(config.episodes):
        state = env.reset()
        for _round in range(config.rounds_per_episode):
            action = _epsilon_greedy_action(q_table, state, actions, rng, epsilon)
            next_state, reward, _done, _info = env.step(action)
            _update_q_value(
                q_table=q_table,
                state=state,
                action=action,
                reward=reward,
                next_state=next_state,
                actions=actions,
                learning_rate=config.learning_rate,
                discount_factor=config.discount_factor,
            )
            state = next_state
        epsilon = max(config.min_epsilon, epsilon * config.epsilon_decay)

    return {
        "policy_version": "q_learning_v1",
        "params": env.params.to_policy_dict(),
        "config": asdict(config),
        "q_table": q_table,
        "best_action_by_state": _best_actions(q_table, actions),
    }


def choose_action_from_policy(
    policy: Mapping[str, object],
    state: str,
    params: SingleBottleneckParams,
) -> int:
    best_actions = policy.get("best_action_by_state", {})
    if isinstance(best_actions, Mapping) and state in best_actions:
        action = int(best_actions[state])
        if action in params.departure_slots():
            return action

    q_table = policy.get("q_table", {})
    if isinstance(q_table, Mapping) and state in q_table and isinstance(q_table[state], Mapping):
        action = _best_action_from_values(q_table[state], params.departure_slots())
        if action in params.departure_slots():
            return action

    return lowest_schedule_cost_slot(params)


def lowest_schedule_cost_slot(params: SingleBottleneckParams) -> int:
    best_slot = params.free_flow_slot()
    best_score = float("inf")
    free_flow_minute = params.preferred_arrival_minute - params.free_flow_travel_minutes
    for slot in params.departure_slots():
        departure_minute = params.departure_minute_for_slot(slot)
        arrival_minute = departure_minute + params.free_flow_travel_minutes
        early = max(0.0, params.preferred_arrival_minute - arrival_minute)
        late = max(0.0, arrival_minute - params.preferred_arrival_minute)
        score = (
            params.early_cost_per_minute * early
            + params.late_cost_per_minute * late
            + float(params.toll_by_slot.get(slot, 0))
            - float(params.reward_by_slot.get(slot, 0))
        )
        tie_breaker = abs(departure_minute - free_flow_minute)
        current = (score, tie_breaker)
        best = (best_score, abs(params.departure_minute_for_slot(best_slot) - free_flow_minute))
        if current < best:
            best_score = score
            best_slot = slot
    return best_slot


def save_policy(policy: Mapping[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_policy(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _epsilon_greedy_action(
    q_table: Mapping[str, Mapping[str, float]],
    state: str,
    actions: Sequence[int],
    rng: random.Random,
    epsilon: float,
) -> int:
    if rng.random() < epsilon or state not in q_table:
        return rng.choice(list(actions))
    return _best_action_from_values(q_table[state], actions)


def _update_q_value(
    *,
    q_table: MutableMapping[str, Dict[str, float]],
    state: str,
    action: int,
    reward: float,
    next_state: str,
    actions: Sequence[int],
    learning_rate: float,
    discount_factor: float,
) -> None:
    action_key = str(action)
    state_values = q_table.setdefault(state, {})
    old_value = state_values.get(action_key, 0.0)
    next_values = q_table.get(next_state, {})
    next_best = max((float(next_values.get(str(next_action), 0.0)) for next_action in actions), default=0.0)
    state_values[action_key] = round(
        old_value + learning_rate * (reward + discount_factor * next_best - old_value),
        6,
    )


def _best_actions(q_table: Mapping[str, Mapping[str, float]], actions: Sequence[int]) -> Dict[str, int]:
    return {state: _best_action_from_values(values, actions) for state, values in q_table.items()}


def _best_action_from_values(values: Mapping[str, float], actions: Sequence[int]) -> int:
    best_action = actions[0]
    best_value = float("-inf")
    for action in actions:
        value = float(values.get(str(action), 0.0))
        if value > best_value:
            best_action = action
            best_value = value
    return best_action


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _parse_slot_values(raw: str) -> Dict[int, float]:
    values: Dict[int, float] = {}
    if not raw:
        return values
    for item in raw.split(","):
        slot_text, value_text = item.split(":", 1)
        values[int(slot_text.strip())] = float(value_text.strip())
    return values


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train a tabular Q-learning policy for single_bottleneck.")
    parser.add_argument("--episodes", type=int, default=1000)
    parser.add_argument("--rounds-per-episode", type=int, default=10)
    parser.add_argument("--background-count", type=int, default=4)
    parser.add_argument("--num-slots", type=int, default=21)
    parser.add_argument("--capacity-per-window", type=int, default=1)
    parser.add_argument("--tolls", default="", help="Comma-separated slot:value entries, e.g. 8:8,9:8,10:8")
    parser.add_argument("--rewards", default="", help="Comma-separated slot:value entries, e.g. 4:8,18:8")
    parser.add_argument("--seed", type=int, default=20260519)
    parser.add_argument("--output", type=Path, default=Path("single_bottleneck/agents/policies/q_policy_v1.json"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    params = SingleBottleneckParams(
        num_slots=args.num_slots,
        background_count=args.background_count,
        capacity_per_window=args.capacity_per_window,
        toll_by_slot=_parse_slot_values(args.tolls),
        reward_by_slot=_parse_slot_values(args.rewards),
    )
    env = SingleBottleneckEnv(params=params, seed=args.seed)
    config = QLearningConfig(
        episodes=args.episodes,
        rounds_per_episode=args.rounds_per_episode,
        seed=args.seed,
    )
    policy = train_q_learning(env, config)
    save_policy(policy, args.output)
    print(f"saved policy to {args.output}")
    print(f"states learned: {len(policy['q_table'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
