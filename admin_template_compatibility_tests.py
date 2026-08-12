import unittest
from pathlib import Path


class CreateSessionTemplateCompatibilityTests(unittest.TestCase):
    def test_create_session_inherits_installed_otree_admin_template(self):
        template_path = Path('_templates/otree/CreateSession.html')
        first_line = template_path.read_text(encoding='utf-8').splitlines()[0]

        self.assertEqual(first_line, '{% extends "otree/BaseAdmin.html" %}')


if __name__ == '__main__':
    unittest.main()
