import importlib.util
import unittest
from pathlib import Path


def load_strict_room_labels_module():
    module_path = Path('access_gate/strict_room_labels.py')
    spec = importlib.util.spec_from_file_location(
        '_strict_room_labels_compatibility_test',
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OTreeRoomLabelCompatibilityTests(unittest.TestCase):
    def test_installer_supports_otree_5_11_lookup_name(self):
        from otree.views import participant as participant_views

        strict_room_labels = load_strict_room_labels_module()
        try:
            strict_room_labels.install_strict_room_label_lookup()
        except AttributeError as exc:
            self.fail(f'installer is incompatible with oTree 5.11: {exc}')

        installed = participant_views.get_existing_or_new_participant
        self.assertTrue(
            getattr(installed, strict_room_labels.STRICT_LOOKUP_MARKER, False)
        )
        strict_room_labels.install_strict_room_label_lookup()
        self.assertIs(
            participant_views.get_existing_or_new_participant,
            installed,
        )


if __name__ == '__main__':
    unittest.main()
