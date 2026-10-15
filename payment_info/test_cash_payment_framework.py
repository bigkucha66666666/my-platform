"""Cash accounting in a disposable real oTree DB, not a participant flow test."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_framework_checks():
    from otree.main import setup
    setup()
    from otree.api import cu
    from otree.database import db
    from otree.session import create_session, SessionConfig, SESSION_CONFIGS_DICT
    from otree.templating.template import Template
    import dynamic_bottleneck_round as experiment
    import payment_info
    from payment_info.cash_payment import BUDGET_RULE_VERSION
    from unittest.mock import patch

    formal_template = Template((PROJECT_ROOT / 'dynamic_bottleneck_round/FormalStart.html').read_text())
    receipt_template = Template((PROJECT_ROOT / 'payment_info/PaymentInfo.html').read_text())

    with patch.object(experiment, 'choose_agent_departure', side_effect=AssertionError('No real API allowed')):
        sessions = [create_session('dynamic_bottleneck_round_prod', num_participants=40,
            modified_session_config_fields={
                'group_treatment_spec': f'G01:H-{condition};G02:HA-{condition}',
                'capacity_information_condition': condition,
                'api_agent_mode': 'active', 'api_agent_count_per_group': 10,
                'rl_agent_enabled': 1, 'rl_agent_count_per_group': 10,
            }) for condition in ('I0', 'I1')]
    old_config = SessionConfig(SESSION_CONFIGS_DICT['dynamic_bottleneck_round_demo'].copy())
    old_config.update(name='cash_legacy_fixture',
        app_sequence=['dynamic_bottleneck_round', 'payment_info'],
        cash_payment_rule='dynamic_cost_v1', participation_fee=15,
        real_world_currency_per_point=0.01, api_agent_mode='off', rl_agent_enabled=0)
    SESSION_CONFIGS_DICT['cash_legacy_fixture'] = old_config
    legacy = create_session('cash_legacy_fixture', num_participants=1)

    def settle(session, expected_cny, expected_rule, max_label):
        totals = []
        for participant in session.get_participants():
            # Synthetic completed-round inputs; no decisions or LLM calls.
            first = participant.get_player('dynamic_bottleneck_round', 1)
            first.payoff = cu(4200)
            participant.vars.update(
                dynamic_bottleneck_round_total_cost_decimal='0',
                dynamic_bottleneck_round_total_cost=0,
                dynamic_bottleneck_round_mean_cost=0,
                dynamic_bottleneck_round_settled_formal_rounds=30,
                dynamic_bottleneck_round_total_payoff=4200)
            player = participant.get_player('payment_info', 1)
            context = payment_info.PaymentInfo.vars_for_template(player)
            assert context['payment_rule_version'] == expected_rule
            assert context['cash_total_label'] == f'{expected_cny:.2f}'
            html = receipt_template.render(context)
            assert f'最高 {max_label} 元' in html
            payment_info.PaymentInfo.before_next_page(player, False)
            payment_info.PaymentInfo.vars_for_template(player)
            assert float(participant.payoff_plus_participation_fee()) == expected_cny
            assert participant.vars['dynamic_bottleneck_cash_payment_cents'] == expected_cny * 100
            formal_player = participant.get_player('dynamic_bottleneck_round', 4)
            disclosure = formal_template.render(experiment.FormalStart.vars_for_template(formal_player))
            assert f'最高 {max_label} 元' in disclosure
            if expected_rule == BUDGET_RULE_VERSION:
                assert '最高 35 元' not in disclosure
            headers, row = list(payment_info.custom_export([player]))
            exported = dict(zip(headers, row))
            assert exported['native_cash_total_cny'] == exported['total_cny'] == f'{expected_cny:.2f}'
            assert exported['max_total_cny'] == f'{float(max_label):.2f}'
            assert exported['budget_total_cny'] == ('2000.00' if expected_rule == BUDGET_RULE_VERSION else '')
            totals.append(float(participant.payoff_plus_participation_fee()))
        db.commit()
        return sum(totals)

    for session in sessions:
        assert session.config['cash_payment_rule'] == BUDGET_RULE_VERSION
    totals = [settle(session, 25, BUDGET_RULE_VERSION, '25') for session in sessions]
    assert totals == [1000, 1000] and sum(totals) == 2000, totals
    assert settle(legacy, 35, 'dynamic_cost_v1', '35') == 35
    print('PASS: two independent 40-Human Sessions <=2000; real receipt/native/export agree; legacy stays 35')


class CashBudgetFrameworkTests(unittest.TestCase):
    def test_two_rooms_budget_and_legacy_in_real_framework(self):
        with tempfile.TemporaryDirectory(prefix='cash-budget-test-') as directory:
            for name in ('_static', '_templates', '_rooms', 'access_gate',
                         'dynamic_bottleneck_round', 'dynamic_bottleneck_survey',
                         'payment_info', 'route_choice', 'single_bottleneck'):
                Path(directory, name).symlink_to(PROJECT_ROOT / name, target_is_directory=True)
            env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT),
                DATABASE_URL='sqlite:///:memory:', OTREE_IN_MEMORY='1', OTREE_PRODUCTION='0',
                DYNAMIC_ROOM_RELEASE_PROTOCOL='', DYNAMIC_ROOM_SYNC_DB=str(Path(directory) / 'journal.sqlite3'),
                DYNAMIC_BOTTLENECK_API_AGENT_MODE='off', DYNAMIC_BOTTLENECK_RL_AGENT_ENABLED='0')
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'],
                cwd=directory, env=env, capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if '--child' in sys.argv:
        run_framework_checks()
    else:
        unittest.main()
