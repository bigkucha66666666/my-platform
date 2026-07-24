from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from math import ceil, comb, floor


EPSILON = 1e-9
CALIBRATION_MODE_AUTO = 'auto'
CALIBRATION_MODE_EXACT = 'exact'
CALIBRATION_MODE_LARGE_GROUP = 'large-group'
MAX_EXACT_DISTRIBUTIONS = 2_500


class CalibrationError(RuntimeError):
    pass


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
    calibration_source: str = 'computed'

    @property
    def window_spec(self):
        if self.window_start == self.window_end:
            return str(self.window_start)
        return f'{self.window_start}-{self.window_end}'


def parse_calibration_mode(value):
    normalized = str(value or '').strip().lower().replace('_', '-')
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
        raise CalibrationError('校准模式必须是 auto、exact 或 large-group。')
    return aliases[normalized]


def service_batches_needed(load, capacity):
    if int(load) <= 0:
        return 0
    return ceil(int(load) / max(1, int(capacity)))


def departure_minute_map(valid_slots, first_departure_minute, slot_size_minutes):
    return {
        slot: float(first_departure_minute) + index * float(slot_size_minutes)
        for index, slot in enumerate(valid_slots)
    }


def base_cost_for_choice(
    other_counts,
    chosen_index,
    *,
    valid_slots,
    capacity,
    departure_minutes,
    capacity_window_minutes=1,
    preferred_arrival_minute=480,
    free_flow_travel_minutes=6,
    queue_cost_per_minute=2,
    early_cost_per_minute=1,
    late_cost_per_minute=3,
):
    next_available = departure_minutes[valid_slots[0]]
    chosen_cost = None

    for index, slot in enumerate(valid_slots):
        departure_minute = departure_minutes[slot]
        load = int(other_counts[index]) + (1 if index == chosen_index else 0)
        if load <= 0:
            next_available = max(next_available, departure_minute)
            continue

        first_service_start = max(departure_minute, next_available)
        batches = service_batches_needed(load, capacity)
        if index == chosen_index:
            queue_delay = max(
                0,
                first_service_start
                + (batches - 1) * capacity_window_minutes
                - departure_minute,
            )
            arrival = departure_minute + free_flow_travel_minutes + queue_delay
            early = max(0, preferred_arrival_minute - arrival)
            late = max(0, arrival - preferred_arrival_minute)
            chosen_cost = (
                queue_cost_per_minute * queue_delay
                + early_cost_per_minute * early
                + late_cost_per_minute * late
            )
        next_available = first_service_start + batches * capacity_window_minutes

    if chosen_cost is None:
        raise CalibrationError('无法计算候选出发时间成本。')
    return round(float(chosen_cost), 10)


def base_costs_for_counts(
    counts,
    *,
    valid_slots,
    capacity,
    departure_minutes,
    capacity_window_minutes=1,
    preferred_arrival_minute=480,
    free_flow_travel_minutes=6,
    queue_cost_per_minute=2,
    early_cost_per_minute=1,
    late_cost_per_minute=3,
):
    next_available = departure_minutes[valid_slots[0]]
    costs = []
    for index, slot in enumerate(valid_slots):
        departure_minute = departure_minutes[slot]
        next_available = max(next_available, departure_minute)
        first_service_start = max(departure_minute, next_available)
        load_after_joining = int(counts[index]) + 1
        batches_after_joining = service_batches_needed(load_after_joining, capacity)
        queue_delay = max(
            0,
            first_service_start
            + (batches_after_joining - 1) * capacity_window_minutes
            - departure_minute,
        )
        arrival = departure_minute + free_flow_travel_minutes + queue_delay
        early = max(0, preferred_arrival_minute - arrival)
        late = max(0, arrival - preferred_arrival_minute)
        costs.append(
            queue_cost_per_minute * queue_delay
            + early_cost_per_minute * early
            + late_cost_per_minute * late
        )

        current_load = int(counts[index])
        if current_load > 0:
            next_available = first_service_start + service_batches_needed(
                current_load,
                capacity,
            ) * capacity_window_minutes
    return tuple(round(float(cost), 10) for cost in costs)


def symmetric_windows(valid_slots, departure_minutes, preferred_departure_minute=474):
    center_index = min(
        range(len(valid_slots)),
        key=lambda index: abs(departure_minutes[valid_slots[index]] - preferred_departure_minute),
    )
    maximum_offset = min(center_index, len(valid_slots) - center_index - 1)
    first_offset = 1 if maximum_offset else 0
    return tuple(
        (valid_slots[center_index - offset], valid_slots[center_index + offset])
        for offset in range(first_offset, maximum_offset + 1)
    )


def toll_values(min_toll, max_toll, toll_step):
    steps = int(floor((float(max_toll) - float(min_toll)) / float(toll_step) + EPSILON))
    return tuple(round(float(min_toll) + index * float(toll_step), 10) for index in range(steps + 1))


def toll_vector(window_start, window_end, toll, valid_slots):
    return tuple(float(toll) if window_start <= slot <= window_end else 0 for slot in valid_slots)


def generate_count_keys(total, slots_count):
    if slots_count == 1:
        yield (total,)
        return
    for count in range(total + 1):
        for tail in generate_count_keys(total - count, slots_count - 1):
            yield (count,) + tail


def candidate_sort_key(candidate):
    occupied = sum(1 for count in candidate.distribution if count > 0)
    return (
        round(candidate.deviation_gap, 10),
        round(candidate.cost_gap, 10),
        -occupied,
        round(candidate.toll, 10),
        candidate.window_start,
        candidate.window_end,
        candidate.distribution,
    )


def _selected_cost_gap(selected_costs):
    values = [cost for _, cost in selected_costs]
    return max(values) - min(values) if values else float('inf')


def calibrate_exact_candidates(
    *,
    players,
    capacity,
    valid_slots,
    departure_minutes,
    min_toll,
    max_toll,
    toll_step,
    top_k,
):
    distributions_count = comb(players + len(valid_slots) - 1, len(valid_slots) - 1)
    if distributions_count > MAX_EXACT_DISTRIBUTIONS:
        raise CalibrationError(
            f'精确搜索规模过大：{players} 人、{len(valid_slots)} 个时点、'
            f'{distributions_count} 个分布。'
        )

    count_keys = tuple(generate_count_keys(players, len(valid_slots)))

    @lru_cache(maxsize=None)
    def base_choice_costs(other_counts):
        return tuple(
            base_cost_for_choice(
                other_counts,
                chosen_index,
                valid_slots=valid_slots,
                capacity=capacity,
                departure_minutes=departure_minutes,
            )
            for chosen_index in range(len(valid_slots))
        )

    candidates = []
    for window_start, window_end in symmetric_windows(valid_slots, departure_minutes):
        for toll in toll_values(min_toll, max_toll, toll_step):
            tolls = toll_vector(window_start, window_end, toll, valid_slots)
            best_for_config = None
            nash_count = 0
            for counts in count_keys:
                selected_costs = []
                is_nash = True
                for current_index, count in enumerate(counts):
                    if count <= 0:
                        continue
                    other_counts = list(counts)
                    other_counts[current_index] -= 1
                    choice_costs = tuple(
                        base + charge
                        for base, charge in zip(base_choice_costs(tuple(other_counts)), tolls)
                    )
                    current_cost = choice_costs[current_index]
                    if current_cost > min(choice_costs) + EPSILON:
                        is_nash = False
                        break
                    selected_costs.append((valid_slots[current_index], current_cost))
                if not is_nash or not selected_costs:
                    continue
                nash_count += 1
                candidate = EquilibriumCandidate(
                    window_start=window_start,
                    window_end=window_end,
                    toll=toll,
                    cost_gap=_selected_cost_gap(selected_costs),
                    nash_count=0,
                    distribution=counts,
                    selected_costs=tuple(selected_costs),
                    calibration_mode=CALIBRATION_MODE_EXACT,
                )
                if best_for_config is None or candidate_sort_key(candidate) < candidate_sort_key(best_for_config):
                    best_for_config = candidate
            if best_for_config is not None:
                candidates.append(
                    EquilibriumCandidate(
                        **{
                            **best_for_config.__dict__,
                            'nash_count': nash_count,
                        }
                    )
                )
    return sorted(candidates, key=candidate_sort_key)[:top_k]


def _initial_large_group_distribution(players, valid_slots, departure_minutes):
    center_order = sorted(
        range(len(valid_slots)),
        key=lambda index: (
            abs(departure_minutes[valid_slots[index]] - 474),
            departure_minutes[valid_slots[index]],
        ),
    )
    counts = [0] * len(valid_slots)
    for index in range(players):
        counts[center_order[index % len(center_order)]] += 1
    return counts


def _best_response_candidate(
    *,
    players,
    capacity,
    valid_slots,
    departure_minutes,
    window_start,
    window_end,
    toll,
    max_iterations,
    initial_counts=None,
):
    tolls = toll_vector(window_start, window_end, toll, valid_slots)
    counts = list(initial_counts) if initial_counts is not None else _initial_large_group_distribution(
        players,
        valid_slots,
        departure_minutes,
    )

    @lru_cache(maxsize=20_000)
    def choice_costs(other_counts):
        return tuple(
            base + tolls[index]
            for index, base in enumerate(
                base_costs_for_counts(
                    other_counts,
                    valid_slots=valid_slots,
                    capacity=capacity,
                    departure_minutes=departure_minutes,
                )
            )
        )

    for _ in range(max_iterations):
        best_move = None
        for current_index, count in enumerate(counts):
            if count <= 0:
                continue
            other_counts = list(counts)
            other_counts[current_index] -= 1
            costs = choice_costs(tuple(other_counts))
            best_index = min(range(len(costs)), key=lambda index: (costs[index], index))
            improvement = costs[current_index] - costs[best_index]
            if improvement <= EPSILON:
                continue
            move = (improvement, -current_index, -best_index, current_index, best_index)
            if best_move is None or move > best_move:
                best_move = move
        if best_move is None:
            break
        current_index, best_index = best_move[-2:]
        counts[current_index] -= 1
        counts[best_index] += 1

    selected_costs = []
    deviation_gap = 0
    for current_index, count in enumerate(counts):
        if count <= 0:
            continue
        other_counts = list(counts)
        other_counts[current_index] -= 1
        costs = choice_costs(tuple(other_counts))
        current_cost = costs[current_index]
        deviation_gap = max(deviation_gap, current_cost - min(costs))
        selected_costs.append((valid_slots[current_index], current_cost))

    return EquilibriumCandidate(
        window_start=window_start,
        window_end=window_end,
        toll=toll,
        cost_gap=_selected_cost_gap(selected_costs),
        nash_count=1 if deviation_gap <= EPSILON else 0,
        distribution=tuple(counts),
        selected_costs=tuple(selected_costs),
        calibration_mode=CALIBRATION_MODE_LARGE_GROUP,
        deviation_gap=max(0, deviation_gap),
    )


def calibrate_large_group_candidates(
    *,
    players,
    capacity,
    valid_slots,
    departure_minutes,
    min_toll,
    max_toll,
    toll_step,
    top_k,
    approx_refine_pool_size,
    approx_refine_iterations,
):
    windows = symmetric_windows(valid_slots, departure_minutes)
    if len(windows) > approx_refine_pool_size:
        indexes = {
            round(index * (len(windows) - 1) / max(1, approx_refine_pool_size - 1))
            for index in range(approx_refine_pool_size)
        }
        windows = tuple(windows[index] for index in sorted(indexes))

    coarse_candidates = [
        _best_response_candidate(
            players=players,
            capacity=capacity,
            valid_slots=valid_slots,
            departure_minutes=departure_minutes,
            window_start=window_start,
            window_end=window_end,
            toll=toll,
            max_iterations=0,
        )
        for (window_start, window_end), toll in product(
            windows,
            toll_values(min_toll, max_toll, toll_step),
        )
    ]
    coarse_candidates.sort(key=candidate_sort_key)
    refine_count = max(top_k, approx_refine_pool_size)
    refined_candidates = [
        _best_response_candidate(
            players=players,
            capacity=capacity,
            valid_slots=valid_slots,
            departure_minutes=departure_minutes,
            window_start=candidate.window_start,
            window_end=candidate.window_end,
            toll=candidate.toll,
            max_iterations=approx_refine_iterations,
            initial_counts=candidate.distribution,
        )
        for candidate in coarse_candidates[:refine_count]
    ]
    return sorted(
        refined_candidates + coarse_candidates[:refine_count],
        key=candidate_sort_key,
    )[:top_k]


def calibrate_candidates_by_mode(
    *,
    players,
    capacity,
    valid_slots,
    first_departure_minute,
    slot_size_minutes=1,
    min_toll=0,
    max_toll=40,
    toll_step=1,
    top_k=10,
    calibration_mode=CALIBRATION_MODE_AUTO,
    approx_refine_pool_size=8,
    approx_refine_iterations=160,
):
    if players <= 0 or capacity <= 0:
        raise CalibrationError('参与人数和服务率必须大于 0。')
    if not valid_slots:
        raise CalibrationError('可选出发时点不能为空。')
    if toll_step <= 0 or min_toll > max_toll:
        raise CalibrationError('收费搜索范围或步长无效。')

    requested_mode = parse_calibration_mode(calibration_mode)
    distributions_count = comb(players + len(valid_slots) - 1, len(valid_slots) - 1)
    resolved_mode = requested_mode
    if requested_mode == CALIBRATION_MODE_AUTO:
        resolved_mode = (
            CALIBRATION_MODE_EXACT
            if distributions_count <= MAX_EXACT_DISTRIBUTIONS
            else CALIBRATION_MODE_LARGE_GROUP
        )
    departure_minutes = departure_minute_map(
        valid_slots,
        first_departure_minute,
        slot_size_minutes,
    )

    if resolved_mode == CALIBRATION_MODE_EXACT:
        return calibrate_exact_candidates(
            players=players,
            capacity=capacity,
            valid_slots=valid_slots,
            departure_minutes=departure_minutes,
            min_toll=min_toll,
            max_toll=max_toll,
            toll_step=toll_step,
            top_k=top_k,
        )
    return calibrate_large_group_candidates(
        players=players,
        capacity=capacity,
        valid_slots=valid_slots,
        departure_minutes=departure_minutes,
        min_toll=min_toll,
        max_toll=max_toll,
        toll_step=toll_step,
        top_k=top_k,
        approx_refine_pool_size=max(1, int(approx_refine_pool_size)),
        approx_refine_iterations=max(1, int(approx_refine_iterations)),
    )


def calibrate_best_candidate(**kwargs):
    candidates = calibrate_candidates_by_mode(**kwargs, top_k=1)
    if not candidates:
        raise CalibrationError(
            f"未找到可用粗收费配置：players={kwargs.get('players')}, "
            f"capacity={kwargs.get('capacity')}。"
        )
    return candidates[0]
