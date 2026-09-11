"""Generate the frozen S01-S05 randomized-stratified capacity bank."""

import argparse
import json
from pathlib import Path

from dynamic_bottleneck_round.stochastic_capacity import (
    CAPACITY_DISTRIBUTION,
    CAPACITY_MAX,
    CAPACITY_MIN,
    FORMAL_ROUNDS,
    SEQUENCE_MECHANISM,
    generate_stratified_capacity_sequence,
    load_uniform_capacity_sequence_bank,
    parse_stochastic_capacity_config,
)


SEQUENCE_SEEDS = {
    'S01': 2026091101,
    'S02': 2026091102,
    'S03': 2026091103,
    'S04': 2026091104,
    'S05': 2026091105,
}


def build_bank_payload():
    sequences = []
    fingerprints = set()
    for sequence_id, seed in SEQUENCE_SEEDS.items():
        config = parse_stochastic_capacity_config(
            {'capacity_sequence_seed': seed}
        )
        records = generate_stratified_capacity_sequence(
            config,
            rounds=FORMAL_ROUNDS,
            sequence_id=sequence_id,
        )
        fingerprint = json.dumps(records, sort_keys=True)
        if fingerprint in fingerprints:
            raise ValueError(f'{sequence_id} 与其他随机服务率序列完全相同。')
        fingerprints.add(fingerprint)
        sequences.append(
            {
                'id': sequence_id,
                'generation_seed': seed,
                'mean_actual_capacity': round(
                    sum(record['actual_capacity'] for record in records)
                    / FORMAL_ROUNDS,
                    12,
                ),
                'rounds': records,
            }
        )
    return {
        'version': 1,
        'mechanism': SEQUENCE_MECHANISM,
        'formal_rounds': FORMAL_ROUNDS,
        'distribution': CAPACITY_DISTRIBUTION,
        'capacity_min': CAPACITY_MIN,
        'capacity_max': CAPACITY_MAX,
        'sampling': 'randomized_equal_probability_strata',
        'rounding_decimals': 2,
        'sequences': sequences,
    }


def write_bank(path):
    output_path = Path(path)
    output_path.write_text(
        json.dumps(build_bank_payload(), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    load_uniform_capacity_sequence_bank(output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(write_bank(args.output))


if __name__ == '__main__':
    main()
