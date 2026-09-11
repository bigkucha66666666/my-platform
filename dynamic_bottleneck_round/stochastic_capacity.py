"""Pure uniform stochastic-capacity mechanics for the dynamic bottleneck app."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
from typing import Mapping


INFO_I0 = 'I0'
INFO_I1 = 'I1'
INFORMATION_CONDITIONS = {INFO_I0, INFO_I1}
CAPACITY_DISTRIBUTION = 'uniform'
CAPACITY_MIN = 1.33
CAPACITY_MAX = 4.00
CAPACITY_LEVEL_LOW_MAX = 2.22
CAPACITY_LEVEL_MEDIUM_MAX = 3.11
FORMAL_ROUNDS = 30
SEQUENCE_BANK_FILE = 'uniform_capacity_sequence_bank.json'
SEQUENCE_MECHANISM = 'uniform_stochastic_capacity_v1'
APPROVED_SEQUENCE_IDS = {'S01', 'S02', 'S03', 'S04', 'S05'}
LEGACY_ACCIDENT_FIELDS = {
    'incident_occurred',
    'accident_probability',
    'capacity_loss_ratio',
    'remaining_capacity_ratio',
    'accident_normal_capacity',
    'accident_loss_alpha',
    'accident_loss_beta',
    'accident_information_condition',
}


class StochasticCapacityConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StochasticCapacityConfig:
    distribution: str = CAPACITY_DISTRIBUTION
    capacity_min: float = CAPACITY_MIN
    capacity_max: float = CAPACITY_MAX
    seed: int = 2026091101
    information_condition: str = INFO_I0

    @property
    def theoretical_mean(self):
        return (self.capacity_min + self.capacity_max) / 2

    @property
    def theoretical_standard_deviation(self):
        return (self.capacity_max - self.capacity_min) / math.sqrt(12)


def _finite_float(value, field_name):
    if isinstance(value, bool):
        raise StochasticCapacityConfigError(f'{field_name} 必须是有限数。')
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise StochasticCapacityConfigError(f'{field_name} 必须是有限数。') from exc
    if not math.isfinite(parsed):
        raise StochasticCapacityConfigError(f'{field_name} 必须是有限数。')
    return parsed


def _integer(value, field_name):
    if isinstance(value, bool):
        raise StochasticCapacityConfigError(f'{field_name} 必须是整数。')
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise StochasticCapacityConfigError(f'{field_name} 必须是整数。') from exc
    if isinstance(value, float) and value != parsed:
        raise StochasticCapacityConfigError(f'{field_name} 必须是整数。')
    if isinstance(value, str) and value.strip() != str(parsed):
        raise StochasticCapacityConfigError(f'{field_name} 必须是整数。')
    return parsed


def parse_stochastic_capacity_config(
    session_config: Mapping[str, object],
) -> StochasticCapacityConfig:
    for field_name in sorted(LEGACY_ACCIDENT_FIELDS):
        if field_name in session_config:
            raise StochasticCapacityConfigError(
                f'旧事故配置字段 {field_name} 不允许用于随机服务率实验。'
            )

    distribution = str(
        session_config.get('capacity_distribution', CAPACITY_DISTRIBUTION) or ''
    ).strip().lower()
    if distribution != CAPACITY_DISTRIBUTION:
        raise StochasticCapacityConfigError(
            'capacity_distribution 必须为 uniform。'
        )
    capacity_min = _finite_float(
        session_config.get('capacity_min', CAPACITY_MIN),
        'capacity_min',
    )
    capacity_max = _finite_float(
        session_config.get('capacity_max', CAPACITY_MAX),
        'capacity_max',
    )
    if not math.isclose(capacity_min, CAPACITY_MIN, rel_tol=0, abs_tol=1e-12):
        raise StochasticCapacityConfigError('capacity_min 必须为批准值 1.33。')
    if not math.isclose(capacity_max, CAPACITY_MAX, rel_tol=0, abs_tol=1e-12):
        raise StochasticCapacityConfigError('capacity_max 必须为批准值 4.00。')
    if capacity_min >= capacity_max:
        raise StochasticCapacityConfigError('capacity_min 必须小于 capacity_max。')

    seed = _integer(
        session_config.get('capacity_sequence_seed', 2026091101),
        'capacity_sequence_seed',
    )
    information_condition = str(
        session_config.get('capacity_information_condition', INFO_I0) or ''
    ).strip().upper()
    if information_condition not in INFORMATION_CONDITIONS:
        raise StochasticCapacityConfigError(
            'capacity_information_condition 必须是 I0 或 I1。'
        )

    return StochasticCapacityConfig(
        distribution=distribution,
        capacity_min=capacity_min,
        capacity_max=capacity_max,
        seed=seed,
        information_condition=information_condition,
    )


def capacity_level(actual_capacity):
    capacity = _finite_float(actual_capacity, 'actual_capacity')
    if capacity < CAPACITY_MIN or capacity > CAPACITY_MAX:
        raise StochasticCapacityConfigError(
            f'actual_capacity 必须位于 [{CAPACITY_MIN:.2f}, {CAPACITY_MAX:.2f}]。'
        )
    if capacity < CAPACITY_LEVEL_LOW_MAX:
        return 'low'
    if capacity < CAPACITY_LEVEL_MEDIUM_MAX:
        return 'medium'
    return 'high'


def generate_stratified_capacity_sequence(
    config: StochasticCapacityConfig,
    *,
    rounds: int,
    sequence_id: str,
):
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds <= 0:
        raise StochasticCapacityConfigError('rounds 必须是正整数。')
    normalized_sequence_id = str(sequence_id or '').strip()
    if not normalized_sequence_id:
        raise StochasticCapacityConfigError('sequence_id 不能为空。')

    rng = random.Random(config.seed)
    width = (config.capacity_max - config.capacity_min) / rounds
    draws = []
    for stratum_index in range(1, rounds + 1):
        lower = config.capacity_min + (stratum_index - 1) * width
        raw_capacity = lower + rng.random() * width
        actual_capacity = min(
            config.capacity_max,
            max(config.capacity_min, round(raw_capacity, 2)),
        )
        draws.append(
            {
                'actual_capacity': actual_capacity,
                'capacity_level': capacity_level(actual_capacity),
                'sequence_id': normalized_sequence_id,
                'sequence_seed': config.seed,
                'distribution': config.distribution,
                'capacity_min': config.capacity_min,
                'capacity_max': config.capacity_max,
                'stratum_index': stratum_index,
            }
        )
    rng.shuffle(draws)
    return [
        {'formal_round_number': round_number, **record}
        for round_number, record in enumerate(draws, start=1)
    ]


def stochastic_capacity_public_context(
    config: StochasticCapacityConfig,
    record: Mapping[str, object],
    *,
    after_decision=False,
    warmup=False,
):
    context = {
        'information_condition': config.information_condition,
        'capacity_distribution': config.distribution,
        'capacity_min': config.capacity_min,
        'capacity_max': config.capacity_max,
        'expected_capacity': config.theoretical_mean,
        'capacity_standard_deviation': config.theoretical_standard_deviation,
        'current_capacity_revealed': bool(
            after_decision or warmup or config.information_condition == INFO_I1
        ),
    }
    if warmup:
        context['is_warmup'] = True
    if context['current_capacity_revealed']:
        actual_capacity = _finite_float(
            record.get('actual_capacity'),
            'actual_capacity',
        )
        context.update(
            {
                'actual_capacity': actual_capacity,
                'capacity_level': capacity_level(actual_capacity),
            }
        )
    return context


def load_uniform_capacity_sequence_bank(path=None):
    bank_path = Path(path) if path is not None else Path(__file__).with_name(
        SEQUENCE_BANK_FILE
    )
    try:
        payload = json.loads(bank_path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise StochasticCapacityConfigError(
            f'随机服务率序列库不存在：{bank_path.name}。'
        ) from exc
    except json.JSONDecodeError as exc:
        raise StochasticCapacityConfigError(
            f'随机服务率序列库 {bank_path.name} 不是有效 JSON。'
        ) from exc
    if not isinstance(payload, dict):
        raise StochasticCapacityConfigError('随机服务率序列库根节点必须是对象。')
    if payload.get('version') != 1:
        raise StochasticCapacityConfigError('随机服务率序列库 version 必须为1。')
    if payload.get('mechanism') != SEQUENCE_MECHANISM:
        raise StochasticCapacityConfigError('随机服务率序列库 mechanism 不正确。')
    if payload.get('distribution') != CAPACITY_DISTRIBUTION:
        raise StochasticCapacityConfigError('序列库 distribution 必须为 uniform。')
    capacity_min = _finite_float(payload.get('capacity_min'), 'capacity_min')
    capacity_max = _finite_float(payload.get('capacity_max'), 'capacity_max')
    if not math.isclose(capacity_min, CAPACITY_MIN, rel_tol=0, abs_tol=1e-12):
        raise StochasticCapacityConfigError('序列库 capacity_min 必须为 1.33。')
    if not math.isclose(capacity_max, CAPACITY_MAX, rel_tol=0, abs_tol=1e-12):
        raise StochasticCapacityConfigError('序列库 capacity_max 必须为 4.00。')
    if payload.get('formal_rounds') != FORMAL_ROUNDS:
        raise StochasticCapacityConfigError(
            f'随机服务率序列库 formal_rounds 必须为{FORMAL_ROUNDS}。'
        )
    raw_sequences = payload.get('sequences')
    if not isinstance(raw_sequences, list) or not raw_sequences:
        raise StochasticCapacityConfigError('随机服务率序列库中没有可用序列。')

    validated = {}
    for sequence in raw_sequences:
        if not isinstance(sequence, dict):
            raise StochasticCapacityConfigError('随机服务率序列记录必须是对象。')
        sequence_id = str(sequence.get('id', '') or '').strip().upper()
        if not sequence_id:
            raise StochasticCapacityConfigError('随机服务率序列 id 不能为空。')
        if sequence_id in validated:
            raise StochasticCapacityConfigError(
                f'随机服务率序列库存在重复编号 {sequence_id}。'
            )
        validated[sequence_id] = _validated_bank_sequence(
            sequence,
            sequence_id=sequence_id,
            capacity_min=capacity_min,
            capacity_max=capacity_max,
        )
    if set(validated) != APPROVED_SEQUENCE_IDS:
        raise StochasticCapacityConfigError(
            '随机服务率序列库必须恰好包含 S01–S05。'
        )
    return validated


def _validated_bank_sequence(
    sequence,
    *,
    sequence_id,
    capacity_min,
    capacity_max,
):
    generation_seed = _integer(
        sequence.get('generation_seed'),
        f'{sequence_id} generation_seed',
    )
    raw_rounds = sequence.get('rounds')
    if not isinstance(raw_rounds, list) or len(raw_rounds) != FORMAL_ROUNDS:
        raise StochasticCapacityConfigError(
            f'{sequence_id} rounds 必须恰好包含{FORMAL_ROUNDS}轮。'
        )
    rounds = []
    strata = set()
    for expected_round, raw_record in enumerate(raw_rounds, start=1):
        if not isinstance(raw_record, dict):
            raise StochasticCapacityConfigError(
                f'{sequence_id} 第{expected_round}轮记录必须是对象。'
            )
        if raw_record.get('formal_round_number') != expected_round:
            raise StochasticCapacityConfigError(
                f'{sequence_id} formal_round_number 必须从1连续到{FORMAL_ROUNDS}。'
            )
        if str(raw_record.get('sequence_id', '')).upper() != sequence_id:
            raise StochasticCapacityConfigError(
                f'{sequence_id} 第{expected_round}轮 sequence_id 不一致。'
            )
        if raw_record.get('sequence_seed') != generation_seed:
            raise StochasticCapacityConfigError(
                f'{sequence_id} 第{expected_round}轮 sequence_seed 不一致。'
            )
        if raw_record.get('distribution') != CAPACITY_DISTRIBUTION:
            raise StochasticCapacityConfigError(
                f'{sequence_id} 第{expected_round}轮 distribution 不一致。'
            )
        if raw_record.get('capacity_min') != capacity_min:
            raise StochasticCapacityConfigError(
                f'{sequence_id} 第{expected_round}轮 capacity_min 不一致。'
            )
        if raw_record.get('capacity_max') != capacity_max:
            raise StochasticCapacityConfigError(
                f'{sequence_id} 第{expected_round}轮 capacity_max 不一致。'
            )
        actual_capacity = _finite_float(
            raw_record.get('actual_capacity'),
            'actual_capacity',
        )
        if (
            actual_capacity < capacity_min
            or actual_capacity > capacity_max
            or not math.isclose(
                actual_capacity,
                round(actual_capacity, 2),
                rel_tol=0,
                abs_tol=1e-12,
            )
        ):
            raise StochasticCapacityConfigError(
                f'{sequence_id} 第{expected_round}轮 actual_capacity 不合法。'
            )
        expected_level = capacity_level(actual_capacity)
        if raw_record.get('capacity_level') != expected_level:
            raise StochasticCapacityConfigError(
                f'{sequence_id} 第{expected_round}轮 capacity_level 不一致。'
            )
        stratum_index = _integer(
            raw_record.get('stratum_index'),
            'stratum_index',
        )
        if not 1 <= stratum_index <= FORMAL_ROUNDS or stratum_index in strata:
            raise StochasticCapacityConfigError(
                f'{sequence_id} stratum_index 必须从1到{FORMAL_ROUNDS}各出现一次。'
            )
        strata.add(stratum_index)
        rounds.append(dict(raw_record))
    if strata != set(range(1, FORMAL_ROUNDS + 1)):
        raise StochasticCapacityConfigError(
            f'{sequence_id} 未覆盖全部等概率层。'
        )
    mean_capacity = sum(record['actual_capacity'] for record in rounds) / FORMAL_ROUNDS
    if not math.isclose(
        _finite_float(sequence.get('mean_actual_capacity'), 'mean_actual_capacity'),
        mean_capacity,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise StochasticCapacityConfigError(
            f'{sequence_id} mean_actual_capacity 不一致。'
        )
    return {
        'id': sequence_id,
        'generation_seed': generation_seed,
        'mean_actual_capacity': mean_capacity,
        'rounds': rounds,
    }
