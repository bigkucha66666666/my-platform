"""Pure truncated-normal stochastic-capacity mechanics."""

from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
from statistics import NormalDist, pstdev
from typing import Mapping


INFO_I0 = 'I0'
INFO_I1 = 'I1'
INFORMATION_CONDITIONS = {INFO_I0, INFO_I1}
CAPACITY_DISTRIBUTION = 'truncated_normal'
CAPACITY_MU = 2.665
CAPACITY_SIGMA = 0.80
CAPACITY_MIN = 1.33
CAPACITY_MAX = 4.00
FORMAL_ROUNDS = 30
SEQUENCE_BANK_FILE = 'truncated_normal_capacity_sequence_bank.json'
SEQUENCE_MECHANISM = 'truncated_normal_stochastic_capacity_v2'
SEQUENCE_BANK_VERSION = 2
APPROVED_SEQUENCE_IDS = {'S01', 'S02', 'S03', 'S04', 'S05'}
APPROVED_SEQUENCE_SEEDS = {
    f'S{index:02d}': 2026091100 + index for index in range(1, 6)
}
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
_STANDARD_NORMAL = NormalDist()


class StochasticCapacityConfigError(ValueError):
    pass


def _standard_normal_pdf(value):
    return math.exp(-(value**2) / 2) / math.sqrt(2 * math.pi)


def _truncated_moments(mu, sigma, lower, upper):
    alpha = (lower - mu) / sigma
    beta = (upper - mu) / sigma
    phi_alpha = _standard_normal_pdf(alpha)
    phi_beta = _standard_normal_pdf(beta)
    normalizer = _STANDARD_NORMAL.cdf(beta) - _STANDARD_NORMAL.cdf(alpha)
    mean_adjustment = (phi_alpha - phi_beta) / normalizer
    mean = mu + sigma * mean_adjustment
    variance = sigma**2 * (
        1
        + (alpha * phi_alpha - beta * phi_beta) / normalizer
        - mean_adjustment**2
    )
    return mean, math.sqrt(max(0, variance))


@dataclass(frozen=True)
class StochasticCapacityConfig:
    distribution: str = CAPACITY_DISTRIBUTION
    capacity_mu: float = CAPACITY_MU
    capacity_sigma: float = CAPACITY_SIGMA
    capacity_min: float = CAPACITY_MIN
    capacity_max: float = CAPACITY_MAX
    seed: int = 2026091101
    information_condition: str = INFO_I0

    @property
    def truncated_mean(self):
        return _truncated_moments(
            self.capacity_mu,
            self.capacity_sigma,
            self.capacity_min,
            self.capacity_max,
        )[0]

    @property
    def truncated_standard_deviation(self):
        return _truncated_moments(
            self.capacity_mu,
            self.capacity_sigma,
            self.capacity_min,
            self.capacity_max,
        )[1]

    @property
    def theoretical_mean(self):
        return self.truncated_mean

    @property
    def theoretical_standard_deviation(self):
        return self.truncated_standard_deviation


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


def _require_approved(value, approved, field_name):
    if not math.isclose(value, approved, rel_tol=0, abs_tol=1e-12):
        raise StochasticCapacityConfigError(
            f'{field_name} 必须为批准值 {approved}。'
        )


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
            'capacity_distribution 必须为 truncated_normal。'
        )
    capacity_mu = _finite_float(
        session_config.get('capacity_mu', CAPACITY_MU), 'capacity_mu'
    )
    capacity_sigma = _finite_float(
        session_config.get('capacity_sigma', CAPACITY_SIGMA), 'capacity_sigma'
    )
    capacity_min = _finite_float(
        session_config.get('capacity_min', CAPACITY_MIN), 'capacity_min'
    )
    capacity_max = _finite_float(
        session_config.get('capacity_max', CAPACITY_MAX), 'capacity_max'
    )
    if capacity_min >= capacity_max:
        raise StochasticCapacityConfigError('capacity_min 必须小于 capacity_max。')
    if capacity_sigma <= 0:
        raise StochasticCapacityConfigError('capacity_sigma 必须大于0。')
    _require_approved(capacity_mu, CAPACITY_MU, 'capacity_mu')
    _require_approved(capacity_sigma, CAPACITY_SIGMA, 'capacity_sigma')
    _require_approved(capacity_min, CAPACITY_MIN, 'capacity_min')
    _require_approved(capacity_max, CAPACITY_MAX, 'capacity_max')

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
        capacity_mu=capacity_mu,
        capacity_sigma=capacity_sigma,
        capacity_min=capacity_min,
        capacity_max=capacity_max,
        seed=seed,
        information_condition=information_condition,
    )


def truncated_normal_quantile(probability, config=None):
    probability_value = _finite_float(probability, 'probability')
    if not 0 < probability_value < 1:
        raise StochasticCapacityConfigError('probability 必须严格位于0和1之间。')
    current = config or StochasticCapacityConfig()
    lower_cdf = _STANDARD_NORMAL.cdf(
        (current.capacity_min - current.capacity_mu) / current.capacity_sigma
    )
    upper_cdf = _STANDARD_NORMAL.cdf(
        (current.capacity_max - current.capacity_mu) / current.capacity_sigma
    )
    standard_quantile = _STANDARD_NORMAL.inv_cdf(
        lower_cdf + probability_value * (upper_cdf - lower_cdf)
    )
    return current.capacity_mu + current.capacity_sigma * standard_quantile


def truncated_normal_interval_mean(lower, upper, config=None):
    current = config or StochasticCapacityConfig()
    lower_value = _finite_float(lower, 'interval_lower')
    upper_value = _finite_float(upper, 'interval_upper')
    if not current.capacity_min <= lower_value < upper_value <= current.capacity_max:
        raise StochasticCapacityConfigError(
            'interval_lower 和 interval_upper 必须位于服务率截断范围内，且下界小于上界。'
        )
    return _truncated_moments(
        current.capacity_mu,
        current.capacity_sigma,
        lower_value,
        upper_value,
    )[0]


def capacity_level(actual_capacity):
    capacity = _finite_float(actual_capacity, 'actual_capacity')
    if capacity < CAPACITY_MIN or capacity > CAPACITY_MAX:
        raise StochasticCapacityConfigError(
            f'actual_capacity 必须位于 [{CAPACITY_MIN:.2f}, {CAPACITY_MAX:.2f}]。'
        )
    low_max = truncated_normal_quantile(1 / 3)
    medium_max = truncated_normal_quantile(2 / 3)
    if capacity < low_max:
        return 'low'
    if capacity < medium_max:
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
    draws = []
    for stratum_index in range(1, rounds + 1):
        lower_probability = (stratum_index - 1) / rounds
        probability = lower_probability + rng.random() / rounds
        raw_capacity = truncated_normal_quantile(probability, config)
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
                'capacity_mu': config.capacity_mu,
                'capacity_sigma': config.capacity_sigma,
                'capacity_min': config.capacity_min,
                'capacity_max': config.capacity_max,
                'quantile_stratum_index': stratum_index,
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
        'capacity_mu': config.capacity_mu,
        'capacity_sigma': config.capacity_sigma,
        'capacity_min': config.capacity_min,
        'capacity_max': config.capacity_max,
        'expected_capacity': config.truncated_mean,
        'capacity_standard_deviation': config.truncated_standard_deviation,
        'capacity_truncated_mean': config.truncated_mean,
        'capacity_truncated_sd': config.truncated_standard_deviation,
        'current_capacity_revealed': bool(
            after_decision or warmup or config.information_condition == INFO_I1
        ),
    }
    if warmup:
        context['is_warmup'] = True
    if context['current_capacity_revealed']:
        actual_capacity = _finite_float(record.get('actual_capacity'), 'actual_capacity')
        context.update(
            {
                'actual_capacity': actual_capacity,
                'capacity_level': capacity_level(actual_capacity),
            }
        )
    return context


def _bank_path(path):
    return Path(path) if path is not None else Path(__file__).with_name(
        SEQUENCE_BANK_FILE
    )


def _read_bank(path):
    bank_path = _bank_path(path)
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
    return payload


def _validate_top_level(payload):
    exact = {
        'version': SEQUENCE_BANK_VERSION,
        'mechanism': SEQUENCE_MECHANISM,
        'formal_rounds': FORMAL_ROUNDS,
        'distribution': CAPACITY_DISTRIBUTION,
        'sampling': 'randomized_equal_probability_cdf_strata',
        'rounding_decimals': 2,
    }
    for field_name, expected in exact.items():
        if payload.get(field_name) != expected:
            raise StochasticCapacityConfigError(
                f'序列库 {field_name} 必须为 {expected}。'
            )
    numeric = {
        'capacity_mu': CAPACITY_MU,
        'capacity_sigma': CAPACITY_SIGMA,
        'capacity_min': CAPACITY_MIN,
        'capacity_max': CAPACITY_MAX,
        'truncated_mean': StochasticCapacityConfig().truncated_mean,
        'truncated_standard_deviation': (
            StochasticCapacityConfig().truncated_standard_deviation
        ),
    }
    for field_name, expected in numeric.items():
        actual = _finite_float(payload.get(field_name), field_name)
        if not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-10):
            raise StochasticCapacityConfigError(
                f'序列库 {field_name} 与批准值不一致。'
            )


def _validated_bank_sequence(sequence, sequence_id):
    if not isinstance(sequence, dict):
        raise StochasticCapacityConfigError('随机服务率序列记录必须是对象。')
    generation_seed = _integer(
        sequence.get('generation_seed'), f'{sequence_id} generation_seed'
    )
    if generation_seed != APPROVED_SEQUENCE_SEEDS[sequence_id]:
        raise StochasticCapacityConfigError(f'{sequence_id} generation_seed 不一致。')
    raw_rounds = sequence.get('rounds')
    config = parse_stochastic_capacity_config(
        {'capacity_sequence_seed': generation_seed}
    )
    expected_rounds = generate_stratified_capacity_sequence(
        config, rounds=FORMAL_ROUNDS, sequence_id=sequence_id
    )
    if raw_rounds != expected_rounds:
        raise StochasticCapacityConfigError(
            f'{sequence_id} rounds 与冻结生成合同不一致。'
        )
    values = [record['actual_capacity'] for record in expected_rounds]
    summaries = {
        'mean_actual_capacity': sum(values) / len(values),
        'population_standard_deviation': pstdev(values),
        'min_actual_capacity': min(values),
        'max_actual_capacity': max(values),
    }
    for field_name, expected in summaries.items():
        actual = _finite_float(sequence.get(field_name), field_name)
        if not math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12):
            raise StochasticCapacityConfigError(
                f'{sequence_id} {field_name} 不一致。'
            )
    return {
        'id': sequence_id,
        'generation_seed': generation_seed,
        **summaries,
        'rounds': [dict(record) for record in expected_rounds],
    }


def load_stochastic_capacity_sequence_bank(path=None):
    payload = _read_bank(path)
    _validate_top_level(payload)
    raw_sequences = payload.get('sequences')
    if not isinstance(raw_sequences, list):
        raise StochasticCapacityConfigError('随机服务率序列库 sequences 必须是数组。')
    validated = {}
    for sequence in raw_sequences:
        if not isinstance(sequence, dict):
            raise StochasticCapacityConfigError('随机服务率序列记录必须是对象。')
        sequence_id = str(sequence.get('id', '') or '').strip().upper()
        if sequence_id not in APPROVED_SEQUENCE_IDS or sequence_id in validated:
            raise StochasticCapacityConfigError('序列库必须恰好包含唯一的 S01-S05。')
        validated[sequence_id] = _validated_bank_sequence(sequence, sequence_id)
    if set(validated) != APPROVED_SEQUENCE_IDS:
        raise StochasticCapacityConfigError('序列库必须恰好包含 S01-S05。')
    return validated
