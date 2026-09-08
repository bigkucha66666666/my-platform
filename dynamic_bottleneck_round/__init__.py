from dataclasses import asdict, dataclass
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache
import fcntl
from math import ceil, floor, isfinite
import json
import os
from pathlib import Path
import random
import tempfile
from threading import Lock
import time

from otree.api import *
from .accident_capacity import (
    INFO_I0,
    INFO_I1,
    INFO_I2,
    AccidentRiskConfig,
    AccidentRiskConfigError,
    accident_public_context,
    generate_accident_sequence,
    load_accident_sequence_bank,
    parse_accident_risk_config,
)
from .agents.deepseek_agent import (
    AgentChoice,
    AgentChoiceSet,
    choose_agent_departure,
    config_from_session,
    fallback_lowest_schedule_cost,
)
from .agents.independent_rl_agent import (
    INDEPENDENT_RL_POLICY_VERSION,
    choose_independent_rl_departure,
    observe_independent_rl_outcome,
    valid_or_initial_independent_rl_state,
)
from .agents.personas import (
    get_or_create_api_agent_persona,
    get_or_create_rl_agent_persona,
    initialize_api_agent_personas,
    initialize_rl_agent_personas,
)
from .agents.rl_fallback import (
    choose_rl_departure,
    initial_rl_state,
    observe_rl_outcome,
    public_feedback_observation,
    valid_or_initial_state,
)


doc = """
动态瓶颈服务率实验。
同一小组在同一轮面对相同的瓶颈服务率，不同轮次的服务率按 session config 确定。
"""


DRAW_MODE_BALANCED = 'balanced_shuffle'
DRAW_MODE_IID = 'iid'
DRAW_MODE_PHASED_MARKOV = 'phased_markov'
DRAW_MODE_MANUAL_SEQUENCE = 'manual_sequence'
CAPACITY_SEQUENCE_PRESET_AUTO = 'auto'
CAPACITY_SEQUENCE_BANK_FILE = 'capacity_sequence_bank.json'
CAPACITY_SEQUENCE_SCOPE_GROUP = 'group'
CAPACITY_SEQUENCE_SCOPE_SESSION = 'session'
REVEAL_BEFORE_DECISION = 'before_decision'
REVEAL_AFTER_DECISION = 'after_decision'

DECISION_SOURCE_MANUAL = 'manual'
DECISION_SOURCE_TIMEOUT_AUTO = 'timeout_auto'
DECISION_SOURCE_DISCONNECT_AUTO = 'disconnect_auto'
DECISION_SOURCE_SUSPENDED_AUTO = 'suspended_auto'

AUTO_CHOICE_LAST_MANUAL = 'last_manual_choice'
AUTO_CHOICE_NEUTRAL_BASELINE = 'neutral_baseline'

CAPACITY_SEQUENCE_VAR = 'dynamic_bottleneck_round_capacity_sequence'
ACCIDENT_SEQUENCE_SESSION_VAR = 'dynamic_bottleneck_round_accident_sequence_v1'
TOTAL_PAYOFF_VAR = 'dynamic_bottleneck_round_total_payoff'
COMPREHENSION_SEEN_VAR = 'dynamic_bottleneck_round_comprehension_seen'
DEPARTURE_SCHEDULE_VAR = 'dynamic_bottleneck_round_departure_schedule'
TOLL_BY_CAPACITY_VAR = 'dynamic_bottleneck_round_toll_by_capacity'
TOLL_CALIBRATION_CACHE_FILE = 'toll_calibration_cache.json'
TOLL_SOURCE_AUTO = 'auto'
TOLL_SOURCE_MANUAL = 'manual'
SAME_TIME_QUEUE_RULE = 'batch_max_wait'
TOLL_WINDOW_RULE = 'symmetric_continuous'
API_AGENT_MODE_OFF = 'off'
API_AGENT_MODE_ACTIVE = 'active'
API_AGENT_TYPE_DEEPSEEK = 'deepseek_api_agent'
API_AGENT_LEGACY_ACTOR_TYPE = 'api_agent'
API_AGENT_COUNT_MIN = 1
API_AGENT_COUNT_MAX = 5
API_AGENT_COUNT_ERROR = '每组 Agent 数量必须是 1 到 5 之间的整数。'
RL_AGENT_TYPE = 'rl_agent'
RL_AGENT_COUNT_MIN = 1
RL_AGENT_COUNT_MAX = 5
RL_AGENT_COUNT_ERROR = '每组独立 RL Agent 数量必须是 1 到 5 之间的整数。'
AGENT_DECISIONS_PARTICIPANT_VAR = 'dynamic_bottleneck_round_agent_decisions_by_round_v1'
INDEPENDENT_RL_DECISIONS_PARTICIPANT_VAR = (
    'dynamic_bottleneck_round_independent_rl_decisions_by_round_v1'
)
API_AGENT_MEMORY_PARTICIPANT_VAR = 'dynamic_bottleneck_round_api_agent_memory_v1'
GROUP_AGENT_COUNTS_SESSION_VAR = 'dynamic_bottleneck_round_group_agent_counts_v1'
RL_AGENT_STATE_PARTICIPANT_VAR = 'dynamic_bottleneck_round_rl_agent_state_v1'
DROPOUT_AUDIT_PARTICIPANT_VAR = 'dynamic_bottleneck_round_dropout_audit_v1'
INDEPENDENT_RL_STATE_PARTICIPANT_VAR = (
    'dynamic_bottleneck_round_independent_rl_state_v1'
)
PUBLIC_FEEDBACK_PARTICIPANT_VAR = 'dynamic_bottleneck_round_public_feedback_v1'
API_AGENT_DECISIONS_PENDING = object()
_API_AGENT_PREFETCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix='dynamic-bottleneck-agent',
)
_API_AGENT_PREFETCH_TASKS = {}
_API_AGENT_PREFETCH_LOCK = Lock()


class DynamicCapacityConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DynamicCapacityConfig:
    values: tuple[int, ...]
    probabilities: tuple[float, ...]
    seed: int
    draw_mode: str
    sequence_scope: str
    reveal_timing: str
    random_rounds: int
    transition_matrix: tuple[tuple[float, ...], ...]
    manual_sequence: tuple[int, ...]


def _csv_items(value, field_name):
    items = [item.strip() for item in str(value or '').split(',') if item.strip()]
    if not items:
        raise DynamicCapacityConfigError(f'{field_name} 不能为空。')
    return items


@lru_cache(maxsize=1)
def load_capacity_sequence_bank():
    path = Path(__file__).with_name(CAPACITY_SEQUENCE_BANK_FILE)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise DynamicCapacityConfigError(
            f'服务率序列库不存在：{path.name}。'
        ) from exc
    except json.JSONDecodeError as exc:
        raise DynamicCapacityConfigError(
            f'服务率序列库 {path.name} 不是有效 JSON。'
        ) from exc

    raw_records = payload.get('sequences') if isinstance(payload, dict) else None
    if not isinstance(raw_records, list) or not raw_records:
        raise DynamicCapacityConfigError('服务率序列库中没有可用序列。')

    records = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            raise DynamicCapacityConfigError('服务率序列库记录格式错误。')
        sequence_id = str(raw_record.get('id', '') or '').strip().upper()
        sequence_spec = str(
            raw_record.get('manual_sequence_spec', '') or ''
        ).strip()
        if not sequence_id or not sequence_spec:
            raise DynamicCapacityConfigError(
                '服务率序列库记录必须包含 id 和 manual_sequence_spec。'
            )
        if sequence_id in records:
            raise DynamicCapacityConfigError(
                f'服务率序列库中存在重复编号 {sequence_id}。'
            )
        records[sequence_id] = sequence_spec
    return records


def manual_sequence_spec_for_preset(session_config):
    raw_preset = str(
        session_config.get(
            'dynamic_capacity_sequence_preset',
            CAPACITY_SEQUENCE_PRESET_AUTO,
        )
        or CAPACITY_SEQUENCE_PRESET_AUTO
    ).strip()
    if not raw_preset or raw_preset.lower() == CAPACITY_SEQUENCE_PRESET_AUTO:
        return None

    preset = raw_preset.upper()
    bank = load_capacity_sequence_bank()
    if preset not in bank:
        available = ', '.join(sorted(bank))
        raise DynamicCapacityConfigError(
            f'dynamic_capacity_sequence_preset={preset} 不存在；'
            f'可选值为 auto, {available}。'
        )
    return bank[preset]


def parse_dynamic_capacity_config(session_config) -> DynamicCapacityConfig:
    preset_manual_sequence = manual_sequence_spec_for_preset(session_config)
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

    draw_mode = (
        DRAW_MODE_MANUAL_SEQUENCE
        if preset_manual_sequence is not None
        else str(
            session_config.get('dynamic_capacity_draw_mode', DRAW_MODE_BALANCED)
        ).strip().lower()
    )
    if draw_mode not in {
        DRAW_MODE_BALANCED,
        DRAW_MODE_IID,
        DRAW_MODE_PHASED_MARKOV,
        DRAW_MODE_MANUAL_SEQUENCE,
    }:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_draw_mode 必须是 balanced_shuffle、iid、'
            'phased_markov 或 manual_sequence。'
        )

    try:
        random_rounds = int(session_config.get('dynamic_capacity_random_rounds', 20))
    except (TypeError, ValueError) as exc:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_random_rounds 必须是非负整数。'
        ) from exc
    if random_rounds < 0:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_random_rounds 必须是非负整数。'
        )

    raw_matrix = str(
        session_config.get(
            'dynamic_capacity_transition_matrix',
            '0.8,0.1,0.1;0.1,0.8,0.1;0.1,0.1,0.8',
        )
        or ''
    ).strip()
    try:
        transition_matrix = tuple(
            tuple(float(item.strip()) for item in row.split(','))
            for row in raw_matrix.split(';')
            if row.strip()
        )
    except (TypeError, ValueError) as exc:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_transition_matrix 必须是分号分行、逗号分列的概率矩阵。'
        ) from exc
    matrix_size_valid = (
        len(transition_matrix) == len(values)
        and all(len(row) == len(values) for row in transition_matrix)
    )
    matrix_probabilities_valid = all(
        isfinite(probability) and 0 <= probability <= 1
        for row in transition_matrix
        for probability in row
    )
    matrix_rows_sum_to_one = all(
        abs(sum(row) - 1.0) <= 1e-9
        for row in transition_matrix
    )
    if not (
        matrix_size_valid
        and matrix_probabilities_valid
        and matrix_rows_sum_to_one
    ):
        raise DynamicCapacityConfigError(
            'dynamic_capacity_transition_matrix 必须与服务率状态数量一致，'
            '且每行均由 0 到 1 的有限概率组成并且概率之和为 1。'
        )

    raw_manual_sequence = (
        preset_manual_sequence
        if preset_manual_sequence is not None
        else str(
            session_config.get('dynamic_capacity_manual_sequence', '') or ''
        ).strip()
    )
    try:
        manual_sequence = tuple(
            int(item.strip())
            for item in raw_manual_sequence.split(',')
            if item.strip()
        )
    except (TypeError, ValueError) as exc:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_manual_sequence 必须是逗号分隔的整数。'
        ) from exc

    sequence_scope = str(
        session_config.get(
            'dynamic_capacity_sequence_scope',
            CAPACITY_SEQUENCE_SCOPE_GROUP,
        )
    ).strip().lower()
    if sequence_scope not in {
        CAPACITY_SEQUENCE_SCOPE_GROUP,
        CAPACITY_SEQUENCE_SCOPE_SESSION,
    }:
        raise DynamicCapacityConfigError(
            'dynamic_capacity_sequence_scope 必须是 group 或 session。'
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
        sequence_scope=sequence_scope,
        reveal_timing=reveal_timing,
        random_rounds=random_rounds,
        transition_matrix=transition_matrix,
        manual_sequence=manual_sequence,
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
    if config.draw_mode == DRAW_MODE_MANUAL_SEQUENCE:
        if len(config.manual_sequence) != rounds:
            raise DynamicCapacityConfigError(
                f'dynamic_capacity_manual_sequence 必须恰好包含 {rounds} 个服务率。'
            )
        unknown_values = sorted(set(config.manual_sequence) - set(config.values))
        if unknown_values:
            raise DynamicCapacityConfigError(
                'dynamic_capacity_manual_sequence 中的服务率必须属于 '
                f'dynamic_capacity_values 候选集合；非法值={unknown_values}。'
            )
        return list(config.manual_sequence)
    sequence_id = (
        0
        if config.sequence_scope == CAPACITY_SEQUENCE_SCOPE_SESSION
        else int(group_id)
    )
    rng = random.Random(config.seed + sequence_id * 1009)
    if config.draw_mode == DRAW_MODE_IID:
        return rng.choices(config.values, weights=config.probabilities, k=rounds)
    if config.draw_mode == DRAW_MODE_PHASED_MARKOV:
        if config.random_rounds < 1 or config.random_rounds > rounds:
            raise DynamicCapacityConfigError(
                'phased_markov 模式下 dynamic_capacity_random_rounds '
                f'必须在 1 到总轮数 {rounds} 之间。'
            )
        sequence = rng.choices(
            config.values,
            weights=config.probabilities,
            k=config.random_rounds,
        )
        value_index = {value: index for index, value in enumerate(config.values)}
        while len(sequence) < rounds:
            previous_index = value_index[sequence[-1]]
            sequence.append(
                rng.choices(
                    config.values,
                    weights=config.transition_matrix[previous_index],
                    k=1,
                )[0]
            )
        return sequence

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
    value_index = {value: index for index, value in enumerate(config.values)}
    records = []
    for index, capacity in enumerate(sequence):
        previous_capacity = sequence[index - 1] if index else None
        if config.draw_mode == DRAW_MODE_MANUAL_SEQUENCE:
            probability = 0.0
        elif (
            config.draw_mode == DRAW_MODE_PHASED_MARKOV
            and index >= config.random_rounds
        ):
            previous_index = value_index[previous_capacity]
            capacity_index = value_index[capacity]
            probability = config.transition_matrix[previous_index][capacity_index]
        else:
            probability = probability_by_capacity[capacity]
        records.append(
            {
                'round_number': index + 1,
                'capacity': capacity,
                'state': f'capacity_{capacity}',
                'probability': probability,
                'previous_capacity': previous_capacity,
            }
        )
    return records


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
        'capacity_states': [{'capacity': capacity} for capacity in config.values],
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
    WARMUP_ROUNDS = 5
    FORMAL_ROUNDS = 60
    NUM_ROUNDS = WARMUP_ROUNDS + FORMAL_ROUNDS

    DECISION_TIMEOUT_SECONDS = 60
    RESULTS_TIMEOUT_SECONDS = 50
    DROPOUT_TIMEOUT_SECONDS = 1
    DROPOUT_SUSPEND_AFTER_MISSES = 2
    SYNC_POLL_INTERVAL_SECONDS = 1.5
    AUTO_CONTINUE_DELAY_MS = 200

    PREFERRED_ARRIVAL_MINUTE = 8 * 60
    FREE_FLOW_TRAVEL_MINUTES = 6
    CAPACITY_WINDOW_MINUTES = 1
    DEPARTURE_CHOICE_STEP_MINUTES = 1
    NUM_DEPARTURE_SLOTS = 16
    MAX_DEPARTURE_SLOT_CHOICES = 401
    FIRST_DEPARTURE_MINUTE = 7 * 60 + 46

    BASE_POINTS = 140
    FIXED_TRAVEL_TIME_COST = 0
    QUEUE_COST_PER_MINUTE = 2
    EARLY_COST_PER_MINUTE = 1
    LATE_COST_PER_MINUTE = 5


def is_warmup_round(round_number):
    return 1 <= int(round_number) <= C.WARMUP_ROUNDS


def formal_round_number(round_number):
    raw_round = int(round_number)
    if is_warmup_round(raw_round):
        return None
    return raw_round - C.WARMUP_ROUNDS


def round_phase_context(round_number):
    raw_round = int(round_number)
    if is_warmup_round(raw_round):
        display_round = raw_round
        return {
            'is_warmup': True,
            'phase_name': 'warmup',
            'display_round_number': display_round,
            'display_total_rounds': C.WARMUP_ROUNDS,
            'round_label': f'热身第 {display_round} 轮',
        }
    display_round = formal_round_number(raw_round)
    return {
        'is_warmup': False,
        'phase_name': 'formal',
        'display_round_number': display_round,
        'display_total_rounds': C.FORMAL_ROUNDS,
        'round_label': f'正式第 {display_round} 轮',
    }


def formal_payoff_total(player):
    return sum(
        round_player.payoff
        for round_player in player.in_all_rounds()
        if not is_warmup_round(round_player.round_number)
    )


def parse_warmup_capacity(session_config, dynamic_config):
    raw_capacity = session_config.get('dynamic_warmup_capacity', 2)
    if isinstance(raw_capacity, bool):
        raise DynamicCapacityConfigError(
            'dynamic_warmup_capacity 必须是正整数。'
        )
    try:
        capacity = int(str(raw_capacity).strip())
    except (TypeError, ValueError) as exc:
        raise DynamicCapacityConfigError(
            'dynamic_warmup_capacity 必须是正整数。'
        ) from exc
    if capacity <= 0:
        raise DynamicCapacityConfigError(
            'dynamic_warmup_capacity 必须是正整数。'
        )
    if capacity not in dynamic_config.values:
        raise DynamicCapacityConfigError(
            'dynamic_warmup_capacity 必须属于 '
            'dynamic_capacity_values 候选集合。'
        )
    return capacity


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
    dynamic_capacity = models.FloatField(initial=0)
    dynamic_capacity_state = models.StringField(blank=True)
    capacity_probability = models.FloatField(initial=0)
    incident_occurred = models.BooleanField(initial=False)
    capacity_loss_ratio = models.FloatField(initial=0)
    remaining_capacity_ratio = models.FloatField(initial=1)
    information_condition = models.StringField(blank=True)
    accident_sequence_id = models.StringField(blank=True)
    accident_sequence_seed = models.IntegerField(initial=0)


class Player(BasePlayer):
    round_start_ready = models.BooleanField(initial=False)
    dynamic_capacity = models.FloatField(initial=0)
    dynamic_capacity_state = models.StringField(blank=True)
    previous_round_capacity = models.IntegerField(initial=0)
    capacity_probability = models.FloatField(initial=0)
    capacity_reveal_timing = models.StringField(blank=True)
    dynamic_capacity_seed = models.IntegerField(initial=0)
    dynamic_capacity_draw_mode = models.StringField(blank=True)
    incident_occurred = models.BooleanField(initial=False)
    capacity_loss_ratio = models.FloatField(initial=0)
    remaining_capacity_ratio = models.FloatField(initial=1)
    information_condition = models.StringField(blank=True)
    accident_sequence_id = models.StringField(blank=True)
    accident_sequence_seed = models.IntegerField(initial=0)
    actor_composition = models.StringField(blank=True)

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
    'consecutive_missed_decisions',
    'dropout_suspended',
    'automatic_choice_strategy',
    'actor_type',
    'agent_id',
    'agent_type',
    'api_agent_mode',
    'agent_fallback_used',
    'agent_latency_ms',
    'agent_reason',
    'agent_context_json',
    'agent_memory_input',
    'agent_memory_output',
    'agent_persona_id',
    'agent_persona_label',
    'rl_policy_version',
    'rl_rounds_observed',
]


def config_flag(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return False


def api_agent_mode(session) -> str:
    mode = str(session.config.get('api_agent_mode', API_AGENT_MODE_OFF) or '').strip().lower()
    return API_AGENT_MODE_ACTIVE if mode == API_AGENT_MODE_ACTIVE else API_AGENT_MODE_OFF


def rl_fallback_enabled(session) -> bool:
    return (
        api_agent_mode(session) == API_AGENT_MODE_ACTIVE
        and config_flag(session.config.get('rl_fallback_enabled', 0))
    )


def _canonical_group_label(group_id) -> str:
    if isinstance(group_id, str):
        value = group_id.strip().upper()
        if value.startswith('G'):
            value = value[1:]
    else:
        value = group_id
    try:
        numeric_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('实验组编号必须为 G01 这样的正整数编号。') from exc
    if numeric_id <= 0:
        raise ValueError('实验组编号必须为 G01 这样的正整数编号。')
    return f'G{numeric_id:02d}'


def parse_group_agent_spec(spec):
    parsed = {}
    for raw_group in str(spec or '').replace('\n', ';').split(';'):
        raw_group = raw_group.strip()
        if not raw_group:
            continue
        if ':' not in raw_group:
            raise ValueError(
                'group_agent_spec 格式应为 G01:api=0,rl=0;G02:api=5,rl=0。'
            )
        raw_label, raw_counts = raw_group.split(':', 1)
        label = _canonical_group_label(raw_label)
        if label in parsed:
            raise ValueError(f'group_agent_spec 中的 {label} 重复配置。')
        counts = {}
        for raw_item in raw_counts.split(','):
            raw_item = raw_item.strip()
            if not raw_item or '=' not in raw_item:
                raise ValueError(
                    'group_agent_spec 格式应为 G01:api=0,rl=0;G02:api=5,rl=0。'
                )
            raw_key, raw_value = raw_item.split('=', 1)
            key = raw_key.strip().lower()
            if key not in {'api', 'rl'} or key in counts:
                raise ValueError('group_agent_spec 每组只能配置一个 api 和一个 rl 数量。')
            try:
                count = int(raw_value.strip())
            except ValueError as exc:
                raise ValueError('group_agent_spec 中 Agent 数量必须是整数。') from exc
            if not 0 <= count <= API_AGENT_COUNT_MAX:
                raise ValueError('group_agent_spec 中 Agent 数量必须在 0 到 5 之间。')
            counts[key] = count
        if set(counts) != {'api', 'rl'}:
            raise ValueError('group_agent_spec 每组必须同时配置 api 和 rl 数量。')
        parsed[label] = counts
    return parsed


def validate_group_agent_configuration(session, matrix):
    raw_spec = str(session.config.get('group_agent_spec', '') or '').strip()
    if not raw_spec:
        session.vars.pop(GROUP_AGENT_COUNTS_SESSION_VAR, None)
        return {}

    parsed = parse_group_agent_spec(raw_spec)
    expected_labels = {f'G{index:02d}' for index in range(1, len(matrix) + 1)}
    configured_labels = set(parsed)
    if configured_labels != expected_labels:
        missing = sorted(expected_labels - configured_labels)
        unknown = sorted(configured_labels - expected_labels)
        raise ValueError(
            'group_agent_spec 必须覆盖全部实验组。'
            f' 缺失={missing}，未知={unknown}。'
        )
    if api_agent_mode(session) != API_AGENT_MODE_ACTIVE and any(
        counts['api'] for counts in parsed.values()
    ):
        raise ValueError('group_agent_spec 配置了 API Agent，请将 api_agent_mode 设为 active。')
    if not rl_agent_enabled(session) and any(
        counts['rl'] for counts in parsed.values()
    ):
        raise ValueError('group_agent_spec 配置了 RL Agent，请将 rl_agent_enabled 设为 1。')
    session.vars[GROUP_AGENT_COUNTS_SESSION_VAR] = deepcopy(parsed)
    return parsed


def _configured_group_agent_count(session, group_id, actor_key):
    if group_id is None:
        return None
    stored = session.vars.get(GROUP_AGENT_COUNTS_SESSION_VAR, {})
    if not isinstance(stored, dict):
        return None
    counts = stored.get(_canonical_group_label(group_id))
    if not isinstance(counts, dict):
        return None
    return max(0, config_int(counts.get(actor_key, 0), 0))


def api_agent_count_per_group(session, group_id=None) -> int:
    if api_agent_mode(session) == API_AGENT_MODE_OFF:
        return 0
    configured_count = _configured_group_agent_count(session, group_id, 'api')
    if configured_count is not None:
        return configured_count
    return max(0, config_int(session.config.get('api_agent_count_per_group', 0), 0))


def api_agent_limited_memory_enabled(session) -> bool:
    return (
        api_agent_mode(session) == API_AGENT_MODE_ACTIVE
        and config_flag(session.config.get('api_agent_limited_memory_enabled', 0))
    )


def api_agent_limited_memory_max_chars(session) -> int:
    return max(
        1,
        config_int(session.config.get('api_agent_limited_memory_max_chars', 400), 400),
    )


def rl_agent_enabled(session) -> bool:
    return config_flag(session.config.get('rl_agent_enabled', 0))


def rl_agent_count_per_group(session, group_id=None) -> int:
    if not rl_agent_enabled(session):
        return 0
    configured_count = _configured_group_agent_count(session, group_id, 'rl')
    if configured_count is not None:
        return configured_count
    return max(0, config_int(session.config.get('rl_agent_count_per_group', 0), 0))


def validate_api_agent_count(session) -> int:
    raw_mode = str(
        session.config.get('api_agent_mode', API_AGENT_MODE_OFF) or ''
    ).strip().lower()
    if raw_mode not in {API_AGENT_MODE_OFF, API_AGENT_MODE_ACTIVE}:
        raise ValueError('api_agent_mode 必须是 off 或 active。')
    raw_group_spec = str(session.config.get('group_agent_spec', '') or '').strip()
    if raw_group_spec:
        parsed = parse_group_agent_spec(raw_group_spec)
        mode = (
            API_AGENT_MODE_ACTIVE
            if any(counts['api'] for counts in parsed.values())
            else API_AGENT_MODE_OFF
        )
        session.config = {**session.config, 'api_agent_mode': mode}
        return 0
    if raw_mode == API_AGENT_MODE_OFF:
        return 0

    raw_count = session.config.get('api_agent_count_per_group', 0)
    if isinstance(raw_count, bool):
        raise ValueError(API_AGENT_COUNT_ERROR)
    if isinstance(raw_count, int):
        count = raw_count
    elif isinstance(raw_count, str):
        try:
            count = int(raw_count.strip())
        except ValueError as exc:
            raise ValueError(API_AGENT_COUNT_ERROR) from exc
    else:
        raise ValueError(API_AGENT_COUNT_ERROR)
    if not API_AGENT_COUNT_MIN <= count <= API_AGENT_COUNT_MAX:
        raise ValueError(API_AGENT_COUNT_ERROR)

    session.config = {
        **session.config,
        'api_agent_mode': API_AGENT_MODE_ACTIVE,
        'api_agent_count_per_group': count,
    }
    return count


def validate_rl_agent_count(session) -> int:
    raw_group_spec = str(session.config.get('group_agent_spec', '') or '').strip()
    if raw_group_spec:
        parsed = parse_group_agent_spec(raw_group_spec)
        enabled = '1' if any(counts['rl'] for counts in parsed.values()) else '0'
        session.config = {**session.config, 'rl_agent_enabled': enabled}
        return 0
    if not rl_agent_enabled(session):
        session.config = {**session.config, 'rl_agent_enabled': '0'}
        return 0
    raw_count = session.config.get('rl_agent_count_per_group', 0)
    if isinstance(raw_count, bool):
        raise ValueError(RL_AGENT_COUNT_ERROR)
    if isinstance(raw_count, int):
        count = raw_count
    elif isinstance(raw_count, str):
        try:
            count = int(raw_count.strip())
        except ValueError as exc:
            raise ValueError(RL_AGENT_COUNT_ERROR) from exc
        if raw_count.strip() != str(count):
            raise ValueError(RL_AGENT_COUNT_ERROR)
    else:
        raise ValueError(RL_AGENT_COUNT_ERROR)
    if not RL_AGENT_COUNT_MIN <= count <= RL_AGENT_COUNT_MAX:
        raise ValueError(RL_AGENT_COUNT_ERROR)
    session.config = {
        **session.config,
        'rl_agent_enabled': '1',
        'rl_agent_count_per_group': count,
    }
    return count


def effective_group_actor_count(session, human_count, group_id=None) -> int:
    return (
        int(human_count)
        + api_agent_count_per_group(session, group_id)
        + rl_agent_count_per_group(session, group_id)
    )


def validate_formal_actor_composition(session, matrix):
    if session.config.get('name') != 'dynamic_bottleneck_round_prod':
        return 'demo'
    if str(session.config.get('group_agent_spec', '') or '').strip():
        raise ValueError(
            '事故风险正式实验不允许使用 group_agent_spec 按组改变主体构成。'
        )
    if len(matrix) != 1:
        raise ValueError(
            '事故风险正式实验每个 Session 必须且只能包含一个实验组。'
        )

    human_count = len(matrix[0])
    api_count = api_agent_count_per_group(session, 1)
    rl_count = rl_agent_count_per_group(session, 1)
    if (human_count, api_count, rl_count) == (20, 0, 0):
        return 'H'
    if (human_count, api_count, rl_count) == (16, 2, 2):
        return 'HA'
    raise ValueError(
        '事故风险正式实验主体构成只能是 20 Human，'
        '或 16 Human + 2 LLM + 2 RL。'
    )


def agent_capacity_context(
    config: DynamicCapacityConfig,
    *,
    actual_capacity,
    previous_capacity,
):
    context = decision_capacity_context(
        config,
        actual_capacity=actual_capacity,
    )
    context['previous_capacity'] = previous_capacity
    return context


def accident_record_for_group(group):
    return {
        'formal_round_number': formal_round_number(group.round_number),
        'incident_occurred': bool(group.incident_occurred),
        'capacity_loss_ratio': float(group.capacity_loss_ratio),
        'remaining_capacity_ratio': float(group.remaining_capacity_ratio),
        'actual_capacity': float(group.dynamic_capacity),
        'sequence_id': str(group.accident_sequence_id),
        'sequence_seed': int(group.accident_sequence_seed),
    }


def public_accident_context_for_group(group, *, after_decision=False):
    config = parse_accident_risk_config(group.session.config)
    context = accident_public_context(
        config,
        accident_record_for_group(group),
        after_decision=after_decision,
        warmup=is_warmup_round(group.round_number),
    )
    expected_incident_capacity = context['expected_incident_capacity']
    context['capacity_states'] = [
        {
            'state': 'normal',
            'capacity': config.normal_capacity,
            'probability': 1 - config.incident_probability,
        },
        {
            'state': 'incident_expected',
            'capacity': expected_incident_capacity,
            'probability': config.incident_probability,
        },
    ]
    context['capacity_reveal_timing'] = config.information_condition
    return context


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
    first_minute = C.FIRST_DEPARTURE_MINUTE
    last_minute = first_minute + (
        C.NUM_DEPARTURE_SLOTS - 1
    ) * C.DEPARTURE_CHOICE_STEP_MINUTES
    return {
        'enabled': True,
        'source': 'fixed_accident_design',
        'players_count': 20,
        'capacity_basis': 4.0,
        'required_occupied_slots': C.NUM_DEPARTURE_SLOTS,
        'slots_each_side': None,
        'num_slots': C.NUM_DEPARTURE_SLOTS,
        'slot_size_minutes': C.DEPARTURE_CHOICE_STEP_MINUTES,
        'first_departure_minute': first_minute,
        'last_departure_minute': last_minute,
        'first_departure_time': minute_to_clock(first_minute),
        'last_departure_time': minute_to_clock(last_minute),
    }


def apply_fixed_departure_schedules(session, matrix):
    for group_id, group_players in enumerate(matrix, start=1):
        schedule = static_departure_schedule()
        schedule['players_count'] = effective_group_actor_count(
            session,
            len(group_players),
            group_id,
        )
        for player in group_players:
            player.participant.vars[DEPARTURE_SCHEDULE_VAR] = deepcopy(schedule)


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
    for group_id, group_players in enumerate(matrix, start=1):
        actor_count = effective_group_actor_count(
            session,
            len(group_players),
            group_id,
        )
        if auto_enabled:
            schedule = build_dynamic_departure_schedule(
                players_count=actor_count,
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


def assign_group_treatment_metadata(session, matrix):
    for group_id, group_players in enumerate(matrix, start=1):
        api_count = api_agent_count_per_group(session, group_id)
        rl_count = rl_agent_count_per_group(session, group_id)
        treatment_group = 'HA' if api_count + rl_count > 0 else 'H'
        for player in group_players:
            player.participant.vars['dynamic_bottleneck_treatment_group'] = (
                treatment_group
            )
            player.participant.vars['dynamic_bottleneck_api_agent_count'] = api_count
            player.participant.vars['dynamic_bottleneck_rl_agent_count'] = rl_count


def accident_sequence_for_session(session):
    config = parse_accident_risk_config(session.config)
    raw_preset = str(
        session.config.get('dynamic_capacity_sequence_preset', 'auto') or 'auto'
    ).strip()
    if raw_preset.lower() == 'auto':
        if session.config.get('name') == 'dynamic_bottleneck_round_prod':
            raise ValueError('事故风险正式实验必须选择 S01-S05 固定事故序列。')
        return generate_accident_sequence(
            config,
            rounds=C.FORMAL_ROUNDS,
            sequence_id='auto',
        )
    sequence_id = raw_preset.upper()
    bank = load_accident_sequence_bank()
    if sequence_id not in bank:
        raise ValueError(
            f'未知事故序列 {sequence_id}；请选择 S01-S05 或 demo auto。'
        )
    approved = AccidentRiskConfig()
    configured_parameters = (
        config.normal_capacity,
        config.incident_probability,
        config.loss_alpha,
        config.loss_beta,
    )
    approved_parameters = (
        approved.normal_capacity,
        approved.incident_probability,
        approved.loss_alpha,
        approved.loss_beta,
    )
    if configured_parameters != approved_parameters:
        raise ValueError('固定事故序列只能与批准的 4.0/0.20/Beta 参数共同使用。')
    return deepcopy(bank[sequence_id]['rounds'])


def initialize_group_capacity_sequences(subsession, config=None):
    records = accident_sequence_for_session(subsession.session)
    subsession.session.vars[ACCIDENT_SEQUENCE_SESSION_VAR] = deepcopy(records)


def _warmup_accident_record(group, config):
    return {
        'formal_round_number': None,
        'incident_occurred': False,
        'capacity_loss_ratio': 0.0,
        'remaining_capacity_ratio': 1.0,
        'actual_capacity': config.normal_capacity,
        'sequence_id': 'warmup',
        'sequence_seed': config.seed,
    }


def apply_round_capacity(group, config=None):
    config = config or parse_accident_risk_config(group.session.config)
    players = group.get_players()
    if not players:
        return
    if is_warmup_round(group.round_number):
        record = _warmup_accident_record(group, config)
        previous_capacity = config.normal_capacity if group.round_number > 1 else 0
    else:
        records = group.session.vars.get(ACCIDENT_SEQUENCE_SESSION_VAR)
        if not isinstance(records, list) or len(records) != C.FORMAL_ROUNDS:
            records = accident_sequence_for_session(group.session)
            group.session.vars[ACCIDENT_SEQUENCE_SESSION_VAR] = deepcopy(records)
        record = records[formal_round_number(group.round_number) - 1]
        previous_record_index = formal_round_number(group.round_number) - 2
        previous_capacity = (
            records[previous_record_index]['actual_capacity']
            if previous_record_index >= 0
            else 0
        )
    incident_probability = (
        config.incident_probability
        if record['incident_occurred']
        else 1 - config.incident_probability
    )
    state = 'incident' if record['incident_occurred'] else 'normal'
    if is_warmup_round(group.round_number):
        state = 'warmup_normal'
        incident_probability = 1.0

    group.dynamic_capacity = record['actual_capacity']
    group.dynamic_capacity_state = state
    group.capacity_probability = incident_probability
    group.incident_occurred = record['incident_occurred']
    group.capacity_loss_ratio = record['capacity_loss_ratio']
    group.remaining_capacity_ratio = record['remaining_capacity_ratio']
    group.information_condition = config.information_condition
    group.accident_sequence_id = record['sequence_id']
    group.accident_sequence_seed = record['sequence_seed']
    for player in players:
        player.dynamic_capacity = record['actual_capacity']
        player.dynamic_capacity_state = state
        player.previous_round_capacity = previous_capacity
        player.capacity_probability = incident_probability
        player.capacity_reveal_timing = config.information_condition
        player.dynamic_capacity_seed = config.seed
        player.dynamic_capacity_draw_mode = 'iid_accident_beta'
        player.incident_occurred = record['incident_occurred']
        player.capacity_loss_ratio = record['capacity_loss_ratio']
        player.remaining_capacity_ratio = record['remaining_capacity_ratio']
        player.information_condition = config.information_condition
        player.accident_sequence_id = record['sequence_id']
        player.accident_sequence_seed = record['sequence_seed']
        player.actor_composition = str(
            player.participant.vars.get('dynamic_bottleneck_treatment_group', '')
        )


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
    for group_id, group_players in enumerate(matrix, start=1):
        if not group_players:
            continue
        actor_count = effective_group_actor_count(
            session,
            len(group_players),
            group_id,
        )
        schedule = departure_schedule_for_player(group_players[0])
        cache_key = (
            actor_count,
            tuple(config.values),
            schedule['num_slots'],
            schedule['first_departure_minute'],
            schedule['slot_size_minutes'],
            settings_signature,
        )
        if cache_key not in result_cache:
            result_cache[cache_key] = calibrate_tolls_for_group(
                players_count=actor_count,
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
        'calibration_players': effective_group_actor_count(
            session,
            len(player.group.get_players()),
            player.group.id_in_subsession,
        ),
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
    config = parse_accident_risk_config(subsession.session.config)
    if subsession.round_number == 1:
        validate_api_agent_count(subsession.session)
        validate_rl_agent_count(subsession.session)
        players = subsession.get_players()
        for player in players:
            player.participant.is_dropout = False
            player.participant.dropout_active = False
            player.participant.dropout_reason = ''
            player.participant.vars['dropout_suspended'] = False
            player.participant.vars['consecutive_missed_decisions'] = 0
            player.participant.vars['last_missed_round_number'] = 0
            player.participant.vars['last_manual_departure_minute'] = None
            player.participant.has_recovered_after_disconnect = False
            player.participant.has_recovered_after_timeout = False
            player.participant.finished = False
            player.participant.vars[DROPOUT_AUDIT_PARTICIPANT_VAR] = {}

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
        validate_group_agent_configuration(subsession.session, matrix)
        validate_formal_actor_composition(subsession.session, matrix)
        apply_fixed_departure_schedules(subsession.session, matrix)
        subsession.set_group_matrix(matrix)
        assign_group_metadata(matrix, grouping_enabled)
        assign_group_treatment_metadata(subsession.session, matrix)
        for group_id in range(1, len(matrix) + 1):
            group_label = f'G{group_id:02d}'
            api_count = api_agent_count_per_group(subsession.session, group_id)
            if api_count:
                initialize_api_agent_personas(
                    subsession.session,
                    [group_label],
                    api_count,
                )
            rl_count = rl_agent_count_per_group(subsession.session, group_id)
            if rl_count:
                initialize_rl_agent_personas(
                    subsession.session,
                    [group_label],
                    rl_count,
                )
        initialize_group_capacity_sequences(subsession)
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
        fill_suspended_choices(group)
        return True
    now_ts = time.time() if now_ts is None else float(now_ts)
    if not group.round_start_deadline_ts:
        group.round_start_deadline_ts = now_ts + round_start_wait_seconds(group.round_number)
    players = round_waiting_players(group)
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
    fill_suspended_choices(group)
    return True


def mark_round_ready(player, now_ts=None):
    if not participant_dropout_suspended(player):
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


def set_dropout_runtime_var(player, field_name, value):
    player.participant.vars[field_name] = value


def participant_dropout_active(player):
    return bool(participant_var(player, 'dropout_active', False))


def participant_dropout_suspended(player):
    return bool(participant_var(player, 'dropout_suspended', False))


def participant_dropout_reason(player):
    reason = str(participant_var(player, 'dropout_reason', '') or '').strip()
    return reason if reason in {'timeout', 'disconnect'} else ''


def dropout_audit_for_player_round(player):
    audit = participant_var(player, DROPOUT_AUDIT_PARTICIPANT_VAR, {})
    if not isinstance(audit, dict):
        return {}
    return audit.get(str(player.round_number), audit.get(player.round_number, {})) or {}


def save_dropout_audit(player, *, automatic_choice_strategy='', reason=''):
    audit = participant_var(player, DROPOUT_AUDIT_PARTICIPANT_VAR, {})
    if not isinstance(audit, dict):
        audit = {}
    audit[str(player.round_number)] = {
        'consecutive_missed_decisions': int(
            participant_var(player, 'consecutive_missed_decisions', 0) or 0
        ),
        'dropout_suspended': participant_dropout_suspended(player),
        'automatic_choice_strategy': str(automatic_choice_strategy or ''),
        'reason': str(reason or ''),
    }
    set_dropout_runtime_var(player, DROPOUT_AUDIT_PARTICIPANT_VAR, audit)


def record_missed_decision(player, *, reason, automatic_choice_strategy=''):
    current_round = int(player.round_number)
    last_missed_round = int(
        participant_var(player, 'last_missed_round_number', 0) or 0
    )
    streak = int(participant_var(player, 'consecutive_missed_decisions', 0) or 0)
    if last_missed_round != current_round:
        streak += 1
        set_dropout_runtime_var(player, 'last_missed_round_number', current_round)
    suspended = streak >= C.DROPOUT_SUSPEND_AFTER_MISSES
    set_dropout_runtime_var(player, 'consecutive_missed_decisions', streak)
    set_dropout_runtime_var(player, 'dropout_suspended', suspended)
    save_dropout_audit(
        player,
        automatic_choice_strategy=automatic_choice_strategy,
        reason=reason,
    )
    return suspended


def record_manual_decision(player):
    set_dropout_runtime_var(
        player,
        'last_manual_departure_minute',
        float(player.departure_minute),
    )
    set_dropout_runtime_var(player, 'consecutive_missed_decisions', 0)
    set_dropout_runtime_var(player, 'dropout_suspended', False)
    save_dropout_audit(player)


def automatic_departure_for_player(player):
    schedule = departure_schedule_for_player(player)
    last_manual = participant_var(player, 'last_manual_departure_minute')
    if departure_slot_for_minute(last_manual, schedule) is not None:
        return float(last_manual), AUTO_CHOICE_LAST_MANUAL

    neutral_minute = C.PREFERRED_ARRIVAL_MINUTE - C.FREE_FLOW_TRAVEL_MINUTES
    if departure_slot_for_minute(neutral_minute, schedule) is not None:
        return float(neutral_minute), AUTO_CHOICE_NEUTRAL_BASELINE

    closest_slot = min(
        departure_slots(schedule),
        key=lambda slot: abs(
            departure_minute_for_slot(slot, schedule) - neutral_minute
        ),
    )
    return (
        departure_minute_for_slot(closest_slot, schedule),
        AUTO_CHOICE_NEUTRAL_BASELINE,
    )


def set_automatic_departure_choice(player, decision_source):
    departure_minute, strategy = automatic_departure_for_player(player)
    set_player_departure_choice(player, departure_minute, decision_source)
    return strategy


def mark_timeout(player, automatic_choice_strategy=''):
    player.timeout_happened = True
    player.dropout_event = 'timeout'
    set_participant_var(player, 'is_dropout', True)
    set_participant_var(player, 'dropout_active', True)
    set_participant_var(player, 'dropout_reason', 'timeout')
    record_missed_decision(
        player,
        reason='timeout',
        automatic_choice_strategy=automatic_choice_strategy,
    )


def mark_disconnect(player, automatic_choice_strategy=''):
    set_participant_var(player, 'is_dropout', True)
    set_participant_var(player, 'dropout_active', True)
    if participant_dropout_reason(player) != 'timeout':
        player.dropout_event = 'disconnect'
        set_participant_var(player, 'dropout_reason', 'disconnect')
    record_missed_decision(
        player,
        reason='disconnect',
        automatic_choice_strategy=automatic_choice_strategy,
    )


def confirm_dropout_recovery(player):
    if not participant_dropout_active(player):
        return False
    reason = participant_dropout_reason(player)
    if not reason:
        return False

    was_suspended = participant_dropout_suspended(player)
    set_participant_var(player, 'dropout_active', False)
    set_participant_var(player, 'dropout_reason', '')
    set_dropout_runtime_var(player, 'dropout_suspended', False)
    if was_suspended:
        set_dropout_runtime_var(player, 'consecutive_missed_decisions', 0)
    if reason == 'timeout':
        set_participant_var(player, 'has_recovered_after_timeout', True)
    else:
        set_participant_var(player, 'has_recovered_after_disconnect', True)
    player.recovered_this_round = True
    player.recovery_reason = reason
    return True


def all_players_have_choice(group):
    return all(player_has_departure_choice(player) for player in group.get_players())


def round_waiting_players(group):
    return [
        player
        for player in group.get_players()
        if not participant_dropout_suspended(player)
    ]


def fill_suspended_choices(group):
    for player in group.get_players():
        if not participant_dropout_suspended(player) or player_has_departure_choice(player):
            continue
        strategy = set_automatic_departure_choice(
            player,
            DECISION_SOURCE_SUSPENDED_AUTO,
        )
        player.dropout_event = 'suspended_proxy'
        save_dropout_audit(
            player,
            automatic_choice_strategy=strategy,
            reason=participant_dropout_reason(player) or 'disconnect',
        )


def fill_missing_choices(group):
    for player in group.get_players():
        if player_has_departure_choice(player):
            continue
        strategy = set_automatic_departure_choice(
            player,
            DECISION_SOURCE_DISCONNECT_AUTO,
        )
        mark_disconnect(player, strategy)


def service_batch_clear_minute(first_service_start_minute, load, capacity):
    batches = max(1, ceil(int(load) / int(capacity)))
    return round(first_service_start_minute + batches * C.CAPACITY_WINDOW_MINUTES, 2)


def group_results_lock_path(group):
    return Path(tempfile.gettempdir()) / (
        'dynamic_bottleneck_round_'
        f'uid{os.getuid()}_'
        f'{group.session.code}_{group.id_in_subsession}_{group.round_number}.lock'
    )


@contextmanager
def try_group_results_lock(group):
    """Prevent duplicate Agent calls while result pages poll concurrently."""
    lock_path = group_results_lock_path(group)
    lock_file = lock_path.open('a+', encoding='utf-8')
    acquired = False
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            pass
        yield acquired
    finally:
        if acquired:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def set_results(group):
    with try_group_results_lock(group) as acquired:
        if not acquired:
            return False
        return _set_results_locked(group)


def _set_results_locked(group):
    if group.results_ready:
        return True
    if not all_players_have_choice(group):
        fill_missing_choices(group)

    api_agent_records = collect_api_agent_prefetch(group)
    if api_agent_records is API_AGENT_DECISIONS_PENDING:
        return False
    rl_agent_records = prepare_independent_rl_decisions_for_group(group)
    virtual_records = [*api_agent_records, *rl_agent_records]

    players = group.get_players()
    actors_by_minute = {}
    for player in players:
        schedule = departure_schedule_for_player(player)
        slot = player_departure_slot(player)
        if slot is None:
            strategy = set_automatic_departure_choice(
                player,
                DECISION_SOURCE_DISCONNECT_AUTO,
            )
            mark_disconnect(player, strategy)
            slot = player_departure_slot(player)
        else:
            set_player_departure_choice(player, departure_minute_for_slot(slot, schedule))
        actors_by_minute.setdefault(player.departure_minute, []).append(
            {
                'actor_type': 'human',
                'source': player,
                'reference_player': player,
                'departure_slot': slot,
            }
        )

    if players:
        reference_player = players[0]
        for record in virtual_records:
            actors_by_minute.setdefault(record['departure_minute'], []).append(
                {
                    'actor_type': record.get(
                        'actor_type',
                        API_AGENT_TYPE_DEEPSEEK,
                    ),
                    'source': record,
                    'reference_player': reference_player,
                    'departure_slot': record['departure_slot'],
                }
            )

    reference_schedule = departure_schedule_for_player(players[0])
    next_available_minute = reference_schedule['first_departure_minute']
    capacity = group.dynamic_capacity
    for departure_minute in sorted(actors_by_minute):
        same_time_actors = actors_by_minute[departure_minute]
        load = len(same_time_actors)
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

        for actor in same_time_actors:
            source = actor['source']
            reference_player = actor['reference_player']
            slot = actor['departure_slot']
            reward_bonus = reward_bonus_for_slot(group.session, slot)
            toll = coarse_toll_for_player_slot(reference_player, slot)
            cost_components = calculate_cost_components(
                queue_delay=queue_delay,
                early_minutes=early_minutes,
                late_minutes=late_minutes,
                toll=toll,
            )
            total_cost = cost_components['total_cost']
            payoff = (
                0
                if is_warmup_round(group.round_number)
                else max(0, round(C.BASE_POINTS - total_cost + reward_bonus, 2))
            )
            result_values = {
                'slot_load': load,
                'arrival_minute': round(arrival_minute, 2),
                'arrival_time_label': minute_to_clock(arrival_minute),
                'queue_delay_minutes': queue_delay,
                'travel_time_minutes': round(
                    C.FREE_FLOW_TRAVEL_MINUTES + queue_delay,
                    2,
                ),
                'early_minutes': round(early_minutes, 2),
                'late_minutes': round(late_minutes, 2),
                'reward_bonus': round(float(reward_bonus), 2),
                'coarse_toll_charge': round(float(toll), 2),
                'total_cost': round(float(total_cost), 2),
                'payoff': round(float(payoff), 2),
            }
            if actor['actor_type'] != 'human':
                source.update(result_values)
                source['arrival_time_label'] = minute_to_clock(arrival_minute)
            else:
                for field_name, value in result_values.items():
                    if field_name in {
                        'reward_bonus',
                        'coarse_toll_charge',
                        'total_cost',
                        'payoff',
                    }:
                        value = cu(value)
                    setattr(source, field_name, value)

        next_available_minute = service_batch_clear_minute(first_service_start, load, capacity)

    save_public_feedback_snapshot(group, virtual_records=virtual_records)
    if api_agent_records and not is_warmup_round(group.round_number):
        update_rl_shadow_states(group, api_agent_records)
    if rl_agent_records and not is_warmup_round(group.round_number):
        update_independent_rl_states(group, rl_agent_records)
    if virtual_records:
        save_virtual_decisions_for_group(group, virtual_records)

    if group.round_number == C.NUM_ROUNDS:
        for player in players:
            player.participant.vars[TOTAL_PAYOFF_VAR] = formal_payoff_total(player)
    group.results_ready = True
    return True


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


def virtual_decision_identity(record):
    return (
        str(record.get('actor_type', API_AGENT_TYPE_DEEPSEEK)),
        str(record.get('agent_id', '')),
    )


def active_virtual_decisions_for_group(group):
    players = group.get_players()
    if not players:
        return []
    merged = {}
    for store_key in (
        AGENT_DECISIONS_PARTICIPANT_VAR,
        INDEPENDENT_RL_DECISIONS_PARTICIPANT_VAR,
    ):
        participant_records = players[0].participant.vars.get(store_key, {})
        records = (
            participant_records.get(str(group.round_number), [])
            if isinstance(participant_records, dict)
            else []
        )
        for record in records if isinstance(records, list) else []:
            merged[virtual_decision_identity(record)] = deepcopy(record)
    return list(merged.values())


def save_virtual_decisions_for_group(group, records):
    players = group.get_players()
    if players:
        records_by_store = {
            AGENT_DECISIONS_PARTICIPANT_VAR: [],
            INDEPENDENT_RL_DECISIONS_PARTICIPANT_VAR: [],
        }
        for record in records:
            store_key = (
                INDEPENDENT_RL_DECISIONS_PARTICIPANT_VAR
                if record.get('actor_type') == RL_AGENT_TYPE
                else AGENT_DECISIONS_PARTICIPANT_VAR
            )
            records_by_store[store_key].append(record)
        for store_key, store_records in records_by_store.items():
            if not store_records:
                continue
            _save_virtual_decisions_to_store(
                players[0].participant,
                store_key,
                group.round_number,
                store_records,
            )


def _save_virtual_decisions_to_store(participant, store_key, round_number, records):
    stored = participant.vars.get(store_key, {})
    by_round = deepcopy(stored) if isinstance(stored, dict) else {}
    round_key = str(round_number)
    merged = {
        virtual_decision_identity(record): deepcopy(record)
        for record in by_round.get(round_key, [])
    }
    for record in records:
        merged[virtual_decision_identity(record)] = deepcopy(record)
    by_round[round_key] = list(merged.values())
    participant.vars[store_key] = by_round


def active_agent_decisions_for_group(group):
    if api_agent_mode(group.session) != API_AGENT_MODE_ACTIVE:
        return []
    return [
        record
        for record in active_virtual_decisions_for_group(group)
        if record.get('actor_type', API_AGENT_TYPE_DEEPSEEK)
        in {API_AGENT_TYPE_DEEPSEEK, API_AGENT_LEGACY_ACTOR_TYPE}
    ]


def save_agent_decisions_for_group(group, records):
    save_virtual_decisions_for_group(group, records)


def limited_memory_for_agent(group, agent_id):
    if not api_agent_limited_memory_enabled(group.session):
        return ''
    players = group.get_players()
    if not players:
        return ''
    store = players[0].participant.vars.get(API_AGENT_MEMORY_PARTICIPANT_VAR, {})
    if not isinstance(store, dict):
        return ''
    value = store.get(str(agent_id), '')
    return str(value)[:api_agent_limited_memory_max_chars(group.session)]


def save_api_agent_memory_updates(group, records):
    if (
        is_warmup_round(group.round_number)
        or not api_agent_limited_memory_enabled(group.session)
    ):
        return
    players = group.get_players()
    if not players:
        return
    stored = players[0].participant.vars.get(API_AGENT_MEMORY_PARTICIPANT_VAR, {})
    memory_by_agent = deepcopy(stored) if isinstance(stored, dict) else {}
    max_chars = api_agent_limited_memory_max_chars(group.session)
    changed = False
    for record in records:
        if record.get('decision_source') != 'deepseek_api':
            continue
        memory_output = record.get('memory_output')
        if not isinstance(memory_output, str) or not memory_output.strip():
            continue
        memory_by_agent[str(record.get('agent_id', ''))] = memory_output[:max_chars]
        changed = True
    if changed:
        players[0].participant.vars[API_AGENT_MEMORY_PARTICIPANT_VAR] = memory_by_agent


def virtual_decisions_for_group_round(group, round_number):
    players = group.get_players()
    if not players:
        return []
    merged = {}
    for store_key in (
        AGENT_DECISIONS_PARTICIPANT_VAR,
        INDEPENDENT_RL_DECISIONS_PARTICIPANT_VAR,
    ):
        participant_store = players[0].participant.vars.get(store_key, {})
        records = (
            participant_store.get(str(round_number), [])
            if isinstance(participant_store, dict)
            else []
        )
        for record in records if isinstance(records, list) else []:
            merged[virtual_decision_identity(record)] = deepcopy(record)
    return list(merged.values())


def previous_public_feedback_for_group(group):
    current_formal_round = formal_round_number(group.round_number)
    previous_round = (
        int(group.round_number) - 1
        if current_formal_round is not None and current_formal_round > 1
        else None
    )
    players = group.get_players()
    if previous_round is None or not players:
        return None
    stored = players[0].participant.vars.get(PUBLIC_FEEDBACK_PARTICIPANT_VAR, {})
    snapshot = stored.get(str(previous_round)) if isinstance(stored, dict) else None
    return deepcopy(snapshot) if isinstance(snapshot, dict) else None


def public_personal_result_from_record(record):
    public_fields = (
        'round_number',
        'departure_time_label',
        'queue_delay_minutes',
        'arrival_time_label',
        'slot_load',
        'travel_time_minutes',
        'total_cost',
    )
    return {
        field_name: record.get(field_name)
        for field_name in public_fields
    }


def agent_history_for_group(group, agent_id):
    current_formal_round = formal_round_number(group.round_number)
    previous_round = (
        int(group.round_number) - 1
        if current_formal_round is not None and current_formal_round > 1
        else None
    )
    public_feedback = previous_public_feedback_for_group(group)
    own_result = None
    if previous_round is not None and public_feedback is not None:
        for record in virtual_decisions_for_group_round(group, previous_round):
            if record.get('agent_id') == agent_id:
                own_result = public_personal_result_from_record(record)
                break
    return {
        'public_feedback': public_feedback,
        'own_previous_result': own_result,
    }


def api_agent_choice_set_for_group(group, reference_player, agent_id, persona):
    schedule = departure_schedule_for_player(reference_player)
    preview = choice_preview(reference_player)
    phase = round_phase_context(group.round_number)
    capacity_context = public_accident_context_for_group(group)
    capacity_context['experiment_phase'] = phase['phase_name']
    capacity_context['round_label'] = phase['round_label']
    return AgentChoiceSet(
        round_number=phase['display_round_number'],
        total_rounds=phase['display_total_rounds'],
        available_slots=[
            {
                'slot': item['slot'],
                'departure_minute': item['minute'],
                'departure_time': item['time'],
            }
            for item in preview
        ],
        cost_parameters={
            'fixed_travel_time_cost': C.FIXED_TRAVEL_TIME_COST,
            'queue_cost_per_minute': C.QUEUE_COST_PER_MINUTE,
            'early_cost_per_minute': C.EARLY_COST_PER_MINUTE,
            'late_cost_per_minute': C.LATE_COST_PER_MINUTE,
            'preferred_arrival_minute': C.PREFERRED_ARRIVAL_MINUTE,
            'preferred_arrival_time': minute_to_clock(C.PREFERRED_ARRIVAL_MINUTE),
            'free_flow_travel_minutes': C.FREE_FLOW_TRAVEL_MINUTES,
            'first_departure_minute': schedule['first_departure_minute'],
            'last_departure_minute': schedule['last_departure_minute'],
            'capacity_window_minutes': C.CAPACITY_WINDOW_MINUTES,
        },
        capacity_context=capacity_context,
        tolls=[
            {'slot': item['slot'], 'charge': item['toll']}
            for item in preview
            if item['toll_active']
        ],
        rewards=[
            {'slot': item['slot'], 'bonus': item['reward']}
            for item in preview
            if item['reward']
        ],
        history=agent_history_for_group(group, agent_id),
        agent_id=agent_id,
        persona=persona,
        limited_memory=limited_memory_for_agent(group, agent_id),
    )


def rl_state_store_for_group(group):
    players = group.get_players()
    if not players:
        return {}, None
    stored = players[0].participant.vars.get(RL_AGENT_STATE_PARTICIPANT_VAR, {})
    return deepcopy(stored) if isinstance(stored, dict) else {}, players[0]


def independent_rl_state_store_for_group(group):
    players = group.get_players()
    if not players:
        return {}, None
    reference_player = players[0]
    stored = reference_player.participant.vars.get(
        INDEPENDENT_RL_STATE_PARTICIPANT_VAR,
        {},
    )
    return (
        deepcopy(stored) if isinstance(stored, dict) else {},
        reference_player,
    )


def independent_rl_records_for_group(group):
    return [
        record
        for record in active_virtual_decisions_for_group(group)
        if record.get('actor_type') == RL_AGENT_TYPE
    ]


def prepare_independent_rl_decisions_for_group(group):
    if not rl_agent_enabled(group.session):
        return []
    existing = independent_rl_records_for_group(group)
    if existing:
        return existing
    players = group.get_players()
    if not players:
        return []

    reference_player = players[0]
    schedule = departure_schedule_for_player(reference_player)
    group_label = reference_player.participant.vars.get(
        'assigned_group_label',
        f'G{group.id_in_subsession:02d}',
    )
    states, _reference_player = independent_rl_state_store_for_group(group)
    records = []
    phase = round_phase_context(group.round_number)
    for index in range(
        1,
        rl_agent_count_per_group(group.session, group.id_in_subsession) + 1,
    ):
        agent_id = f'{group_label}_RL_{index:02d}'
        persona = get_or_create_rl_agent_persona(
            group.session,
            group_label,
            agent_id,
        )
        choice_set = api_agent_choice_set_for_group(
            group,
            reference_player,
            agent_id,
            persona,
        )
        capacity_states = choice_set.capacity_context.get('capacity_states', [])
        state = valid_or_initial_independent_rl_state(
            states.get(agent_id),
            capacity_states,
        )
        try:
            choice = choose_independent_rl_departure(
                state=state,
                available_slots=choice_set.available_slots,
                cost_parameters=choice_set.cost_parameters,
                capacity_states=capacity_states,
                tolls=choice_set.tolls,
                rewards=choice_set.rewards,
                persona=persona,
                known_current_capacity=choice_set.capacity_context.get(
                    'actual_capacity'
                ),
                known_incident_status=choice_set.capacity_context.get(
                    'incident_occurred'
                ),
            )
            slot = int(choice['departure_slot'])
            decision_source = str(choice['decision_source'])
            reason = str(choice.get('reason', ''))
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            slot = int(fallback_lowest_schedule_cost(choice_set))
            decision_source = 'rl_fallback_lowest_schedule_cost'
            reason = f'Independent RL policy failed: {exc}'
            choice = {
                'policy_version': INDEPENDENT_RL_POLICY_VERSION,
                'rounds_observed': int(state.get('rounds_observed', 0)),
                'belief': {},
            }
        departure_minute = departure_minute_for_slot(slot, schedule)
        records.append(
            {
                'actor_type': RL_AGENT_TYPE,
                'agent_id': agent_id,
                'agent_type': RL_AGENT_TYPE,
                'api_agent_mode': api_agent_mode(group.session),
                'policy_version': str(choice['policy_version']),
                'persona_id': str(persona['persona_id']),
                'persona_label': str(persona['label']),
                'group_id': int(group.id_in_subsession),
                'round_number': phase['display_round_number'],
                'dynamic_capacity': int(group.dynamic_capacity),
                'dynamic_capacity_state': str(group.dynamic_capacity_state),
                'capacity_probability': float(group.capacity_probability),
                'capacity_reveal_timing': (
                    REVEAL_BEFORE_DECISION
                    if phase['is_warmup']
                    else str(group.session.config.get(
                        'capacity_reveal_timing',
                        REVEAL_BEFORE_DECISION,
                    ))
                ),
                'departure_slot': slot,
                'departure_minute': round(departure_minute, 2),
                'departure_time_label': minute_to_clock(departure_minute),
                'decision_source': decision_source,
                'fallback_used': decision_source.startswith('rl_fallback_'),
                'latency_ms': 0,
                'reason': reason,
                'raw_response_json': '',
                'context_json': json.dumps(
                    {
                        'agent_id': agent_id,
                        'round_number': phase['display_round_number'],
                        'capacity_context': choice_set.capacity_context,
                        'belief': choice.get('belief', {}),
                        'rounds_observed': int(
                            choice.get('rounds_observed', 0)
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                'rounds_observed': int(choice.get('rounds_observed', 0)),
                'state_updated': False,
                'arrival_minute': 0,
                'arrival_time_label': '',
                'queue_delay_minutes': 0,
                'travel_time_minutes': 0,
                'early_minutes': 0,
                'late_minutes': 0,
                'slot_load': 0,
                'reward_bonus': 0,
                'coarse_toll_charge': 0,
                'total_cost': 0,
                'payoff': 0,
            }
        )
    save_virtual_decisions_for_group(group, records)
    return deepcopy(records)


def update_independent_rl_states(group, records):
    if (
        is_warmup_round(group.round_number)
        or not rl_agent_enabled(group.session)
        or not records
    ):
        return
    feedback = current_public_feedback_for_group(group)
    if feedback is None:
        return
    observation = public_feedback_observation(feedback)
    config = parse_dynamic_capacity_config(group.session.config)
    capacity_states = capacity_state_rows(config)
    states, reference_player = independent_rl_state_store_for_group(group)
    if reference_player is None:
        return
    group_label = reference_player.participant.vars.get(
        'assigned_group_label',
        f'G{group.id_in_subsession:02d}',
    )
    changed = False
    for record in records:
        if record.get('actor_type') != RL_AGENT_TYPE:
            continue
        agent_id = str(record['agent_id'])
        state = valid_or_initial_independent_rl_state(
            states.get(agent_id),
            capacity_states,
        )
        if int(state.get('rounds_observed', 0)) >= formal_round_number(group.round_number):
            continue
        persona = get_or_create_rl_agent_persona(
            group.session,
            group_label,
            agent_id,
        )
        states[agent_id] = observe_independent_rl_outcome(
            state,
            revealed_capacity=observation['revealed_capacity'],
            departure_slot=record['departure_slot'],
            total_cost=record['total_cost'],
            anonymous_slot_counts=observation['anonymous_slot_counts'],
            departure_average_costs=observation['departure_average_costs'],
            group_average_cost=observation['group_average_cost'],
            own_public_result=public_personal_result_from_record(record),
            persona=persona,
        )
        record['state_updated'] = True
        record['rounds_observed'] = states[agent_id]['rounds_observed']
        changed = True
    if changed:
        reference_player.participant.vars[
            INDEPENDENT_RL_STATE_PARTICIPANT_VAR
        ] = states
        save_virtual_decisions_for_group(group, records)


def rl_candidate_for_choice_set(group, choice_set):
    if not rl_fallback_enabled(group.session):
        return None
    capacity_states = choice_set.capacity_context.get('capacity_states', [])
    if not capacity_states:
        return None
    store, _reference_player = rl_state_store_for_group(group)
    state = valid_or_initial_state(store.get(choice_set.agent_id), capacity_states)
    visible_capacity = choice_set.capacity_context.get('actual_capacity')
    choice = choose_rl_departure(
        state=state,
        available_slots=choice_set.available_slots,
        cost_parameters=choice_set.cost_parameters,
        capacity_states=capacity_states,
        tolls=choice_set.tolls,
        rewards=choice_set.rewards,
        persona=choice_set.persona,
        known_current_capacity=visible_capacity,
        known_incident_status=choice_set.capacity_context.get(
            'incident_occurred'
        ),
    )
    return {
        **choice,
        'context_json': json.dumps(
            {
                'agent_id': choice_set.agent_id,
                'round_number': choice_set.round_number,
                'capacity_context': choice_set.capacity_context,
                'rl_policy': choice,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    }


def prepare_rl_candidates_for_agents(group, prepared_agents):
    candidates = {}
    for agent_id, choice_set in prepared_agents:
        try:
            candidates[agent_id] = rl_candidate_for_choice_set(group, choice_set)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            candidates[agent_id] = None
    return candidates


def apply_rl_fallback_to_choice(choice, choice_set, rl_candidate):
    if not choice.fallback_used or not rl_candidate:
        return choice
    slot = int(rl_candidate['departure_slot'])
    if slot not in choice_set.valid_slots():
        return choice
    reason = str(rl_candidate.get('reason', 'Local RL fallback selected an action.'))
    if choice.reason:
        reason = f'{reason} DeepSeek failure: {choice.reason}'
    return AgentChoice(
        departure_slot=slot,
        decision_source='deepseek_fallback_rl',
        fallback_used=True,
        reason=reason,
        latency_ms=choice.latency_ms,
        raw_response_json=choice.raw_response_json,
        context_json=str(rl_candidate.get('context_json', choice.context_json)),
        memory_summary=None,
    )


def update_rl_shadow_states(group, agent_records):
    if (
        is_warmup_round(group.round_number)
        or not rl_fallback_enabled(group.session)
        or not agent_records
    ):
        return
    feedback = current_public_feedback_for_group(group)
    if feedback is None:
        return
    observation = public_feedback_observation(feedback)
    config = parse_dynamic_capacity_config(group.session.config)
    capacity_states = capacity_state_rows(config)
    store, reference_player = rl_state_store_for_group(group)
    if reference_player is None:
        return
    changed = False
    for record in agent_records:
        agent_id = str(record['agent_id'])
        state = valid_or_initial_state(store.get(agent_id), capacity_states)
        if int(state.get('rounds_observed', 0)) >= formal_round_number(group.round_number):
            continue
        group_label = reference_player.participant.vars.get(
            'assigned_group_label',
            f'G{group.id_in_subsession:02d}',
        )
        persona = get_or_create_api_agent_persona(
            group.session,
            group_label,
            agent_id,
        )
        store[agent_id] = observe_rl_outcome(
            state,
            revealed_capacity=observation['revealed_capacity'],
            departure_slot=record['departure_slot'],
            total_cost=record['total_cost'],
            anonymous_slot_counts=observation['anonymous_slot_counts'],
            departure_average_costs=observation['departure_average_costs'],
            group_average_cost=observation['group_average_cost'],
            own_public_result=public_personal_result_from_record(record),
            persona=persona,
        )
        record['rl_shadow_updated'] = True
        changed = True
    if changed:
        reference_player.participant.vars[RL_AGENT_STATE_PARTICIPANT_VAR] = store


def choose_api_agent_departures(config, choice_sets):
    if not choice_sets:
        return []

    def choose_one(choice_set):
        return choose_agent_departure(config=config, choice_set=choice_set)

    with ThreadPoolExecutor(max_workers=len(choice_sets)) as executor:
        return list(executor.map(choose_one, choice_sets))


def api_agent_prefetch_key(group):
    return (
        str(group.session.code),
        int(group.id_in_subsession),
        int(group.round_number),
    )


def prepare_api_agent_requests_for_group(group):
    players = group.get_players()
    if not players:
        return None, []
    reference_player = players[0]
    group_label = reference_player.participant.vars.get(
        'assigned_group_label',
        f'G{group.id_in_subsession:02d}',
    )
    prepared_agents = []
    for index in range(
        1,
        api_agent_count_per_group(group.session, group.id_in_subsession) + 1,
    ):
        agent_id = f'{group_label}_API_{index:02d}'
        persona = get_or_create_api_agent_persona(
            group.session,
            group_label,
            agent_id,
        )
        prepared_agents.append(
            (
                agent_id,
                api_agent_choice_set_for_group(
                    group,
                    reference_player,
                    agent_id,
                    persona,
                ),
            )
        )
    return config_from_session(group.session.config), prepared_agents


def build_api_agent_records_for_group(group, prepared_agents, choices):
    players = group.get_players()
    if not players:
        return []
    schedule = departure_schedule_for_player(players[0])
    records = []
    phase = round_phase_context(group.round_number)
    for (agent_id, choice_set), choice in zip(prepared_agents, choices):
        slot = int(choice.departure_slot)
        departure_minute = departure_minute_for_slot(slot, schedule)
        persona = choice_set.persona or {}
        records.append(
            {
                'actor_type': API_AGENT_TYPE_DEEPSEEK,
                'agent_id': agent_id,
                'agent_type': API_AGENT_TYPE_DEEPSEEK,
                'api_agent_mode': API_AGENT_MODE_ACTIVE,
                'policy_version': str(
                    group.session.config.get(
                        'api_agent_policy_version',
                        group.session.config.get('api_agent_model', ''),
                    )
                ),
                'persona_id': str(persona.get('persona_id', '')),
                'persona_label': str(persona.get('label', '')),
                'group_id': int(group.id_in_subsession),
                'round_number': phase['display_round_number'],
                'dynamic_capacity': int(group.dynamic_capacity),
                'dynamic_capacity_state': str(group.dynamic_capacity_state),
                'capacity_probability': float(group.capacity_probability),
                'capacity_reveal_timing': (
                    REVEAL_BEFORE_DECISION
                    if phase['is_warmup']
                    else str(group.session.config.get(
                        'capacity_reveal_timing',
                        REVEAL_BEFORE_DECISION,
                    ))
                ),
                'departure_slot': slot,
                'departure_minute': round(departure_minute, 2),
                'departure_time_label': minute_to_clock(departure_minute),
                'decision_source': choice.decision_source,
                'fallback_used': bool(choice.fallback_used),
                'latency_ms': int(choice.latency_ms),
                'reason': choice.reason,
                'raw_response_json': choice.raw_response_json,
                'context_json': choice.context_json,
                'memory_input': choice_set.limited_memory,
                'memory_output': getattr(choice, 'memory_summary', None),
                'arrival_minute': 0,
                'arrival_time_label': '',
                'queue_delay_minutes': 0,
                'travel_time_minutes': 0,
                'early_minutes': 0,
                'late_minutes': 0,
                'slot_load': 0,
                'reward_bonus': 0,
                'coarse_toll_charge': 0,
                'total_cost': 0,
                'payoff': 0,
            }
        )
    return records


def _fallback_choices_for_prefetch_error(prepared_agents, exc):
    reason = f'Agent prefetch worker failed: {exc}'
    return [
        AgentChoice(
            departure_slot=fallback_lowest_schedule_cost(choice_set),
            decision_source='fallback_lowest_schedule_cost',
            fallback_used=True,
            reason=reason,
            context_json=json.dumps(
                asdict(choice_set),
                ensure_ascii=False,
                sort_keys=True,
            ),
            memory_summary=None,
        )
        for _agent_id, choice_set in prepared_agents
    ]


def start_api_agent_prefetch(group):
    if api_agent_mode(group.session) != API_AGENT_MODE_ACTIVE:
        return None
    if active_agent_decisions_for_group(group):
        return None

    key = api_agent_prefetch_key(group)
    with _API_AGENT_PREFETCH_LOCK:
        current = _API_AGENT_PREFETCH_TASKS.get(key)
        if current:
            return current['future']

        config, prepared_agents = prepare_api_agent_requests_for_group(group)
        if not prepared_agents:
            return None
        rl_candidates = prepare_rl_candidates_for_agents(group, prepared_agents)
        future = _API_AGENT_PREFETCH_EXECUTOR.submit(
            choose_api_agent_departures,
            config,
            [choice_set for _, choice_set in prepared_agents],
        )
        _API_AGENT_PREFETCH_TASKS[key] = {
            'future': future,
            'prepared_agents': prepared_agents,
            'rl_candidates': rl_candidates,
        }
        return future


def collect_api_agent_prefetch(group):
    if api_agent_mode(group.session) != API_AGENT_MODE_ACTIVE:
        return []
    existing = active_agent_decisions_for_group(group)
    if existing:
        return existing

    future = start_api_agent_prefetch(group)
    if future is None:
        return []
    key = api_agent_prefetch_key(group)
    with _API_AGENT_PREFETCH_LOCK:
        task = _API_AGENT_PREFETCH_TASKS.get(key)
    if not task or not future.done():
        return API_AGENT_DECISIONS_PENDING

    try:
        choices = future.result()
    except Exception as exc:
        choices = _fallback_choices_for_prefetch_error(
            task['prepared_agents'],
            exc,
        )
    rl_candidates = task.get('rl_candidates', {})
    if rl_candidates:
        choices = [
            apply_rl_fallback_to_choice(
                choice,
                choice_set,
                rl_candidates.get(agent_id),
            )
            for (agent_id, choice_set), choice in zip(task['prepared_agents'], choices)
        ]
    records = build_api_agent_records_for_group(
        group,
        task['prepared_agents'],
        choices,
    )
    save_agent_decisions_for_group(group, records)
    save_api_agent_memory_updates(group, records)
    with _API_AGENT_PREFETCH_LOCK:
        if _API_AGENT_PREFETCH_TASKS.get(key) is task:
            _API_AGENT_PREFETCH_TASKS.pop(key, None)
    return deepcopy(records)


def create_api_agent_decisions_for_group(group):
    """Synchronous compatibility wrapper used by tests and offline tools."""
    if api_agent_mode(group.session) != API_AGENT_MODE_ACTIVE:
        return []
    existing = active_agent_decisions_for_group(group)
    if existing:
        return existing

    future = start_api_agent_prefetch(group)
    if future is None:
        return []
    try:
        future.result()
    except Exception:
        # The collector converts worker failures to the configured local fallback.
        pass
    records = collect_api_agent_prefetch(group)
    if records is API_AGENT_DECISIONS_PENDING:
        return []
    return records


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
    for record in active_virtual_decisions_for_group(group):
        slot = int(record.get('departure_slot', 0) or 0)
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


def public_feedback_snapshot_for_group(group, *, virtual_records=None):
    players = group.get_players()
    schedule = (
        departure_schedule_for_player(players[0])
        if players
        else static_departure_schedule()
    )
    costs_by_slot = {slot: [] for slot in departure_slots(schedule)}
    for player in players:
        slot = player_departure_slot(player)
        if slot in costs_by_slot and player_has_departure_choice(player):
            costs_by_slot[slot].append(float(player.total_cost))
    records = (
        active_virtual_decisions_for_group(group)
        if virtual_records is None
        else virtual_records
    )
    for record in records:
        slot = int(record.get('departure_slot', 0) or 0)
        if slot in costs_by_slot:
            costs_by_slot[slot].append(float(record.get('total_cost', 0)))

    all_costs = [cost for values in costs_by_slot.values() for cost in values]
    return {
        'round_number': round_phase_context(group.round_number)['display_round_number'],
        'dynamic_capacity': int(group.dynamic_capacity),
        'departure_outcomes': [
            {
                'slot': slot,
                'departure_minute': departure_minute_for_slot(slot, schedule),
                'departure_time': minute_to_clock(
                    departure_minute_for_slot(slot, schedule)
                ),
                'participant_count': len(costs_by_slot[slot]),
                'average_cost': (
                    round(sum(costs_by_slot[slot]) / len(costs_by_slot[slot]), 4)
                    if costs_by_slot[slot]
                    else None
                ),
            }
            for slot in departure_slots(schedule)
        ],
        'group_average_cost': (
            round(sum(all_costs) / len(all_costs), 4) if all_costs else 0
        ),
    }


def save_public_feedback_snapshot(group, *, virtual_records=None):
    players = group.get_players()
    if not players:
        return {}
    snapshot = public_feedback_snapshot_for_group(
        group,
        virtual_records=virtual_records,
    )
    stored = players[0].participant.vars.get(PUBLIC_FEEDBACK_PARTICIPANT_VAR, {})
    by_round = deepcopy(stored) if isinstance(stored, dict) else {}
    by_round[str(group.round_number)] = snapshot
    players[0].participant.vars[PUBLIC_FEEDBACK_PARTICIPANT_VAR] = by_round
    return deepcopy(snapshot)


def current_public_feedback_for_group(group):
    players = group.get_players()
    if not players:
        return None
    stored = players[0].participant.vars.get(PUBLIC_FEEDBACK_PARTICIPANT_VAR, {})
    snapshot = (
        stored.get(str(group.round_number))
        if isinstance(stored, dict)
        else None
    )
    return deepcopy(snapshot) if isinstance(snapshot, dict) else None


def result_current_round_cost_snapshot(player):
    schedule = departure_schedule_for_player(player)
    public_snapshot = current_public_feedback_for_group(player.group)
    if public_snapshot is None:
        public_snapshot = public_feedback_snapshot_for_group(player.group)
    outcome_by_slot = {
        int(row['slot']): row
        for row in public_snapshot.get('departure_outcomes', [])
    }
    averages = [
        float(row['average_cost'])
        for row in outcome_by_slot.values()
        if row.get('average_cost') is not None
    ]
    group_average = float(public_snapshot.get('group_average_cost', 0))
    axis_max = max(5, ceil(max(averages + [group_average, 0]) / 5) * 5)
    current_slot = player_departure_slot(player)
    bars = []
    for slot in departure_slots(schedule):
        outcome = outcome_by_slot.get(slot, {})
        participant_count = int(outcome.get('participant_count', 0))
        has_participants = participant_count > 0
        average_cost = float(outcome.get('average_cost') or 0)
        bars.append(
            {
                'slot': slot,
                'departure_time': minute_to_clock(departure_minute_for_slot(slot, schedule)),
                'participant_count': participant_count,
                'has_participants': has_participants,
                'average_cost_label': (
                    number_display(average_cost) if has_participants else '-'
                ),
                'height_pct': (
                    round((average_cost / axis_max) * 100, 2)
                    if has_participants
                    else 0
                ),
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
    exported_round_number = formal_round_number(player.round_number)
    dropout_audit = dropout_audit_for_player_round(player)
    return [
        player.session.code,
        player.participant.code,
        player.group.id_in_subsession,
        exported_round_number,
        player.dynamic_capacity,
        player.dynamic_capacity_state,
        (
            player.previous_round_capacity
            if exported_round_number is not None and exported_round_number > 1
            else ''
        ),
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
        int(dropout_audit.get('consecutive_missed_decisions', 0) or 0),
        bool(dropout_audit.get('dropout_suspended', False)),
        dropout_audit.get('automatic_choice_strategy', ''),
        'human',
        '',
        '',
        api_agent_mode(player.session),
        '',
        '',
        '',
        '',
        '',
        '',
        '',
        '',
        '',
        '',
    ]


def agent_decisions_for_players(players):
    groups = {}
    for player in players:
        if is_warmup_round(player.round_number):
            continue
        key = (
            player.session.code,
            player.round_number,
            player.group.id_in_subsession,
        )
        groups.setdefault(key, player.group)
    decisions = []
    for key, group in sorted(groups.items()):
        group_players = group.get_players()
        if not group_players:
            continue
        reference_player = group_players[0]
        for record in active_virtual_decisions_for_group(group):
            decisions.append((record, reference_player))
    return decisions


def export_row_for_agent_record(record, reference_player):
    row = export_row_for_player(reference_player)
    values = {
        'participant_code': '',
        'group_id': record.get('group_id', reference_player.group.id_in_subsession),
        'round_number': formal_round_number(reference_player.round_number),
        'dynamic_capacity': record.get('dynamic_capacity', reference_player.dynamic_capacity),
        'dynamic_capacity_state': record.get(
            'dynamic_capacity_state',
            reference_player.dynamic_capacity_state,
        ),
        'departure_slot': record.get('departure_slot', ''),
        'departure_minute': record.get('departure_minute', ''),
        'queue_delay_minutes': record.get('queue_delay_minutes', 0),
        'arrival_minute': record.get('arrival_minute', 0),
        'early_minutes': record.get('early_minutes', 0),
        'late_minutes': record.get('late_minutes', 0),
        'total_cost': record.get('total_cost', 0),
        'payoff': record.get('payoff', 0),
        'decision_source': record.get('decision_source', ''),
        'timeout_happened': False,
        'dropout_event': '',
        'recovered_this_round': False,
        'recovery_reason': '',
        'historical_dropout': False,
        'dropout_active_at_export': False,
        'dropout_reason_at_export': '',
        'has_recovered_after_disconnect': False,
        'has_recovered_after_timeout': False,
        'consecutive_missed_decisions': 0,
        'dropout_suspended': False,
        'automatic_choice_strategy': '',
        'coarse_toll_charge': record.get('coarse_toll_charge', 0),
        'actor_type': record.get('actor_type', API_AGENT_TYPE_DEEPSEEK),
        'agent_id': record.get('agent_id', ''),
        'agent_type': record.get('agent_type', ''),
        'api_agent_mode': record.get('api_agent_mode', API_AGENT_MODE_ACTIVE),
        'agent_fallback_used': bool(record.get('fallback_used', False)),
        'agent_latency_ms': int(record.get('latency_ms', 0)),
        'agent_reason': record.get('reason', ''),
        'agent_context_json': record.get('context_json', ''),
        'agent_memory_input': record.get('memory_input', ''),
        'agent_memory_output': record.get('memory_output', ''),
        'agent_persona_id': record.get('persona_id', ''),
        'agent_persona_label': record.get('persona_label', ''),
        'rl_policy_version': record.get('policy_version', ''),
        'rl_rounds_observed': record.get('rounds_observed', ''),
    }
    for field_name, value in values.items():
        row[EXPORT_HEADERS.index(field_name)] = value
    return row


def build_admin_report_rows(players):
    players = [
        player for player in players
        if not is_warmup_round(player.round_number)
    ]
    groups = {}
    state_counts = {}
    api_agent_record_count = 0
    independent_rl_agent_count = 0
    agent_fallback_count = 0
    rl_fallback_count = 0
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
        virtual_records = active_virtual_decisions_for_group(representative.group)
        api_agent_records = [
            record for record in virtual_records
            if record.get('actor_type', API_AGENT_TYPE_DEEPSEEK)
            in {API_AGENT_TYPE_DEEPSEEK, API_AGENT_LEGACY_ACTOR_TYPE}
        ]
        independent_rl_records = [
            record for record in virtual_records
            if record.get('actor_type') == RL_AGENT_TYPE
        ]
        api_agent_record_count += len(api_agent_records)
        independent_rl_agent_count += len(independent_rl_records)
        agent_fallback_count += sum(
            bool(record.get('fallback_used'))
            for record in api_agent_records
        )
        rl_fallback_count += sum(
            record.get('decision_source') == 'deepseek_fallback_rl'
            for record in api_agent_records
        )
        queue_values = [
            float(player.queue_delay_minutes)
            for player in completed
        ] + [
            float(record.get('queue_delay_minutes', 0))
            for record in virtual_records
        ]
        cost_values = [
            float(player.total_cost)
            for player in completed
        ] + [
            float(record.get('total_cost', 0))
            for record in virtual_records
        ]
        toll_values = [
            float(player.coarse_toll_charge)
            for player in completed
        ] + [
            float(record.get('coarse_toll_charge', 0))
            for record in virtual_records
        ]
        average_queue = (
            sum(queue_values) / len(queue_values)
            if queue_values else 0
        )
        average_cost = (
            sum(cost_values) / len(cost_values)
            if cost_values else 0
        )
        average_toll = (
            sum(toll_values) / len(toll_values)
            if toll_values else 0
        )
        distribution = {}
        for player in completed:
            label = minute_to_clock(player.departure_minute)
            distribution[label] = distribution.get(label, 0) + 1
        for record in virtual_records:
            label = minute_to_clock(record.get('departure_minute', 0))
            distribution[label] = distribution.get(label, 0) + 1
        round_rows.append(
            {
                'round_number': formal_round_number(round_number),
                'group_id': group_id,
                'dynamic_capacity': representative.dynamic_capacity,
                'dynamic_capacity_state': representative.dynamic_capacity_state,
                'capacity_probability': probability_display(representative.capacity_probability),
                'average_queue_delay': number_display(average_queue),
                'average_cost': number_display(average_cost),
                'coarse_toll_time_window_spec': representative.coarse_toll_time_window_spec or '无',
                'coarse_toll_points': number_display(representative.coarse_toll_points),
                'average_toll': number_display(average_toll),
                'api_agent_count': len(api_agent_records),
                'independent_rl_agent_count': len(independent_rl_records),
                'agent_fallback_count': sum(
                    bool(record.get('fallback_used'))
                    for record in api_agent_records
                ),
                'rl_fallback_count': sum(
                    record.get('decision_source') == 'deepseek_fallback_rl'
                    for record in api_agent_records
                ),
                'departure_distribution': ', '.join(
                    f'{time_label}: {count}' for time_label, count in sorted(distribution.items())
                ) or '暂无决策',
            }
        )
    state_rows = [
        {'state': state, 'count': count}
        for state, count in sorted(state_counts.items())
    ]
    return round_rows, state_rows, {
        'api_agent_mode': (
            api_agent_mode(players[0].session)
            if players else API_AGENT_MODE_OFF
        ),
        'api_agent_record_count': api_agent_record_count,
        'independent_rl_agent_count': independent_rl_agent_count,
        'agent_fallback_count': agent_fallback_count,
        'rl_fallback_enabled': (
            rl_fallback_enabled(players[0].session) if players else False
        ),
        'rl_fallback_count': rl_fallback_count,
        'limited_memory_enabled': (
            api_agent_limited_memory_enabled(players[0].session)
            if players else False
        ),
        'limited_memory_max_chars': (
            api_agent_limited_memory_max_chars(players[0].session)
            if players else 400
        ),
    }


def vars_for_admin_report(subsession):
    players = []
    for round_subsession in subsession.in_all_rounds():
        players.extend(round_subsession.get_players())
    round_rows, state_rows, agent_summary = build_admin_report_rows(players)
    return {
        'round_rows': round_rows,
        'state_rows': state_rows,
        'agent_summary': agent_summary,
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
            'total_rounds': C.FORMAL_ROUNDS,
            'warmup_rounds': C.WARMUP_ROUNDS,
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


class WarmupStart(Page):
    @staticmethod
    def is_displayed(player):
        return player.round_number == 1 and access_allowed(player)

    @staticmethod
    def vars_for_template(player):
        config = parse_dynamic_capacity_config(player.session.config)
        return {
            'warmup_rounds': C.WARMUP_ROUNDS,
            'warmup_capacity': parse_warmup_capacity(player.session.config, config),
            'capacity_window_minutes': C.CAPACITY_WINDOW_MINUTES,
        }


class FormalStart(Page):
    @staticmethod
    def is_displayed(player):
        return (
            player.round_number == C.WARMUP_ROUNDS + 1
            and access_allowed(player)
        )

    @staticmethod
    def vars_for_template(player):
        return {'formal_rounds': C.FORMAL_ROUNDS}


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
        if group.round_started:
            start_api_agent_prefetch(group)
            prepare_independent_rl_decisions_for_group(group)
        ready_count = sum(
            bool(group_player.round_start_ready)
            for group_player in round_waiting_players(group)
        )
        waiting_players = round_waiting_players(group)
        return {
            **round_phase_context(player.round_number),
            'round_started': group.round_started,
            'ready_count': ready_count,
            'group_size': len(waiting_players),
            'remaining_seconds': max(
                0,
                ceil(group.round_start_deadline_ts - now_ts),
            ),
            'poll_interval_ms': int(C.SYNC_POLL_INTERVAL_SECONDS * 1000),
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
        start_api_agent_prefetch(player.group)
        prepare_independent_rl_decisions_for_group(player.group)
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
        context = public_accident_context_for_group(player.group)
        schedule = departure_schedule_for_player(player)
        preview = choice_preview(player)
        return {
            **context,
            **round_phase_context(player.round_number),
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
            strategy = set_automatic_departure_choice(
                player,
                DECISION_SOURCE_TIMEOUT_AUTO,
            )
            mark_timeout(player, strategy)
            return
        choice_saved = set_player_departure_choice(
            player,
            player.field_maybe_none('departure_minute'),
            DECISION_SOURCE_MANUAL,
        )
        if choice_saved:
            record_manual_decision(player)
        player.timeout_happened = False


class RecoveryGate(Page):
    @staticmethod
    def is_displayed(player):
        return access_allowed(player) and participant_dropout_active(player)

    @staticmethod
    def vars_for_template(player):
        reason = participant_dropout_reason(player)
        return {
            **round_phase_context(player.round_number),
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
            'poll_interval_ms': int(C.SYNC_POLL_INTERVAL_SECONDS * 1000),
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
        phase = round_phase_context(player.round_number)
        has_previous_capacity = (
            player.round_number > 1
            if phase['is_warmup']
            else phase['display_round_number'] > 1
        )
        return {
            **phase,
            'dynamic_capacity': player.dynamic_capacity,
            'dynamic_capacity_state': player.dynamic_capacity_state,
            'capacity_probability': probability_display(player.capacity_probability),
            'capacity_window_minutes': C.CAPACITY_WINDOW_MINUTES,
            'previous_capacity_label': (
                str(player.previous_round_capacity) if has_previous_capacity else '无'
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
    formal_players = [
        player for player in players
        if not is_warmup_round(player.round_number)
    ]
    for player in formal_players:
        yield export_row_for_player(player)
    for record, reference_player in agent_decisions_for_players(formal_players):
        yield export_row_for_agent_record(record, reference_player)


page_sequence = [
    Introduction,
    ComprehensionCheck,
    WarmupStart,
    FormalStart,
    RoundStartSync,
    Decision,
    RecoveryGate,
    ResultsSync,
    Results,
]
