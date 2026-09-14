import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from . import (
    assign_formal_room_labels,
    build_treatment_group_matrix,
)


class FakeParticipant:
    def __init__(self):
        self.label = None
        self.vars = {}

    def set_label(self, label):
        self.label = label


def make_players(count):
    return [SimpleNamespace(participant=FakeParticipant()) for _ in range(count)]


class FormalRoomLabelIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.label_path = Path(self.directory.name) / 'labels.txt'
        self.labels = [f'P{number:03d}' for number in range(1, 41)]
        self.label_path.write_text('\n'.join(self.labels), encoding='utf-8')
        self.treatments = {
            'G01': {'human': 30},
            'G02': {'human': 10},
        }
        self.config = {
            'participant_label_assignment': 'sequential',
            'participant_label_file': str(self.label_path),
        }

    def test_prebinds_labels_before_treatment_grouping(self):
        players = make_players(40)

        plan = assign_formal_room_labels(
            players,
            self.treatments,
            self.config,
        )
        matrix = build_treatment_group_matrix(players, self.treatments)

        self.assertEqual(
            [player.participant.label for player in matrix[0]],
            self.labels[:30],
        )
        self.assertEqual(
            [player.participant.label for player in matrix[1]],
            self.labels[30:40],
        )
        self.assertEqual(plan['G02'], self.labels[30:40])
        self.assertEqual(
            players[39].participant.vars['expected_room_label'],
            'P040',
        )

    def test_prebound_label_to_group_mapping_is_order_independent(self):
        players = make_players(40)
        assign_formal_room_labels(players, self.treatments, self.config)
        matrix = build_treatment_group_matrix(players, self.treatments)
        player_by_label = {
            player.participant.label: player
            for player in reversed(players)
        }

        self.assertIn(player_by_label['P040'], matrix[1])
        self.assertIn(player_by_label['P001'], matrix[0])

    def test_rejects_nonsequential_assignment_mode(self):
        config = {**self.config, 'participant_label_assignment': 'arrival'}

        with self.assertRaisesRegex(ValueError, 'sequential'):
            assign_formal_room_labels(
                make_players(40),
                self.treatments,
                config,
            )

    def test_rejects_session_size_that_differs_from_plan(self):
        with self.assertRaisesRegex(ValueError, '40.*39|39.*40'):
            assign_formal_room_labels(
                make_players(39),
                self.treatments,
                self.config,
            )


if __name__ == '__main__':
    unittest.main()
