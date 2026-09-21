"""Generate the frozen truncated-normal capacity bank used by formal sessions."""

import json
from pathlib import Path
from statistics import pstdev

try:
    from .stochastic_capacity import (
        APPROVED_SEQUENCE_SEEDS,
        CAPACITY_DISTRIBUTION,
        CAPACITY_MAX,
        CAPACITY_MIN,
        CAPACITY_MU,
        CAPACITY_SIGMA,
        FORMAL_ROUNDS,
        SEQUENCE_BANK_FILE,
        SEQUENCE_BANK_VERSION,
        SEQUENCE_MECHANISM,
        StochasticCapacityConfig,
        generate_stratified_capacity_sequence,
    )
except ImportError:
    from stochastic_capacity import (
        APPROVED_SEQUENCE_SEEDS,
        CAPACITY_DISTRIBUTION,
        CAPACITY_MAX,
        CAPACITY_MIN,
        CAPACITY_MU,
        CAPACITY_SIGMA,
        FORMAL_ROUNDS,
        SEQUENCE_BANK_FILE,
        SEQUENCE_BANK_VERSION,
        SEQUENCE_MECHANISM,
        StochasticCapacityConfig,
        generate_stratified_capacity_sequence,
    )


def build_sequence_bank():
    distribution = StochasticCapacityConfig()
    sequences = []
    for sequence_id, seed in sorted(APPROVED_SEQUENCE_SEEDS.items()):
        config = StochasticCapacityConfig(seed=seed)
        rounds = generate_stratified_capacity_sequence(
            config,
            rounds=FORMAL_ROUNDS,
            sequence_id=sequence_id,
        )
        values = [record['actual_capacity'] for record in rounds]
        sequences.append(
            {
                'id': sequence_id,
                'generation_seed': seed,
                'mean_actual_capacity': sum(values) / len(values),
                'population_standard_deviation': pstdev(values),
                'min_actual_capacity': min(values),
                'max_actual_capacity': max(values),
                'rounds': rounds,
            }
        )
    return {
        'version': SEQUENCE_BANK_VERSION,
        'mechanism': SEQUENCE_MECHANISM,
        'formal_rounds': FORMAL_ROUNDS,
        'distribution': CAPACITY_DISTRIBUTION,
        'capacity_mu': CAPACITY_MU,
        'capacity_sigma': CAPACITY_SIGMA,
        'capacity_min': CAPACITY_MIN,
        'capacity_max': CAPACITY_MAX,
        'truncated_mean': distribution.truncated_mean,
        'truncated_standard_deviation': (
            distribution.truncated_standard_deviation
        ),
        'sampling': 'randomized_equal_probability_cdf_strata',
        'rounding_decimals': 2,
        'sequences': sequences,
    }


def main():
    target = Path(__file__).with_name(SEQUENCE_BANK_FILE)
    target.write_text(
        json.dumps(build_sequence_bank(), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return target


if __name__ == '__main__':
    print(main())
