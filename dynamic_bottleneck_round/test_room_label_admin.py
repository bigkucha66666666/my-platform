import unittest
import re
import shutil
import subprocess
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

    def test_dual_condition_rooms_reuse_existing_labels_and_preserve_old_rooms(self):
        rooms = {room['name']: room for room in settings.ROOMS}
        self.assertIn('prod_room', rooms)
        self.assertIn('demo_room', rooms)
        for condition in ('i0', 'i1'):
            name = f'prod_room_{condition}'
            with self.subTest(room=name):
                self.assertIn(name, rooms)
                self.assertEqual(rooms[name]['participant_label_file'], '_rooms/econ101.txt')
                self.assertEqual(rooms[name]['use_secure_urls'], rooms['prod_room']['use_secure_urls'])
                self.assertIn(condition.upper(), rooms[name]['display_name'])

    def test_room_default_preset_mapping_executes_existing_paired_presets(self):
        function = re.search(
            r'function defaultPresetForRoom\(roomName\) \{[^}]+\}', self.html
        )
        self.assertIsNotNone(function, 'room-specific preset helper is missing')
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is needed for the JavaScript behavior check')
        script = function.group(0) + "\n" + '''
const assert = require('node:assert/strict');
assert.equal(defaultPresetForRoom('prod_room_i0'), 'PAIR-I0');
assert.equal(defaultPresetForRoom('prod_room_i1'), 'PAIR-I1');
assert.equal(defaultPresetForRoom('prod_room'), 'PAIR-I0');
assert.equal(defaultPresetForRoom('demo_room'), 'PAIR-I0');
assert.equal(defaultPresetForRoom(''), 'PAIR-I0');
'''
        result = subprocess.run([node, '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("document.querySelector('[name=\"room_name\"]')", self.html)
        self.assertIn('roomPresetApplied', self.html)
        self.assertEqual(self.html.count('applyRoomDefaultPreset();'), 2)
        self.assertEqual(self.html.count('applyPreset(roomDefaultPreset);'), 3)

    def test_room_default_only_applies_once_to_the_formal_scene(self):
        function = re.search(
            r'function applyRoomDefaultPreset\(\) \{[^}]+\}', self.html
        )
        self.assertIsNotNone(function)
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js is needed for the JavaScript behavior check')
        script = '''
const assert = require('node:assert/strict');
let isConditionRoom = true;
let roomPresetApplied = false;
let currentConfig = 'dynamic_bottleneck_round_demo';
const formalScenarioConfig = 'dynamic_bottleneck_round_prod';
const roomDefaultPreset = 'PAIR-I1';
const choices = [];
function applyPreset(code) { choices.push(code); }
''' + function.group(0) + '''
applyRoomDefaultPreset();
assert.deepEqual(choices, []);
currentConfig = formalScenarioConfig;
applyRoomDefaultPreset();
assert.deepEqual(choices, ['PAIR-I1']);
applyPreset('CUSTOM');
applyRoomDefaultPreset();
assert.deepEqual(choices, ['PAIR-I1', 'CUSTOM']);
roomPresetApplied = false;
isConditionRoom = false;
applyRoomDefaultPreset();
assert.deepEqual(choices, ['PAIR-I1', 'CUSTOM']);
'''
        result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installed_otree_registers_both_condition_rooms(self):
        from otree.room import get_room_dict

        rooms = get_room_dict()
        self.assertIsNot(rooms['prod_room_i0'], rooms['prod_room_i1'])
        for name in ('prod_room_i0', 'prod_room_i1'):
            self.assertTrue(rooms[name].has_participant_labels)
            self.assertEqual(rooms[name].get_participant_labels()[:40],
                             [f'P{index:03d}' for index in range(1, 41)])


if __name__ == '__main__':
    unittest.main()
