from dataclasses import dataclass
from math import ceil, floor, isfinite
import json
from pathlib import Path
import random
import time

from otree.api import *


doc = """
整轮随机瓶颈服务率实验。
同一小组在同一轮面对相同的瓶颈服务率，不同轮次的服务率按 session config 随机确定。
"""


DRAW_MODE_BALANCED = 'balanced_shuffle'
DRAW_MODE_IID = 'iid'
REVEAL_BEFORE_DECISION = 'before_decision'
REVEAL_AFTER_DECISION = 'after_decision'

DECISION_SOURCE_MANUAL = 'manual'
DECISION_SOURCE_TIMEOUT_AUTO = 'timeout_auto'
DECISION_SOURCE_DISCONNECT_AUTO = 'disconnect_auto'

CAPACITY_SEQUENCE_VAR = 'dynamic_bottleneck_round_capacity_sequence'
TOTAL_PAYOFF_VAR = 'dynamic_bottleneck_round_total_payoff'
COMPREHENSION_SEEN_VAR = 'dynamic_bottleneck_round_comprehension_seen'
DEPARTURE_SCHEDULE_VAR = 'dynamic_bottleneck_round_departure_schedule'
TOLL_BY_CAPACITY_VAR = 'dynamic_bottleneck_round_toll_by_capacity'
TOLL_CALIBRATION_CACHE_FILE = 'toll_calibration_cache.json'
TOLL_SOURCE_AUTO = 'auto'
TOLL_SOURCE_MANUAL = 'manual'
SAME_TIME_QUEUE_RULE = 'batch_max_wait'
TOLL_WINDOW_RULE = 'symmetric_continuous'


class DynamicCapacityConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DynamicCapacityConfig:
    values: tuple[int, ...]
    probabilities: tuple[float, ...]
    seed: int
    draw_mode: str
    reveal_timing: str


def _csv_items(value, field_name):
    items = [item.strip() for item in str(value or '').split(',') if item.strip()]
    if not items:
        raise DynamicCapacityConfigError(f'{field_name} 不能为空。')
    return items


def parse_dynamic_capacity_config(session_config) -> DynamicCapacityConfig:
    raw_values = _csv_items(
        session_config.get('dynamic_capacity_values', '1,2,3'),
        'dynamic_capacity_values',
    )
    try:
        values = tuple(int(item) for item in raw_values)
    except (TypeError, ValueError) as exc:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_values 中的服务率必须为正整数。'
        ) from exc
    if any(value <= 0 for value in values):
        raise DynamicCapacityConfigError(
            'dynamic_capacity_values 中的服务率必须为正整数。'
        )
    if len(set(values)) != len(values):
        raise DynamicCapacityConfigError('dynamic_capacity_values 不能包含重复服务率。')

    raw_probabilities = _csv_items(
        session_config.get('dynamic_capacity_probabilities', '0.3,0.5,0.2'),
        'dynamic_capacity_probabilities',
    )
    try:
        probabilities = tuple(float(item) for item in raw_probabilities)
    except (TypeError, ValueError) as exc:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_probabilities 必须是逗号分隔的数字。'
        ) from exc
    if any(not isfinite(probability) for probability in probabilities):
        raise DynamicCapacityConfigError(
            'dynamic_capacity_probabilities 中每个概率必须是有限数。'
        )
    if len(values) != len(probabilities):
        raise DynamicCapacityConfigError(
            'dynamic_capacity_values 与 dynamic_capacity_probabilities 的状态数量必须一致。'
        )
    if any(probability < 0 or probability > 1 for probability in probabilities):
        raise DynamicCapacityConfigError('dynamic_capacity_probabilities 中每个概率必须在 0 到 1 之间。')
    if abs(sum(probabilities) - 1.0) > 1e-9:
        raise DynamicCapacityConfigError('dynamic_capacity_probabilities 的概率之和必须为 1。')

    try:
        seed = int(session_config.get('dynamic_capacity_seed', 20260718))
    except (TypeError, ValueError) as exc:
        raise DynamicCapacityConfigError('dynamic_capacity_seed 必须是整数。') from exc

    draw_mode = str(
        session_config.get('dynamic_capacity_draw_mode', DRAW_MODE_BALANCED)
    ).strip().lower()
    if draw_mode not in {DRAW_MODE_BALANCED, DRAW_MODE_IID}:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_draw_mode 必须是 balanced_shuffle 或 iid。'
        )

    reveal_timing = str(
        session_config.get('capacity_reveal_timing', REVEAL_BEFORE_DECISION)
    ).strip().lower()
    if reveal_timing not in {REVEAL_BEFORE_DECISION, REVEAL_AFTER_DECISION}:
        raise DynamicCapacityConfigError(
            'capacity_reveal_timing 必须是 before_decision 或 after_decision。'
        )

    return DynamicCapacityConfig(
        values=values,
        probabilities=probabilities,
        seed=seed,
        draw_mode=draw_mode,
        reveal_timing=reveal_timing,
    )


def _balanced_counts(probabilities, rounds):
    exact_counts = [probability * rounds for probability in probabilities]
    counts = [floor(value) for value in exact_counts]
    remaining = rounds - sum(counts)
    remainder_order = sorted(
        range(len(probabilities)),
        key=lambda index: (-(exact_counts[index] - counts[index]), index),
    )
    for index in remainder_order[:remaining]:
        counts[index] += 1
    return counts


def generate_capacity_sequence(
    config: DynamicCapacityConfig,
    *,
    rounds: int,
    group_id: int,
) -> list[int]:
    if rounds <= 0:
        return []
    rng = random.Random(config.seed + int(group_id) * 1009)
    if config.draw_mode == DRAW_MODE_IID:
        return rng.choices(config.values, weights=config.probabilities, k=rounds)

    counts = _balanced_counts(config.probabilities, rounds)
    sequence = [
        capacity
        for capacity, count in zip(config.values, counts)
        for _ in range(count)
    ]
    rng.shuffle(sequence)
    return sequence


def build_capacity_round_records(
    config: DynamicCapacityConfig,
    *,
    rounds: int,
    group_id: int,
):
    sequence = generate_capacity_sequence(config, rounds=rounds, group_id=group_id)
    probability_by_capacity = dict(zip(config.values, config.probabilities))
    return [
        {
            'round_number': index + 1,
            'capacity': capacity,
            'state': f'capacity_{capacity}',
            'probability': probability_by_capacity[capacity],
            'previous_capacity': sequence[index - 1] if index else None,
        }
        for index, capacity in enumerate(sequence)
    ]


def service_batch_wait_minutes(
    *,
    departure_minute: float,
    first_service_start_minute: float,
    load: int,
    capacity: int,
    capacity_window_minutes: float = 1,
) -> float:
    batches = max(1, (int(load) + int(capacity) - 1) // int(capacity))
    inherited_wait = max(0, float(first_service_start_minute) - float(departure_minute))
    return round(inherited_wait + (batches - 1) * float(capacity_window_minutes), 2)


def decision_capacity_context(
    config: DynamicCapacityConfig,
    *,
    actual_capacity: int,
):
    context = {
        'capacity_revealed': config.reveal_timing == REVEAL_BEFORE_DECISION,
        'capacity_reveal_timing': config.reveal_timing,
        'capacity_states': [
            {
                'capacity': capacity,
                'probability': probability,
                'probability_percent': round(probability * 100, 2),
            }
            for capacity, probability in zip(config.values, config.probabilities)
        ],
    }
    if context['capacity_revealed']:
        context['actual_capacity'] = int(actual_capacity)
    return context


def capacity_reveal_description(config: DynamicCapacityConfig) -> str:
    if config.reveal_timing == REVEAL_BEFORE_DECISION:
        return '每轮真实服务率会在选择出发时间前公布。'
    return '每轮真实服务率会在提交出发时间后公布。'


def comprehension_queue_example(config: DynamicCapacityConfig):
    capacity = min(config.values)
    people = max(5, 2 * capacity + 1)
    departure_minute = 474
    wait_minutes = service_batch_wait_minutes(
        departure_minute=departure_minute,
        first_service_start_minute=departure_minute,
        load=people,
        capacity=capacity,
    )
    arrival_without_queue_minute = departure_minute + C.FREE_FLOW_TRAVEL_MINUTES
    return {
        'people': people,
        'capacity': capacity,
        'departure_minute': departure_minute,
        'wait_minutes': wait_minutes,
        'arrival_without_queue_minute': arrival_without_queue_minute,
        'arrival_with_short_wait_minute': arrival_without_queue_minute + 1,
        'arrival_minute': arrival_without_queue_minute + wait_minutes,
    }


def round_start_wait_seconds(round_number):
    return 120 if int(round_number) == 1 else 60


def should_start_round(*, ready_count, group_size, now_ts, deadline_ts):
    return int(ready_count) >= int(group_size) or float(now_ts) >= float(deadline_ts)


class C(BaseConstants):
    NAME_IN_URL = 'dynamic_bottleneck_round'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 10

    DECISION_TIMEOUT_SECONDS = 60
    RESULTS_TIMEOUT_SECONDS = 50
    DROPOUT_TIMEOUT_SECONDS = 1
    SYNC_POLL_INTERVAL_SECONDS = 3
    AUTO_CONTINUE_DELAY_MS = 200

    PREFERRED_ARRIVAL_MINUTE = 8 * 60
    FREE_FLOW_TRAVEL_MINUTES = 6
    CAPACITY_WINDOW_MINUTES = 1
    DEPARTURE_CHOICE_STEP_MINUTES = 1
    NUM_DEPARTURE_SLOTS = 21
    MAX_DEPARTURE_SLOT_CHOICES = 401
    FIRST_DEPARTURE_MINUTE = PREFERRED_ARRIVAL_MINUTE - FREE_FLOW_TRAVEL_MINUTES - 10

    BASE_POINTS = 140
    FIXED_TRAVEL_TIME_COST = 12
    QUEUE_COST_PER_MINUTE = 2
    EARLY_COST_PER_MINUTE = 1
    LATE_COST_PER_MINUTE = 3


DEPARTURE_SLOT_CHOICES = [
    [slot, str(slot)]
    for slot in range(1, C.MAX_DEPARTURE_SLOT_CHOICES + 1)
]


class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    round_start_deadline_ts = models.FloatField(initial=0)
    round_started_at_ts = models.FloatField(initial=0)
    round_started = models.BooleanField(initial=False)
    decision_deadline_ts = models.FloatField(initial=0)
    results_ready = models.BooleanField(initial=False)
    dynamic_capacity = models.IntegerField(initial=0)
    dynamic_capacity_state = models.StringField(blank=True)
    capacity_probability = models.FloatField(initial=0)


class Player(BasePlayer):
    round_start_ready = models.BooleanField(initial=False)
    dynamic_capacity = models.IntegerField(initial=0)
    dynamic_capacity_state = models.StringField(blank=True)
    previous_round_capacity = models.IntegerField(initial=0)
    capacity_probability = models.FloatField(initial=0)
    capacity_reveal_timing = models.StringField(blank=True)
    dynamic_capacity_seed = models.IntegerField(initial=0)
    dynamic_capacity_draw_mode = models.StringField(blank=True)

    departure_slot = models.IntegerField(choices=DEPARTURE_SLOT_CHOICES)
    departure_minute = models.FloatField(initial=0)
    departure_time_label = models.StringField(blank=True)
    arrival_minute = models.FloatField(initial=0)
    arrival_time_label = models.StringField(blank=True)
    queue_delay_minutes = models.FloatField(initial=0)
    travel_time_minutes = models.FloatField(initial=0)
    early_minutes = models.FloatField(initial=0)
    late_minutes = models.FloatField(initial=0)
    slot_load = models.IntegerField(initial=0)

    total_cost = models.CurrencyField(initial=0)
    reward_bonus = models.CurrencyField(initial=0)
    coarse_toll_charge = models.CurrencyField(initial=0)
    coarse_toll_source = models.StringField(blank=True)
    coarse_toll_auto_enabled = models.BooleanField(initial=False)
    coarse_toll_calibration_source = models.StringField(blank=True)
    coarse_toll_calibration_mode = models.StringField(blank=True)
    coarse_toll_calibration_players = models.IntegerField(initial=0)
    coarse_toll_calibration_capacity = models.IntegerField(initial=0)
    coarse_toll_calibration_cost_gap = models.FloatField(initial=0)
    coarse_toll_calibration_deviation_gap = models.FloatField(initial=0)
    coarse_toll_calibration_nash_count = models.IntegerField(initial=0)
    coarse_toll_equilibrium_distribution = models.LongStringField(blank=True)
    coarse_toll_equilibrium_costs = models.LongStringField(blank=True)
    coarse_toll_enabled = models.BooleanField(initial=False)
    coarse_toll_slot_spec = models.StringField(blank=True)
    coarse_toll_time_window_spec = models.StringField(blank=True)
    coarse_toll_points = models.CurrencyField(initial=0)
    decision_source = models.StringField(blank=True)
    timeout_happened = models.BooleanField(initial=False)
    dropout_event = models.StringField(blank=True)
    recovered_this_round = models.BooleanField(initial=False)
    recovery_reason = models.StringField(blank=True)


EXPORT_HEADERS = [
    'session_code',
    'participant_code',
    'group_id',
    'round_number',
    'dynamic_capacity',
    'dynamic_capacity_state',
    'previous_round_capacity',
    'capacity_probability',
    'capacity_reveal_timing',
    'dynamic_capacity_seed',
    'dynamic_capacity_draw_mode',
    'coarse_toll_source',
    'coarse_toll_auto_enabled',
    'coarse_toll_calibration_source',
    'coarse_toll_calibration_mode',
    'coarse_toll_calibration_players',
    'coarse_toll_calibration_capacity',
    'coarse_toll_calibration_cost_gap',
    'coarse_toll_calibration_deviation_gap',
    'coarse_toll_calibration_nash_count',
    'coarse_toll_equilibrium_distribution',
    'coarse_toll_equilibrium_costs',
    'coarse_toll_enabled',
    'coarse_toll_slot_spec',
    'coarse_toll_time_window_spec',
    'coarse_toll_points',
    'coarse_toll_charge',
    'departure_schedule_source',
    'departure_schedule_capacity_basis',
    'departure_schedule_num_slots',
    'departure_schedule_first_time',
    'departure_schedule_last_time',
    'departure_slot',
    'departure_minute',
    'queue_delay_minutes',
    'arrival_minute',
    'early_minutes',
    'late_minutes',
    'total_cost',
    'payoff',
    'decision_source',
    'timeout_happened',
    'dropout_event',
    'recovered_this_round',
    'recovery_reason',
    'historical_dropout',
    'dropout_active_at_export',
    'dropout_reason_at_export',
    'has_recovered_after_disconnect',
    'has_recovered_after_timeout',
]


def config_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return False


def minute_to_clock(value):
    total_seconds = int(round(float(value) * 60))
    hours = (total_seconds // 3600) % 24
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if seconds:
        return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
    return f'{hours:02d}:{minutes:02d}'


def clock_to_minute(value, field_name='时间'):
    text = str(value or '').strip()
    parts = text.split(':')
    if len(parts) != 2:
        raise ValueError(f'{field_name} 必须使用 HH:MM 格式。')
    try:
        hours, minutes = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f'{field_name} 必须使用 HH:MM 格式。') from exc
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
        raise ValueError(f'{field_name} 必须是有效的 24 小时时间。')
    return hours * 60 + minutes


def number_display(value):
    numeric = round(float(value or 0), 2)
    if abs(numeric - round(numeric)) < 1e-9:
        return str(int(round(numeric)))
    return f'{numeric:.2f}'.rstrip('0').rstrip('.')


def probability_display(probability):
    return f'{float(probability) * 100:g}%'


def config_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def config_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_dynamic_departure_schedule(
    *,
    players_count,
    capacity_values,
    min_slots_each_side=10,
):
    values = tuple(int(value) for value in capacity_values)
    if not values or min(values) <= 0:
        raise ValueError('动态出发时间范围需要至少一个正服务率。')
    capacity_basis = min(values)
    required_occupied_slots = ceil(max(0, int(players_count)) / capacity_basis)
    slots_each_side = max(max(0, int(min_slots_each_side)), ceil(required_occupied_slots / 2))
    center_minute = C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES
    first_minute = center_minute - slots_each_side * C.DEPARTURE_CHOICE_STEP_MINUTES
    last_minute = center_minute + slots_each_side * C.DEPARTURE_CHOICE_STEP_MINUTES
    return {
        'enabled': True,
        'source': 'auto',
        'players_count': int(players_count),
        'capacity_basis': capacity_basis,
        'required_occupied_slots': required_occupied_slots,
        'slots_each_side': slots_each_side,
        'num_slots': slots_each_side * 2 + 1,
        'slot_size_minutes': C.DEPARTURE_CHOICE_STEP_MINUTES,
        'first_departure_minute': first_minute,
        'last_departure_minute': last_minute,
        'first_departure_time': minute_to_clock(first_minute),
        'last_departure_time': minute_to_clock(last_minute),
    }


def static_departure_schedule():
    return build_dynamic_departure_schedule(
        players_count=0,
        capacity_values=(1,),
        min_slots_each_side=(C.NUM_DEPARTURE_SLOTS - 1) // 2,
    )


def departure_schedule_for_player(player):
    schedule = player.participant.vars.get(DEPARTURE_SCHEDULE_VAR, {})
    if isinstance(schedule, dict) and schedule.get('enabled'):
        return schedule
    return static_departure_schedule()


def apply_dynamic_departure_schedules(session, matrix, config):
    auto_enabled = config_flag(session.config.get('departure_schedule_auto_enabled', 1))
    min_slots_each_side = max(
        0,
        config_int(session.config.get('departure_schedule_min_slots_each_side', 10), 10),
    )
    for group_players in matrix:
        if auto_enabled:
            schedule = build_dynamic_departure_schedule(
                players_count=len(group_players),
                capacity_values=config.values,
                min_slots_each_side=min_slots_each_side,
            )
        else:
            schedule = static_departure_schedule()
            schedule['source'] = 'static'
        if schedule['num_slots'] > C.MAX_DEPARTURE_SLOT_CHOICES:
            raise ValueError(
                f"动态出发时点数量 {schedule['num_slots']} 超过上限 "
                f'{C.MAX_DEPARTURE_SLOT_CHOICES}。'
            )
        for player in group_players:
            player.participant.vars[DEPARTURE_SCHEDULE_VAR] = schedule


def validate_toll_reveal_compatibility(session_config):
    reveal_timing = str(
        session_config.get('capacity_reveal_timing', REVEAL_BEFORE_DECISION)
    ).strip().lower()
    if config_flag(session_config.get('coarse_toll_auto_enabled', 0)) and reveal_timing == REVEAL_AFTER_DECISION:
        raise ValueError(
            'capacity_reveal_timing=after_decision 时不能启用自动粗收费；'
            '请改为 before_decision，或关闭自动粗收费并使用统一手动收费。'
        )


def departure_slots(schedule=None):
    schedule = schedule or static_departure_schedule()
    return list(range(1, int(schedule['num_slots']) + 1))


def departure_minute_for_slot(slot, schedule=None):
    schedule = schedule or static_departure_schedule()
    return float(schedule['first_departure_minute']) + (
        int(slot) - 1
    ) * float(schedule['slot_size_minutes'])


def departure_slot_for_minute(value, schedule=None):
    schedule = schedule or static_departure_schedule()
    try:
        minute = float(value)
    except (TypeError, ValueError):
        return None
    offset = (minute - float(schedule['first_departure_minute'])) / float(
        schedule['slot_size_minutes']
    )
    rounded_offset = round(offset)
    if abs(offset - rounded_offset) > 1e-6:
        return None
    slot = rounded_offset + 1
    return int(slot) if slot in departure_slots(schedule) else None


def player_departure_slot(player):
    schedule = departure_schedule_for_player(player)
    minute = player.field_maybe_none('departure_minute')
    slot = departure_slot_for_minute(minute, schedule)
    if slot is not None:
        return slot
    stored_slot = player.field_maybe_none('departure_slot')
    return stored_slot if stored_slot in departure_slots(schedule) else None


def player_has_departure_choice(player):
    return player_departure_slot(player) is not None


def set_player_departure_choice(player, departure_minute, decision_source=None):
    schedule = departure_schedule_for_player(player)
    slot = departure_slot_for_minute(departure_minute, schedule)
    if slot is None:
        return False
    minute = departure_minute_for_slot(slot, schedule)
    player.departure_slot = slot
    player.departure_minute = minute
    player.departure_time_label = minute_to_clock(minute)
    if decision_source is not None:
        player.decision_source = decision_source
    return True


def build_auto_group_matrix(players, cohort_size):
    if cohort_size <= 0:
        return [players]
    return [players[index:index + cohort_size] for index in range(0, len(players), cohort_size)]


def parse_manual_grouping_spec(spec):
    groups = []
    for raw_group in str(spec or '').replace('\n', ';').split(';'):
        raw_group = raw_group.strip()
        if not raw_group:
            continue
        member_part = raw_group.split(':', 1)[1] if ':' in raw_group else raw_group
        labels = [label.strip() for label in member_part.split(',') if label.strip()]
        if labels:
            groups.append(labels)
    return groups


def build_manual_group_matrix(players, spec):
    groups = parse_manual_grouping_spec(spec)
    if not groups:
        raise ValueError('grouping_enabled=1 时 manual_grouping_spec 不能为空。')
    player_by_label = {player.participant.label: player for player in players}
    expected_labels = set(player_by_label)
    configured_labels = [label for group in groups for label in group]
    if len(configured_labels) != len(set(configured_labels)):
        raise ValueError('manual_grouping_spec 中存在重复 participant_label。')
    missing = sorted(expected_labels - set(configured_labels))
    unknown = sorted(set(configured_labels) - expected_labels)
    if missing or unknown or None in expected_labels:
        raise ValueError(
            'manual_grouping_spec 必须且只能包含本 session 的全部 participant_label。'
            f' 缺失={missing}，未知={unknown}。'
        )
    return [[player_by_label[label] for label in group] for group in groups]


def assign_group_metadata(matrix, grouping_enabled):
    for index, group_players in enumerate(matrix, start=1):
        labels = [player.participant.label or player.participant.code for player in group_players]
        for player in group_players:
            player.participant.vars['assigned_group_id'] = index
            player.participant.vars['assigned_group_label'] = f'G{index:02d}'
            player.participant.vars['assigned_group_members'] = ','.join(labels)
            player.participant.vars['grouping_enabled'] = grouping_enabled


def initialize_group_capacity_sequences(subsession, config):
    for group in subsession.get_groups():
        records = build_capacity_round_records(
            config,
            rounds=C.NUM_ROUNDS,
            group_id=group.id_in_subsession,
        )
        for player in group.get_players():
            player.participant.vars[CAPACITY_SEQUENCE_VAR] = records


def apply_round_capacity(group, config):
    players = group.get_players()
    if not players:
        return
    records = players[0].participant.vars.get(CAPACITY_SEQUENCE_VAR)
    if not isinstance(records, list) or len(records) != C.NUM_ROUNDS:
        records = build_capacity_round_records(
            config,
            rounds=C.NUM_ROUNDS,
            group_id=group.id_in_subsession,
        )
        for player in players:
            player.participant.vars[CAPACITY_SEQUENCE_VAR] = records
    record = records[group.round_number - 1]
    group.dynamic_capacity = record['capacity']
    group.dynamic_capacity_state = record['state']
    group.capacity_probability = record['probability']
    for player in players:
        player.dynamic_capacity = record['capacity']
        player.dynamic_capacity_state = record['state']
        player.previous_round_capacity = record['previous_capacity'] or 0
        player.capacity_probability = record['probability']
        player.capacity_reveal_timing = config.reveal_timing
        player.dynamic_capacity_seed = config.seed
        player.dynamic_capacity_draw_mode = config.draw_mode


def parse_slot_spec(spec, field_name, valid_slots=None):
    valid_slots = set(valid_slots or departure_slots())
    slots = set()
    for part in str(spec or '').split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            start_raw, end_raw = part.split('-', 1)
            try:
                start, end = int(start_raw), int(end_raw)
            except ValueError as exc:
                raise ValueError(f'{field_name} 必须使用如 8-10,12 的时点格式。') from exc
            if start > end:
                raise ValueError(f'{field_name} 的起始时点不能大于结束时点。')
            slots.update(range(start, end + 1))
        else:
            try:
                slots.add(int(part))
            except ValueError as exc:
                raise ValueError(f'{field_name} 必须使用如 8-10,12 的时点格式。') from exc
    invalid = slots - valid_slots
    if invalid:
        raise ValueError(f'{field_name} 包含不可选时点：{sorted(invalid)}。')
    return slots


def parse_time_window_spec(spec, field_name, schedule):
    text = str(spec or '').strip()
    if not text:
        return set()
    parts = [part.strip() for part in text.split('-')]
    if len(parts) == 1:
        start_minute = end_minute = clock_to_minute(parts[0], field_name)
    elif len(parts) == 2:
        start_minute = clock_to_minute(parts[0], field_name)
        end_minute = clock_to_minute(parts[1], field_name)
    else:
        raise ValueError(f'{field_name} 必须使用如 07:52-07:56 的时间范围。')
    if start_minute > end_minute:
        raise ValueError(f'{field_name} 的起始时间不能晚于结束时间。')

    first_minute = float(schedule['first_departure_minute'])
    last_minute = float(schedule['last_departure_minute'])
    if start_minute < first_minute or end_minute > last_minute:
        raise ValueError(
            f'{field_name} 必须位于可选出发时间范围 '
            f"{schedule['first_departure_time']}-{schedule['last_departure_time']} 内。"
        )
    start_slot = departure_slot_for_minute(start_minute, schedule)
    end_slot = departure_slot_for_minute(end_minute, schedule)
    if start_slot is None or end_slot is None:
        raise ValueError(f'{field_name} 的端点必须落在可选出发时间网格上。')
    return set(range(start_slot, end_slot + 1))


def reward_bonus_for_slot(session, slot):
    if not config_flag(session.config.get('reward_treatment_enabled', 0)):
        return 0
    slots = parse_slot_spec(session.config.get('rewarded_slot_spec', ''), 'rewarded_slot_spec')
    return float(session.config.get('reward_bonus_points', 0)) if slot in slots else 0


def coarse_toll_for_slot(session, slot):
    if not config_flag(session.config.get('coarse_toll_enabled', 0)):
        return 0
    slots = parse_slot_spec(session.config.get('coarse_toll_slot_spec', ''), 'coarse_toll_slot_spec')
    return float(session.config.get('coarse_toll_points', 0)) if slot in slots else 0


def validate_optional_treatments(session):
    if config_flag(session.config.get('reward_treatment_enabled', 0)):
        if not parse_slot_spec(session.config.get('rewarded_slot_spec', ''), 'rewarded_slot_spec'):
            raise ValueError('reward_treatment_enabled=1 时 rewarded_slot_spec 不能为空。')
    if (
        config_flag(session.config.get('coarse_toll_enabled', 0))
        and not config_flag(session.config.get('coarse_toll_auto_enabled', 0))
    ):
        time_window_spec = str(
            session.config.get('coarse_toll_time_window_spec', '') or ''
        ).strip()
        slot_spec = str(session.config.get('coarse_toll_slot_spec', '') or '').strip()
        if time_window_spec:
            parts = [part.strip() for part in time_window_spec.split('-')]
            if len(parts) not in {1, 2}:
                raise ValueError(
                    'coarse_toll_time_window_spec 必须使用如 07:52-07:56 的时间范围。'
                )
            for part in parts:
                clock_to_minute(part, 'coarse_toll_time_window_spec')
        elif slot_spec:
            parse_slot_spec(slot_spec, 'coarse_toll_slot_spec')
        else:
            raise ValueError(
                'coarse_toll_enabled=1 时 coarse_toll_time_window_spec '
                '或 coarse_toll_slot_spec 不能为空。'
            )


def toll_calibration_settings(settings):
    mode = str(settings.get('coarse_toll_auto_mode', 'auto')).strip().lower().replace('_', '-')
    return {
        'min_toll': max(0, config_float(settings.get('coarse_toll_auto_min_toll', 0), 0)),
        'max_toll': max(0, config_float(settings.get('coarse_toll_auto_max_toll', 40), 40)),
        'toll_step': max(0, config_float(settings.get('coarse_toll_auto_toll_step', 1), 1)),
        'calibration_mode': mode,
        'approx_refine_pool_size': max(
            1,
            config_int(settings.get('coarse_toll_auto_approx_refine_pool_size', 8), 8),
        ),
        'approx_refine_iterations': max(
            1,
            config_int(settings.get('coarse_toll_auto_approx_refine_iterations', 160), 160),
        ),
    }


def load_toll_calibration_cache(cache_path=None):
    path = Path(cache_path) if cache_path else Path(__file__).with_name(
        TOLL_CALIBRATION_CACHE_FILE
    )
    if not path.exists():
        return {
            'version': 1,
            'same_time_queue_rule': SAME_TIME_QUEUE_RULE,
            'toll_window_rule': TOLL_WINDOW_RULE,
            'records': [],
        }
    try:
        cache_data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'粗收费缓存文件不是有效 JSON：{path}') from exc
    if not isinstance(cache_data, dict) or not isinstance(cache_data.get('records'), list):
        raise ValueError(f'粗收费缓存文件结构无效：{path}')
    return cache_data


def _same_number(left, right):
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except (TypeError, ValueError):
        return False


def cached_toll_candidate(
    *,
    players_count,
    capacity,
    schedule,
    settings,
    cache_data=None,
):
    from .toll_calibration import EquilibriumCandidate

    cache_data = cache_data if cache_data is not None else load_toll_calibration_cache()
    if cache_data.get('version') != 1:
        return None
    if cache_data.get('same_time_queue_rule') != SAME_TIME_QUEUE_RULE:
        return None
    if cache_data.get('toll_window_rule') != TOLL_WINDOW_RULE:
        return None

    calibration = toll_calibration_settings(settings)
    expected = {
        'players': int(players_count),
        'capacity': int(capacity),
        'num_slots': int(schedule['num_slots']),
        'first_departure_minute': float(schedule['first_departure_minute']),
        'last_departure_minute': float(schedule['last_departure_minute']),
        'slot_size_minutes': float(schedule['slot_size_minutes']),
        'min_toll': calibration['min_toll'],
        'max_toll': calibration['max_toll'],
        'toll_step': calibration['toll_step'],
        'requested_calibration_mode': calibration['calibration_mode'],
        'approx_refine_pool_size': calibration['approx_refine_pool_size'],
        'approx_refine_iterations': calibration['approx_refine_iterations'],
    }
    numeric_keys = {
        'players',
        'capacity',
        'num_slots',
        'first_departure_minute',
        'last_departure_minute',
        'slot_size_minutes',
        'min_toll',
        'max_toll',
        'toll_step',
        'approx_refine_pool_size',
        'approx_refine_iterations',
    }
    valid_slots = set(departure_slots(schedule))
    center_slot = min(
        valid_slots,
        key=lambda slot: abs(
            departure_minute_for_slot(slot, schedule)
            - (C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES)
        ),
    )
    for record in cache_data.get('records', []):
        if not isinstance(record, dict):
            continue
        matches = all(
            _same_number(record.get(key), value)
            if key in numeric_keys
            else record.get(key) == value
            for key, value in expected.items()
        )
        if not matches:
            continue
        try:
            window_start = int(record['window_start'])
            window_end = int(record['window_end'])
            toll = float(record['toll'])
            distribution = tuple(int(value) for value in record['distribution'])
            selected_costs = tuple(
                (int(slot), float(cost)) for slot, cost in record['selected_costs']
            )
            toll_step_offset = (
                (toll - calibration['min_toll']) / calibration['toll_step']
                if calibration['toll_step'] else float('inf')
            )
            if (
                window_start not in valid_slots
                or window_end not in valid_slots
                or window_start > window_end
                or window_start + window_end != center_slot * 2
                or not isfinite(toll)
                or toll < calibration['min_toll'] - 1e-9
                or toll > calibration['max_toll'] + 1e-9
                or abs(toll_step_offset - round(toll_step_offset)) > 1e-9
                or len(distribution) != len(valid_slots)
                or any(count < 0 for count in distribution)
                or sum(distribution) != int(players_count)
                or any(
                    slot not in valid_slots or not isfinite(cost)
                    for slot, cost in selected_costs
                )
            ):
                continue
            return EquilibriumCandidate(
                window_start=window_start,
                window_end=window_end,
                toll=toll,
                cost_gap=float(record.get('cost_gap', 0)),
                nash_count=int(record.get('nash_count', 0)),
                distribution=distribution,
                selected_costs=selected_costs,
                calibration_mode=str(record.get('calibration_mode', 'large-group')),
                deviation_gap=float(record.get('deviation_gap', 0)),
                calibration_source='cache',
            )
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _equilibrium_distribution_summary(candidate, schedule):
    return ', '.join(
        f'{minute_to_clock(departure_minute_for_slot(slot, schedule))}: {count}人'
        for slot, count in zip(departure_slots(schedule), candidate.distribution)
        if count > 0
    )


def _equilibrium_cost_summary(candidate, schedule):
    return ', '.join(
        f'{minute_to_clock(departure_minute_for_slot(slot, schedule))}: {number_display(cost)}'
        for slot, cost in candidate.selected_costs
    )


def _toll_result_from_candidate(candidate, players_count, capacity, schedule):
    start_time = minute_to_clock(departure_minute_for_slot(candidate.window_start, schedule))
    end_time = minute_to_clock(departure_minute_for_slot(candidate.window_end, schedule))
    return {
        'enabled': True,
        'source': TOLL_SOURCE_AUTO,
        'capacity': int(capacity),
        'calibration_players': int(players_count),
        'calibration_mode': candidate.calibration_mode,
        'calibration_source': candidate.calibration_source,
        'slot_spec': candidate.window_spec,
        'time_window_spec': start_time if start_time == end_time else f'{start_time}-{end_time}',
        'points': round(float(candidate.toll), 2),
        'cost_gap': round(float(candidate.cost_gap), 6),
        'deviation_gap': round(float(candidate.deviation_gap), 6),
        'nash_count': int(candidate.nash_count),
        'equilibrium_distribution': _equilibrium_distribution_summary(candidate, schedule),
        'equilibrium_costs': _equilibrium_cost_summary(candidate, schedule),
    }


def calibrate_tolls_for_group(
    *, players_count, capacities, schedule, settings, cache_data=None
):
    calibration = toll_calibration_settings(settings)
    if calibration['toll_step'] <= 0:
        raise ValueError('coarse_toll_auto_toll_step 必须大于 0。')
    if calibration['min_toll'] > calibration['max_toll']:
        raise ValueError('coarse_toll_auto_min_toll 不能大于 coarse_toll_auto_max_toll。')

    from .toll_calibration import CalibrationError, calibrate_best_candidate

    results = {}
    valid_slots = tuple(departure_slots(schedule))
    for capacity in sorted(set(int(value) for value in capacities)):
        candidate = cached_toll_candidate(
            players_count=players_count,
            capacity=capacity,
            schedule=schedule,
            settings=settings,
            cache_data=cache_data,
        )
        if candidate is not None:
            results[str(capacity)] = _toll_result_from_candidate(
                candidate,
                players_count,
                capacity,
                schedule,
            )
            continue
        try:
            candidate = calibrate_best_candidate(
                players=int(players_count),
                capacity=capacity,
                valid_slots=valid_slots,
                first_departure_minute=schedule['first_departure_minute'],
                slot_size_minutes=schedule['slot_size_minutes'],
                **calibration,
            )
        except CalibrationError as exc:
            raise ValueError(
                f'动态粗收费自动校准失败：人数={players_count}, 服务率={capacity}, '
                f"时点数={len(valid_slots)}, 模式={calibration['calibration_mode']}。{exc}"
            ) from exc
        results[str(capacity)] = _toll_result_from_candidate(
            candidate,
            players_count,
            capacity,
            schedule,
        )
    return results


def apply_dynamic_toll_calibrations(session, matrix, config):
    auto_enabled = config_flag(session.config.get('coarse_toll_auto_enabled', 0))
    for group_players in matrix:
        for player in group_players:
            player.participant.vars.pop(TOLL_BY_CAPACITY_VAR, None)
    if not auto_enabled:
        return

    result_cache = {}
    file_cache = load_toll_calibration_cache()
    settings_signature = tuple(sorted(toll_calibration_settings(session.config).items()))
    for group_players in matrix:
        if not group_players:
            continue
        schedule = departure_schedule_for_player(group_players[0])
        cache_key = (
            len(group_players),
            tuple(config.values),
            schedule['num_slots'],
            schedule['first_departure_minute'],
            schedule['slot_size_minutes'],
            settings_signature,
        )
        if cache_key not in result_cache:
            result_cache[cache_key] = calibrate_tolls_for_group(
                players_count=len(group_players),
                capacities=config.values,
                schedule=schedule,
                settings=session.config,
                cache_data=file_cache,
            )
        for player in group_players:
            player.participant.vars[TOLL_BY_CAPACITY_VAR] = result_cache[cache_key]


def _manual_round_toll(player):
    session = player.session
    schedule = departure_schedule_for_player(player)
    enabled = config_flag(session.config.get('coarse_toll_enabled', 0))
    slot_spec = str(session.config.get('coarse_toll_slot_spec', '') or '').strip()
    time_window_spec = str(
        session.config.get('coarse_toll_time_window_spec', '') or ''
    ).strip()
    if enabled:
        if time_window_spec:
            slots = parse_time_window_spec(
                time_window_spec,
                'coarse_toll_time_window_spec',
                schedule,
            )
            slot_spec = (
                str(min(slots))
                if min(slots) == max(slots)
                else f'{min(slots)}-{max(slots)}'
            )
        else:
            parse_slot_spec(slot_spec, 'coarse_toll_slot_spec', departure_slots(schedule))
            slots = sorted(parse_slot_spec(
                slot_spec,
                'coarse_toll_slot_spec',
                departure_slots(schedule),
            ))
            if slots:
                start = minute_to_clock(departure_minute_for_slot(slots[0], schedule))
                end = minute_to_clock(departure_minute_for_slot(slots[-1], schedule))
                time_window_spec = start if start == end else f'{start}-{end}'
    return {
        'enabled': enabled,
        'source': TOLL_SOURCE_MANUAL,
        'capacity': int(player.dynamic_capacity),
        'calibration_players': len(player.group.get_players()),
        'calibration_mode': '',
        'calibration_source': '',
        'slot_spec': slot_spec,
        'time_window_spec': time_window_spec,
        'points': max(0, config_float(session.config.get('coarse_toll_points', 0), 0)),
        'cost_gap': 0,
        'deviation_gap': 0,
        'nash_count': 0,
        'equilibrium_distribution': '',
        'equilibrium_costs': '',
    }


def apply_round_toll(group):
    for player in group.get_players():
        results = player.participant.vars.get(TOLL_BY_CAPACITY_VAR, {})
        result = results.get(str(group.dynamic_capacity)) if isinstance(results, dict) else None
        if not isinstance(result, dict):
            result = _manual_round_toll(player)
        player.coarse_toll_source = result.get('source', TOLL_SOURCE_MANUAL)
        player.coarse_toll_auto_enabled = player.coarse_toll_source == TOLL_SOURCE_AUTO
        player.coarse_toll_calibration_source = result.get('calibration_source', '')
        player.coarse_toll_calibration_mode = result.get('calibration_mode', '')
        player.coarse_toll_calibration_players = int(result.get('calibration_players', 0))
        player.coarse_toll_calibration_capacity = int(result.get('capacity', group.dynamic_capacity))
        player.coarse_toll_calibration_cost_gap = float(result.get('cost_gap', 0))
        player.coarse_toll_calibration_deviation_gap = float(result.get('deviation_gap', 0))
        player.coarse_toll_calibration_nash_count = int(result.get('nash_count', 0))
        player.coarse_toll_equilibrium_distribution = result.get('equilibrium_distribution', '')
        player.coarse_toll_equilibrium_costs = result.get('equilibrium_costs', '')
        player.coarse_toll_enabled = bool(result.get('enabled'))
        player.coarse_toll_slot_spec = result.get('slot_spec', '')
        player.coarse_toll_time_window_spec = result.get('time_window_spec', '')
        player.coarse_toll_points = cu(max(0, float(result.get('points', 0))))


def coarse_toll_slots_for_player(player):
    if not bool(player.coarse_toll_enabled):
        return set()
    schedule = departure_schedule_for_player(player)
    return parse_slot_spec(
        player.coarse_toll_slot_spec,
        'coarse_toll_slot_spec',
        departure_slots(schedule),
    )


def coarse_toll_for_player_slot(player, slot):
    if slot not in coarse_toll_slots_for_player(player):
        return 0
    return float(player.coarse_toll_points)


def coarse_toll_description_for_player(player):
    slots = sorted(coarse_toll_slots_for_player(player))
    if not slots:
        return '当前未开启粗收费。'
    schedule = departure_schedule_for_player(player)
    start = minute_to_clock(departure_minute_for_slot(slots[0], schedule))
    end = minute_to_clock(departure_minute_for_slot(slots[-1], schedule))
    time_window = start if start == end else f'{start}-{end}'
    return f'选择 {time_window} 出发时，需支付 {number_display(player.coarse_toll_points)} 成本。'


def calculate_cost_components(*, queue_delay, early_minutes, late_minutes, toll):
    fixed_cost = float(C.FIXED_TRAVEL_TIME_COST)
    queue_cost = float(C.QUEUE_COST_PER_MINUTE) * float(queue_delay)
    early_cost = float(C.EARLY_COST_PER_MINUTE) * float(early_minutes)
    late_cost = float(C.LATE_COST_PER_MINUTE) * float(late_minutes)
    total_cost = fixed_cost + queue_cost + early_cost + late_cost + float(toll)
    return {
        'fixed_cost': round(fixed_cost, 2),
        'queue_cost': round(queue_cost, 2),
        'early_cost': round(early_cost, 2),
        'late_cost': round(late_cost, 2),
        'toll_cost': round(float(toll), 2),
        'total_cost': round(total_cost, 2),
    }


def creating_session(subsession):
    config = parse_dynamic_capacity_config(subsession.session.config)
    if subsession.round_number == 1:
        validate_toll_reveal_compatibility(subsession.session.config)
        validate_optional_treatments(subsession.session)
        players = subsession.get_players()
        for player in players:
            player.participant.is_dropout = False
            player.participant.dropout_active = False
            player.participant.dropout_reason = ''
            player.participant.has_recovered_after_disconnect = False
            player.participant.has_recovered_after_timeout = False
            player.participant.finished = False

        grouping_enabled = config_flag(subsession.session.config.get('grouping_enabled', 0))
        manual_spec = str(subsession.session.config.get('manual_grouping_spec', '') or '').strip()
        if grouping_enabled:
            matrix = build_manual_group_matrix(players, manual_spec)
        else:
            try:
                cohort_size = int(subsession.session.config.get('cohort_size', 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError('cohort_size 必须是非负整数。') from exc
            if cohort_size < 0:
                raise ValueError('cohort_size 必须是非负整数。')
            matrix = build_auto_group_matrix(players, cohort_size)
        apply_dynamic_departure_schedules(subsession.session, matrix, config)
        apply_dynamic_toll_calibrations(subsession.session, matrix, config)
        subsession.set_group_matrix(matrix)
        assign_group_metadata(matrix, grouping_enabled)
        initialize_group_capacity_sequences(subsession, config)
    else:
        subsession.group_like_round(1)

    for group in subsession.get_groups():
        group.round_start_deadline_ts = 0
        group.round_started_at_ts = 0
        group.round_started = False
        group.decision_deadline_ts = 0
        group.results_ready = False
        apply_round_capacity(group, config)
        apply_round_toll(group)


def maybe_start_round(group, now_ts=None):
    if group.round_started:
        return True
    now_ts = time.time() if now_ts is None else float(now_ts)
    if not group.round_start_deadline_ts:
        group.round_start_deadline_ts = now_ts + round_start_wait_seconds(group.round_number)
    players = group.get_players()
    ready_count = sum(bool(player.round_start_ready) for player in players)
    all_ready = ready_count >= len(players)
    if not should_start_round(
        ready_count=ready_count,
        group_size=len(players),
        now_ts=now_ts,
        deadline_ts=group.round_start_deadline_ts,
    ):
        return False
    effective_start_ts = now_ts if all_ready else group.round_start_deadline_ts
    group.round_started = True
    group.round_started_at_ts = effective_start_ts
    group.decision_deadline_ts = effective_start_ts + C.DECISION_TIMEOUT_SECONDS
    return True


def mark_round_ready(player, now_ts=None):
    player.round_start_ready = True
    return maybe_start_round(player.group, now_ts=now_ts)


def remaining_decision_seconds(group, now_ts=None):
    if not group.round_started or not group.decision_deadline_ts:
        return 0
    now_ts = time.time() if now_ts is None else float(now_ts)
    return max(0, group.decision_deadline_ts - now_ts)


def decision_submission_closed(group, now_ts=None):
    if group.results_ready:
        return True
    now_ts = time.time() if now_ts is None else float(now_ts)
    return bool(
        group.round_started
        and group.decision_deadline_ts
        and now_ts >= group.decision_deadline_ts
    )


def participant_var(player, field_name, default=None):
    return player.participant.vars.get(field_name, default)


def set_participant_var(player, field_name, value):
    setattr(player.participant, field_name, value)
    player.participant.vars[field_name] = value


def participant_dropout_active(player):
    return bool(participant_var(player, 'dropout_active', False))


def participant_dropout_reason(player):
    reason = str(participant_var(player, 'dropout_reason', '') or '').strip()
    return reason if reason in {'timeout', 'disconnect'} else ''


def mark_timeout(player):
    player.timeout_happened = True
    player.dropout_event = 'timeout'
    set_participant_var(player, 'is_dropout', True)
    set_participant_var(player, 'dropout_active', True)
    set_participant_var(player, 'dropout_reason', 'timeout')


def mark_disconnect(player):
    set_participant_var(player, 'is_dropout', True)
    set_participant_var(player, 'dropout_active', True)
    if participant_dropout_reason(player) != 'timeout':
        player.dropout_event = 'disconnect'
        set_participant_var(player, 'dropout_reason', 'disconnect')


def confirm_dropout_recovery(player):
    if not participant_dropout_active(player):
        return False
    reason = participant_dropout_reason(player)
    if not reason:
        return False

    set_participant_var(player, 'dropout_active', False)
    set_participant_var(player, 'dropout_reason', '')
    if reason == 'timeout':
        set_participant_var(player, 'has_recovered_after_timeout', True)
    else:
        set_participant_var(player, 'has_recovered_after_disconnect', True)
    player.recovered_this_round = True
    player.recovery_reason = reason
    return True


def all_players_have_choice(group):
    return all(player_has_departure_choice(player) for player in group.get_players())


def fill_missing_choices(group):
    for player in group.get_players():
        if player_has_departure_choice(player):
            continue
        schedule = departure_schedule_for_player(player)
        slot = random.choice(departure_slots(schedule))
        set_player_departure_choice(
            player,
            departure_minute_for_slot(slot, schedule),
            DECISION_SOURCE_DISCONNECT_AUTO,
        )
        mark_disconnect(player)


def service_batch_clear_minute(first_service_start_minute, load, capacity):
    batches = max(1, ceil(int(load) / int(capacity)))
    return round(first_service_start_minute + batches * C.CAPACITY_WINDOW_MINUTES, 2)


def set_results(group):
    if group.results_ready:
        return
    if not all_players_have_choice(group):
        fill_missing_choices(group)

    players_by_minute = {}
    for player in group.get_players():
        schedule = departure_schedule_for_player(player)
        slot = player_departure_slot(player)
        if slot is None:
            slot = random.choice(departure_slots(schedule))
            set_player_departure_choice(
                player,
                departure_minute_for_slot(slot, schedule),
                DECISION_SOURCE_DISCONNECT_AUTO,
            )
            mark_disconnect(player)
        else:
            set_player_departure_choice(player, departure_minute_for_slot(slot, schedule))
        players_by_minute.setdefault(player.departure_minute, []).append(player)

    reference_schedule = departure_schedule_for_player(group.get_players()[0])
    next_available_minute = reference_schedule['first_departure_minute']
    capacity = group.dynamic_capacity
    for departure_minute in sorted(players_by_minute):
        same_time_players = players_by_minute[departure_minute]
        load = len(same_time_players)
        first_service_start = max(departure_minute, next_available_minute)
        queue_delay = service_batch_wait_minutes(
            departure_minute=departure_minute,
            first_service_start_minute=first_service_start,
            load=load,
            capacity=capacity,
            capacity_window_minutes=C.CAPACITY_WINDOW_MINUTES,
        )
        arrival_minute = departure_minute + C.FREE_FLOW_TRAVEL_MINUTES + queue_delay
        early_minutes = max(0, C.PREFERRED_ARRIVAL_MINUTE - arrival_minute)
        late_minutes = max(0, arrival_minute - C.PREFERRED_ARRIVAL_MINUTE)

        for player in same_time_players:
            slot = player_departure_slot(player)
            reward_bonus = reward_bonus_for_slot(group.session, slot)
            toll = coarse_toll_for_player_slot(player, slot)
            cost_components = calculate_cost_components(
                queue_delay=queue_delay,
                early_minutes=early_minutes,
                late_minutes=late_minutes,
                toll=toll,
            )
            total_cost = cost_components['total_cost']
            payoff = max(0, round(C.BASE_POINTS - total_cost + reward_bonus, 2))
            player.slot_load = load
            player.arrival_minute = round(arrival_minute, 2)
            player.arrival_time_label = minute_to_clock(arrival_minute)
            player.queue_delay_minutes = queue_delay
            player.travel_time_minutes = round(C.FREE_FLOW_TRAVEL_MINUTES + queue_delay, 2)
            player.early_minutes = round(early_minutes, 2)
            player.late_minutes = round(late_minutes, 2)
            player.reward_bonus = cu(reward_bonus)
            player.coarse_toll_charge = cu(toll)
            player.total_cost = cu(total_cost)
            player.payoff = cu(payoff)

        next_available_minute = service_batch_clear_minute(first_service_start, load, capacity)

    if group.round_number == C.NUM_ROUNDS:
        for player in group.get_players():
            player.participant.vars[TOTAL_PAYOFF_VAR] = sum(
                round_player.payoff for round_player in player.in_all_rounds()
            )
    group.results_ready = True


def maybe_prepare_results(group):
    if not maybe_start_round(group):
        return
    if group.results_ready:
        return
    if all_players_have_choice(group):
        set_results(group)
    elif time.time() >= group.decision_deadline_ts:
        fill_missing_choices(group)
        set_results(group)


def access_allowed(player):
    if player.session.config.get('name') != 'dynamic_bottleneck_round_prod':
        return True
    return bool(player.participant.vars.get('access_granted'))


def capacity_state_rows(config):
    return [
        {
            'capacity': capacity,
            'probability': probability,
            'probability_label': probability_display(probability),
            'state': f'capacity_{capacity}',
        }
        for capacity, probability in zip(config.values, config.probabilities)
    ]


def choice_preview(player):
    session = player.session
    schedule = departure_schedule_for_player(player)
    tolled_slots = coarse_toll_slots_for_player(player)
    rewarded_slots = parse_slot_spec(
        session.config.get('rewarded_slot_spec', ''),
        'rewarded_slot_spec',
    ) if config_flag(session.config.get('reward_treatment_enabled', 0)) else set()
    toll = float(player.coarse_toll_points)
    reward = float(session.config.get('reward_bonus_points', 0))
    return [
        {
            'slot': slot,
            'minute': departure_minute_for_slot(slot, schedule),
            'time': minute_to_clock(departure_minute_for_slot(slot, schedule)),
            'toll': toll if slot in tolled_slots else 0,
            'toll_active': slot in tolled_slots,
            'toll_charge_label': number_display(toll) if slot in tolled_slots else '0',
            'reward': reward if slot in rewarded_slots else 0,
        }
        for slot in departure_slots(schedule)
    ]


def result_cost_components(player):
    return calculate_cost_components(
        queue_delay=player.queue_delay_minutes,
        early_minutes=player.early_minutes,
        late_minutes=player.late_minutes,
        toll=float(player.coarse_toll_charge),
    )


def group_departure_distribution(group):
    players = group.get_players()
    schedule = departure_schedule_for_player(players[0]) if players else static_departure_schedule()
    counts = {slot: 0 for slot in departure_slots(schedule)}
    for player in group.get_players():
        slot = player_departure_slot(player)
        if slot in counts:
            counts[slot] += 1
    maximum = max(counts.values() or [0])
    return [
        {
            'slot': slot,
            'time': minute_to_clock(departure_minute_for_slot(slot, schedule)),
            'count': counts[slot],
            'height_pct': round((counts[slot] / maximum) * 100, 2) if maximum else 0,
        }
        for slot in departure_slots(schedule)
    ]


def result_current_round_cost_snapshot(player):
    group_players = player.group.get_players()
    schedule = departure_schedule_for_player(player)
    costs_by_slot = {slot: [] for slot in departure_slots(schedule)}
    for group_player in group_players:
        slot = player_departure_slot(group_player)
        if slot in costs_by_slot and player_has_departure_choice(group_player):
            costs_by_slot[slot].append(float(group_player.total_cost))

    averages = [
        sum(values) / len(values)
        for values in costs_by_slot.values()
        if values
    ]
    group_average = (
        sum(sum(values) for values in costs_by_slot.values())
        / sum(len(values) for values in costs_by_slot.values())
        if averages else 0
    )
    axis_max = max(5, ceil(max(averages + [group_average, 0]) / 5) * 5)
    current_slot = player_departure_slot(player)
    bars = []
    for slot in departure_slots(schedule):
        values = costs_by_slot[slot]
        average_cost = sum(values) / len(values) if values else 0
        bars.append(
            {
                'slot': slot,
                'departure_time': minute_to_clock(departure_minute_for_slot(slot, schedule)),
                'participant_count': len(values),
                'has_participants': bool(values),
                'average_cost_label': number_display(average_cost) if values else '-',
                'height_pct': round((average_cost / axis_max) * 100, 2) if values else 0,
                'is_current': slot == current_slot,
            }
        )
    return {
        'bars': bars,
        'average_cost_label': number_display(group_average),
        'average_line_pct': round((group_average / axis_max) * 100, 2) if axis_max else 0,
        'axis_max_label': number_display(axis_max),
    }


def export_row_for_player(player):
    schedule = departure_schedule_for_player(player)
    return [
        player.session.code,
        player.participant.code,
        player.group.id_in_subsession,
        player.round_number,
        player.dynamic_capacity,
        player.dynamic_capacity_state,
        player.previous_round_capacity if player.round_number > 1 else '',
        player.capacity_probability,
        player.capacity_reveal_timing,
        player.dynamic_capacity_seed,
        player.dynamic_capacity_draw_mode,
        player.coarse_toll_source,
        player.coarse_toll_auto_enabled,
        player.coarse_toll_calibration_source,
        player.coarse_toll_calibration_mode,
        player.coarse_toll_calibration_players,
        player.coarse_toll_calibration_capacity,
        player.coarse_toll_calibration_cost_gap,
        player.coarse_toll_calibration_deviation_gap,
        player.coarse_toll_calibration_nash_count,
        player.coarse_toll_equilibrium_distribution,
        player.coarse_toll_equilibrium_costs,
        player.coarse_toll_enabled,
        player.coarse_toll_slot_spec,
        player.coarse_toll_time_window_spec,
        player.coarse_toll_points,
        player.coarse_toll_charge,
        schedule.get('source', ''),
        schedule.get('capacity_basis', ''),
        schedule.get('num_slots', ''),
        schedule.get('first_departure_time', ''),
        schedule.get('last_departure_time', ''),
        player.field_maybe_none('departure_slot') or '',
        player.field_maybe_none('departure_minute') or '',
        player.queue_delay_minutes,
        player.arrival_minute,
        player.early_minutes,
        player.late_minutes,
        player.total_cost,
        player.payoff,
        player.decision_source,
        player.timeout_happened,
        player.dropout_event,
        player.recovered_this_round,
        player.recovery_reason,
        bool(participant_var(player, 'is_dropout', False)),
        participant_dropout_active(player),
        participant_dropout_reason(player),
        bool(participant_var(player, 'has_recovered_after_disconnect', False)),
        bool(participant_var(player, 'has_recovered_after_timeout', False)),
    ]


def build_admin_report_rows(players):
    groups = {}
    state_counts = {}
    for player in players:
        key = (player.round_number, player.group.id_in_subsession)
        groups.setdefault(key, []).append(player)
    round_rows = []
    for (round_number, group_id), group_players in sorted(groups.items()):
        representative = group_players[0]
        state_counts[representative.dynamic_capacity_state] = (
            state_counts.get(representative.dynamic_capacity_state, 0) + 1
        )
        completed = [player for player in group_players if player_has_departure_choice(player)]
        average_queue = (
            sum(player.queue_delay_minutes for player in completed) / len(completed)
            if completed else 0
        )
        average_cost = (
            sum(float(player.total_cost) for player in completed) / len(completed)
            if completed else 0
        )
        average_toll = (
            sum(float(player.coarse_toll_charge) for player in completed) / len(completed)
            if completed else 0
        )
        distribution = {}
        for player in completed:
            label = minute_to_clock(player.departure_minute)
            distribution[label] = distribution.get(label, 0) + 1
        round_rows.append(
            {
                'round_number': round_number,
                'group_id': group_id,
                'dynamic_capacity': representative.dynamic_capacity,
                'dynamic_capacity_state': representative.dynamic_capacity_state,
                'capacity_probability': probability_display(representative.capacity_probability),
                'average_queue_delay': number_display(average_queue),
                'average_cost': number_display(average_cost),
                'coarse_toll_time_window_spec': representative.coarse_toll_time_window_spec or '无',
                'coarse_toll_points': number_display(representative.coarse_toll_points),
                'average_toll': number_display(average_toll),
                'departure_distribution': ', '.join(
                    f'{time_label}: {count}' for time_label, count in sorted(distribution.items())
                ) or '暂无决策',
            }
        )
    state_rows = [
        {'state': state, 'count': count}
        for state, count in sorted(state_counts.items())
    ]
    return round_rows, state_rows


def vars_for_admin_report(subsession):
    players = []
    for round_subsession in subsession.in_all_rounds():
        players.extend(round_subsession.get_players())
    round_rows, state_rows = build_admin_report_rows(players)
    return {
        'round_rows': round_rows,
        'state_rows': state_rows,
        'export_headers': EXPORT_HEADERS,
        'session_code': subsession.session.code,
    }


class Introduction(Page):
    @staticmethod
    def is_displayed(player):
        return player.round_number == 1 and access_allowed(player)

    @staticmethod
    def vars_for_template(player):
        config = parse_dynamic_capacity_config(player.session.config)
        schedule = departure_schedule_for_player(player)
        return {
            'capacity_states': capacity_state_rows(config),
            'draw_mode': config.draw_mode,
            'total_rounds': C.NUM_ROUNDS,
            'capacity_window_minutes': C.CAPACITY_WINDOW_MINUTES,
            'preferred_arrival_time': minute_to_clock(C.PREFERRED_ARRIVAL_MINUTE),
            'free_flow_travel_minutes': C.FREE_FLOW_TRAVEL_MINUTES,
            'fixed_travel_cost': number_display(C.FIXED_TRAVEL_TIME_COST),
            'queue_cost_per_minute': number_display(C.QUEUE_COST_PER_MINUTE),
            'early_cost_per_minute': number_display(C.EARLY_COST_PER_MINUTE),
            'late_cost_per_minute': number_display(C.LATE_COST_PER_MINUTE),
            'departure_time_min': schedule['first_departure_time'],
            'departure_time_max': schedule['last_departure_time'],
            'coarse_toll_description': coarse_toll_description_for_player(player),
            'capacity_frequency_label': (
                '目标比例' if config.draw_mode == DRAW_MODE_BALANCED else '每轮抽取概率'
            ),
            'capacity_reveal_description': capacity_reveal_description(config),
        }


class ComprehensionCheck(Page):
    @staticmethod
    def is_displayed(player):
        return player.round_number == 1 and access_allowed(player)

    @staticmethod
    def vars_for_template(player):
        config = parse_dynamic_capacity_config(player.session.config)
        queue_example = comprehension_queue_example(config)
        example_people = queue_example['people']
        example_capacity = queue_example['capacity']
        example_departure_minute = queue_example['departure_minute']
        example_wait = queue_example['wait_minutes']
        example_arrival_without_queue = queue_example['arrival_without_queue_minute']
        example_arrival_with_short_wait = queue_example[
            'arrival_with_short_wait_minute'
        ]
        example_arrival = queue_example['arrival_minute']
        example_queue_minutes = 4
        example_toll = max(3, float(player.coarse_toll_points))
        return {
            'capacity_states': capacity_state_rows(config),
            'example_people': example_people,
            'example_capacity': example_capacity,
            'example_wait': number_display(example_wait),
            'example_same_departure_people': example_people,
            'example_capacity_per_slot': example_capacity,
            'example_capacity_window_minutes': number_display(C.CAPACITY_WINDOW_MINUTES),
            'example_departure_time': minute_to_clock(example_departure_minute),
            'example_same_departure_wait_minutes': number_display(example_wait),
            'example_arrival_without_queue_time': minute_to_clock(
                example_arrival_without_queue
            ),
            'example_arrival_with_short_wait_time': minute_to_clock(
                example_arrival_with_short_wait
            ),
            'example_arrival_time': minute_to_clock(example_arrival),
            'example_arrival': minute_to_clock(example_arrival),
            'preferred_arrival_time': minute_to_clock(C.PREFERRED_ARRIVAL_MINUTE),
            'free_flow_travel_minutes': number_display(C.FREE_FLOW_TRAVEL_MINUTES),
            'queue_cost_per_minute': number_display(C.QUEUE_COST_PER_MINUTE),
            'early_cost_per_minute': number_display(C.EARLY_COST_PER_MINUTE),
            'late_cost_per_minute': number_display(C.LATE_COST_PER_MINUTE),
            'example_queue_minutes': number_display(example_queue_minutes),
            'example_queue_cost': number_display(
                example_queue_minutes * C.QUEUE_COST_PER_MINUTE
            ),
            'example_early_minutes': 3,
            'example_early_cost': number_display(3 * C.EARLY_COST_PER_MINUTE),
            'example_toll': number_display(example_toll),
            'example_total_with_toll': number_display(10 + example_toll),
            'coarse_toll_description': coarse_toll_description_for_player(player),
        }

    @staticmethod
    def before_next_page(player, timeout_happened):
        player.participant.vars[COMPREHENSION_SEEN_VAR] = True


class RoundStartSync(Page):
    @staticmethod
    def is_displayed(player):
        if not access_allowed(player):
            return False
        mark_round_ready(player)
        return True

    @staticmethod
    def vars_for_template(player):
        mark_round_ready(player)
        group = player.group
        now_ts = time.time()
        maybe_start_round(group, now_ts=now_ts)
        ready_count = sum(
            bool(group_player.round_start_ready)
            for group_player in group.get_players()
        )
        return {
            'round_number': player.round_number,
            'total_rounds': C.NUM_ROUNDS,
            'round_started': group.round_started,
            'ready_count': ready_count,
            'group_size': len(group.get_players()),
            'remaining_seconds': max(
                0,
                ceil(group.round_start_deadline_ts - now_ts),
            ),
            'poll_interval_ms': C.SYNC_POLL_INTERVAL_SECONDS * 1000,
            'auto_continue_delay_ms': C.AUTO_CONTINUE_DELAY_MS,
        }

    @staticmethod
    def before_next_page(player, timeout_happened):
        mark_round_ready(player)


class Decision(Page):
    form_model = 'player'
    form_fields = ['departure_minute']

    @staticmethod
    def is_displayed(player):
        if not access_allowed(player):
            return False
        if not maybe_start_round(player.group):
            return False
        maybe_prepare_results(player.group)
        if player.group.results_ready or player_has_departure_choice(player):
            return False
        return True

    @staticmethod
    def get_timeout_seconds(player):
        maybe_prepare_results(player.group)
        if player.participant.vars.get('dropout_active'):
            return C.DROPOUT_TIMEOUT_SECONDS
        return min(C.DECISION_TIMEOUT_SECONDS, remaining_decision_seconds(player.group))

    @staticmethod
    def vars_for_template(player):
        config = parse_dynamic_capacity_config(player.session.config)
        context = decision_capacity_context(config, actual_capacity=player.dynamic_capacity)
        schedule = departure_schedule_for_player(player)
        preview = choice_preview(player)
        return {
            **context,
            'round_number': player.round_number,
            'total_rounds': C.NUM_ROUNDS,
            'capacity_window_minutes': C.CAPACITY_WINDOW_MINUTES,
            'departure_time_min': schedule['first_departure_time'],
            'departure_time_max': schedule['last_departure_time'],
            'default_departure_minute': C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES,
            'default_departure_time': minute_to_clock(
                C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES
            ),
            'choice_preview': preview,
            'choice_preview_json': json.dumps(
                {str(int(item['minute'])): item for item in preview},
                ensure_ascii=False,
            ),
            'auto_advance_seconds': Decision.get_timeout_seconds(player),
            'coarse_toll_description': coarse_toll_description_for_player(player),
        }

    @staticmethod
    def error_message(player, values):
        if decision_submission_closed(player.group):
            maybe_prepare_results(player.group)
            return '本轮决策时间已结束，系统将使用截止时的有效决策。'
        if departure_slot_for_minute(
            values.get('departure_minute'),
            departure_schedule_for_player(player),
        ) is None:
            return '请选择时间范围内、1 分钟精度的有效出发时间。'

    @staticmethod
    def before_next_page(player, timeout_happened):
        if decision_submission_closed(player.group):
            maybe_prepare_results(player.group)
            return
        if timeout_happened and not player_has_departure_choice(player):
            schedule = departure_schedule_for_player(player)
            slot = random.choice(departure_slots(schedule))
            set_player_departure_choice(
                player,
                departure_minute_for_slot(slot, schedule),
                DECISION_SOURCE_TIMEOUT_AUTO,
            )
            mark_timeout(player)
            return
        set_player_departure_choice(
            player,
            player.field_maybe_none('departure_minute'),
            DECISION_SOURCE_MANUAL,
        )
        player.timeout_happened = False


class RecoveryGate(Page):
    @staticmethod
    def is_displayed(player):
        return access_allowed(player) and participant_dropout_active(player)

    @staticmethod
    def vars_for_template(player):
        reason = participant_dropout_reason(player)
        return {
            'round_number': player.round_number,
            'recovery_reason_label': '决策超时' if reason == 'timeout' else '未按时提交',
            'automatic_departure_time': player.departure_time_label or '等待系统补选',
        }

    @staticmethod
    def before_next_page(player, timeout_happened):
        confirm_dropout_recovery(player)


class ResultsSync(Page):
    @staticmethod
    def is_displayed(player):
        return access_allowed(player)

    @staticmethod
    def vars_for_template(player):
        maybe_prepare_results(player.group)
        return {
            'results_ready': player.group.results_ready,
            'remaining_seconds': max(0, ceil(player.group.decision_deadline_ts - time.time())),
            'poll_interval_ms': C.SYNC_POLL_INTERVAL_SECONDS * 1000,
            'auto_continue_delay_ms': C.AUTO_CONTINUE_DELAY_MS,
        }

    @staticmethod
    def before_next_page(player, timeout_happened):
        maybe_prepare_results(player.group)


class Results(Page):
    @staticmethod
    def is_displayed(player):
        return access_allowed(player)

    @staticmethod
    def get_timeout_seconds(player):
        if participant_dropout_active(player):
            return C.DROPOUT_TIMEOUT_SECONDS
        return C.RESULTS_TIMEOUT_SECONDS

    @staticmethod
    def vars_for_template(player):
        maybe_prepare_results(player.group)
        components = result_cost_components(player)
        snapshot = result_current_round_cost_snapshot(player)
        return {
            'dynamic_capacity': player.dynamic_capacity,
            'dynamic_capacity_state': player.dynamic_capacity_state,
            'capacity_probability': probability_display(player.capacity_probability),
            'capacity_window_minutes': C.CAPACITY_WINDOW_MINUTES,
            'previous_capacity_label': (
                str(player.previous_round_capacity) if player.round_number > 1 else '无'
            ),
            'departure_time': player.departure_time_label,
            'arrival_time': player.arrival_time_label,
            'queue_delay': number_display(player.queue_delay_minutes),
            'travel_time': number_display(player.travel_time_minutes),
            'early_minutes': number_display(player.early_minutes),
            'late_minutes': number_display(player.late_minutes),
            'fixed_cost': number_display(components['fixed_cost']),
            'queue_cost': number_display(components['queue_cost']),
            'early_cost': number_display(components['early_cost']),
            'late_cost': number_display(components['late_cost']),
            'toll_cost': number_display(components['toll_cost']),
            'total_cost': number_display(components['total_cost']),
            'reward_bonus': number_display(float(player.reward_bonus)),
            'payoff_value': number_display(float(player.payoff)),
            'departure_distribution': group_departure_distribution(player.group),
            'slot_load': player.slot_load,
            'coarse_toll_description': coarse_toll_description_for_player(player),
            'cost_snapshot_bars': snapshot['bars'],
            'cost_snapshot_average_cost_label': snapshot['average_cost_label'],
            'cost_snapshot_average_line_pct': snapshot['average_line_pct'],
            'cost_snapshot_axis_max_label': snapshot['axis_max_label'],
            'auto_advance_seconds': Results.get_timeout_seconds(player),
        }


def custom_export(players):
    yield EXPORT_HEADERS
    for player in players:
        yield export_row_for_player(player)


page_sequence = [
    Introduction,
    ComprehensionCheck,
    RoundStartSync,
    Decision,
    RecoveryGate,
    ResultsSync,
    Results,
]
