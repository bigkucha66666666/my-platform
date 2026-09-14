import unittest
from pathlib import Path

import settings


class RoomLabelAdminConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.formal_config = next(
            config
            for config in settings.SESSION_CONFIGS
            if config['name'] == 'dynamic_bottleneck_round_prod'
        )
        cls.html = Path(
            '_templates/otree/includes/DynamicSessionControls.html'
        ).read_text(encoding='utf-8')

    def test_formal_session_uses_the_prod_room_label_file_sequentially(self):
        self.assertEqual(
            self.formal_config['participant_label_file'],
            '_rooms/econ101.txt',
        )
        self.assertEqual(
            self.formal_config['participant_label_assignment'],
            'sequential',
        )
        self.assertEqual(
            self.formal_config['group_treatment_spec'],
            'G01:H-I0;G02:HA-I0',
        )
        self.assertEqual(self.formal_config['num_demo_participants'], 40)

    def test_admin_controls_offer_paired_i0_and_i1_presets(self):
        self.assertIn('data-preset="PAIR-I0"', self.html)
        self.assertIn('data-preset="PAIR-I1"', self.html)
        self.assertIn("treatments: ['H-I0', 'HA-I0']", self.html)
        self.assertIn("treatments: ['H-I1', 'HA-I1']", self.html)

    def test_paired_presets_show_the_exact_room_label_ranges(self):
        self.assertIn('P001–P030', self.html)
        self.assertIn('P031–P040', self.html)
        self.assertIn('40 个 Human 登录席位', self.html)
        self.assertIn('formatRoomLabel', self.html)
        self.assertIn('labelRangeForGroup', self.html)


if __name__ == '__main__':
    unittest.main()
