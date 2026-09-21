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
    def test_installer_wraps_available_otree_lookup_names_once(self):
        from otree.views import participant as participant_views

        strict_room_labels = load_strict_room_labels_module()
        strict_room_labels.install_strict_room_label_lookup()
        installed = {
            function_name: getattr(participant_views, function_name)
            for function_name in strict_room_labels.LOOKUP_FUNCTION_NAMES
            if callable(getattr(participant_views, function_name, None))
        }
        self.assertTrue(installed)
        for installed_lookup in installed.values():
            self.assertTrue(
                getattr(
                    installed_lookup,
                    strict_room_labels.STRICT_LOOKUP_MARKER,
                    False,
                )
            )

        strict_room_labels.install_strict_room_label_lookup()
        for function_name, installed_lookup in installed.items():
            self.assertIs(
                getattr(participant_views, function_name),
                installed_lookup,
            )


if __name__ == '__main__':
    unittest.main()
