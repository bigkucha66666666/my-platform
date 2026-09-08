"""Pure accident-risk capacity mechanics for the dynamic bottleneck app."""

from dataclasses import dataclass
from math import isfinite
import random
from typing import Mapping


INFO_I0 = 'I0'
INFO_I1 = 'I1'
INFO_I2 = 'I2'
INFORMATION_CONDITIONS = {INFO_I0, INFO_I1, INFO_I2}


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
            'accident_information_condition 必须是 I0、I1 或 I2。'
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
