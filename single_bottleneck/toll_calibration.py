"""CLI tool for calibrating one-step coarse tolls in single_bottleneck.

Run from the oTree project directory:
    python -m single_bottleneck.toll_calibration --players 5
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from math import comb, floor
from typing import Iterable

from . import C, departure_minute_for_slot, departure_slots, minute_to_clock


EPSILON = 1e-9
MAX_EXACT_DISTRIBUTIONS = 500_000


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


def ensure_exact_search_size(players: int, slots_count: int):
    distribution_count = count_distributions(players, slots_count)
    if distribution_count > MAX_EXACT_DISTRIBUTIONS:
        raise SystemExit(
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


def service_interval_minutes(capacity: int) -> float:
    return C.SLOT_SIZE_MINUTES / capacity


def base_expected_cost(
    other_counts: tuple[int, ...],
    chosen_index: int,
    *,
    valid_slots: tuple[int, ...],
    capacity: int,
) -> float:
    next_available_minute = departure_minute_for_slot(valid_slots[0])
    interval = service_interval_minutes(capacity)
    expected_cost = None

    for index, slot in enumerate(valid_slots):
        departure_minute = departure_minute_for_slot(slot)
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
) -> dict[tuple[int, ...], tuple[float, ...]]:
    table = {}
    for other_counts in other_count_keys:
        table[other_counts] = tuple(
            base_expected_cost(
                other_counts,
                chosen_index,
                valid_slots=valid_slots,
                capacity=capacity,
            )
            for chosen_index in range(len(valid_slots))
        )
    return table


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


def continuous_windows(valid_slots: tuple[int, ...]) -> Iterable[tuple[int, int]]:
    for start in valid_slots:
        for end in valid_slots:
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
) -> list[EquilibriumCandidate]:
    candidates = []
    seen_toll_vectors = set()

    for window_start, window_end in continuous_windows(valid_slots):
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


def candidate_to_record(
    candidate: EquilibriumCandidate,
    *,
    valid_slots: tuple[int, ...],
) -> dict:
    return {
        'coarse_toll_slot_spec': candidate.window_spec,
        'coarse_toll_points': candidate.toll,
        'cost_gap': round(candidate.cost_gap, 6),
        'nash_count': candidate.nash_count,
        'distribution': [
            {
                'slot': slot,
                'departure_time': minute_to_clock(departure_minute_for_slot(slot)),
                'count': count,
            }
            for slot, count in selected_distribution_items(candidate.distribution, valid_slots)
        ],
        'selected_costs': [
            {
                'slot': slot,
                'departure_time': minute_to_clock(departure_minute_for_slot(slot)),
                'expected_cost': round(cost, 6),
            }
            for slot, cost in candidate.selected_costs
        ],
        'settings_snippet': {
            'coarse_toll_enabled': 1,
            'coarse_toll_slot_spec': candidate.window_spec,
            'coarse_toll_points': candidate.toll,
        },
    }


def print_candidate(candidate: EquilibriumCandidate, index: int, valid_slots: tuple[int, ...]):
    distribution_text = ', '.join(
        f'slot {slot}({minute_to_clock(departure_minute_for_slot(slot))}): {count} 人'
        for slot, count in selected_distribution_items(candidate.distribution, valid_slots)
    )
    cost_text = ', '.join(
        f'slot {slot}: {format_number(cost)}'
        for slot, cost in candidate.selected_costs
    )

    print(f'候选 {index}')
    print(f'  coarse_toll_slot_spec = {candidate.window_spec!r}')
    print(f'  coarse_toll_points = {format_number(candidate.toll)}')
    print(f'  均衡数量 = {candidate.nash_count}')
    print(f'  最佳均衡分布 = {distribution_text}')
    print(f'  被选择时点预期成本 = {cost_text}')
    print(f'  成本差距 = {format_number(candidate.cost_gap)}')
    print('  settings.py 片段：')
    print('      coarse_toll_enabled=1,')
    print(f'      coarse_toll_slot_spec={candidate.window_spec!r},')
    print(f'      coarse_toll_points={format_number(candidate.toll)},')


def print_text_output(
    candidates: list[EquilibriumCandidate],
    *,
    config: CalibrationConfig,
    valid_slots: tuple[int, ...],
    mode: str,
):
    print('单瓶颈粗收费校准')
    print(
        '参数：'
        f'players={config.players}, capacity={config.capacity}, '
        f'slots={valid_slots[0]}-{valid_slots[-1]}, mode={mode}'
    )
    print()

    if not candidates:
        print('没有找到聚合纯策略 Nash 均衡。请调整收费窗口、收费额或搜索范围。')
        return

    for index, candidate in enumerate(candidates, start=1):
        print_candidate(candidate, index, valid_slots)
        if index != len(candidates):
            print()


def print_json_output(
    candidates: list[EquilibriumCandidate],
    *,
    config: CalibrationConfig,
    valid_slots: tuple[int, ...],
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
        },
        'results': [
            candidate_to_record(candidate, valid_slots=valid_slots)
            for candidate in candidates
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    valid_slots = tuple(departure_slots())
    parser = build_parser()
    args = parser.parse_args(argv)
    window = validate_args(parser, args, valid_slots)
    ensure_exact_search_size(args.players, len(valid_slots))

    config = CalibrationConfig(
        players=args.players,
        capacity=args.capacity,
        min_toll=args.min_toll,
        max_toll=args.max_toll,
        toll_step=args.toll_step,
        top_k=args.top_k,
    )
    count_keys = tuple(generate_count_keys(args.players, len(valid_slots)))
    other_count_keys = tuple(generate_count_keys(args.players - 1, len(valid_slots)))
    base_cost_table = build_base_cost_table(
        other_count_keys,
        valid_slots=valid_slots,
        capacity=args.capacity,
    )

    if window is not None:
        mode = 'evaluate'
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
        mode = 'search'
        candidates = search_configs(
            config=config,
            count_keys=count_keys,
            base_cost_table=base_cost_table,
            valid_slots=valid_slots,
        )

    if args.json:
        print_json_output(candidates, config=config, valid_slots=valid_slots, mode=mode)
    else:
        print_text_output(candidates, config=config, valid_slots=valid_slots, mode=mode)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
