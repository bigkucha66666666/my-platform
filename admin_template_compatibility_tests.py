import unittest
from pathlib import Path

import otree


class CreateSessionTemplateCompatibilityTests(unittest.TestCase):
    def test_create_session_inherits_installed_otree_admin_template(self):
        from otree.templating.loader import ibis_loader

        # Resolve through the real loader, including the installed parent.
        # A static assertion on an obsolete parent name missed a production 500.
        try:
            ibis_loader.load('otree/CreateSession.html')
        except Exception as exc:
            self.fail(f'Installed admin template cannot be loaded: {exc}')
        native = Path(otree.__file__).parent / 'templates/otree/CreateSession.html'
        self.assertTrue(native.is_file())
        custom_form = Path('_templates/otree/includes/CreateSessionForm.html')
        self.assertTrue(custom_form.is_file())


if __name__ == '__main__':
    unittest.main()
