"""Real oTree download checks, always in a disposable database/process."""
import asyncio
import base64
import csv
import io
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent


async def request(app, url, cookie=''):
    parsed = urlsplit(url)
    messages = []
    complete = asyncio.Event()
    supplied = False

    async def receive():
        nonlocal supplied
        if not supplied:
            supplied = True
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await complete.wait()
        return {'type': 'http.disconnect'}

    async def send(message):
        messages.append(message)
        if message['type'] == 'http.response.body' and not message.get('more_body'):
            complete.set()

    scope = dict(type='http', asgi={'version': '3.0'}, http_version='1.1',
                 method='GET', scheme='http', path=parsed.path,
                 raw_path=parsed.path.encode(), root_path='',
                 query_string=parsed.query.encode(), server=('testserver', 80),
                 client=('127.0.0.1', 12345),
                 headers=[(b'host', b'testserver'), (b'cookie', cookie.encode())])
    await asyncio.wait_for(app(scope, receive, send), 15)
    start = next(m for m in messages if m['type'] == 'http.response.start')
    body = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    return start['status'], dict(start['headers']), body


def run_framework_checks():
    from otree.main import setup
    setup()
    from otree.database import db, engine, AnyModel
    from otree.session import create_session, SessionConfig, SESSION_CONFIGS_DICT
    from otree.common import AUTH_COOKIE_NAME, AUTH_COOKIE_VALUE, _SECRET
    from itsdangerous import TimestampSigner
    from sqlalchemy import event
    from unittest.mock import patch
    import dynamic_bottleneck_round as experiment
    import dynamic_bottleneck_survey as survey
    import payment_info
    import project_asgi
    from otree.asgi import app as native_app
    protocol_two = os.environ['DYNAMIC_ROOM_RELEASE_PROTOCOL'] == '2'

    fixture_config = SessionConfig(SESSION_CONFIGS_DICT['dynamic_bottleneck_round_demo'].copy())
    fixture_config['name'] = 'session_export_test'
    fixture_config['app_sequence'] = ['dynamic_bottleneck_round', 'dynamic_bottleneck_survey', 'payment_info']
    SESSION_CONFIGS_DICT[fixture_config['name']] = fixture_config
    sessions = [create_session('session_export_test', num_participants=2)
                for _ in range(2)]
    target, foreign = sessions
    players = experiment.Player.objects_filter(session=target).all()
    for player in players:
        if player.round_number in (1, 4, 33):
            player.departure_slot = 1
            player.departure_minute = 474
            player.departure_time_label = '07:54'
            player.decision_source = 'manual'
            player.total_cost = 0
            player.queue_delay_minutes = 0
            player.arrival_time_label = '08:03'
            player.payoff_unrounded = 140
            player.group.results_ready = True
    first = min((p for p in players if p.round_number == 1), key=lambda p: p.id_in_group)
    first.participant.label = '中文,测试'
    for number in (1, 4):
        for store, actor, agent_id in (
            (experiment.AGENT_DECISIONS_PARTICIPANT_VAR, 'deepseek_api_agent', 'LLM01'),
            (experiment.INDEPENDENT_RL_DECISIONS_PARTICIPANT_VAR, 'rl_agent', 'RL01')):
            records = first.participant.vars.setdefault(store, {})
            records[str(number)] = [dict(actor_type=actor, agent_id=agent_id,
                group_id=first.group.id_in_subsession, departure_slot=1,
                departure_minute=475, total_cost=7, payoff_unrounded=133,
                policy_version='frozen-test-v1')]
            if actor == 'rl_agent':
                records[str(number)][0]['payoff'] = records[str(number)][0].pop('payoff_unrounded')
    for player in survey.Player.objects_filter(session=target):
        player.service_rate_understanding = 'fewer_pass_more_queue'
    for player in payment_info.Player.objects_filter(session=target):
        player.final_total_payoff = 420
    if protocol_two:
        journal = experiment.sync_runtime.get_journal()
        journal.ensure_round(target.code, 1, expected_codes=[p.code for p in target.get_participants()],
            now_ts=100, decision_page_index=1, drain_limit_seconds=1,
            information_condition='I0', allowed_minutes=[474, 475])
        target.vars[experiment.sync_integration.ROUNDS_VAR] = [1]
        experiment.sync_runtime.latch_control_fault(
            {'session_code': target.code, 'round_number': 1}, 'isolated_existing_fault')
    db.commit()

    cookie_data = base64.b64encode(json.dumps({AUTH_COOKIE_NAME: AUTH_COOKIE_VALUE}).encode())
    cookie = 'session=' + TimestampSigner(str(_SECRET)).sign(cookie_data).decode()

    def snapshot():
        with engine.connect() as connection:
            return {table.name: [tuple(row) for row in connection.execute(table.select().order_by(table.c.id))]
                    for table in AnyModel.metadata.sorted_tables}

    writes = []
    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ', 'REPLACE ')):
            writes.append(statement)

    async def exercise():
        # Test the new download first: on the red run it is a real HTTP 404.
        prefix = f'/SessionDataExport/{target.code}'
        status, headers, body = await request(project_asgi.app, prefix + '/all_rounds', cookie)
        assert status == 200, ('all-round download route missing or failed', status)
        assert body.startswith(b'\xef\xbb\xbf')
        assert target.code.encode() in headers[b'content-disposition']
        assert headers.get(b'cache-control') == b'no-store'
        rows = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        assert {row['session_code'] for row in rows} == {target.code}
        humans = [row for row in rows if row['actor_type'] == 'human']
        agents = [row for row in rows if row['actor_type'] != 'human']
        assert len(humans) == 66 and len(agents) == 4, (len(humans), len(agents))
        assert {int(row['round_number']) for row in humans} == set(range(1, 34))
        assert {int(row['round_number']) for row in agents} == {1, 4}
        assert all(row['payoff'] == '133' for row in agents), 'legacy Agent payoff must be preserved'
        assert all(row['formal_round_number'] == '' for row in rows if row['round_phase'] == 'warmup')
        assert all(row['total_cost'] == '' for row in humans if row['round_number'] == '2')
        assert all(row['total_cost'] == '0.0' for row in humans if row['round_number'] == '33')
        assert all(float(row['coarse_toll_charge']) == 0 for row in humans if row['round_number'] == '33')
        if protocol_two:
            affected = [row for row in rows if row['round_number'] == '1']
            assert all(row['room_release_technical_anomaly'] == 'isolated_existing_fault' for row in affected)
            assert all(row['room_release_export_audit_status'] == 'available' for row in affected)

        status, _, body = await request(project_asgi.app, prefix + '/raw', cookie)
        assert status == 200, status
        raw = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        assert len(raw) == 2 and {row['session.code'] for row in raw} == {target.code}
        assert 'dynamic_bottleneck_round.33.player.departure_minute' in raw[0]
        assert raw[0]['dynamic_bottleneck_survey.1.player.service_rate_understanding'] == 'fewer_pass_more_queue'
        assert raw[0]['payment_info.1.player.final_total_payoff'] == '420.0'
        status, _, body = await request(project_asgi.app, f'/SessionDataExport/{foreign.code}/all_rounds', cookie)
        assert status == 200
        foreign_rows = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        assert {row['session_code'] for row in foreign_rows} == {foreign.code}
        assert len(foreign_rows) == 66

        for url, expected in (
            (prefix + '/all_rounds', 302), (prefix + '/raw', 302),
            ('/SessionDataExport/doesnotexist/all_rounds', 404),
            (prefix + '/unsafe_type', 400), ('/SessionDataExport//raw', 404),
            ('/SessionDataExport/invalid!code/all_rounds', 400)):
            status, _, _ = await request(project_asgi.app, url, '' if expected == 302 else cookie)
            assert status == expected, (url, status, expected)
        # A normal Session from another app is never a global-export fallback.
        other = create_session('route_choice_demo', num_participants=2)
        db.commit()
        status, _, _ = await request(project_asgi.app, f'/SessionDataExport/{other.code}/raw', cookie)
        assert status == 400, status

        from session_data_export_views import register_session_export_routes
        old_count = len(native_app.routes)
        register_session_export_routes(native_app)
        assert len(native_app.routes) == old_count, 'route registration must be idempotent'

        # Both real Report rounds render the same Session-specific links.
        for number in (1, 4):
            status, _, html = await request(project_asgi.app,
                f'/AdminReport/{target.code}?app_name=dynamic_bottleneck_round&round_number={number}', cookie)
            assert status == 200, status
            text = html.decode()
            assert '导出本 Session 全部轮次' in text
            assert prefix + '/all_rounds' in text and prefix + '/raw' in text
        # A native startup with no project route renders an explicit notice,
        # not broken or unscoped download links.
        saved_routes = list(native_app.router.routes)
        native_app.router.routes[:] = [route for route in saved_routes
                                      if getattr(route, 'name', '') != 'SessionDataExport']
        try:
            status, _, html = await request(project_asgi.app,
                f'/AdminReport/{target.code}?app_name=dynamic_bottleneck_round&round_number=1', cookie)
            assert status == 200 and '单 Session 导出尚未加载' in html.decode()
            assert prefix + '/all_rounds' not in html.decode()
        finally:
            native_app.router.routes[:] = saved_routes
        print('real-framework checks: auth, scoped long/wide CSV, all rounds, Agents, Report links: PASS')

    # The framework middleware commits each request; monitor the whole request,
    # not only the generator, for unintended writes.
    with patch.object(experiment, 'set_results', side_effect=AssertionError('export must not settle')), \
         patch.object(experiment, 'choose_agent_departure', side_effect=AssertionError('export must not call LLM')):
        event.listen(engine, 'before_cursor_execute', observe_sql)
        try:
            asyncio.run(exercise())
        finally:
            event.remove(engine, 'before_cursor_execute', observe_sql)
    # The other-app fixture was deliberately created during exercise. Compare
    # only the two original Sessions through a second strictly read-only sweep.
    after_setup = snapshot()
    journal_path = Path(os.environ['DYNAMIC_ROOM_SYNC_DB'])
    def journal_snapshot():
        if not journal_path.exists():
            return None
        with sqlite3.connect(journal_path.as_uri() + '?mode=ro', uri=True) as connection:
            return connection.execute('SELECT session_code, round_number, record FROM sync_rounds ORDER BY 1, 2').fetchall()
    before_journal = journal_snapshot()
    writes.clear()
    event.listen(engine, 'before_cursor_execute', observe_sql)
    try:
        for kind in ('all_rounds', 'raw'):
            status, _, _ = asyncio.run(request(project_asgi.app,
                f'/SessionDataExport/{target.code}/{kind}', cookie))
            assert status == 200
            assert not writes, ('download issued database writes', kind, writes)
    finally:
        event.remove(engine, 'before_cursor_execute', observe_sql)
    assert snapshot() == after_setup, 'exports changed saved records'
    assert not writes, ('exports issued database writes', writes)
    assert journal_snapshot() == before_journal, 'export changed or created sync history'
    if not protocol_two:
        assert not journal_path.exists(), 'export created a sync journal'
    else:
        # Failure latching is still in memory; export must not persist it.
        assert 'isolated_existing_fault' not in before_journal[0][2]
        asyncio.run(project_asgi.app.aclose())
    print('real-framework checks: zero database writes and unchanged sync history: PASS')


class SessionExportFrameworkTests(unittest.TestCase):
    def test_real_framework_downloads(self):
        self.run_isolated('1')

    def test_protocol_two_with_existing_sync_history(self):
        self.run_isolated('2')

    def run_isolated(self, protocol):
        with tempfile.TemporaryDirectory(prefix='session-export-test-') as directory:
            for name in ('_static', '_templates', '_rooms', 'access_gate',
                         'dynamic_bottleneck_round', 'dynamic_bottleneck_survey',
                         'payment_info', 'route_choice', 'single_bottleneck'):
                Path(directory, name).symlink_to(PROJECT_ROOT / name, target_is_directory=True)
            env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT), OTREE_AUTH_LEVEL='STUDY',
                OTREE_PRODUCTION='0', OTREE_IN_MEMORY='1', DYNAMIC_ROOM_RELEASE_PROTOCOL=protocol,
                DYNAMIC_ROOM_RELEASE_DRAIN_SECONDS='1',
                DYNAMIC_ROOM_SYNC_DB=str(Path(directory) / 'journal.sqlite3'),
                DYNAMIC_BOTTLENECK_API_AGENT_MODE='off', DYNAMIC_BOTTLENECK_RL_AGENT_ENABLED='0',
                OTREE_ADMIN_PASSWORD='isolated-test-only', OTREE_SKIP_CSRF='0')
            completed = subprocess.run([sys.executable, str(Path(__file__)), '--child'],
                cwd=directory, env=env, capture_output=True, text=True, timeout=55)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == '__main__':
    if '--child' in sys.argv:
        run_framework_checks()
    else:
        unittest.main()
