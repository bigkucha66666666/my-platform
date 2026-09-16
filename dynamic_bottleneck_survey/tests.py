import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest

from otree.api import Bot, Submission, expect, widgets


survey_app = importlib.import_module('dynamic_bottleneck_survey')

PAGE_ONE_FIELDS = [
    'service_rate_understanding',
    'predecision_capacity_access',
    'primary_decision_basis',
]
PAGE_TWO_FIELDS = [
    'adjust_after_high_cost',
    'anticipate_others',
    'avoid_crowded_slots',
    'decision_confidence',
    'perceived_information_benefit',
    'perceived_departure_concentration',
]
AGENT_FIELDS = [
    'agent_changed_strategy',
    'expected_agent_consistency',
    'agent_induced_avoidance',
]
ALL_ANSWER_FIELDS = PAGE_ONE_FIELDS + PAGE_TWO_FIELDS + AGENT_FIELDS
OLD_FIELDS = [
    'pattern_recognition',
    'noticed_pattern_round',
    'pattern_description',
    'reference_previous_capacity',
    'predict_next_capacity',
    'expect_capacity_persistence',
    'agent_choice_influence',
    'agent_predictability_effect',
]


def make_player(*, treatment='H', information='I0', api_agents=0, rl_agents=0):
    return SimpleNamespace(
        participant=SimpleNamespace(
            code='P001',
            label='G03-H01',
            vars={
                'assigned_group_id': 3,
                'assigned_group_label': 'G03',
                'dynamic_bottleneck_treatment_group': treatment,
                'dynamic_bottleneck_information_condition': information,
                'dynamic_bottleneck_api_agent_count': api_agents,
                'dynamic_bottleneck_rl_agent_count': rl_agents,
            },
        ),
        session=SimpleNamespace(
            code='SESSION1',
            config={
                'app_sequence': [
                    'dynamic_bottleneck_round',
                    'dynamic_bottleneck_survey',
                ]
            },
        ),
    )


class SurveyModelTests(unittest.TestCase):
    def test_app_defines_only_the_new_choice_question_fields(self):
        self.assertEqual(survey_app.C.NUM_ROUNDS, 1)
        for field_name in ALL_ANSWER_FIELDS:
            self.assertTrue(hasattr(survey_app.Player, field_name), field_name)
        for field_name in OLD_FIELDS:
            self.assertFalse(hasattr(survey_app.Player, field_name), field_name)

    def test_all_questions_use_radio_buttons_and_no_open_text_field(self):
        for field_name in ALL_ANSWER_FIELDS:
            column = survey_app.Player.__table__.columns[field_name]
            self.assertIs(
                column.form_props.get('widget'),
                widgets.RadioSelect,
                field_name,
            )
            self.assertNotEqual(column.type.__class__.__name__, 'Text', field_name)

    def test_experiment_metadata_preserves_full_two_by_two_treatment(self):
        player = make_player(
            treatment='HA', information='I1', api_agents=10, rl_agents=10
        )

        survey_app.copy_experiment_metadata(player)

        self.assertEqual(player.dynamic_group_id, 3)
        self.assertEqual(player.dynamic_group_label, 'G03')
        self.assertEqual(player.treatment_group, 'HA')
        self.assertEqual(player.information_condition, 'I1')
        self.assertEqual(player.treatment_condition, 'HA-I1')
        self.assertEqual(player.api_agent_count, 10)
        self.assertEqual(player.rl_agent_count, 10)

    def test_q3_i1_choices_include_current_rate_and_use_semantic_codes(self):
        choices = survey_app.primary_decision_basis_choices(
            make_player(information='I1')
        )

        self.assertEqual(
            [value for value, _label in choices],
            [
                'current_rate',
                'distribution',
                'history_cost',
                'others',
                'fixed_time',
                'no_fixed_rule',
            ],
        )

    def test_q3_i0_choices_exclude_current_rate_without_relettering_values(self):
        choices = survey_app.primary_decision_basis_choices(
            make_player(information='I0')
        )

        self.assertEqual(
            [value for value, _label in choices],
            [
                'distribution',
                'history_cost',
                'others',
                'fixed_time',
                'no_fixed_rule',
            ],
        )
        self.assertFalse(
            any(value in {'A', 'B', 'C', 'D', 'E', 'F'} for value, _ in choices)
        )

    def test_likert_questions_share_the_required_five_point_scale(self):
        expected = [
            [1, '完全不同意'],
            [2, '比较不同意'],
            [3, '不确定'],
            [4, '比较同意'],
            [5, '完全同意'],
        ]
        for field_name in PAGE_TWO_FIELDS + AGENT_FIELDS:
            props = survey_app.Player.__table__.columns[field_name].form_props
            self.assertEqual(props.get('choices'), expected, field_name)


class SurveyFlowContractTests(unittest.TestCase):
    app_dir = Path(__file__).resolve().parent

    def test_page_sequence_contains_two_question_pages_then_completion(self):
        self.assertEqual(
            [page.__name__ for page in survey_app.page_sequence],
            ['UnderstandingAndStrategy', 'DecisionExperience', 'SurveyComplete'],
        )
        self.assertEqual(survey_app.UnderstandingAndStrategy.form_fields, PAGE_ONE_FIELDS)

    def test_h_and_ha_receive_the_correct_second_page_fields(self):
        page = survey_app.DecisionExperience

        self.assertEqual(page.get_form_fields(make_player(treatment='H')), PAGE_TWO_FIELDS)
        self.assertEqual(
            page.get_form_fields(
                make_player(treatment='HA', api_agents=6, rl_agents=4)
            ),
            PAGE_TWO_FIELDS + AGENT_FIELDS,
        )

    def test_h_group_never_receives_agent_required_errors(self):
        values = {field_name: 4 for field_name in PAGE_TWO_FIELDS}

        self.assertIsNone(
            survey_app.DecisionExperience.error_message(
                make_player(treatment='H'), values
            )
        )

    def test_ha_group_requires_every_visible_agent_question(self):
        values = {field_name: 4 for field_name in PAGE_TWO_FIELDS}
        values.update(
            agent_changed_strategy=4,
            expected_agent_consistency=None,
            agent_induced_avoidance=None,
        )

        self.assertEqual(
            survey_app.DecisionExperience.error_message(
                make_player(treatment='HA', api_agents=6, rl_agents=4), values
            ),
            {
                'expected_agent_consistency': '请回答这道题。',
                'agent_induced_avoidance': '请回答这道题。',
            },
        )

    def test_templates_show_correct_numbering_progress_and_no_old_questions(self):
        first = (self.app_dir / 'UnderstandingAndStrategy.html').read_text(
            encoding='utf-8'
        )
        second = (self.app_dir / 'DecisionExperience.html').read_text(
            encoding='utf-8'
        )
        complete = (self.app_dir / 'SurveyComplete.html').read_text(encoding='utf-8')
        combined = first + second

        self.assertIn('第 1 / 2 页', first)
        self.assertIn('第 2 / 2 页', second)
        for number in range(1, 4):
            self.assertIn(f'Q{number}', first)
        for number in range(4, 13):
            self.assertIn(f'Q{number}', second)
        self.assertIn('{{ if show_agent_questions }}', second)
        self.assertIn('问卷已完成', complete)
        self.assertNotIn('后半段规律', combined)
        self.assertNotIn('规律开始轮次', combined)
        self.assertNotIn('textarea', combined.lower())

    def test_dynamic_sessions_include_survey_before_payment(self):
        settings = importlib.import_module('settings')
        configs = {item['name']: item for item in settings.SESSION_CONFIGS}

        self.assertEqual(
            configs['dynamic_bottleneck_round_prod']['app_sequence'],
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

    def test_custom_export_has_behavior_merge_keys_metadata_and_new_answers(self):
        expected_prefix = [
            'session_code',
            'participant_code',
            'participant_label',
            'group_id',
            'dynamic_group_id',
            'dynamic_group_label',
            'treatment_group',
            'information_condition',
            'treatment_condition',
            'api_agent_count',
            'rl_agent_count',
        ]

        self.assertEqual(survey_app.EXPORT_HEADERS, expected_prefix + ALL_ANSWER_FIELDS)
        for field_name in OLD_FIELDS:
            self.assertNotIn(field_name, survey_app.EXPORT_HEADERS)

    def test_custom_export_row_can_join_behavior_data_by_session_and_participant(self):
        player = make_player(
            treatment='HA', information='I1', api_agents=6, rl_agents=4
        )
        survey_app.copy_experiment_metadata(player)
        answers = {
            'service_rate_understanding': 'fewer_pass_more_queue',
            'predecision_capacity_access': 'exact_current_rate',
            'primary_decision_basis': 'current_rate',
            **{field_name: 4 for field_name in PAGE_TWO_FIELDS + AGENT_FIELDS},
        }
        player.field_maybe_none = answers.get

        headers, row = list(survey_app.custom_export([player]))
        exported = dict(zip(headers, row))

        self.assertEqual(exported['session_code'], 'SESSION1')
        self.assertEqual(exported['participant_code'], 'P001')
        self.assertEqual(exported['group_id'], 3)
        self.assertEqual(exported['primary_decision_basis'], 'current_rate')

    def test_completion_count_matches_treatment(self):
        self.assertEqual(
            survey_app.SurveyComplete.vars_for_template(
                make_player(treatment='H')
            )['answered_questions'],
            9,
        )
        self.assertEqual(
            survey_app.SurveyComplete.vars_for_template(
                make_player(treatment='HA', api_agents=6, rl_agents=4)
            )['answered_questions'],
            12,
        )


if __name__ == '__main__':
    unittest.main()


class PlayerBot(Bot):
    def play_round(self):
        first_page_answers = {
            'service_rate_understanding': 'fewer_pass_more_queue',
            'predecision_capacity_access': (
                'exact_current_rate'
                if survey_app.information_condition_for_player(self.player) == 'I1'
                else 'distribution_then_reveal'
            ),
            'primary_decision_basis': (
                'current_rate'
                if survey_app.information_condition_for_player(self.player) == 'I1'
                else 'distribution'
            ),
        }
        yield Submission(survey_app.UnderstandingAndStrategy, first_page_answers)

        second_page_answers = {field_name: 4 for field_name in PAGE_TWO_FIELDS}
        if survey_app.treatment_group_for_player(self.player) == 'HA':
            second_page_answers.update(
                {field_name: 3 for field_name in AGENT_FIELDS}
            )
        yield Submission(survey_app.DecisionExperience, second_page_answers)
        expect(self.player.treatment_group, 'in', {'H', 'HA'})
        yield Submission(survey_app.SurveyComplete)
