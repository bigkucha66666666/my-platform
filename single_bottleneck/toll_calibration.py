"""CLI tool for calibrating one-step coarse tolls in single_bottleneck.

Run from the oTree project directory:
    python -m single_bottleneck.toll_calibration --players 5
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from math import ceil, comb, floor
from typing import Iterable

from . import (
    C,
    build_departure_schedule_record,
    departure_minute_for_slot,
    departure_slots,
    departure_slots_from_schedule,
    default_slots_each_side,
    minute_to_clock,
)


EPSILON = 1e-9
MAX_EXACT_DISTRIBUTIONS = 500_000
CALIBRATION_MODE_AUTO = 'auto'
CALIBRATION_MODE_EXACT = 'exact'
CALIBRATION_MODE_LARGE_GROUP = 'large-group'
DEFAULT_APPROX_REFINE_POOL_SIZE = 8
DEFAULT_APPROX_REFINE_ITERATIONS = 160
DEFAULT_APPROX_MAX_WINDOWS = 24


class CalibrationError(Exception):
    """Raised when exact toll calibration cannot be completed safely."""


@dataclass(frozen=True)
class CalibrationConfig:
    players: int
    capacity: int
    min_toll: float
    max_toll: float
    toll_step: float
    top_k: int


@dataclass(frozen=True)
class EquilibriumCandidate:
    window_start: int
    window_end: int
    toll: float
    cost_gap: float
    nash_count: int
    distribution: tuple[int, ...]
    selected_costs: tuple[tuple[int, float], ...]
    calibration_mode: str = CALIBRATION_MODE_EXACT
    deviation_gap: float = 0

    @property
    def window_spec(self) -> str:
        if self.window_start == self.window_end:
            return str(self.window_start)
        return f'{self.window_start}-{self.window_end}'


def format_number(value: float) -> str:
    rounded = round(float(value), 4)
    if abs(rounded - round(rounded)) < EPSILON:
        return str(int(round(rounded)))
    return f'{rounded:.4f}'.rstrip('0').rstrip('.')


def parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('必须是正整数。') from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError('必须是正整数。')
    return parsed


def parse_non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('必须是非负数字。') from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError('必须是非负数字。')
    return parsed


def parse_calibration_mode(value: str) -> str:
    normalized = (value or '').strip().lower().replace('_', '-')
    aliases = {
        'auto': CALIBRATION_MODE_AUTO,
        'exact': CALIBRATION_MODE_EXACT,
        'large': CALIBRATION_MODE_LARGE_GROUP,
        'large-group': CALIBRATION_MODE_LARGE_GROUP,
        'largegroup': CALIBRATION_MODE_LARGE_GROUP,
        'approx': CALIBRATION_MODE_LARGE_GROUP,
        'approximate': CALIBRATION_MODE_LARGE_GROUP,
    }
    if normalized not in aliases:
        raise argparse.ArgumentTypeError('校准模式必须是 auto、exact 或 large-group。')
    return aliases[normalized]


def parse_window(value: str, valid_slots: tuple[int, ...]) -> tuple[int, int]:
    raw_value = (value or '').strip()
    if not raw_value:
        raise argparse.ArgumentTypeError('收费窗口不能为空。示例：4-8')

    if '-' in raw_value:
        start_text, end_text = raw_value.split('-', 1)
    else:
        start_text = end_text = raw_value

    try:
        start = int(start_text.strip())
        end = int(end_text.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError('收费窗口必须使用时点编号。示例：4-8') from exc

    if start > end:
        raise argparse.ArgumentTypeError('收费窗口起点不能晚于终点。')
    if start not in valid_slots or end not in valid_slots:
        raise argparse.ArgumentTypeError(
            f'收费窗口必须落在 {valid_slots[0]}-{valid_slots[-1]} 范围内。'
        )
    return start, end


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='搜索或评估 single_bottleneck 的单步粗收费配置。'
    )
    parser.add_argument('--players', type=parse_positive_int, required=True, help='每组参与者人数。')
    parser.add_argument(
        '--capacity',
        type=parse_positive_int,
        default=C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT,
        help='每个出发时点窗口的瓶颈容量，默认使用当前实验常数。',
    )
    parser.add_argument('--min-toll', type=parse_non_negative_float, default=0, help='搜索收费下限。')
    parser.add_argument('--max-toll', type=parse_non_negative_float, default=40, help='搜索收费上限。')
    parser.add_argument('--toll-step', type=parse_non_negative_float, default=1, help='搜索收费步长。')
    parser.add_argument(
        '--calibration-mode',
        type=parse_calibration_mode,
        default=CALIBRATION_MODE_AUTO,
        help='校准模式：auto 自动选择，exact 精确枚举，large-group 大组近似。',
    )
    parser.add_argument(
        '--approx-refine-pool-size',
        type=parse_positive_int,
        default=DEFAULT_APPROX_REFINE_POOL_SIZE,
        help='large-group 模式下进入 best-response 微调的候选数量。',
    )
    parser.add_argument(
        '--approx-refine-iterations',
        type=parse_positive_int,
        default=DEFAULT_APPROX_REFINE_ITERATIONS,
        help='large-group 模式下每个候选的 best-response 最大微调轮数。',
    )
    parser.add_argument(
        '--min-slots-each-side',
        type=parse_positive_int,
        default=default_slots_each_side(),
        help='按人数动态生成出发窗口时，无排队基准两侧至少保留的 2 分钟时点数。',
    )
    parser.add_argument(
        '--static-schedule',
        action='store_true',
        help='使用常量中的静态出发窗口；默认按 --players 和 --capacity 动态生成。',
    )
    parser.add_argument('--window', help='只评估指定连续收费窗口，例如 4-8。必须与 --toll 同时使用。')
    parser.add_argument('--toll', type=parse_non_negative_float, help='只评估指定收费额。必须与 --window 同时使用。')
    parser.add_argument('--top-k', type=parse_positive_int, default=10, help='搜索模式下输出候选数量。')
    parser.add_argument('--json', action='store_true', help='输出机器可读 JSON。')
    return parser


def validate_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    valid_slots: tuple[int, ...],
) -> tuple[int, int] | None:
    if args.toll_step <= 0:
        parser.error('--toll-step 必须大于 0。')
    if args.min_toll > args.max_toll:
        parser.error('--min-toll 不能大于 --max-toll。')
    if (args.window is None) != (args.toll is None):
        parser.error('--window 与 --toll 必须同时提供，或同时省略。')
    if args.window is None:
        return None
    try:
        return parse_window(args.window, valid_slots)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))


def count_distributions(total: int, slots_count: int) -> int:
    return comb(total + slots_count - 1, slots_count - 1)


def exact_search_distribution_count(players: int, slots_count: int) -> int:
    return count_distributions(players, slots_count)


def exact_search_feasible(players: int, slots_count: int) -> bool:
    return exact_search_distribution_count(players, slots_count) <= MAX_EXACT_DISTRIBUTIONS


def resolve_calibration_mode(mode: str, players: int, equilibrium_slots_count: int) -> str:
    normalized = parse_calibration_mode(mode)
    if normalized == CALIBRATION_MODE_AUTO:
        return (
            CALIBRATION_MODE_EXACT
            if exact_search_feasible(players, equilibrium_slots_count)
            else CALIBRATION_MODE_LARGE_GROUP
        )
    return normalized


def ensure_exact_search_size(players: int, slots_count: int):
    distribution_count = exact_search_distribution_count(players, slots_count)
    if distribution_count > MAX_EXACT_DISTRIBUTIONS:
        raise CalibrationError(
            '精确搜索规模过大：'
            f'{players} 人、{slots_count} 个时点会产生 {distribution_count} 个分布。'
            '请减少 --players，或缩小收费搜索范围后再尝试。'
        )


def generate_count_keys(total: int, slots_count: int) -> Iterable[tuple[int, ...]]:
    if slots_count == 1:
        yield (total,)
        return
    for count in range(total + 1):
        for tail in generate_count_keys(total - count, slots_count - 1):
            yield (count,) + tail


def default_equilibrium_slots(valid_slots: tuple[int, ...]) -> tuple[int, ...]:
    """Use the 2-minute service grid as candidate support inside the 1-minute choice grid."""
    if len(valid_slots) <= C.NUM_DEPARTURE_SLOTS:
        return valid_slots

    step_ratio = max(1, int(round(C.SLOT_SIZE_MINUTES / C.DEPARTURE_CHOICE_STEP_MINUTES)))
    return tuple(slot for index, slot in enumerate(valid_slots) if index % step_ratio == 0)


def generate_supported_count_keys(
    total: int,
    valid_slots: tuple[int, ...],
    support_slots: tuple[int, ...],
) -> Iterable[tuple[int, ...]]:
    slot_to_index = {slot: index for index, slot in enumerate(valid_slots)}
    support_indexes = tuple(slot_to_index[slot] for slot in support_slots)
    for compact_counts in generate_count_keys(total, len(support_indexes)):
        full_counts = [0] * len(valid_slots)
        for support_index, count in zip(support_indexes, compact_counts):
            full_counts[support_index] = count
        yield tuple(full_counts)


def build_other_count_keys(count_keys: Iterable[tuple[int, ...]]) -> tuple[tuple[int, ...], ...]:
    other_count_keys = set()
    for counts in count_keys:
        for index, count in enumerate(counts):
            if count <= 0:
                continue
            other_counts = list(counts)
            other_counts[index] -= 1
            other_count_keys.add(tuple(other_counts))
    return tuple(sorted(other_count_keys))


def service_interval_minutes(capacity: int) -> float:
    return C.SLOT_SIZE_MINUTES / capacity


def build_departure_minute_map(
    valid_slots: tuple[int, ...],
    *,
    first_departure_minute: float | None = None,
    slot_size_minutes: float = C.SLOT_SIZE_MINUTES,
) -> dict[int, float]:
    if first_departure_minute is None:
        return {slot: departure_minute_for_slot(slot) for slot in valid_slots}
    return {
        slot: float(first_departure_minute) + (slot - 1) * float(slot_size_minutes)
        for slot in valid_slots
    }


def base_expected_cost(
    other_counts: tuple[int, ...],
    chosen_index: int,
    *,
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
) -> float:
    next_available_minute = departure_minutes[valid_slots[0]]
    interval = service_interval_minutes(capacity)
    expected_cost = None

    for index, slot in enumerate(valid_slots):
        departure_minute = departure_minutes[slot]
        total_in_slot = other_counts[index] + (1 if index == chosen_index else 0)

        if total_in_slot <= 0:
            if next_available_minute < departure_minute:
                next_available_minute = departure_minute
            continue

        first_service_start = max(departure_minute, next_available_minute)

        if index == chosen_index:
            same_slot_others = other_counts[index]
            possible_costs = []
            for position in range(same_slot_others + 1):
                service_start = first_service_start + position * interval
                queue_delay = max(0, service_start - departure_minute)
                arrival_minute = departure_minute + C.FREE_FLOW_TRAVEL_MINUTES + queue_delay
                early_minutes = max(0, C.PREFERRED_ARRIVAL_MINUTE - arrival_minute)
                late_minutes = max(0, arrival_minute - C.PREFERRED_ARRIVAL_MINUTE)
                possible_costs.append(
                    C.QUEUE_COST_PER_MINUTE * queue_delay
                    + C.EARLY_COST_PER_MINUTE * early_minutes
                    + C.LATE_COST_PER_MINUTE * late_minutes
                )
            expected_cost = sum(possible_costs) / len(possible_costs)

        next_available_minute = first_service_start + total_in_slot * interval

    if expected_cost is None:
        raise RuntimeError('未能计算选择成本，请检查出发时点配置。')
    return expected_cost


def build_base_cost_table(
    other_count_keys: Iterable[tuple[int, ...]],
    *,
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
) -> dict[tuple[int, ...], tuple[float, ...]]:
    table = {}
    for other_counts in other_count_keys:
        table[other_counts] = tuple(
            base_expected_cost(
                other_counts,
                chosen_index,
                valid_slots=valid_slots,
                capacity=capacity,
                departure_minutes=departure_minutes,
            )
            for chosen_index in range(len(valid_slots))
        )
    return table


def base_expected_costs_for_counts(
    counts: tuple[int, ...],
    *,
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
) -> tuple[float, ...]:
    next_available_minute = departure_minutes[valid_slots[0]]
    interval = service_interval_minutes(capacity)
    costs = []

    for index, slot in enumerate(valid_slots):
        departure_minute = departure_minutes[slot]
        if next_available_minute < departure_minute:
            next_available_minute = departure_minute

        first_service_start = max(departure_minute, next_available_minute)
        same_slot_others = counts[index]
        expected_service_start = first_service_start + (same_slot_others / 2) * interval
        queue_delay = max(0, expected_service_start - departure_minute)
        arrival_minute = departure_minute + C.FREE_FLOW_TRAVEL_MINUTES + queue_delay
        early_minutes = max(0, C.PREFERRED_ARRIVAL_MINUTE - arrival_minute)
        late_minutes = max(0, arrival_minute - C.PREFERRED_ARRIVAL_MINUTE)
        costs.append(
            C.QUEUE_COST_PER_MINUTE * queue_delay
            + C.EARLY_COST_PER_MINUTE * early_minutes
            + C.LATE_COST_PER_MINUTE * late_minutes
        )

        current_count = counts[index]
        if current_count > 0:
            next_available_minute = first_service_start + current_count * interval

    return tuple(costs)


def toll_vector_for_window(
    window_start: int,
    window_end: int,
    toll: float,
    valid_slots: tuple[int, ...],
) -> tuple[float, ...]:
    return tuple(toll if window_start <= slot <= window_end else 0 for slot in valid_slots)


def selected_distribution_items(
    counts: tuple[int, ...],
    valid_slots: tuple[int, ...],
) -> tuple[tuple[int, int], ...]:
    return tuple((slot, count) for slot, count in zip(valid_slots, counts) if count > 0)


def slot_time_label(slot: int, departure_minutes: dict[int, float] | None = None) -> str:
    if departure_minutes and slot in departure_minutes:
        return minute_to_clock(departure_minutes[slot])
    return minute_to_clock(departure_minute_for_slot(slot))


def evaluate_distribution(
    counts: tuple[int, ...],
    *,
    tolls: tuple[float, ...],
    base_cost_table: dict[tuple[int, ...], tuple[float, ...]],
    valid_slots: tuple[int, ...],
) -> tuple[float, tuple[tuple[int, float], ...]] | None:
    selected_costs = []

    for index, count in enumerate(counts):
        if count <= 0:
            continue

        other_counts = list(counts)
        other_counts[index] -= 1
        choice_base_costs = base_cost_table[tuple(other_counts)]
        choice_costs = tuple(base + toll for base, toll in zip(choice_base_costs, tolls))
        current_cost = choice_costs[index]
        best_cost = min(choice_costs)

        if current_cost > best_cost + EPSILON:
            return None

        selected_costs.append((valid_slots[index], current_cost))

    if not selected_costs:
        return None

    costs_only = [cost for _, cost in selected_costs]
    return max(costs_only) - min(costs_only), tuple(selected_costs)


def evaluate_toll_config(
    *,
    window_start: int,
    window_end: int,
    toll: float,
    count_keys: Iterable[tuple[int, ...]],
    base_cost_table: dict[tuple[int, ...], tuple[float, ...]],
    valid_slots: tuple[int, ...],
) -> EquilibriumCandidate | None:
    tolls = toll_vector_for_window(window_start, window_end, toll, valid_slots)
    best_candidate = None
    nash_count = 0

    for counts in count_keys:
        result = evaluate_distribution(
            counts,
            tolls=tolls,
            base_cost_table=base_cost_table,
            valid_slots=valid_slots,
        )
        if result is None:
            continue

        nash_count += 1
        cost_gap, selected_costs = result
        candidate = EquilibriumCandidate(
            window_start=window_start,
            window_end=window_end,
            toll=toll,
            cost_gap=cost_gap,
            nash_count=0,
            distribution=counts,
            selected_costs=selected_costs,
        )
        if best_candidate is None or candidate_sort_key(candidate) < candidate_sort_key(best_candidate):
            best_candidate = candidate

    if best_candidate is None:
        return None

    return EquilibriumCandidate(
        window_start=best_candidate.window_start,
        window_end=best_candidate.window_end,
        toll=best_candidate.toll,
        cost_gap=best_candidate.cost_gap,
        nash_count=nash_count,
        distribution=best_candidate.distribution,
        selected_costs=best_candidate.selected_costs,
    )


def candidate_sort_key(candidate: EquilibriumCandidate):
    occupied_slots = len([count for count in candidate.distribution if count > 0])
    return (
        round(candidate.cost_gap, 10),
        -occupied_slots,
        round(candidate.toll, 10),
        candidate.window_start,
        candidate.window_end,
        candidate.distribution,
    )


def approximate_candidate_sort_key(candidate: EquilibriumCandidate):
    occupied_slots = len([count for count in candidate.distribution if count > 0])
    return (
        round(candidate.deviation_gap, 10),
        -occupied_slots,
        round(candidate.cost_gap, 10),
        round(candidate.toll, 10),
        candidate.window_start,
        candidate.window_end,
        candidate.distribution,
    )


def occupied_slots_count(candidate: EquilibriumCandidate) -> int:
    return len([count for count in candidate.distribution if count > 0])


def minimum_large_group_spread_slots(players: int, capacity: int, valid_slots: tuple[int, ...]) -> int:
    return min(len(valid_slots), max(2, ceil(players / max(1, capacity * 4))))


def continuous_windows(window_slots: tuple[int, ...]) -> Iterable[tuple[int, int]]:
    for start in window_slots:
        for end in window_slots:
            if end >= start:
                yield start, end


def toll_values(min_toll: float, max_toll: float, step: float) -> Iterable[float]:
    total_steps = int(floor((max_toll - min_toll) / step + EPSILON))
    for index in range(total_steps + 1):
        value = min_toll + index * step
        if value <= max_toll + EPSILON:
            yield round(value, 10)


def search_configs(
    *,
    config: CalibrationConfig,
    count_keys: tuple[tuple[int, ...], ...],
    base_cost_table: dict[tuple[int, ...], tuple[float, ...]],
    valid_slots: tuple[int, ...],
    window_slots: tuple[int, ...] | None = None,
) -> list[EquilibriumCandidate]:
    candidates = []
    seen_toll_vectors = set()
    if window_slots is None:
        window_slots = default_equilibrium_slots(valid_slots)

    for window_start, window_end in continuous_windows(window_slots):
        for toll in toll_values(config.min_toll, config.max_toll, config.toll_step):
            tolls = toll_vector_for_window(window_start, window_end, toll, valid_slots)
            rounded_tolls = tuple(round(value, 10) for value in tolls)
            if rounded_tolls in seen_toll_vectors:
                continue
            seen_toll_vectors.add(rounded_tolls)

            candidate = evaluate_toll_config(
                window_start=window_start,
                window_end=window_end,
                toll=toll,
                count_keys=count_keys,
                base_cost_table=base_cost_table,
                valid_slots=valid_slots,
            )
            if candidate is not None:
                candidates.append(candidate)

    candidates.sort(key=candidate_sort_key)
    return candidates[: config.top_k]


def approximate_windows(
    *,
    players: int,
    capacity: int,
    valid_slots: tuple[int, ...],
    departure_minutes: dict[int, float],
) -> tuple[tuple[int, int], ...]:
    window_slots = default_equilibrium_slots(valid_slots)
    free_flow_minute = C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES
    center_index = min(
        range(len(window_slots)),
        key=lambda index: abs(departure_minutes[window_slots[index]] - free_flow_minute),
    )
    required_service_slots = max(1, ceil(players / capacity))
    max_offset = max(2, min(len(window_slots) - 1, ceil(required_service_slots / 2) + 4))
    raw_offsets = [0, 4, 8, 16, 24, max_offset]
    offsets = sorted({offset for offset in raw_offsets if 0 <= offset <= max_offset})

    windows = set()
    for left_offset in offsets:
        for right_offset in offsets:
            start_index = max(0, center_index - left_offset)
            end_index = min(len(window_slots) - 1, center_index + right_offset)
            if start_index <= end_index:
                windows.add((window_slots[start_index], window_slots[end_index]))

    # Include a few broad windows anchored at the feasible range boundaries.
    for offset in offsets:
        windows.add((window_slots[0], window_slots[min(len(window_slots) - 1, center_index + offset)]))
        windows.add((window_slots[max(0, center_index - offset)], window_slots[-1]))

    def window_score(window):
        start, end = window
        start_index = window_slots.index(start)
        end_index = window_slots.index(end)
        midpoint = (start_index + end_index) / 2
        width = end_index - start_index + 1
        target_width = max(1, min(len(window_slots), ceil(required_service_slots / 2)))
        return (
            abs(midpoint - center_index),
            abs(width - target_width),
            start_index,
            end_index,
        )

    return tuple(sorted(windows, key=window_score)[:DEFAULT_APPROX_MAX_WINDOWS])


def choice_costs_for_counts(
    counts: tuple[int, ...],
    tolls: tuple[float, ...],
    *,
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
) -> tuple[float, ...]:
    return tuple(
        base_cost + toll
        for base_cost, toll in zip(
            base_expected_costs_for_counts(
                counts,
                valid_slots=valid_slots,
                capacity=capacity,
                departure_minutes=departure_minutes,
            ),
            tolls,
        )
    )


def best_choice_index(choice_costs: tuple[float, ...], departure_minutes: dict[int, float], valid_slots: tuple[int, ...]):
    free_flow_minute = C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES
    return min(
        range(len(valid_slots)),
        key=lambda index: (
            round(choice_costs[index], 10),
            abs(departure_minutes[valid_slots[index]] - free_flow_minute),
            index,
        ),
    )


def greedy_distribution(
    *,
    players: int,
    tolls: tuple[float, ...],
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
) -> tuple[int, ...]:
    counts = [0] * len(valid_slots)
    for _ in range(players):
        costs = choice_costs_for_counts(
            tuple(counts),
            tolls,
            valid_slots=valid_slots,
            capacity=capacity,
            departure_minutes=departure_minutes,
        )
        counts[best_choice_index(costs, departure_minutes, valid_slots)] += 1
    return tuple(counts)


def approximate_distribution_metrics(
    counts: tuple[int, ...],
    *,
    tolls: tuple[float, ...],
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
) -> tuple[float, float, tuple[tuple[int, float], ...]]:
    selected_costs = []
    max_deviation_gap = 0.0

    for index, count in enumerate(counts):
        if count <= 0:
            continue

        other_counts = list(counts)
        other_counts[index] -= 1
        costs = choice_costs_for_counts(
            tuple(other_counts),
            tolls,
            valid_slots=valid_slots,
            capacity=capacity,
            departure_minutes=departure_minutes,
        )
        current_cost = costs[index]
        best_cost = min(costs)
        max_deviation_gap = max(max_deviation_gap, current_cost - best_cost)
        selected_costs.append((valid_slots[index], current_cost))

    if not selected_costs:
        return 0, 0, tuple()

    cost_values = [cost for _, cost in selected_costs]
    return max_deviation_gap, max(cost_values) - min(cost_values), tuple(selected_costs)


def improve_distribution_by_best_response(
    counts: tuple[int, ...],
    *,
    tolls: tuple[float, ...],
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
    max_iterations: int,
) -> tuple[int, ...]:
    counts_list = list(counts)
    for _ in range(max_iterations):
        best_move = None
        best_improvement = EPSILON

        for index, count in enumerate(counts_list):
            if count <= 0:
                continue
            other_counts = counts_list[:]
            other_counts[index] -= 1
            costs = choice_costs_for_counts(
                tuple(other_counts),
                tolls,
                valid_slots=valid_slots,
                capacity=capacity,
                departure_minutes=departure_minutes,
            )
            best_index = best_choice_index(costs, departure_minutes, valid_slots)
            improvement = costs[index] - costs[best_index]
            if improvement > best_improvement:
                best_improvement = improvement
                best_move = (index, best_index)

        if best_move is None:
            break

        from_index, to_index = best_move
        counts_list[from_index] -= 1
        counts_list[to_index] += 1

    return tuple(counts_list)


def build_approximate_candidate(
    *,
    window_start: int,
    window_end: int,
    toll: float,
    counts: tuple[int, ...],
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
) -> EquilibriumCandidate:
    tolls = toll_vector_for_window(window_start, window_end, toll, valid_slots)
    deviation_gap, cost_gap, selected_costs = approximate_distribution_metrics(
        counts,
        tolls=tolls,
        valid_slots=valid_slots,
        capacity=capacity,
        departure_minutes=departure_minutes,
    )
    return EquilibriumCandidate(
        window_start=window_start,
        window_end=window_end,
        toll=toll,
        cost_gap=cost_gap,
        nash_count=1 if deviation_gap <= EPSILON else 0,
        distribution=counts,
        selected_costs=selected_costs,
        calibration_mode=CALIBRATION_MODE_LARGE_GROUP,
        deviation_gap=deviation_gap,
    )


def search_large_group_configs(
    *,
    config: CalibrationConfig,
    valid_slots: tuple[int, ...],
    capacity: int,
    departure_minutes: dict[int, float],
    refine_pool_size: int = DEFAULT_APPROX_REFINE_POOL_SIZE,
    refine_iterations: int = DEFAULT_APPROX_REFINE_ITERATIONS,
) -> list[EquilibriumCandidate]:
    candidates = []
    windows = approximate_windows(
        players=config.players,
        capacity=capacity,
        valid_slots=valid_slots,
        departure_minutes=departure_minutes,
    )

    for window_start, window_end in windows:
        for toll in toll_values(config.min_toll, config.max_toll, config.toll_step):
            tolls = toll_vector_for_window(window_start, window_end, toll, valid_slots)
            counts = greedy_distribution(
                players=config.players,
                tolls=tolls,
                valid_slots=valid_slots,
                capacity=capacity,
                departure_minutes=departure_minutes,
            )
            candidates.append(
                build_approximate_candidate(
                    window_start=window_start,
                    window_end=window_end,
                    toll=toll,
                    counts=counts,
                    valid_slots=valid_slots,
                    capacity=capacity,
                    departure_minutes=departure_minutes,
                )
            )

    candidates.sort(key=approximate_candidate_sort_key)
    refined_candidates = []
    for candidate in candidates[: max(config.top_k, refine_pool_size)]:
        tolls = toll_vector_for_window(candidate.window_start, candidate.window_end, candidate.toll, valid_slots)
        improved_counts = improve_distribution_by_best_response(
            candidate.distribution,
            tolls=tolls,
            valid_slots=valid_slots,
            capacity=capacity,
            departure_minutes=departure_minutes,
            max_iterations=refine_iterations,
        )
        refined_candidates.append(
            build_approximate_candidate(
                window_start=candidate.window_start,
                window_end=candidate.window_end,
                toll=candidate.toll,
                counts=improved_counts,
                valid_slots=valid_slots,
                capacity=capacity,
                departure_minutes=departure_minutes,
            )
        )

    candidate_pool = refined_candidates + candidates[: max(config.top_k, refine_pool_size)]
    min_spread_slots = minimum_large_group_spread_slots(config.players, capacity, valid_slots)
    spread_candidates = [
        candidate
        for candidate in candidate_pool
        if occupied_slots_count(candidate) >= min_spread_slots
    ]
    ranked_candidates = spread_candidates or refined_candidates
    ranked_candidates.sort(key=approximate_candidate_sort_key)
    return ranked_candidates[: config.top_k]


def build_search_tables(
    *,
    players: int,
    capacity: int,
    valid_slots: tuple[int, ...] | None = None,
    equilibrium_slots: tuple[int, ...] | None = None,
    first_departure_minute: float | None = None,
    slot_size_minutes: float = C.SLOT_SIZE_MINUTES,
) -> tuple[
    tuple[int, ...],
    tuple[tuple[int, ...], ...],
    dict[tuple[int, ...], tuple[float, ...]],
]:
    if valid_slots is None:
        valid_slots = tuple(departure_slots())
    if equilibrium_slots is None:
        equilibrium_slots = default_equilibrium_slots(valid_slots)

    departure_minutes = build_departure_minute_map(
        valid_slots,
        first_departure_minute=first_departure_minute,
        slot_size_minutes=slot_size_minutes,
    )
    ensure_exact_search_size(players, len(equilibrium_slots))
    count_keys = tuple(generate_supported_count_keys(players, valid_slots, equilibrium_slots))
    other_count_keys = build_other_count_keys(count_keys)
    base_cost_table = build_base_cost_table(
        other_count_keys,
        valid_slots=valid_slots,
        capacity=capacity,
        departure_minutes=departure_minutes,
    )
    return valid_slots, count_keys, base_cost_table


def calibrate_candidates(
    *,
    players: int,
    capacity: int = C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT,
    min_toll: float = 0,
    max_toll: float = 40,
    toll_step: float = 1,
    top_k: int = 10,
    valid_slots: tuple[int, ...] | None = None,
    equilibrium_slots: tuple[int, ...] | None = None,
    window_slots: tuple[int, ...] | None = None,
    first_departure_minute: float | None = None,
    slot_size_minutes: float = C.SLOT_SIZE_MINUTES,
) -> list[EquilibriumCandidate]:
    if players <= 0:
        raise CalibrationError('players 必须大于 0。')
    if capacity <= 0:
        raise CalibrationError('capacity 必须大于 0。')
    if toll_step <= 0:
        raise CalibrationError('toll_step 必须大于 0。')
    if min_toll > max_toll:
        raise CalibrationError('min_toll 不能大于 max_toll。')

    valid_slots, count_keys, base_cost_table = build_search_tables(
        players=players,
        capacity=capacity,
        valid_slots=valid_slots,
        equilibrium_slots=equilibrium_slots,
        first_departure_minute=first_departure_minute,
        slot_size_minutes=slot_size_minutes,
    )
    config = CalibrationConfig(
        players=players,
        capacity=capacity,
        min_toll=min_toll,
        max_toll=max_toll,
        toll_step=toll_step,
        top_k=top_k,
    )
    return search_configs(
        config=config,
        count_keys=count_keys,
        base_cost_table=base_cost_table,
        valid_slots=valid_slots,
        window_slots=window_slots,
    )


def calibrate_large_group_candidates(
    *,
    players: int,
    capacity: int = C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT,
    min_toll: float = 0,
    max_toll: float = 40,
    toll_step: float = 1,
    top_k: int = 10,
    valid_slots: tuple[int, ...] | None = None,
    first_departure_minute: float | None = None,
    slot_size_minutes: float = C.SLOT_SIZE_MINUTES,
    approx_refine_pool_size: int = DEFAULT_APPROX_REFINE_POOL_SIZE,
    approx_refine_iterations: int = DEFAULT_APPROX_REFINE_ITERATIONS,
) -> list[EquilibriumCandidate]:
    if players <= 0:
        raise CalibrationError('players 必须大于 0。')
    if capacity <= 0:
        raise CalibrationError('capacity 必须大于 0。')
    if toll_step <= 0:
        raise CalibrationError('toll_step 必须大于 0。')
    if min_toll > max_toll:
        raise CalibrationError('min_toll 不能大于 max_toll。')

    if valid_slots is None:
        valid_slots = tuple(departure_slots())

    departure_minutes = build_departure_minute_map(
        valid_slots,
        first_departure_minute=first_departure_minute,
        slot_size_minutes=slot_size_minutes,
    )
    config = CalibrationConfig(
        players=players,
        capacity=capacity,
        min_toll=min_toll,
        max_toll=max_toll,
        toll_step=toll_step,
        top_k=top_k,
    )
    return search_large_group_configs(
        config=config,
        valid_slots=valid_slots,
        capacity=capacity,
        departure_minutes=departure_minutes,
        refine_pool_size=approx_refine_pool_size,
        refine_iterations=approx_refine_iterations,
    )


def calibrate_candidates_by_mode(
    *,
    players: int,
    capacity: int = C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT,
    min_toll: float = 0,
    max_toll: float = 40,
    toll_step: float = 1,
    top_k: int = 10,
    valid_slots: tuple[int, ...] | None = None,
    equilibrium_slots: tuple[int, ...] | None = None,
    window_slots: tuple[int, ...] | None = None,
    first_departure_minute: float | None = None,
    slot_size_minutes: float = C.SLOT_SIZE_MINUTES,
    calibration_mode: str = CALIBRATION_MODE_AUTO,
    approx_refine_pool_size: int = DEFAULT_APPROX_REFINE_POOL_SIZE,
    approx_refine_iterations: int = DEFAULT_APPROX_REFINE_ITERATIONS,
) -> list[EquilibriumCandidate]:
    if valid_slots is None:
        valid_slots = tuple(departure_slots())
    if equilibrium_slots is None:
        equilibrium_slots = default_equilibrium_slots(valid_slots)

    resolved_mode = resolve_calibration_mode(calibration_mode, players, len(equilibrium_slots))
    if resolved_mode == CALIBRATION_MODE_LARGE_GROUP:
        return calibrate_large_group_candidates(
            players=players,
            capacity=capacity,
            min_toll=min_toll,
            max_toll=max_toll,
            toll_step=toll_step,
            top_k=top_k,
            valid_slots=valid_slots,
            first_departure_minute=first_departure_minute,
            slot_size_minutes=slot_size_minutes,
            approx_refine_pool_size=approx_refine_pool_size,
            approx_refine_iterations=approx_refine_iterations,
        )

    return calibrate_candidates(
        players=players,
        capacity=capacity,
        min_toll=min_toll,
        max_toll=max_toll,
        toll_step=toll_step,
        top_k=top_k,
        valid_slots=valid_slots,
        equilibrium_slots=equilibrium_slots,
        window_slots=window_slots,
        first_departure_minute=first_departure_minute,
        slot_size_minutes=slot_size_minutes,
    )


def calibrate_best_candidate(
    *,
    players: int,
    capacity: int = C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT,
    min_toll: float = 0,
    max_toll: float = 40,
    toll_step: float = 1,
    valid_slots: tuple[int, ...] | None = None,
    equilibrium_slots: tuple[int, ...] | None = None,
    window_slots: tuple[int, ...] | None = None,
    first_departure_minute: float | None = None,
    slot_size_minutes: float = C.SLOT_SIZE_MINUTES,
    calibration_mode: str = CALIBRATION_MODE_AUTO,
    approx_refine_pool_size: int = DEFAULT_APPROX_REFINE_POOL_SIZE,
    approx_refine_iterations: int = DEFAULT_APPROX_REFINE_ITERATIONS,
) -> EquilibriumCandidate:
    candidates = calibrate_candidates_by_mode(
        players=players,
        capacity=capacity,
        min_toll=min_toll,
        max_toll=max_toll,
        toll_step=toll_step,
        top_k=1,
        valid_slots=valid_slots,
        equilibrium_slots=equilibrium_slots,
        window_slots=window_slots,
        first_departure_minute=first_departure_minute,
        slot_size_minutes=slot_size_minutes,
        calibration_mode=calibration_mode,
        approx_refine_pool_size=approx_refine_pool_size,
        approx_refine_iterations=approx_refine_iterations,
    )
    if not candidates:
        raise CalibrationError(
            f'未找到可用粗收费配置：players={players}, capacity={capacity}, '
            f'min_toll={min_toll}, max_toll={max_toll}, toll_step={toll_step}'
        )
    return candidates[0]


def candidate_to_record(
    candidate: EquilibriumCandidate,
    *,
    valid_slots: tuple[int, ...],
    departure_minutes: dict[int, float] | None = None,
) -> dict:
    start_time = slot_time_label(candidate.window_start, departure_minutes)
    end_time = slot_time_label(candidate.window_end, departure_minutes)
    time_window_spec = start_time if candidate.window_start == candidate.window_end else f'{start_time}-{end_time}'
    return {
        'calibration_mode': candidate.calibration_mode,
        'coarse_toll_slot_spec': candidate.window_spec,
        'coarse_toll_time_window_spec': time_window_spec,
        'coarse_toll_points': candidate.toll,
        'cost_gap': round(candidate.cost_gap, 6),
        'deviation_gap': round(candidate.deviation_gap, 6),
        'nash_count': candidate.nash_count,
        'distribution': [
            {
                'slot': slot,
                'departure_time': slot_time_label(slot, departure_minutes),
                'count': count,
            }
            for slot, count in selected_distribution_items(candidate.distribution, valid_slots)
        ],
        'selected_costs': [
            {
                'slot': slot,
                'departure_time': slot_time_label(slot, departure_minutes),
                'expected_cost': round(cost, 6),
            }
            for slot, cost in candidate.selected_costs
        ],
        'settings_snippet': {
            'coarse_toll_enabled': 1,
            'coarse_toll_time_window_spec': time_window_spec,
            'coarse_toll_points': candidate.toll,
        },
    }


def print_candidate(
    candidate: EquilibriumCandidate,
    index: int,
    valid_slots: tuple[int, ...],
    departure_minutes: dict[int, float] | None = None,
):
    start_time = slot_time_label(candidate.window_start, departure_minutes)
    end_time = slot_time_label(candidate.window_end, departure_minutes)
    time_window_spec = start_time if candidate.window_start == candidate.window_end else f'{start_time}-{end_time}'
    distribution_text = ', '.join(
        f'slot {slot}({slot_time_label(slot, departure_minutes)}): {count} 人'
        for slot, count in selected_distribution_items(candidate.distribution, valid_slots)
    )
    cost_text = ', '.join(
        f'slot {slot}: {format_number(cost)}'
        for slot, cost in candidate.selected_costs
    )

    print(f'候选 {index}')
    print(f'  calibration_mode = {candidate.calibration_mode!r}')
    print(f'  coarse_toll_slot_spec = {candidate.window_spec!r}')
    print(f'  coarse_toll_time_window_spec = {time_window_spec!r}')
    print(f'  coarse_toll_points = {format_number(candidate.toll)}')
    print(f'  均衡数量 = {candidate.nash_count}')
    print(f'  最佳均衡分布 = {distribution_text}')
    print(f'  被选择时点预期成本 = {cost_text}')
    print(f'  成本差距 = {format_number(candidate.cost_gap)}')
    if candidate.calibration_mode == CALIBRATION_MODE_LARGE_GROUP:
        print(f'  最大可改进成本差 = {format_number(candidate.deviation_gap)}')
    print('  settings.py 片段：')
    print('      coarse_toll_enabled=1,')
    print(f'      coarse_toll_time_window_spec={time_window_spec!r},')
    print(f'      coarse_toll_points={format_number(candidate.toll)},')


def print_text_output(
    candidates: list[EquilibriumCandidate],
    *,
    config: CalibrationConfig,
    valid_slots: tuple[int, ...],
    equilibrium_slots: tuple[int, ...],
    window_slots: tuple[int, ...],
    departure_minutes: dict[int, float] | None = None,
    mode: str,
):
    equilibrium_label = ','.join(str(slot) for slot in equilibrium_slots)
    window_label = ','.join(str(slot) for slot in window_slots)
    print('单瓶颈粗收费校准')
    print(
        '参数：'
        f'players={config.players}, capacity={config.capacity}, '
        f'choice_slots={valid_slots[0]}-{valid_slots[-1]}({len(valid_slots)}个), '
        f'equilibrium_support={equilibrium_label}({len(equilibrium_slots)}个), '
        f'window_boundaries={window_label}({len(window_slots)}个), mode={mode}'
    )
    print()

    if not candidates:
        print('没有找到聚合纯策略 Nash 均衡。请调整收费窗口、收费额或搜索范围。')
        return

    for index, candidate in enumerate(candidates, start=1):
        print_candidate(candidate, index, valid_slots, departure_minutes)
        if index != len(candidates):
            print()


def print_json_output(
    candidates: list[EquilibriumCandidate],
    *,
    config: CalibrationConfig,
    valid_slots: tuple[int, ...],
    equilibrium_slots: tuple[int, ...],
    window_slots: tuple[int, ...],
    departure_minutes: dict[int, float] | None = None,
    mode: str,
):
    payload = {
        'mode': mode,
        'parameters': {
            'players': config.players,
            'capacity': config.capacity,
            'min_toll': config.min_toll,
            'max_toll': config.max_toll,
            'toll_step': config.toll_step,
            'top_k': config.top_k,
            'slots': list(valid_slots),
            'equilibrium_slots': list(equilibrium_slots),
            'window_slots': list(window_slots),
        },
        'results': [
            candidate_to_record(candidate, valid_slots=valid_slots, departure_minutes=departure_minutes)
            for candidate in candidates
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.static_schedule:
        valid_slots = tuple(departure_slots())
        first_departure_minute = None
        slot_size_minutes = C.DEPARTURE_CHOICE_STEP_MINUTES
    else:
        schedule = build_departure_schedule_record(
            players_count=args.players,
            capacity=args.capacity,
            min_slots_each_side=args.min_slots_each_side,
            auto_enabled=True,
        )
        valid_slots = tuple(departure_slots_from_schedule(schedule))
        first_departure_minute = schedule.get('first_departure_minute')
        slot_size_minutes = schedule.get('slot_size_minutes', C.DEPARTURE_CHOICE_STEP_MINUTES)

    equilibrium_slots = default_equilibrium_slots(valid_slots)
    window_slots = equilibrium_slots
    output_departure_minutes = build_departure_minute_map(
        valid_slots,
        first_departure_minute=first_departure_minute,
        slot_size_minutes=slot_size_minutes,
    )
    window = validate_args(parser, args, valid_slots)
    resolved_mode = resolve_calibration_mode(args.calibration_mode, args.players, len(equilibrium_slots))
    if resolved_mode == CALIBRATION_MODE_EXACT:
        try:
            ensure_exact_search_size(args.players, len(equilibrium_slots))
        except CalibrationError as exc:
            raise SystemExit(str(exc)) from exc

    config = CalibrationConfig(
        players=args.players,
        capacity=args.capacity,
        min_toll=args.min_toll,
        max_toll=args.max_toll,
        toll_step=args.toll_step,
        top_k=args.top_k,
    )

    if window is not None:
        mode = 'evaluate'
        if resolved_mode == CALIBRATION_MODE_LARGE_GROUP:
            departure_minutes = build_departure_minute_map(
                valid_slots,
                first_departure_minute=first_departure_minute,
                slot_size_minutes=slot_size_minutes,
            )
            tolls = toll_vector_for_window(window[0], window[1], args.toll, valid_slots)
            counts = greedy_distribution(
                players=args.players,
                tolls=tolls,
                valid_slots=valid_slots,
                capacity=args.capacity,
                departure_minutes=departure_minutes,
            )
            improved_counts = improve_distribution_by_best_response(
                counts,
                tolls=tolls,
                valid_slots=valid_slots,
                capacity=args.capacity,
                departure_minutes=departure_minutes,
                max_iterations=args.approx_refine_iterations,
            )
            candidate = build_approximate_candidate(
                window_start=window[0],
                window_end=window[1],
                toll=args.toll,
                counts=improved_counts,
                valid_slots=valid_slots,
                capacity=args.capacity,
                departure_minutes=departure_minutes,
            )
        else:
            valid_slots, count_keys, base_cost_table = build_search_tables(
                players=args.players,
                capacity=args.capacity,
                valid_slots=valid_slots,
                equilibrium_slots=equilibrium_slots,
                first_departure_minute=first_departure_minute,
                slot_size_minutes=slot_size_minutes,
            )
            candidate = evaluate_toll_config(
                window_start=window[0],
                window_end=window[1],
                toll=args.toll,
                count_keys=count_keys,
                base_cost_table=base_cost_table,
                valid_slots=valid_slots,
            )
        candidates = [candidate] if candidate is not None else []
    else:
        mode = f'search/{resolved_mode}'
        candidates = calibrate_candidates_by_mode(
            players=args.players,
            capacity=args.capacity,
            min_toll=args.min_toll,
            max_toll=args.max_toll,
            toll_step=args.toll_step,
            top_k=args.top_k,
            valid_slots=valid_slots,
            equilibrium_slots=equilibrium_slots,
            window_slots=window_slots,
            first_departure_minute=first_departure_minute,
            slot_size_minutes=slot_size_minutes,
            calibration_mode=resolved_mode,
            approx_refine_pool_size=args.approx_refine_pool_size,
            approx_refine_iterations=args.approx_refine_iterations,
        )

    if args.json:
        print_json_output(
            candidates,
            config=config,
            valid_slots=valid_slots,
            equilibrium_slots=equilibrium_slots,
            window_slots=window_slots,
            departure_minutes=output_departure_minutes,
            mode=mode,
        )
    else:
        print_text_output(
            candidates,
            config=config,
            valid_slots=valid_slots,
            equilibrium_slots=equilibrium_slots,
            window_slots=window_slots,
            departure_minutes=output_departure_minutes,
            mode=mode,
        )

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
