import tempfile
import unittest
from pathlib import Path

from .room_label_assignment import (
    RoomLabelAssignmentError,
    build_sequential_label_plan,
    flatten_label_plan,
    load_room_labels,
)


class RoomLabelAssignmentTests(unittest.TestCase):
    def test_loads_nonempty_labels_in_file_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'labels.txt'
            path.write_text('P001\n\nP002 P003\n', encoding='utf-8')

            self.assertEqual(load_room_labels(path), ['P001', 'P002', 'P003'])

    def test_builds_contiguous_ranges_from_treatment_human_counts(self):
        plan = build_sequential_label_plan(
            ['P001', 'P002', 'P003', 'P004'],
            {'G01': {'human': 3}, 'G02': {'human': 1}},
        )

        self.assertEqual(plan['G01'], ['P001', 'P002', 'P003'])
        self.assertEqual(plan['G02'], ['P004'])
        self.assertEqual(flatten_label_plan(plan), ['P001', 'P002', 'P003', 'P004'])

    def test_rejects_duplicate_labels(self):
        with self.assertRaisesRegex(RoomLabelAssignmentError, '重复'):
            build_sequential_label_plan(
                ['P001', 'P001'],
                {'G01': {'human': 2}},
            )

    def test_rejects_insufficient_labels(self):
        with self.assertRaisesRegex(RoomLabelAssignmentError, '不足'):
            build_sequential_label_plan(
                ['P001'],
                {'G01': {'human': 2}},
            )

    def test_rejects_nonpositive_human_count(self):
        with self.assertRaisesRegex(RoomLabelAssignmentError, '正整数'):
            build_sequential_label_plan(
                ['P001'],
                {'G01': {'human': 0}},
            )

    def test_rejects_empty_label_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'labels.txt'
            path.write_text('\n', encoding='utf-8')

            with self.assertRaisesRegex(RoomLabelAssignmentError, '为空'):
                load_room_labels(path)


if __name__ == '__main__':
    unittest.main()
