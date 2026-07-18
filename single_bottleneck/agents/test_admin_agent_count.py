import unittest
from inspect import getsource
from pathlib import Path
from types import SimpleNamespace

import single_bottleneck as app
import settings


class AdminAgentCountValidationTests(unittest.TestCase):
    @staticmethod
    def make_session(mode, count):
        return SimpleNamespace(
            config={
                'api_agent_mode': mode,
                'api_agent_count_per_group': count,
            }
        )

    def test_active_mode_accepts_boundary_values(self):
        for count in (1, 5):
            with self.subTest(count=count):
                self.assertEqual(
                    app.validate_api_agent_count(
                        self.make_session('active', count)
                    ),
                    count,
                )

    def test_shadow_mode_uses_same_validation(self):
        self.assertEqual(
            app.validate_api_agent_count(self.make_session('shadow', 3)),
            3,
        )

    def test_active_mode_accepts_integer_string(self):
        session = self.make_session('active', '3')

        self.assertEqual(
            app.validate_api_agent_count(session),
            3,
        )
        self.assertEqual(session.config['api_agent_count_per_group'], 3)

    def test_active_mode_rejects_invalid_values(self):
        for count in (0, -1, 6, '2.5', 'invalid', True):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, '1 到 5'):
                    app.validate_api_agent_count(
                        self.make_session('active', count)
                    )

    def test_off_mode_does_not_require_positive_agent_count(self):
        self.assertEqual(
            app.validate_api_agent_count(self.make_session('off', 0)),
            0,
        )

    def test_creating_session_validates_before_reading_players(self):
        source = getsource(app.creating_session)

        self.assertLess(
            source.index('validate_api_agent_count'),
            source.index('get_players'),
        )

    def test_active_configs_preserve_raw_input_for_strict_server_validation(self):
        configs = {config['name']: config for config in settings.SESSION_CONFIGS}

        for name in (
            'single_bottleneck_prod_agent_active',
            'single_bottleneck_demo_agent_active',
        ):
            with self.subTest(name=name):
                self.assertIsInstance(
                    configs[name]['api_agent_count_per_group'],
                    str,
                )


class AdminAgentCountTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template_path = Path('_templates/otree/CreateSession.html')

    def test_template_exposes_prominent_agent_count_control(self):
        template = self.template_path.read_text(encoding='utf-8')

        self.assertIn('每组 Agent 数量', template)
        self.assertIn('min="1"', template)
        self.assertIn('max="5"', template)
        self.assertIn('step="1"', template)
        self.assertIn('api_agent_count_per_group', template)

    def test_template_targets_only_active_agent_configs(self):
        template = self.template_path.read_text(encoding='utf-8')

        self.assertIn('"single_bottleneck_prod_agent_active"', template)
        self.assertIn('"single_bottleneck_demo_agent_active"', template)
        self.assertNotIn('"single_bottleneck_prod"', template)
        self.assertNotIn('"single_bottleneck_demo"', template)

    def test_template_keeps_standard_otree_form(self):
        template = self.template_path.read_text(encoding='utf-8')

        self.assertIn('otree/includes/CreateSessionForm.html', template)
        self.assertIn('agent-count-control', template)
        self.assertIn('reportValidity', template)

    def test_template_keeps_standard_field_until_enhancement_is_ready(self):
        template = self.template_path.read_text(encoding='utf-8')

        guard = template.index('if (!dropdown || !participantBlock')
        remove_name = template.index('input.removeAttribute("name")')
        self.assertLess(guard, remove_name)

    def test_template_uses_native_numeric_validation_message(self):
        template = self.template_path.read_text(encoding='utf-8')

        self.assertNotIn('setCustomValidity', template)


if __name__ == '__main__':
    unittest.main()
