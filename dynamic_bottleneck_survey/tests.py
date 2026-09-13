import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest

from otree.api import Bot, Submission, expect


survey_app = importlib.import_module('dynamic_bottleneck_survey')


class SurveyModelTests(unittest.TestCase):
    def test_app_defines_one_round_survey_model(self):
        constants = getattr(survey_app, 'C', None)
        player_class = getattr(survey_app, 'Player', None)

        self.assertIsNotNone(constants)
        self.assertIsNotNone(player_class)
        self.assertEqual(constants.NUM_ROUNDS, 1)
        for field_name in (
            'pattern_recognition',
            'noticed_pattern_round',
            'pattern_description',
            'reference_previous_capacity',
            'predict_next_capacity',
            'adjust_after_high_cost',
            'expect_capacity_persistence',
            'agent_choice_influence',
            'agent_predictability_effect',
            'treatment_group',
            'information_condition',
            'treatment_condition',
            'dynamic_group_label',
        ):
            self.assertTrue(hasattr(player_class, field_name), field_name)

    def test_experiment_metadata_preserves_full_two_by_two_treatment(self):
        player = SimpleNamespace(
            participant=SimpleNamespace(
                vars={
                    'assigned_group_id': 3,
                    'assigned_group_label': 'G03',
                    'dynamic_bottleneck_treatment_group': 'HA',
                    'dynamic_bottleneck_information_condition': 'I1',
                    'dynamic_bottleneck_api_agent_count': 10,
                    'dynamic_bottleneck_rl_agent_count': 10,
                }
            )
        )

        survey_app.copy_experiment_metadata(player)

        self.assertEqual(player.information_condition, 'I1')
        self.assertEqual(player.treatment_condition, 'HA-I1')

    def test_notice_round_is_selection_from_none_or_formal_rounds(self):
        choices = getattr(survey_app, 'NOTICE_ROUND_CHOICES', None)

        self.assertIsNotNone(choices)
        self.assertEqual(choices[0], ['none', '未发现明显变化'])
        self.assertEqual(choices[1], ['1', '正式第 1 轮'])
        self.assertEqual(choices[-1], ['30', '正式第 30 轮'])
        self.assertEqual(len(choices), 31)

    def test_human_only_and_human_agent_receive_different_second_page_fields(self):
        page = getattr(survey_app, 'StrategySurvey', None)
        self.assertIsNotNone(page)
        human = SimpleNamespace(
            participant=SimpleNamespace(
                vars={'dynamic_bottleneck_treatment_group': 'H'}
            )
        )
        human_agent = SimpleNamespace(
            participant=SimpleNamespace(
                vars={'dynamic_bottleneck_treatment_group': 'HA'}
            )
        )

        self.assertEqual(
            page.get_form_fields(human),
            [
                'reference_previous_capacity',
                'predict_next_capacity',
                'adjust_after_high_cost',
                'expect_capacity_persistence',
            ],
        )
        self.assertEqual(
            page.get_form_fields(human_agent)[-2:],
            ['agent_choice_influence', 'agent_predictability_effect'],
        )

    def test_human_agent_questions_cannot_be_left_unanswered(self):
        page = survey_app.StrategySurvey
        human_agent = SimpleNamespace(
            participant=SimpleNamespace(
                vars={'dynamic_bottleneck_treatment_group': 'HA'}
            )
        )

        errors = page.error_message(
            human_agent,
            {
                'reference_previous_capacity': 4,
                'predict_next_capacity': 4,
                'adjust_after_high_cost': 3,
                'expect_capacity_persistence': 4,
                'agent_choice_influence': None,
                'agent_predictability_effect': None,
            },
        )

        self.assertEqual(
            errors,
            {
                'agent_choice_influence': '请回答这道题。',
                'agent_predictability_effect': '请回答这道题。',
            },
        )


class SurveyFlowContractTests(unittest.TestCase):
    app_dir = Path(__file__).resolve().parent

    def test_page_sequence_preserves_non_cued_question_order(self):
        page_sequence = getattr(survey_app, 'page_sequence', None)

        self.assertIsNotNone(page_sequence)
        self.assertEqual(
            [page.__name__ for page in page_sequence],
            ['PatternRecognition', 'StrategySurvey', 'SurveyComplete'],
        )

    def test_templates_use_two_step_progress_and_agent_condition(self):
        recognition = (self.app_dir / 'PatternRecognition.html')
        strategy = (self.app_dir / 'StrategySurvey.html')
        complete = (self.app_dir / 'SurveyComplete.html')

        self.assertTrue(recognition.exists())
        self.assertTrue(strategy.exists())
        self.assertTrue(complete.exists())
        self.assertIn('规律识别', recognition.read_text(encoding='utf-8'))
        strategy_html = strategy.read_text(encoding='utf-8')
        self.assertIn('{{ if show_agent_questions }}', strategy_html)
        self.assertIn('Agent 对你的影响', strategy_html)
        self.assertIn('问卷已完成', complete.read_text(encoding='utf-8'))

    def test_dynamic_sessions_include_survey_before_payment(self):
        settings = importlib.import_module('settings')
        configs = {item['name']: item for item in settings.SESSION_CONFIGS}

        for name in ('dynamic_bottleneck_round_prod',):
            with self.subTest(name=name):
                self.assertEqual(
                    configs[name]['app_sequence'],
                    [
                        'access_gate',
                        'dynamic_bottleneck_round',
                        'dynamic_bottleneck_survey',
                        'payment_info',
                    ],
                )
        self.assertEqual(
            configs['dynamic_bottleneck_round_demo']['app_sequence'],
            ['dynamic_bottleneck_round', 'dynamic_bottleneck_survey'],
        )

    def test_custom_export_contains_treatment_and_all_answers(self):
        headers = getattr(survey_app, 'EXPORT_HEADERS', None)

        self.assertIsNotNone(headers)
        for field_name in (
            'session_code',
            'participant_code',
            'dynamic_group_label',
            'treatment_group',
            'information_condition',
            'treatment_condition',
            'api_agent_count',
            'rl_agent_count',
            'pattern_recognition',
            'noticed_pattern_round',
            'pattern_description',
            'agent_choice_influence',
            'agent_predictability_effect',
        ):
            self.assertIn(field_name, headers)


if __name__ == '__main__':
    unittest.main()


class PlayerBot(Bot):
    def play_round(self):
        yield Submission(
            survey_app.PatternRecognition,
            {
                'pattern_recognition': 4,
                'noticed_pattern_round': '26',
                'pattern_description': '后半段相邻轮次的服务率更容易保持不变。',
            },
        )

        strategy_answers = {
            'reference_previous_capacity': 4,
            'predict_next_capacity': 4,
            'adjust_after_high_cost': 4,
            'expect_capacity_persistence': 4,
        }
        if survey_app.treatment_group_for_player(self.player) == 'HA':
            strategy_answers.update(
                agent_choice_influence=3,
                agent_predictability_effect=3,
            )
        yield Submission(survey_app.StrategySurvey, strategy_answers)
        expect(self.player.treatment_group, 'in', {'H', 'HA'})
        yield Submission(survey_app.SurveyComplete)
