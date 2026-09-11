"""Generate the frozen S01-S05 accident-capacity sequence bank."""

import argparse
import json
from pathlib import Path

from dynamic_bottleneck_round.accident_capacity import (
    FORMAL_ROUNDS,
    generate_accident_sequence,
    load_accident_sequence_bank,
    parse_accident_risk_config,
)


SEQUENCE_SEEDS = {
    'S01': 2026090801,
    'S02': 2026090802,
    'S03': 2026090803,
    'S04': 2026090804,
    'S05': 2026090805,
}


def build_bank_payload():
    sequences = []
    fingerprints = set()
    for sequence_id, seed in SEQUENCE_SEEDS.items():
        config = parse_accident_risk_config({'accident_sequence_seed': seed})
        records = generate_accident_sequence(
            config,
            rounds=FORMAL_ROUNDS,
            sequence_id=sequence_id,
        )
        incident_rounds = [
            record['formal_round_number']
            for record in records
            if record['incident_occurred']
        ]
        if not incident_rounds:
            raise ValueError(f'{sequence_id} 没有事故轮，不能进入正式序列库。')
        fingerprint = json.dumps(records, sort_keys=True)
        if fingerprint in fingerprints:
            raise ValueError(f'{sequence_id} 与其他事故序列完全相同。')
        fingerprints.add(fingerprint)
        sequences.append(
            {
                'id': sequence_id,
                'generation_seed': seed,
                'incident_rounds': incident_rounds,
                'mean_actual_capacity': round(
                    sum(record['actual_capacity'] for record in records)
                    / FORMAL_ROUNDS,
                    12,
                ),
                'rounds': records,
            }
        )
    return {
        'version': 2,
        'mechanism': 'iid_accident_capacity_loss_beta',
        'formal_rounds': FORMAL_ROUNDS,
        'normal_capacity': 4.0,
        'incident_probability': 0.2,
        'loss_distribution': {
            'name': 'beta',
            'alpha': 6.83057,
            'beta': 4.05907,
        },
        'sequences': sequences,
    }


def write_bank(path):
    output_path = Path(path)
    output_path.write_text(
        json.dumps(build_bank_payload(), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    load_accident_sequence_bank(output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output_path = write_bank(args.output)
    print(output_path)


if __name__ == '__main__':
    main()
