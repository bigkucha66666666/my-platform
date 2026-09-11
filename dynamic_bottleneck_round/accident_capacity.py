"""Pure accident-risk capacity mechanics for the dynamic bottleneck app."""

from dataclasses import dataclass
import json
from math import isclose, isfinite
from pathlib import Path
import random
from typing import Mapping


INFO_I0 = 'I0'
INFO_I1 = 'I1'
INFORMATION_CONDITIONS = {INFO_I0, INFO_I1}
FORMAL_ROUNDS = 30
SEQUENCE_BANK_FILE = 'capacity_sequence_bank.json'
APPROVED_SEQUENCE_IDS = {'S01', 'S02', 'S03', 'S04', 'S05'}


class AccidentRiskConfigError(ValueError):
    pass


@dataclass(frozen=True)
class AccidentRiskConfig:
    normal_capacity: float = 4.0
    incident_probability: float = 0.20
    loss_alpha: float = 6.83057
    loss_beta: float = 4.05907
    seed: int = 2026090801
    information_condition: str = INFO_I0

    @property
    def expected_loss_ratio(self):
        return self.loss_alpha / (self.loss_alpha + self.loss_beta)

    @property
    def expected_incident_capacity(self):
        return self.normal_capacity * (1 - self.expected_loss_ratio)

    @property
    def expected_unconditional_capacity(self):
        return (
            (1 - self.incident_probability) * self.normal_capacity
            + self.incident_probability * self.expected_incident_capacity
        )


def _finite_float(value, field_name):
    if isinstance(value, bool):
        raise AccidentRiskConfigError(f'{field_name} 必须是有限数。')
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AccidentRiskConfigError(f'{field_name} 必须是有限数。') from exc
    if not isfinite(parsed):
        raise AccidentRiskConfigError(f'{field_name} 必须是有限数。')
    return parsed


def parse_accident_risk_config(
    session_config: Mapping[str, object],
) -> AccidentRiskConfig:
    normal_capacity = _finite_float(
        session_config.get('accident_normal_capacity', 4.0),
        'normal_capacity',
    )
    incident_probability = _finite_float(
        session_config.get('accident_probability', 0.20),
        'incident_probability',
    )
    loss_alpha = _finite_float(
        session_config.get('accident_loss_alpha', 6.83057),
        'loss_alpha',
    )
    loss_beta = _finite_float(
        session_config.get('accident_loss_beta', 4.05907),
        'loss_beta',
    )
    if normal_capacity <= 0:
        raise AccidentRiskConfigError('normal_capacity 必须大于0。')
    if not 0 <= incident_probability <= 1:
        raise AccidentRiskConfigError('incident_probability 必须在0到1之间。')
    if loss_alpha <= 0:
        raise AccidentRiskConfigError('loss_alpha 必须大于0。')
    if loss_beta <= 0:
        raise AccidentRiskConfigError('loss_beta 必须大于0。')

    raw_seed = session_config.get('accident_sequence_seed', 2026090801)
    if isinstance(raw_seed, bool):
        raise AccidentRiskConfigError('accident_sequence_seed 必须是整数。')
    try:
        seed = int(raw_seed)
    except (TypeError, ValueError) as exc:
        raise AccidentRiskConfigError(
            'accident_sequence_seed 必须是整数。'
        ) from exc
    if isinstance(raw_seed, float) and raw_seed != seed:
        raise AccidentRiskConfigError('accident_sequence_seed 必须是整数。')

    information_condition = str(
        session_config.get('accident_information_condition', INFO_I0) or ''
    ).strip().upper()
    if information_condition not in INFORMATION_CONDITIONS:
        raise AccidentRiskConfigError(
            'accident_information_condition 必须是 I0 或 I1。'
        )

    return AccidentRiskConfig(
        normal_capacity=normal_capacity,
        incident_probability=incident_probability,
        loss_alpha=loss_alpha,
        loss_beta=loss_beta,
        seed=seed,
        information_condition=information_condition,
    )


def generate_accident_sequence(
    config: AccidentRiskConfig,
    *,
    rounds: int,
    sequence_id: str,
):
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds <= 0:
        raise AccidentRiskConfigError('rounds 必须是正整数。')
    normalized_sequence_id = str(sequence_id or '').strip()
    if not normalized_sequence_id:
        raise AccidentRiskConfigError('sequence_id 不能为空。')

    rng = random.Random(config.seed)
    records = []
    for round_number in range(1, rounds + 1):
        incident_occurred = rng.random() < config.incident_probability
        if incident_occurred:
            raw_loss = rng.betavariate(config.loss_alpha, config.loss_beta)
            capacity_loss_ratio = min(
                0.999999999999,
                max(0.000000000001, round(raw_loss, 12)),
            )
        else:
            capacity_loss_ratio = 0.0
        remaining_capacity_ratio = round(1 - capacity_loss_ratio, 12)
        actual_capacity = round(
            config.normal_capacity * remaining_capacity_ratio,
            12,
        )
        records.append(
            {
                'formal_round_number': round_number,
                'incident_occurred': incident_occurred,
                'capacity_loss_ratio': capacity_loss_ratio,
                'remaining_capacity_ratio': remaining_capacity_ratio,
                'actual_capacity': actual_capacity,
                'sequence_id': normalized_sequence_id,
                'sequence_seed': config.seed,
            }
        )
    return records


def accident_public_context(
    config: AccidentRiskConfig,
    record: Mapping[str, object],
    *,
    after_decision=False,
    warmup=False,
):
    expected_loss = config.loss_alpha / (config.loss_alpha + config.loss_beta)
    expected_incident_capacity = config.normal_capacity * (1 - expected_loss)
    expected_unconditional_capacity = (
        (1 - config.incident_probability) * config.normal_capacity
        + config.incident_probability * expected_incident_capacity
    )
    context = {
        'information_condition': config.information_condition,
        'normal_capacity': config.normal_capacity,
        'incident_probability': config.incident_probability,
        'loss_distribution': {
            'name': 'beta',
            'alpha': config.loss_alpha,
            'beta': config.loss_beta,
            'expected_loss_ratio': expected_loss,
        },
        'expected_incident_capacity': expected_incident_capacity,
        'expected_unconditional_capacity': expected_unconditional_capacity,
        'capacity_revealed': bool(
            after_decision or warmup
        ),
    }
    if warmup:
        context.update(
            {
                'is_warmup': True,
                'incident_occurred': False,
                'actual_capacity': config.normal_capacity,
            }
        )
        return context
    if after_decision:
        context.update(
            {
                'incident_occurred': bool(record['incident_occurred']),
                'capacity_loss_ratio': float(record['capacity_loss_ratio']),
                'remaining_capacity_ratio': float(
                    record['remaining_capacity_ratio']
                ),
                'actual_capacity': float(record['actual_capacity']),
            }
        )
        return context
    if config.information_condition == INFO_I1:
        context['incident_occurred'] = bool(record['incident_occurred'])
    return context


def load_accident_sequence_bank(path=None):
    bank_path = Path(path) if path is not None else Path(__file__).with_name(
        SEQUENCE_BANK_FILE
    )
    try:
        payload = json.loads(bank_path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise AccidentRiskConfigError(
            f'事故序列库不存在：{bank_path.name}。'
        ) from exc
    except json.JSONDecodeError as exc:
        raise AccidentRiskConfigError(
            f'事故序列库 {bank_path.name} 不是有效 JSON。'
        ) from exc
    if not isinstance(payload, dict):
        raise AccidentRiskConfigError('事故序列库根节点必须是对象。')
    if payload.get('version') != 2:
        raise AccidentRiskConfigError('事故序列库 version 必须为2。')
    if payload.get('mechanism') != 'iid_accident_capacity_loss_beta':
        raise AccidentRiskConfigError('事故序列库 mechanism 不正确。')

    normal_capacity = _finite_float(
        payload.get('normal_capacity'),
        'normal_capacity',
    )
    approved = AccidentRiskConfig()
    if not isclose(normal_capacity, approved.normal_capacity, abs_tol=1e-12):
        raise AccidentRiskConfigError('normal_capacity 必须为批准值 4.0。')
    incident_probability = _finite_float(
        payload.get('incident_probability'),
        'incident_probability',
    )
    if not isclose(
        incident_probability,
        approved.incident_probability,
        abs_tol=1e-12,
    ):
        raise AccidentRiskConfigError('incident_probability 必须为批准值 0.2。')
    loss_distribution = payload.get('loss_distribution')
    if not isinstance(loss_distribution, dict):
        raise AccidentRiskConfigError('loss_distribution 必须是对象。')
    if loss_distribution.get('name') != 'beta':
        raise AccidentRiskConfigError('loss_distribution name 必须为 beta。')
    loss_alpha = _finite_float(loss_distribution.get('alpha'), 'alpha')
    loss_beta = _finite_float(loss_distribution.get('beta'), 'beta')
    if not isclose(loss_alpha, approved.loss_alpha, abs_tol=1e-12):
        raise AccidentRiskConfigError('alpha 必须为批准值 6.83057。')
    if not isclose(loss_beta, approved.loss_beta, abs_tol=1e-12):
        raise AccidentRiskConfigError('beta 必须为批准值 4.05907。')
    if payload.get('formal_rounds') != FORMAL_ROUNDS:
        raise AccidentRiskConfigError(
            f'事故序列库 formal_rounds 必须为{FORMAL_ROUNDS}。'
        )
    raw_sequences = payload.get('sequences')
    if not isinstance(raw_sequences, list) or not raw_sequences:
        raise AccidentRiskConfigError('事故序列库中没有可用序列。')

    sequence_ids = []
    for sequence in raw_sequences:
        if not isinstance(sequence, dict):
            raise AccidentRiskConfigError('事故序列记录必须是对象。')
        sequence_id = str(sequence.get('id', '') or '').strip().upper()
        if not sequence_id:
            raise AccidentRiskConfigError('事故序列 id 不能为空。')
        if sequence_id in sequence_ids:
            raise AccidentRiskConfigError(f'事故序列库存在重复编号 {sequence_id}。')
        sequence_ids.append(sequence_id)

    validated = {}
    for sequence, sequence_id in zip(raw_sequences, sequence_ids):
        validated[sequence_id] = _validated_bank_sequence(
            sequence,
            sequence_id=sequence_id,
            normal_capacity=normal_capacity,
        )
    if set(validated) != APPROVED_SEQUENCE_IDS:
        raise AccidentRiskConfigError('事故序列库必须恰好包含 S01–S05。')
    return validated


def _validated_bank_sequence(sequence, *, sequence_id, normal_capacity):
    raw_rounds = sequence.get('rounds')
    if not isinstance(raw_rounds, list) or len(raw_rounds) != FORMAL_ROUNDS:
        raise AccidentRiskConfigError(
            f'{sequence_id} rounds 必须恰好包含{FORMAL_ROUNDS}轮。'
        )
    try:
        generation_seed = int(sequence.get('generation_seed'))
    except (TypeError, ValueError) as exc:
        raise AccidentRiskConfigError(
            f'{sequence_id} generation_seed 必须是整数。'
        ) from exc

    rounds = []
    incident_rounds = []
    for expected_round, raw_record in enumerate(raw_rounds, start=1):
        if not isinstance(raw_record, dict):
            raise AccidentRiskConfigError(
                f'{sequence_id} 第{expected_round}轮记录必须是对象。'
            )
        if raw_record.get('formal_round_number') != expected_round:
            raise AccidentRiskConfigError(
                f'{sequence_id} formal_round_number 必须从1连续到60。'
            )
        if str(raw_record.get('sequence_id', '')).upper() != sequence_id:
            raise AccidentRiskConfigError(
                f'{sequence_id} 第{expected_round}轮 sequence_id 不一致。'
            )
        if raw_record.get('sequence_seed') != generation_seed:
            raise AccidentRiskConfigError(
                f'{sequence_id} 第{expected_round}轮 sequence_seed 不一致。'
            )
        incident_occurred = raw_record.get('incident_occurred')
        if not isinstance(incident_occurred, bool):
            raise AccidentRiskConfigError(
                f'{sequence_id} 第{expected_round}轮 incident_occurred 必须是布尔值。'
            )
        loss_ratio = _finite_float(
            raw_record.get('capacity_loss_ratio'),
            'capacity_loss_ratio',
        )
        remaining_ratio = _finite_float(
            raw_record.get('remaining_capacity_ratio'),
            'remaining_capacity_ratio',
        )
        actual_capacity = _finite_float(
            raw_record.get('actual_capacity'),
            'actual_capacity',
        )
        if incident_occurred:
            if not 0 < loss_ratio < 1:
                raise AccidentRiskConfigError(
                    f'{sequence_id} 第{expected_round}轮 capacity_loss_ratio 必须在0到1之间。'
                )
            incident_rounds.append(expected_round)
        elif loss_ratio != 0:
            raise AccidentRiskConfigError(
                f'{sequence_id} 第{expected_round}轮无事故时 capacity_loss_ratio 必须为0。'
            )
        if not isclose(
            remaining_ratio,
            1 - loss_ratio,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise AccidentRiskConfigError(
                f'{sequence_id} 第{expected_round}轮 remaining_capacity_ratio 不一致。'
            )
        if actual_capacity <= 0 or not isclose(
            actual_capacity,
            normal_capacity * remaining_ratio,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise AccidentRiskConfigError(
                f'{sequence_id} 第{expected_round}轮 actual_capacity 不一致。'
            )
        rounds.append(dict(raw_record))

    if sequence.get('incident_rounds') != incident_rounds:
        raise AccidentRiskConfigError(f'{sequence_id} incident_rounds 不一致。')
    mean_capacity = (
        sum(record['actual_capacity'] for record in rounds) / FORMAL_ROUNDS
    )
    if not isclose(
        _finite_float(sequence.get('mean_actual_capacity'), 'mean_actual_capacity'),
        mean_capacity,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise AccidentRiskConfigError(f'{sequence_id} mean_actual_capacity 不一致。')
    return {
        'id': sequence_id,
        'generation_seed': generation_seed,
        'incident_rounds': incident_rounds,
        'mean_actual_capacity': mean_capacity,
        'rounds': rounds,
    }
