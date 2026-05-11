from math import ceil
import json
import random
import time

from otree.api import *


doc = """
离散化的单瓶颈出发时间实验。
参与者在每轮选择出发时间，系统根据瓶颈通行能力形成排队，
并按照排队延误、早到/晚到惩罚、可选奖励处理和粗收费处理计算收益。
"""

DECISION_SOURCE_MANUAL = 'manual'
DECISION_SOURCE_TIMEOUT_AUTO = 'timeout_auto'
DECISION_SOURCE_DISCONNECT_AUTO = 'disconnect_auto'

DROPOUT_REASON_TIMEOUT = 'timeout'
DROPOUT_REASON_DISCONNECT = 'disconnect'

COARSE_TOLL_SOURCE_MANUAL = 'manual'
COARSE_TOLL_SOURCE_AUTO = 'auto'
COARSE_TOLL_AUTO_RESULT_VAR = 'single_bottleneck_coarse_toll_auto_result'

DEPARTURE_SCHEDULE_SOURCE_STATIC = 'static'
DEPARTURE_SCHEDULE_SOURCE_AUTO = 'auto'
DEPARTURE_SCHEDULE_VAR = 'single_bottleneck_departure_schedule'


class C(BaseConstants):
    NAME_IN_URL = 'single_bottleneck'
    PLAYERS_PER_GROUP = None
    NUM_ROUNDS = 10
    DECISION_TIMEOUT_SECONDS = 45
    RESULTS_TIMEOUT_SECONDS = 30
    DROPOUT_TIMEOUT_SECONDS = 1
    ROUND1_JOIN_GRACE_SECONDS = 20
    WAIT_GRACE_SECONDS = 5
    SYNC_POLL_INTERVAL_SECONDS = 1
    AUTO_CONTINUE_DELAY_MS = 200

    PREFERRED_ARRIVAL_MINUTE = 8 * 60
    FREE_FLOW_TRAVEL_MINUTES = 6
    SLOT_SIZE_MINUTES = 2
    NUM_DEPARTURE_SLOTS = 11
    MAX_DEPARTURE_SLOT_CHOICES = 401
    FIRST_DEPARTURE_MINUTE = (
        PREFERRED_ARRIVAL_MINUTE
        - FREE_FLOW_TRAVEL_MINUTES
        - SLOT_SIZE_MINUTES * 5
    )
    DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT = 1

    BASE_POINTS = 140
    QUEUE_COST_PER_MINUTE = 2
    EARLY_COST_PER_MINUTE = 1
    LATE_COST_PER_MINUTE = 3
    DEFAULT_REWARD_BONUS_POINTS = 0
    DEFAULT_COARSE_TOLL_AUTO_ENABLED = 0
    DEFAULT_COARSE_TOLL_AUTO_MIN_TOLL = 0
    DEFAULT_COARSE_TOLL_AUTO_MAX_TOLL = 40
    DEFAULT_COARSE_TOLL_AUTO_TOLL_STEP = 1
    DEFAULT_COARSE_TOLL_ENABLED = 1
    DEFAULT_COARSE_TOLL_SLOT_SPEC = '4-8'
    DEFAULT_COARSE_TOLL_POINTS = 8
    DEFAULT_DEPARTURE_SCHEDULE_AUTO_ENABLED = 0
    DEFAULT_DEPARTURE_SCHEDULE_MIN_SLOTS_EACH_SIDE = 5


def minute_to_clock(value):
    total_seconds = int(round(float(value) * 60))
    hours = (total_seconds // 3600) % 24
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if seconds:
        return f'{hours:02d}:{minutes:02d}:{seconds:02d}'
    return f'{hours:02d}:{minutes:02d}'


def minute_value_display(value):
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return '0'

    rounded = round(numeric_value, 1)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    return f'{rounded:.1f}'


def point_value_display(value):
    return minute_value_display(value)


def default_slots_each_side():
    return max(0, (C.NUM_DEPARTURE_SLOTS - 1) // 2)


def departure_slots():
    return list(range(1, C.NUM_DEPARTURE_SLOTS + 1))


def departure_slots_from_schedule(schedule):
    return list(range(1, int(schedule.get('num_slots', C.NUM_DEPARTURE_SLOTS)) + 1))


def departure_minute_for_slot(slot: int, schedule=None):
    if schedule:
        first_minute = parse_float(schedule.get('first_departure_minute'), C.FIRST_DEPARTURE_MINUTE)
        slot_size = parse_float(schedule.get('slot_size_minutes'), C.SLOT_SIZE_MINUTES)
        return first_minute + (slot - 1) * slot_size
    return C.FIRST_DEPARTURE_MINUTE + (slot - 1) * C.SLOT_SIZE_MINUTES


def free_flow_departure_minute():
    return C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES


def departure_offset_from_free_flow(slot: int, schedule=None):
    return int(round(departure_minute_for_slot(slot, schedule) - free_flow_departure_minute()))


def departure_relation_for_slot(slot: int, schedule=None):
    offset = departure_offset_from_free_flow(slot, schedule)
    if offset == 0:
        return '等于准时到达且无排队延误时所需出发时间'
    if offset < 0:
        return f'相对于准时到达且无排队延误时所需出发时间，提前 {abs(offset)} 分钟'
    return f'相对于准时到达且无排队延误时所需出发时间，延后 {offset} 分钟'


def departure_label_for_slot(slot: int, schedule=None):
    departure_time = minute_to_clock(departure_minute_for_slot(slot, schedule))
    return f'{departure_time}（{departure_relation_for_slot(slot, schedule)}）'


DEPARTURE_SLOT_CHOICES = [
    [slot, str(slot)]
    for slot in range(1, C.MAX_DEPARTURE_SLOT_CHOICES + 1)
]


class Subsession(BaseSubsession):
    pass


class Group(BaseGroup):
    decision_deadline_ts = models.FloatField(initial=0)
    results_ready = models.BooleanField(initial=False)


class Player(BasePlayer):
    departure_slot = models.IntegerField(
        choices=DEPARTURE_SLOT_CHOICES,
        widget=widgets.RadioSelect,
        label='请选择你的出发时间',
    )
    departure_time_label = models.StringField(blank=True)
    arrival_time_label = models.StringField(blank=True)
    departure_minute = models.FloatField(initial=0)
    arrival_minute = models.FloatField(initial=0)
    queue_delay_minutes = models.FloatField(initial=0)
    travel_time_minutes = models.FloatField(initial=0)
    schedule_early_minutes = models.FloatField(initial=0)
    schedule_late_minutes = models.FloatField(initial=0)
    slot_load = models.IntegerField(initial=0)
    reward_bonus = models.CurrencyField(initial=0)
    decision_source = models.StringField(blank=True)


EXPORT_HEADERS = [
    'session_code',
    'session_config_name',
    'data_tier',
    'grouping_enabled',
    'manual_grouping_spec',
    'reward_treatment_enabled',
    'rewarded_slot_spec',
    'reward_bonus_points',
    'coarse_toll_source',
    'coarse_toll_auto_enabled',
    'coarse_toll_calibration_players',
    'coarse_toll_calibration_cost_gap',
    'coarse_toll_calibration_nash_count',
    'coarse_toll_equilibrium_distribution',
    'coarse_toll_equilibrium_costs',
    'coarse_toll_enabled',
    'coarse_toll_slot_spec',
    'coarse_toll_points',
    'bottleneck_capacity_per_slot',
    'departure_schedule_source',
    'departure_schedule_auto_enabled',
    'departure_schedule_calibration_players',
    'departure_schedule_capacity',
    'departure_schedule_slot_count',
    'departure_schedule_first_time',
    'departure_schedule_last_time',
    'assigned_group_id',
    'assigned_group_label',
    'assigned_group_members',
    'participant_code',
    'participant_label',
    'round_number',
    'departure_slot',
    'departure_time_label',
    'decision_source',
    'arrival_time_label',
    'queue_delay_minutes',
    'travel_time_minutes',
    'schedule_early_minutes',
    'schedule_late_minutes',
    'slot_load',
    'reward_bonus',
    'coarse_toll_charge',
    'travel_cost_without_toll',
    'choice_cost_with_toll',
    'payoff',
    'final_total_payoff',
    'is_dropout',
    'dropout_active',
    'dropout_reason',
    'has_recovered_after_disconnect',
    'finished',
]


def config_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return False


def parse_int(value, default: int):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_float(value, default: float):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def participant_display_label(player: Player) -> str:
    return player.participant.label or player.participant.code


def safe_model_field(obj, field_name, default=''):
    value = obj.field_maybe_none(field_name) if hasattr(obj, 'field_maybe_none') else getattr(obj, field_name, None)
    return default if value is None else value


def participant_var(player: Player, field_name, default=''):
    return player.participant.vars.get(field_name, default)


def departure_schedule_auto_enabled(session) -> bool:
    return config_flag(
        session.config.get(
            'departure_schedule_auto_enabled',
            C.DEFAULT_DEPARTURE_SCHEDULE_AUTO_ENABLED,
        )
    )


def departure_schedule_min_slots_each_side(session):
    return max(
        0,
        parse_int(
            session.config.get(
                'departure_schedule_min_slots_each_side',
                C.DEFAULT_DEPARTURE_SCHEDULE_MIN_SLOTS_EACH_SIDE,
            ),
            C.DEFAULT_DEPARTURE_SCHEDULE_MIN_SLOTS_EACH_SIDE,
        ),
    )


def build_departure_schedule_record(
    *,
    players_count: int,
    capacity: int,
    min_slots_each_side: int,
    auto_enabled: bool,
):
    if auto_enabled:
        required_occupied_slots = ceil(players_count / capacity)
        slots_each_side = max(min_slots_each_side, ceil(required_occupied_slots / 2))
        source = DEPARTURE_SCHEDULE_SOURCE_AUTO
    else:
        required_occupied_slots = ''
        slots_each_side = default_slots_each_side()
        source = DEPARTURE_SCHEDULE_SOURCE_STATIC

    num_slots = slots_each_side * 2 + 1
    first_departure_minute = free_flow_departure_minute() - slots_each_side * C.SLOT_SIZE_MINUTES
    last_departure_minute = first_departure_minute + (num_slots - 1) * C.SLOT_SIZE_MINUTES

    return dict(
        enabled=True,
        source=source,
        auto_enabled=auto_enabled,
        calibration_players=players_count if auto_enabled else '',
        capacity=capacity,
        min_slots_each_side=min_slots_each_side,
        required_occupied_slots=required_occupied_slots,
        slots_each_side=slots_each_side,
        num_slots=num_slots,
        slot_size_minutes=C.SLOT_SIZE_MINUTES,
        first_departure_minute=round(first_departure_minute, 2),
        last_departure_minute=round(last_departure_minute, 2),
        first_departure_time=minute_to_clock(first_departure_minute),
        last_departure_time=minute_to_clock(last_departure_minute),
    )


def static_departure_schedule_record():
    return build_departure_schedule_record(
        players_count=0,
        capacity=C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT,
        min_slots_each_side=default_slots_each_side(),
        auto_enabled=False,
    )


def departure_schedule_for_player(player: Player):
    schedule = player.participant.vars.get(DEPARTURE_SCHEDULE_VAR, {})
    if isinstance(schedule, dict) and schedule.get('enabled'):
        return schedule
    return static_departure_schedule_record()


def departure_slots_for_player(player: Player):
    return departure_slots_from_schedule(departure_schedule_for_player(player))


def departure_slots_for_group(group: Group):
    players = group.get_players()
    if not players:
        return departure_slots()
    return departure_slots_for_player(players[0])


def departure_minute_for_player_slot(player: Player, slot: int):
    return departure_minute_for_slot(slot, departure_schedule_for_player(player))


def departure_minute_for_group_slot(group: Group, slot: int):
    players = group.get_players()
    if not players:
        return departure_minute_for_slot(slot)
    return departure_minute_for_player_slot(players[0], slot)


def player_departure_slot(player: Player):
    slot = player.field_maybe_none('departure_slot') if hasattr(player, 'field_maybe_none') else None
    return slot if slot in departure_slots_for_player(player) else None


def player_has_departure_slot(player: Player) -> bool:
    return player_departure_slot(player) is not None


def cumulative_payoff_so_far(player: Player):
    total_payoff = cu(0)
    for round_player in player.in_rounds(1, player.round_number):
        total_payoff += safe_model_field(round_player, 'payoff', cu(0))
    return total_payoff


def bounded_percent(value, max_value):
    try:
        numeric_value = float(value)
        numeric_max = float(max_value)
    except (TypeError, ValueError):
        return 0

    if numeric_max <= 0:
        return 0

    percentage = numeric_value / numeric_max * 100
    return max(0, min(100, round(percentage, 1)))


def build_auto_group_matrix(players, cohort_size: int):
    return [
        players[index:index + cohort_size]
        for index in range(0, len(players), cohort_size)
    ]


def parse_manual_grouping_spec(spec: str):
    raw_spec = (spec or '').strip()
    if not raw_spec:
        raise ValueError(
            '启用手动分组时，manual_grouping_spec 不能为空。示例：P001,P003|P002,P004|P005'
        )

    groups = []
    for raw_group in raw_spec.split('|'):
        raw_labels = [label.strip() for label in raw_group.split(',')]
        if any(not label for label in raw_labels):
            raise ValueError(
                'manual_grouping_spec 中存在空标签。请使用格式：P001,P003|P002,P004|P005'
            )
        groups.append(raw_labels)
    return groups


def validate_manual_groups(players, manual_groups, session_name: str):
    if session_name != 'single_bottleneck_prod':
        raise ValueError('仅正式场次 single_bottleneck_prod 支持按 participant_label 手动分组。')

    available_labels = []
    label_to_player = {}
    missing_labels = []
    for player in players:
        label = player.participant.label
        if not label:
            missing_labels.append(player.participant.code)
            continue
        if label in label_to_player:
            raise ValueError(f'participant_label 重复：{label}。请检查房间标签配置。')
        available_labels.append(label)
        label_to_player[label] = player

    if missing_labels:
        raise ValueError(
            '检测到缺少 participant_label 的参与者，无法执行手动分组：'
            + ', '.join(missing_labels)
        )

    configured_labels = [label for group_labels in manual_groups for label in group_labels]
    duplicate_labels = sorted({label for label in configured_labels if configured_labels.count(label) > 1})
    if duplicate_labels:
        raise ValueError(
            'manual_grouping_spec 中存在重复标签：' + ', '.join(duplicate_labels)
        )

    unknown_labels = sorted(set(configured_labels) - set(available_labels))
    if unknown_labels:
        raise ValueError(
            'manual_grouping_spec 中存在未知标签：' + ', '.join(unknown_labels)
        )

    missing_configured = sorted(set(available_labels) - set(configured_labels))
    if missing_configured:
        raise ValueError(
            'manual_grouping_spec 漏掉了以下标签：' + ', '.join(missing_configured)
        )

    return label_to_player


def build_manual_group_matrix(players, spec: str, session_name: str):
    manual_groups = parse_manual_grouping_spec(spec)
    label_to_player = validate_manual_groups(players, manual_groups, session_name)
    return [[label_to_player[label] for label in group_labels] for group_labels in manual_groups]


def assign_group_metadata(matrix, grouping_enabled: bool):
    for group_index, group_players in enumerate(matrix, start=1):
        member_labels = ','.join(participant_display_label(player) for player in group_players)
        group_label = f'G{group_index:02d}'
        for player in group_players:
            participant = player.participant
            participant.vars['grouping_enabled'] = grouping_enabled
            participant.vars['assigned_group_id'] = group_index
            participant.vars['assigned_group_label'] = group_label
            participant.vars['assigned_group_members'] = member_labels


def parse_slot_spec(spec: str, field_name: str, valid_slots=None):
    if valid_slots is None:
        valid_slots = departure_slots()
    valid_slot_set = set(valid_slots)
    selected_slots = set()
    raw_spec = (spec or '').strip()
    if not raw_spec:
        return selected_slots

    for raw_token in raw_spec.split(','):
        token = raw_token.strip()
        if not token:
            continue
        if '-' in token:
            start_text, end_text = token.split('-', 1)
            start_slot = parse_int(start_text.strip(), 0)
            end_slot = parse_int(end_text.strip(), 0)
            if start_slot <= 0 or end_slot <= 0 or end_slot < start_slot:
                raise ValueError(
                    f'非法 {field_name} 区间：{token}。示例：1-3,9-11'
                )
            selected_slots.update(range(start_slot, end_slot + 1))
            continue
        slot = parse_int(token, 0)
        if slot <= 0:
            raise ValueError(f'非法 {field_name} 项：{token}。示例：1-3,9-11')
        selected_slots.add(slot)

    invalid_slots = sorted(slot for slot in selected_slots if slot not in valid_slot_set)
    if invalid_slots:
        raise ValueError(
            f'{field_name} 中存在超出可选范围的时点：'
            + ', '.join(str(slot) for slot in invalid_slots)
        )
    return selected_slots


def parse_rewarded_slot_spec(spec: str):
    return parse_slot_spec(spec, 'rewarded_slot_spec')


def bottleneck_capacity_per_slot(session):
    return max(
        1,
        parse_int(
            session.config.get('bottleneck_capacity_per_slot', C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT),
            C.DEFAULT_BOTTLENECK_CAPACITY_PER_SLOT,
        ),
    )


def service_interval_minutes(session):
    return C.SLOT_SIZE_MINUTES / bottleneck_capacity_per_slot(session)


def reward_treatment_enabled(session) -> bool:
    return config_flag(session.config.get('reward_treatment_enabled', 0))


def rewarded_slots_for_session(session):
    return parse_rewarded_slot_spec(session.config.get('rewarded_slot_spec', ''))


def rewarded_slots_for_player(player: Player):
    return parse_slot_spec(
        player.session.config.get('rewarded_slot_spec', ''),
        'rewarded_slot_spec',
        departure_slots_for_player(player),
    )


def reward_bonus_points(session):
    raw_value = session.config.get('reward_bonus_points', C.DEFAULT_REWARD_BONUS_POINTS)
    return cu(max(0, round(parse_float(raw_value, C.DEFAULT_REWARD_BONUS_POINTS), 2)))


def reward_bonus_for_slot(session, slot: int):
    if not reward_treatment_enabled(session):
        return cu(0)
    if slot not in rewarded_slots_for_session(session):
        return cu(0)
    return reward_bonus_points(session)


def reward_bonus_for_player_slot(player: Player, slot: int):
    if not reward_treatment_enabled(player.session):
        return cu(0)
    if slot not in rewarded_slots_for_player(player):
        return cu(0)
    return reward_bonus_points(player.session)


def reward_description(session):
    if not reward_treatment_enabled(session):
        return '当前未开启奖励处理。'

    rewarded_slots = sorted(rewarded_slots_for_session(session))
    if not rewarded_slots:
        return '奖励处理已开启，但当前没有配置可获得奖励的出发时点。'

    time_labels = ', '.join(minute_to_clock(departure_minute_for_slot(slot)) for slot in rewarded_slots)
    return (
        f'奖励处理已开启：若选择 {time_labels}，每轮可额外获得 '
        f'{reward_bonus_points(session)} points。'
    )


def reward_description_for_player(player: Player):
    if not reward_treatment_enabled(player.session):
        return '当前未开启奖励处理。'

    rewarded_slots = sorted(rewarded_slots_for_player(player))
    if not rewarded_slots:
        return '奖励处理已开启，但当前没有配置可获得奖励的出发时点。'

    time_labels = ', '.join(
        minute_to_clock(departure_minute_for_player_slot(player, slot))
        for slot in rewarded_slots
    )
    return (
        f'奖励处理已开启：若选择 {time_labels}，每轮可额外获得 '
        f'{reward_bonus_points(player.session)} points。'
    )


def coarse_toll_auto_enabled(session) -> bool:
    return config_flag(session.config.get('coarse_toll_auto_enabled', C.DEFAULT_COARSE_TOLL_AUTO_ENABLED))


def coarse_toll_auto_min_toll(session):
    return max(
        0,
        parse_float(
            session.config.get('coarse_toll_auto_min_toll', C.DEFAULT_COARSE_TOLL_AUTO_MIN_TOLL),
            C.DEFAULT_COARSE_TOLL_AUTO_MIN_TOLL,
        ),
    )


def coarse_toll_auto_max_toll(session):
    return max(
        0,
        parse_float(
            session.config.get('coarse_toll_auto_max_toll', C.DEFAULT_COARSE_TOLL_AUTO_MAX_TOLL),
            C.DEFAULT_COARSE_TOLL_AUTO_MAX_TOLL,
        ),
    )


def coarse_toll_auto_toll_step(session):
    return max(
        0,
        parse_float(
            session.config.get('coarse_toll_auto_toll_step', C.DEFAULT_COARSE_TOLL_AUTO_TOLL_STEP),
            C.DEFAULT_COARSE_TOLL_AUTO_TOLL_STEP,
        ),
    )


def coarse_toll_enabled(session) -> bool:
    return config_flag(session.config.get('coarse_toll_enabled', C.DEFAULT_COARSE_TOLL_ENABLED))


def coarse_toll_slot_spec(session):
    return session.config.get('coarse_toll_slot_spec', C.DEFAULT_COARSE_TOLL_SLOT_SPEC)


def coarse_toll_slots_for_session(session):
    return parse_slot_spec(coarse_toll_slot_spec(session), 'coarse_toll_slot_spec')


def coarse_toll_points(session):
    raw_value = session.config.get('coarse_toll_points', C.DEFAULT_COARSE_TOLL_POINTS)
    return cu(max(0, round(parse_float(raw_value, C.DEFAULT_COARSE_TOLL_POINTS), 2)))


def participant_auto_coarse_toll_result(player: Player):
    result = player.participant.vars.get(COARSE_TOLL_AUTO_RESULT_VAR, {})
    if isinstance(result, dict) and result.get('enabled'):
        return result
    return {}


def coarse_toll_source_for_player(player: Player):
    return COARSE_TOLL_SOURCE_AUTO if participant_auto_coarse_toll_result(player) else COARSE_TOLL_SOURCE_MANUAL


def coarse_toll_enabled_for_player(player: Player) -> bool:
    if participant_auto_coarse_toll_result(player):
        return True
    return coarse_toll_enabled(player.session)


def coarse_toll_slot_spec_for_player(player: Player):
    auto_result = participant_auto_coarse_toll_result(player)
    if auto_result:
        return auto_result.get('slot_spec', C.DEFAULT_COARSE_TOLL_SLOT_SPEC)
    return coarse_toll_slot_spec(player.session)


def coarse_toll_slots_for_player(player: Player):
    return parse_slot_spec(
        coarse_toll_slot_spec_for_player(player),
        'coarse_toll_slot_spec',
        departure_slots_for_player(player),
    )


def coarse_toll_points_for_player(player: Player):
    auto_result = participant_auto_coarse_toll_result(player)
    if auto_result:
        raw_value = auto_result.get('points', C.DEFAULT_COARSE_TOLL_POINTS)
    else:
        raw_value = coarse_toll_points(player.session)
    return cu(max(0, round(parse_float(raw_value, C.DEFAULT_COARSE_TOLL_POINTS), 2)))


def coarse_toll_for_player_slot(player: Player, slot: int):
    if not coarse_toll_enabled_for_player(player):
        return cu(0)
    if slot not in coarse_toll_slots_for_player(player):
        return cu(0)
    return coarse_toll_points_for_player(player)


def coarse_toll_description_for_player(player: Player):
    if not coarse_toll_enabled_for_player(player):
        return '当前未开启粗收费处理。'

    tolled_slots = sorted(coarse_toll_slots_for_player(player))
    if not tolled_slots:
        return '粗收费处理已开启，但当前没有配置收费出发时点。'

    time_labels = ', '.join(
        minute_to_clock(departure_minute_for_player_slot(player, slot))
        for slot in tolled_slots
    )
    prefix = '粗收费已自动校准' if coarse_toll_source_for_player(player) == COARSE_TOLL_SOURCE_AUTO else '粗收费已开启'
    return (
        f'{prefix}：若选择 {time_labels} 出发，每轮需支付 '
        f'{point_value_display(coarse_toll_points_for_player(player))} 成本分。'
    )


def slot_preview_for_player(player: Player):
    schedule = departure_schedule_for_player(player)
    rewarded_slots = rewarded_slots_for_player(player)
    reward_bonus = reward_bonus_points(player.session)
    tolled_slots = coarse_toll_slots_for_player(player) if coarse_toll_enabled_for_player(player) else set()
    toll_charge = coarse_toll_points_for_player(player)
    preview = []
    for slot in departure_slots_from_schedule(schedule):
        preview.append(
            dict(
                slot=slot,
                departure_label=departure_label_for_slot(slot, schedule),
                departure_relation=departure_relation_for_slot(slot, schedule),
                departure_time=minute_to_clock(departure_minute_for_slot(slot, schedule)),
                reward_active=slot in rewarded_slots and reward_treatment_enabled(player.session),
                reward_bonus=reward_bonus if slot in rewarded_slots and reward_treatment_enabled(player.session) else cu(0),
                toll_active=slot in tolled_slots and coarse_toll_enabled_for_player(player),
                toll_charge=toll_charge if slot in tolled_slots and coarse_toll_enabled_for_player(player) else cu(0),
                toll_charge_label=point_value_display(toll_charge) if slot in tolled_slots and coarse_toll_enabled_for_player(player) else '0',
            )
        )
    return preview


def auto_toll_distribution_summary(candidate, valid_slots, schedule):
    items = []
    for slot, count in zip(valid_slots, candidate.distribution):
        if count <= 0:
            continue
        items.append(f'slot {slot}({minute_to_clock(departure_minute_for_slot(slot, schedule))}): {count}人')
    return ', '.join(items)


def auto_toll_cost_summary(candidate, schedule):
    return ', '.join(
        f'slot {slot}({minute_to_clock(departure_minute_for_slot(slot, schedule))}): {point_value_display(cost)}'
        for slot, cost in candidate.selected_costs
    )


def build_auto_toll_result(candidate, players_count: int, capacity: int, schedule):
    valid_slots = tuple(departure_slots_from_schedule(schedule))
    return dict(
        enabled=True,
        source=COARSE_TOLL_SOURCE_AUTO,
        calibration_players=players_count,
        capacity=capacity,
        slot_spec=candidate.window_spec,
        points=round(float(candidate.toll), 2),
        cost_gap=round(float(candidate.cost_gap), 6),
        nash_count=candidate.nash_count,
        equilibrium_distribution=auto_toll_distribution_summary(candidate, valid_slots, schedule),
        equilibrium_costs=auto_toll_cost_summary(candidate, schedule),
    )


def apply_departure_schedules(session, matrix):
    auto_enabled = departure_schedule_auto_enabled(session)
    min_slots_each_side = departure_schedule_min_slots_each_side(session)
    capacity = bottleneck_capacity_per_slot(session)

    for group_players in matrix:
        players_count = len(group_players)
        if players_count <= 0:
            continue

        schedule = build_departure_schedule_record(
            players_count=players_count,
            capacity=capacity,
            min_slots_each_side=min_slots_each_side,
            auto_enabled=auto_enabled,
        )
        for player in group_players:
            player.participant.vars[DEPARTURE_SCHEDULE_VAR] = schedule


def validate_group_slot_configs(session, matrix):
    if reward_treatment_enabled(session):
        for group_index, group_players in enumerate(matrix, start=1):
            if not group_players:
                continue
            rewarded_slots = rewarded_slots_for_player(group_players[0])
            if not rewarded_slots:
                raise ValueError(
                    f'第 {group_index} 组：reward_treatment_enabled=1 时，'
                    'rewarded_slot_spec 不能为空且必须落在该组可选出发时点内。'
                )

    if not coarse_toll_auto_enabled(session) and coarse_toll_enabled(session):
        for group_index, group_players in enumerate(matrix, start=1):
            if not group_players:
                continue
            tolled_slots = coarse_toll_slots_for_player(group_players[0])
            if not tolled_slots:
                raise ValueError(
                    f'第 {group_index} 组：coarse_toll_enabled=1 时，'
                    'coarse_toll_slot_spec 不能为空且必须落在该组可选出发时点内。'
                )


def apply_auto_coarse_toll_calibration(session, matrix):
    for group_players in matrix:
        for player in group_players:
            player.participant.vars.pop(COARSE_TOLL_AUTO_RESULT_VAR, None)

    if not coarse_toll_auto_enabled(session):
        return

    min_toll = coarse_toll_auto_min_toll(session)
    max_toll = coarse_toll_auto_max_toll(session)
    toll_step = coarse_toll_auto_toll_step(session)
    if toll_step <= 0:
        raise ValueError('coarse_toll_auto_toll_step 必须大于 0。')
    if min_toll > max_toll:
        raise ValueError('coarse_toll_auto_min_toll 不能大于 coarse_toll_auto_max_toll。')

    capacity = bottleneck_capacity_per_slot(session)
    candidate_cache = {}

    from .toll_calibration import CalibrationError, calibrate_best_candidate

    for group_index, group_players in enumerate(matrix, start=1):
        players_count = len(group_players)
        if players_count <= 0:
            continue

        schedule = departure_schedule_for_player(group_players[0])
        valid_slots = tuple(departure_slots_from_schedule(schedule))
        cache_key = (
            players_count,
            capacity,
            tuple(valid_slots),
            schedule.get('first_departure_minute'),
            schedule.get('slot_size_minutes'),
        )

        if cache_key not in candidate_cache:
            try:
                candidate_cache[cache_key] = calibrate_best_candidate(
                    players=players_count,
                    capacity=capacity,
                    min_toll=min_toll,
                    max_toll=max_toll,
                    toll_step=toll_step,
                    valid_slots=valid_slots,
                    first_departure_minute=schedule.get('first_departure_minute'),
                    slot_size_minutes=schedule.get('slot_size_minutes', C.SLOT_SIZE_MINUTES),
                )
            except CalibrationError as exc:
                raise ValueError(
                    f'粗收费自动校准失败：第 {group_index} 组人数={players_count}, '
                    f'capacity={capacity}, 时点数={len(valid_slots)}, '
                    f'搜索范围={min_toll}-{max_toll}, 步长={toll_step}。{exc}'
                ) from exc

        result = build_auto_toll_result(candidate_cache[cache_key], players_count, capacity, schedule)
        for player in group_players:
            player.participant.vars[COARSE_TOLL_AUTO_RESULT_VAR] = result


def creating_session(subsession: Subsession):
    if subsession.round_number == 1:
        players = subsession.get_players()
        for player in players:
            player.participant.is_dropout = False
            player.participant.dropout_active = False
            player.participant.dropout_reason = ''
            player.participant.has_recovered_after_disconnect = False
            player.participant.finished = False

        session_name = subsession.session.config.get('name', '')
        grouping_enabled = config_flag(subsession.session.config.get('grouping_enabled', 0))
        manual_grouping_spec = subsession.session.config.get('manual_grouping_spec', '')

        if session_name != 'single_bottleneck_prod' and (grouping_enabled or manual_grouping_spec.strip()):
            raise ValueError('仅正式场次 single_bottleneck_prod 支持配置 participant_label 手动分组。')

        if grouping_enabled:
            matrix = build_manual_group_matrix(players, manual_grouping_spec, session_name)
        else:
            cohort_size = max(1, int(subsession.session.config.get('cohort_size', len(players) or 1)))
            matrix = build_auto_group_matrix(players, cohort_size)

        apply_departure_schedules(subsession.session, matrix)
        validate_group_slot_configs(subsession.session, matrix)
        apply_auto_coarse_toll_calibration(subsession.session, matrix)
        subsession.set_group_matrix(matrix)
        assign_group_metadata(matrix, grouping_enabled)
    else:
        subsession.group_like_round(1)

    for group in subsession.get_groups():
        group.decision_deadline_ts = 0
        group.results_ready = False


def participant_is_dropout(player: Player) -> bool:
    return bool(participant_var(player, 'is_dropout', False))


def participant_dropout_active(player: Player) -> bool:
    return bool(participant_var(player, 'dropout_active', False))


def participant_dropout_reason(player: Player) -> str:
    reason = (participant_var(player, 'dropout_reason', '') or '').strip()
    if reason in {DROPOUT_REASON_TIMEOUT, DROPOUT_REASON_DISCONNECT}:
        return reason
    return ''


def participant_has_recovered_after_disconnect(player: Player) -> bool:
    return bool(participant_var(player, 'has_recovered_after_disconnect', False))


def mark_timeout_dropout(player: Player):
    participant = player.participant
    participant.is_dropout = True
    participant.dropout_active = True
    participant.dropout_reason = DROPOUT_REASON_TIMEOUT


def mark_disconnect_dropout(player: Player):
    participant = player.participant
    participant.is_dropout = True
    participant.dropout_active = True
    if participant_dropout_reason(player) == DROPOUT_REASON_TIMEOUT:
        return
    participant.dropout_reason = DROPOUT_REASON_DISCONNECT


def maybe_restore_disconnect_participant(player: Player):
    if not participant_dropout_active(player):
        return False
    if participant_dropout_reason(player) != DROPOUT_REASON_DISCONNECT:
        return False

    participant = player.participant
    participant.dropout_active = False
    participant.dropout_reason = ''
    participant.has_recovered_after_disconnect = True
    return True


def ensure_decision_deadline(group: Group):
    if group.decision_deadline_ts:
        return

    if group.round_number == 1:
        seconds_until_deadline = (
            C.ROUND1_JOIN_GRACE_SECONDS
            + C.DECISION_TIMEOUT_SECONDS
            + C.WAIT_GRACE_SECONDS
        )
    else:
        seconds_until_deadline = (
            C.RESULTS_TIMEOUT_SECONDS
            + C.DECISION_TIMEOUT_SECONDS
            + C.WAIT_GRACE_SECONDS
        )

    group.decision_deadline_ts = time.time() + seconds_until_deadline


def remaining_decision_seconds(group: Group) -> int:
    ensure_decision_deadline(group)
    return max(0, ceil(group.decision_deadline_ts - time.time()))


def all_players_have_departure_slot(group: Group) -> bool:
    return all(player_has_departure_slot(player) for player in group.get_players())


def fill_missing_departure_slots(group: Group):
    for player in group.get_players():
        if player_has_departure_slot(player):
            continue
        player.departure_slot = random.choice(departure_slots_for_player(player))
        player.decision_source = DECISION_SOURCE_DISCONNECT_AUTO
        mark_disconnect_dropout(player)


def schedule_next_round_deadline(group: Group):
    if group.round_number >= C.NUM_ROUNDS:
        return

    next_group = group.in_round(group.round_number + 1)
    if next_group.decision_deadline_ts:
        return

    next_group.decision_deadline_ts = time.time() + (
        C.RESULTS_TIMEOUT_SECONDS
        + C.DECISION_TIMEOUT_SECONDS
        + C.WAIT_GRACE_SECONDS
    )


def maybe_prepare_results(group: Group):
    ensure_decision_deadline(group)

    if group.results_ready:
        return

    if all_players_have_departure_slot(group):
        set_results(group)
        return

    if time.time() >= group.decision_deadline_ts:
        fill_missing_departure_slots(group)
        set_results(group)


def set_results(group: Group):
    if group.results_ready:
        return

    if not all_players_have_departure_slot(group):
        fill_missing_departure_slots(group)

    players = group.get_players()
    group_slots = departure_slots_for_group(group)
    players_by_slot = {slot: [] for slot in group_slots}
    for player in players:
        slot = player_departure_slot(player)
        if slot is None:
            slot = random.choice(departure_slots_for_player(player))
            player.departure_slot = slot
            player.decision_source = DECISION_SOURCE_DISCONNECT_AUTO
            mark_disconnect_dropout(player)
        players_by_slot[slot].append(player)

    for slot_players in players_by_slot.values():
        random.shuffle(slot_players)

    next_available_bottleneck_minute = departure_minute_for_group_slot(group, group_slots[0])
    slot_service_interval = service_interval_minutes(group.session)

    for slot in group_slots:
        departure_minute = departure_minute_for_group_slot(group, slot)
        slot_players = players_by_slot[slot]
        slot_load = len(slot_players)

        for player in slot_players:
            service_start_minute = max(departure_minute, next_available_bottleneck_minute)
            queue_delay = max(0, service_start_minute - departure_minute)
            arrival_minute = departure_minute + C.FREE_FLOW_TRAVEL_MINUTES + queue_delay
            early_minutes = max(0, C.PREFERRED_ARRIVAL_MINUTE - arrival_minute)
            late_minutes = max(0, arrival_minute - C.PREFERRED_ARRIVAL_MINUTE)
            reward_bonus = reward_bonus_for_player_slot(player, slot)
            coarse_toll_charge = coarse_toll_for_player_slot(player, slot)
            generalized_cost = (
                C.QUEUE_COST_PER_MINUTE * queue_delay
                + C.EARLY_COST_PER_MINUTE * early_minutes
                + C.LATE_COST_PER_MINUTE * late_minutes
            )
            points = max(
                0,
                round(
                    C.BASE_POINTS
                    - generalized_cost
                    - float(coarse_toll_charge)
                    + float(reward_bonus),
                    2,
                ),
            )

            player.slot_load = slot_load
            player.departure_minute = round(departure_minute, 2)
            player.arrival_minute = round(arrival_minute, 2)
            player.departure_time_label = minute_to_clock(departure_minute)
            player.arrival_time_label = minute_to_clock(arrival_minute)
            player.queue_delay_minutes = round(queue_delay, 2)
            player.travel_time_minutes = round(C.FREE_FLOW_TRAVEL_MINUTES + queue_delay, 2)
            player.schedule_early_minutes = round(early_minutes, 2)
            player.schedule_late_minutes = round(late_minutes, 2)
            player.reward_bonus = reward_bonus
            player.payoff = cu(points)

            next_available_bottleneck_minute = service_start_minute + slot_service_interval

        if next_available_bottleneck_minute < departure_minute:
            next_available_bottleneck_minute = departure_minute

    for player in players:
        if group.round_number == C.NUM_ROUNDS:
            total_payoff = sum(round_player.payoff for round_player in player.in_all_rounds())
            player.participant.vars['single_bottleneck_total_payoff'] = total_payoff

    group.results_ready = True
    schedule_next_round_deadline(group)


def export_row_for_player(player: Player):
    session_config_name = player.session.config.get('name', '')
    grouping_enabled = config_flag(player.session.config.get('grouping_enabled', 0))
    manual_grouping_spec = player.session.config.get('manual_grouping_spec', '')
    reward_enabled = reward_treatment_enabled(player.session)
    rewarded_slot_spec = player.session.config.get('rewarded_slot_spec', '')
    reward_bonus_points_value = reward_bonus_points(player.session)
    auto_toll_result = participant_auto_coarse_toll_result(player)
    toll_source = coarse_toll_source_for_player(player)
    toll_auto_enabled = coarse_toll_auto_enabled(player.session)
    toll_calibration_players = auto_toll_result.get('calibration_players', '')
    toll_calibration_cost_gap = auto_toll_result.get('cost_gap', '')
    toll_calibration_nash_count = auto_toll_result.get('nash_count', '')
    toll_equilibrium_distribution = auto_toll_result.get('equilibrium_distribution', '')
    toll_equilibrium_costs = auto_toll_result.get('equilibrium_costs', '')
    toll_enabled = coarse_toll_enabled_for_player(player)
    toll_slot_spec = coarse_toll_slot_spec_for_player(player)
    toll_points_value = coarse_toll_points_for_player(player)
    capacity_per_slot = bottleneck_capacity_per_slot(player.session)
    departure_schedule = departure_schedule_for_player(player)
    departure_schedule_source = departure_schedule.get('source', DEPARTURE_SCHEDULE_SOURCE_STATIC)
    departure_schedule_auto = departure_schedule_auto_enabled(player.session)
    departure_schedule_players = departure_schedule.get('calibration_players', '')
    departure_schedule_capacity = departure_schedule.get('capacity', capacity_per_slot)
    departure_schedule_slot_count = departure_schedule.get('num_slots', C.NUM_DEPARTURE_SLOTS)
    departure_schedule_first_time = departure_schedule.get(
        'first_departure_time',
        minute_to_clock(departure_minute_for_slot(1)),
    )
    departure_schedule_last_time = departure_schedule.get(
        'last_departure_time',
        minute_to_clock(departure_minute_for_slot(C.NUM_DEPARTURE_SLOTS)),
    )
    selected_slot = player_departure_slot(player)
    coarse_toll_charge = coarse_toll_for_player_slot(player, selected_slot) if selected_slot else cu(0)
    queue_cost = round(C.QUEUE_COST_PER_MINUTE * safe_model_field(player, 'queue_delay_minutes', 0), 2)
    early_cost = round(C.EARLY_COST_PER_MINUTE * safe_model_field(player, 'schedule_early_minutes', 0), 2)
    late_cost = round(C.LATE_COST_PER_MINUTE * safe_model_field(player, 'schedule_late_minutes', 0), 2)
    travel_cost_without_toll = round(queue_cost + early_cost + late_cost, 2)
    choice_cost_with_toll = round(travel_cost_without_toll + float(coarse_toll_charge), 2)

    if session_config_name == 'single_bottleneck_prod':
        data_tier = 'prod'
    elif session_config_name == 'single_bottleneck_demo':
        data_tier = 'demo'
    else:
        data_tier = 'other'

    participant = player.participant
    final_total_payoff = participant.vars.get('single_bottleneck_total_payoff', '')
    is_dropout = participant_is_dropout(player)
    dropout_active = participant_dropout_active(player)
    dropout_reason = participant_dropout_reason(player)
    has_recovered_after_disconnect = participant_has_recovered_after_disconnect(player)
    finished = participant.vars.get('finished', False)
    assigned_group_id = participant.vars.get('assigned_group_id', '')
    assigned_group_label = participant.vars.get('assigned_group_label', '')
    assigned_group_members = participant.vars.get('assigned_group_members', '')

    return [
        player.session.code,
        session_config_name,
        data_tier,
        grouping_enabled,
        manual_grouping_spec,
        reward_enabled,
        rewarded_slot_spec,
        reward_bonus_points_value,
        toll_source,
        toll_auto_enabled,
        toll_calibration_players,
        toll_calibration_cost_gap,
        toll_calibration_nash_count,
        toll_equilibrium_distribution,
        toll_equilibrium_costs,
        toll_enabled,
        toll_slot_spec,
        toll_points_value,
        capacity_per_slot,
        departure_schedule_source,
        departure_schedule_auto,
        departure_schedule_players,
        departure_schedule_capacity,
        departure_schedule_slot_count,
        departure_schedule_first_time,
        departure_schedule_last_time,
        assigned_group_id,
        assigned_group_label,
        assigned_group_members,
        safe_model_field(participant, 'code', ''),
        safe_model_field(participant, 'label', ''),
        safe_model_field(player, 'round_number', ''),
        safe_model_field(player, 'departure_slot', ''),
        safe_model_field(player, 'departure_time_label', ''),
        safe_model_field(player, 'decision_source', ''),
        safe_model_field(player, 'arrival_time_label', ''),
        safe_model_field(player, 'queue_delay_minutes', 0),
        safe_model_field(player, 'travel_time_minutes', 0),
        safe_model_field(player, 'schedule_early_minutes', 0),
        safe_model_field(player, 'schedule_late_minutes', 0),
        safe_model_field(player, 'slot_load', 0),
        safe_model_field(player, 'reward_bonus', cu(0)),
        coarse_toll_charge,
        travel_cost_without_toll,
        choice_cost_with_toll,
        safe_model_field(player, 'payoff', ''),
        final_total_payoff,
        is_dropout,
        dropout_active,
        dropout_reason,
        has_recovered_after_disconnect,
        finished,
    ]


def row_to_dict(row):
    def normalize(value):
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)

    record = {}
    for header, value in zip(EXPORT_HEADERS, row):
        record[header] = normalize(value)
    return record


def build_session_reports(players):
    reports = {}
    for player in players:
        row = export_row_for_player(player)
        record = row_to_dict(row)
        session_code = record['session_code']
        session_pk = getattr(player.session, 'id', 0) or 0

        if session_code not in reports:
            reports[session_code] = dict(
                session_code=session_code,
                session_config_name=record['session_config_name'],
                data_tier=record['data_tier'],
                grouping_enabled=record['grouping_enabled'],
                manual_grouping_spec=record['manual_grouping_spec'],
                session_pk=session_pk,
                rows=[],
                participant_codes=set(),
                finished_codes=set(),
                historical_dropout_codes=set(),
                active_dropout_codes=set(),
                recovered_codes=set(),
                rounds=set(),
            )

        report = reports[session_code]
        report['rows'].append(record)
        report['participant_codes'].add(record['participant_code'])
        if record['finished']:
            report['finished_codes'].add(record['participant_code'])
        if record['is_dropout']:
            report['historical_dropout_codes'].add(record['participant_code'])
        if record['dropout_active']:
            report['active_dropout_codes'].add(record['participant_code'])
        if record['has_recovered_after_disconnect']:
            report['recovered_codes'].add(record['participant_code'])
        report['rounds'].add(record['round_number'])

    session_reports = []
    for report in reports.values():
        summary = dict(
            session_code=report['session_code'],
            session_config_name=report['session_config_name'],
            data_tier=report['data_tier'],
            total_records=len(report['rows']),
            participant_count=len(report['participant_codes']),
            finished_count=len(report['finished_codes']),
            historical_dropout_count=len(report['historical_dropout_codes']),
            active_dropout_count=len(report['active_dropout_codes']),
            recovered_count=len(report['recovered_codes']),
            round_count=len(report['rounds']),
            grouping_status='手动分组' if report['grouping_enabled'] else '自动分组',
        )
        session_reports.append(
            dict(
                session_code=report['session_code'],
                session_config_name=report['session_config_name'],
                label=f"{report['session_code']} | {report['session_config_name']}",
                session_pk=report['session_pk'],
                summary=summary,
                rows=report['rows'],
            )
        )

    session_reports.sort(key=lambda item: (item['session_pk'], item['session_code']), reverse=True)
    return session_reports


def vars_for_admin_report(subsession: Subsession):
    players = []
    for round_subsession in subsession.in_all_rounds():
        players.extend(round_subsession.get_players())

    session_reports = build_session_reports(players)
    default_session_code = ''
    current_session_code = subsession.session.code
    available_session_codes = [report['session_code'] for report in session_reports]

    if current_session_code in available_session_codes:
        default_session_code = current_session_code
    elif session_reports:
        default_session_code = session_reports[0]['session_code']

    return dict(
        export_headers=EXPORT_HEADERS,
        export_headers_json=json.dumps(EXPORT_HEADERS, ensure_ascii=False),
        session_options=[
            dict(session_code=report['session_code'], label=report['label'])
            for report in session_reports
        ],
        default_session_code=default_session_code,
        current_session_code=current_session_code,
        session_reports_json=json.dumps(session_reports, ensure_ascii=False),
    )


def access_allowed(player: Player):
    if player.session.config.get('name') != 'single_bottleneck_prod':
        return True
    return bool(player.participant.vars.get('access_granted', False))


def result_slot_summaries(player: Player, current_slot: int):
    group = player.group
    rewarded_slots = rewarded_slots_for_player(player)
    tolled_slots = coarse_toll_slots_for_player(player) if coarse_toll_enabled_for_player(player) else set()
    schedule = departure_schedule_for_player(player)
    summaries = []
    for slot in departure_slots_from_schedule(schedule):
        count = sum(1 for round_player in group.get_players() if player_departure_slot(round_player) == slot)
        summaries.append(
            dict(
                slot=slot,
                departure_time=minute_to_clock(departure_minute_for_slot(slot, schedule)),
                count=count,
                is_current=slot == current_slot,
                is_rewarded=reward_treatment_enabled(group.session) and slot in rewarded_slots,
                is_tolled=coarse_toll_enabled_for_player(player) and slot in tolled_slots,
            )
        )
    return summaries


class Introduction(Page):
    @staticmethod
    def is_displayed(player: Player):
        return player.round_number == 1 and access_allowed(player)

    @staticmethod
    def vars_for_template(player: Player):
        return dict(
            preferred_arrival_time=minute_to_clock(C.PREFERRED_ARRIVAL_MINUTE),
            free_flow_departure_time=minute_to_clock(free_flow_departure_minute()),
            free_flow_travel_minutes=minute_value_display(C.FREE_FLOW_TRAVEL_MINUTES),
            queue_cost_per_minute=C.QUEUE_COST_PER_MINUTE,
            early_cost_per_minute=C.EARLY_COST_PER_MINUTE,
            late_cost_per_minute=C.LATE_COST_PER_MINUTE,
            base_points=C.BASE_POINTS,
            reward_description=reward_description_for_player(player),
            coarse_toll_description=coarse_toll_description_for_player(player),
            slot_preview=slot_preview_for_player(player),
            capacity_per_slot=bottleneck_capacity_per_slot(player.session),
            total_rounds=C.NUM_ROUNDS,
        )


class Decision(Page):
    form_model = 'player'
    form_fields = ['departure_slot']

    @staticmethod
    def is_displayed(player: Player):
        if not access_allowed(player):
            return False
        ensure_decision_deadline(player.group)
        maybe_prepare_results(player.group)
        if player.group.results_ready:
            return False
        if player_has_departure_slot(player):
            return False
        maybe_restore_disconnect_participant(player)
        return True

    @staticmethod
    def get_timeout_seconds(player: Player):
        maybe_prepare_results(player.group)
        if not player.group.results_ready and not player_has_departure_slot(player):
            maybe_restore_disconnect_participant(player)
        if participant_dropout_active(player):
            return C.DROPOUT_TIMEOUT_SECONDS
        return min(C.DECISION_TIMEOUT_SECONDS, max(1, remaining_decision_seconds(player.group)))

    @staticmethod
    def vars_for_template(player: Player):
        return dict(
            auto_advance_seconds=Decision.get_timeout_seconds(player),
            preferred_arrival_time=minute_to_clock(C.PREFERRED_ARRIVAL_MINUTE),
            free_flow_departure_time=minute_to_clock(free_flow_departure_minute()),
            reward_description=reward_description_for_player(player),
            coarse_toll_description=coarse_toll_description_for_player(player),
            slot_preview=slot_preview_for_player(player),
        )

    @staticmethod
    def error_message(player: Player, values):
        if values.get('departure_slot') not in departure_slots_for_player(player):
            return '请选择表格中的有效出发时间。'

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        if timeout_happened and not player_has_departure_slot(player):
            player.departure_slot = random.choice(departure_slots_for_player(player))
            player.decision_source = DECISION_SOURCE_TIMEOUT_AUTO
            mark_timeout_dropout(player)
            return
        player.decision_source = DECISION_SOURCE_MANUAL


class ResultsSync(Page):
    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)

    @staticmethod
    def vars_for_template(player: Player):
        maybe_prepare_results(player.group)
        remaining_seconds = max(0, ceil(player.group.decision_deadline_ts - time.time()))
        return dict(
            results_ready=player.group.results_ready,
            remaining_seconds=remaining_seconds,
            poll_interval_ms=C.SYNC_POLL_INTERVAL_SECONDS * 1000,
            auto_continue_delay_ms=C.AUTO_CONTINUE_DELAY_MS,
        )

    @staticmethod
    def before_next_page(player: Player, timeout_happened):
        maybe_prepare_results(player.group)


class Results(Page):
    @staticmethod
    def is_displayed(player: Player):
        return access_allowed(player)

    @staticmethod
    def get_timeout_seconds(player: Player):
        maybe_restore_disconnect_participant(player)
        if participant_dropout_active(player):
            return C.DROPOUT_TIMEOUT_SECONDS
        return C.RESULTS_TIMEOUT_SECONDS

    @staticmethod
    def vars_for_template(player: Player):
        maybe_prepare_results(player.group)
        schedule = departure_schedule_for_player(player)
        current_slot = player_departure_slot(player) or departure_slots_from_schedule(schedule)[0]
        queue_delay = safe_model_field(player, 'queue_delay_minutes', 0)
        early_minutes = safe_model_field(player, 'schedule_early_minutes', 0)
        late_minutes = safe_model_field(player, 'schedule_late_minutes', 0)
        queue_cost_points = round(C.QUEUE_COST_PER_MINUTE * queue_delay, 2)
        early_cost_points = round(C.EARLY_COST_PER_MINUTE * early_minutes, 2)
        late_cost_points = round(C.LATE_COST_PER_MINUTE * late_minutes, 2)
        schedule_cost_points = round(early_cost_points + late_cost_points, 2)
        total_travel_cost_points = round(queue_cost_points + schedule_cost_points, 2)
        toll_charge_points = round(float(coarse_toll_for_player_slot(player, current_slot)), 2)
        total_choice_cost_points = round(total_travel_cost_points + toll_charge_points, 2)
        queue_cost_pct = bounded_percent(queue_cost_points, total_choice_cost_points)
        early_cost_pct = bounded_percent(early_cost_points, total_choice_cost_points)
        late_cost_pct = bounded_percent(late_cost_points, total_choice_cost_points)
        toll_cost_pct = bounded_percent(toll_charge_points, total_choice_cost_points)
        cost_bar_min_width_px = 18 if total_choice_cost_points > 0 else 0

        return dict(
            departure_time_label=safe_model_field(player, 'departure_time_label', ''),
            arrival_time_label=safe_model_field(player, 'arrival_time_label', ''),
            queue_delay_minutes=minute_value_display(queue_delay),
            travel_time_minutes=minute_value_display(safe_model_field(player, 'travel_time_minutes', 0)),
            schedule_early_minutes=minute_value_display(early_minutes),
            schedule_late_minutes=minute_value_display(late_minutes),
            slot_load=safe_model_field(player, 'slot_load', 0),
            current_slot=current_slot,
            current_slot_time=minute_to_clock(departure_minute_for_slot(current_slot, schedule)),
            preferred_arrival_time=minute_to_clock(C.PREFERRED_ARRIVAL_MINUTE),
            queue_cost_points=minute_value_display(queue_cost_points),
            early_cost_points=minute_value_display(early_cost_points),
            late_cost_points=minute_value_display(late_cost_points),
            schedule_cost_points=minute_value_display(schedule_cost_points),
            total_travel_cost_points=minute_value_display(total_travel_cost_points),
            toll_charge_points=minute_value_display(toll_charge_points),
            total_choice_cost_points=minute_value_display(total_choice_cost_points),
            queue_cost_pct=queue_cost_pct,
            early_cost_pct=early_cost_pct,
            late_cost_pct=late_cost_pct,
            toll_cost_pct=toll_cost_pct,
            cost_bar_min_width_px=cost_bar_min_width_px,
            coarse_toll_description=coarse_toll_description_for_player(player),
            slot_summaries=result_slot_summaries(player, current_slot),
            auto_advance_seconds=Results.get_timeout_seconds(player),
        )


def custom_export(players):
    yield EXPORT_HEADERS
    for player in players:
        yield export_row_for_player(player)


page_sequence = [Introduction, Decision, ResultsSync, Results]
