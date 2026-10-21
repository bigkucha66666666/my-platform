"""Real task/HTTP regressions; imported only after the isolated engine check."""
import asyncio
import base64
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import patch
from urllib.parse import urlencode


class Clock:
    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


def check_scenario(scenario, helper, control_request):
    from otree.main import setup
    os.environ['PROJECT_TIMEOUT_WORKER_SECRET'] = 'test-timeout-only-' + 'a' * 48
    setup()
    import project_asgi
    import dynamic_bottleneck_round as e
    from dynamic_bottleneck_round import sync_runtime as runtime, sync_integration, sync_transport
    from otree.database import db, engine
    from otree.lookup import _get_session_lookups
    from otree.models import Participant, Session
    from otree.session import create_session
    assert engine.url.get_backend_name() == 'sqlite'
    assert Path(engine.url.database).resolve() == Path.cwd() / 'db.sqlite3'
    runtime.get_journal()
    sessions, codes = {}, {}
    for condition in ('I0', 'I1'):
        session = create_session('dynamic_bottleneck_round_prod', num_participants=12,
            modified_session_config_fields=dict(pilot_mode_enabled=1,
                pilot_group_spec=f'G01:H-{condition},human=9,api=0,rl=0;'
                                 f'G02:HA-{condition},human=3,api=3,rl=3',
                capacity_information_condition=condition))
        sessions[condition] = session.code
        codes[condition] = [p.code for p in session.get_participants()]
    db.commit()

    def index(condition, number, name):
        return next(i for i, info in _get_session_lookups(sessions[condition]).items()
                    if info.round_number == number and info.page_class.__name__ == name)

    def player(code, number):
        db.new_session()
        return e.Player.objects_get(participant=Participant.objects_get(code=code), round_number=number)

    def fixture(number, name):
        for condition in codes:
            for code in codes[condition]:
                pp = Participant.objects_get(code=code)
                pp._index_in_pages = index(condition, number, name)
                pp.visited = True
                pp.vars['access_granted'] = True
                pp.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
                pp._last_page_timestamp = int(time.time())
        db.commit()

    async def request(code, *, method='GET', data=None):
        db.new_session()
        path = Participant.objects_get(code=code)._url_i_should_be_on()
        return await helper.http_request(project_asgi.app, path, method=method,
                                        body=urlencode(data or {}).encode())

    async def render_token(code):
        status, content, error = await request(code)
        assert status == 200, (status, error)
        token = re.search(r'data-control-token="([^"]+)"', content)
        assert token, content[:500]
        return token.group(1)

    def provider(*, config, choice_set):
        return e.AgentChoice(departure_slot=min(choice_set.valid_slots()),
                             decision_source='deepseek_api', fallback_used=False,
                             reason='isolated_mock_provider')

    clock = Clock()
    with ExitStack() as stack:
        for module in (e, sync_integration, sync_transport):
            stack.enter_context(patch.object(module, 'time', clock))
        stack.enter_context(patch.object(e, 'choose_agent_departure', side_effect=provider))
        if scenario in {'admin_advance_guard', 'unsettled_results'}:
            fixture(22, 'ResultsSync' if scenario == 'admin_advance_guard' else 'Results')
            return asyncio.run(check_admin_or_results(scenario, sessions, codes, player,
                request, project_asgi.app, helper, e, runtime))
        fixture(22, 'Decision')

        async def exercise():
            previous_tokens = {}
            schedule = e.departure_schedule_for_player(player(codes['I0'][0], 22))
            prior_minute = schedule['first_departure_minute']
            excluded_minute = schedule['last_departure_minute']
            for code in codes['I0']:
                previous_tokens[code] = await render_token(code)
                status, result = await control_request(project_asgi.app, previous_tokens[code], {'kind': 'ack'})
                assert status == 200 and result['acknowledged'], result
            j = runtime.get_journal()
            prior = j.state(sessions['I0'], 22)
            clock.now = prior['release_ts'] + .1
            for code in codes['I0']:
                assert (await control_request(project_asgi.app, previous_tokens[code],
                    {'kind': 'decision', 'departure_minute': prior_minute}))[0] == 200
                assert (await request(code, method='POST', data={'departure_minute': prior_minute}))[0] == 302
            for _ in range(50):
                status, content, error = await request(codes['I0'][0])
                assert status == 200, error
                if 'data-ready="1"' in content:
                    break
                await asyncio.sleep(.03)
            else:
                raise AssertionError('prior H/HA round failed to settle')
            assert j.state(sessions['I0'], 22)['settled']
            for code in codes['I0']:
                assert (await request(code, method='POST'))[0] == 302
                assert (await request(code))[0] == 200
            lagging = codes['I0'][6]
            tokens = {}
            if scenario == 'timeout_redirect':
                for code in codes['I0']:
                    if code == lagging:
                        continue
                    assert (await request(code, method='POST'))[0] == 302
                    tokens[code] = await render_token(code)
                    assert (await control_request(project_asgi.app, tokens[code], {'kind': 'ack'}))[0] == 200
                before = j.state(sessions['I0'], 23)
                assert len(before['ack_ts_by_code']) == 11
                clock.now = before['prepare_deadline_ts'] - 7
            else:
                before = None
            with j._connection() as conn:
                i1_before = list(conn.execute('SELECT round_number, record FROM sync_rounds WHERE session_code=?',
                                              (sessions['I1'],)))
            requests = await asyncio.to_thread(run_real_timeout_task, lagging,
                index('I0', 22, 'Results'), project_asgi.app)
            assert any(method == 'POST' and '/Results/' in path for method, path in requests), requests
            assert any(method == 'GET' and '/Decision/' in path for method, path in requests), requests
            # The other native task performs a direct GET (not a redirect).
            direct = await asyncio.to_thread(run_real_timeout_task, codes['I1'][0],
                index('I1', 22, 'Decision'), project_asgi.app, ensure=True)
            assert any(method == 'GET' and '/Decision/' in path for method, path in direct), direct
            with j._connection() as conn:
                assert list(conn.execute('SELECT round_number, record FROM sync_rounds WHERE session_code=?',
                                          (sessions['I1'],))) == i1_before
            if scenario == 'timeout_first':
                db.new_session()
                saved = Session.objects_get(code=sessions['I0'])._vars
                assert 23 not in saved.get(sync_integration.ROUNDS_VAR, []), \
                    'internal first visitor must not initialize a browser prepare clock'
                with j._connection() as conn:
                    assert conn.execute('SELECT count(*) FROM sync_rounds WHERE session_code=? AND round_number=23',
                                        (sessions['I0'],)).fetchone()[0] == 0
                clock.now += 75
                token = await render_token(lagging)
                initialized = j.state(sessions['I0'], 23)
                assert initialized['prepare_started_ts'] == clock.now
                assert initialized['prepare_deadline_ts'] - clock.now == 60
                assert (await control_request(project_asgi.app, token, {'kind': 'ack'}))[0] == 200
                assert (await request(lagging))[0] == 200
                assert j.state(sessions['I0'], 23)['prepare_started_ts'] == clock.now
                return
            clock.now = before['prepare_deadline_ts'] + .1
            status, result = await control_request(project_asgi.app, next(iter(tokens.values())), {'kind': 'status'})
            assert status == 200 and not result.get('technical_anomaly'), \
                ('internal timeout redirect poisoned preparation', status, result)
            released = j.state(sessions['I0'], 23)
            assert released['excluded_codes'] == [lagging]
            assert lagging not in released['shell_rendered_ts_by_code']
            saved_session = Session.objects_get(code=sessions['I0'])
            expected_seconds = e.round_timing_for_session(saved_session, 23)['decision_seconds']
            assert released['deadline_ts'] - released['release_ts'] == expected_seconds
            clock.now = released['release_ts'] + .1
            late_token = await render_token(lagging)
            status, result = await control_request(project_asgi.app, late_token,
                {'kind': 'decision', 'departure_minute': excluded_minute})
            assert status == 200 and result['decision_kind'] == 'excluded', result
            assert j.receipt(sessions['I0'], 23, lagging)['kind'] != 'manual'
            for code, token in tokens.items():
                assert (await control_request(project_asgi.app, token,
                    {'kind': 'decision', 'departure_minute': 474}))[0] == 200
                assert (await request(code, method='POST', data={'departure_minute': 474}))[0] == 302
            assert (await request(lagging, method='POST', data={'departure_minute': excluded_minute}))[0] == 302
            p = player(lagging, 23).participant
            assert _get_session_lookups(sessions['I0'])[p._index_in_pages].page_class.__name__ == 'RecoveryGate'
            assert (await request(lagging))[0] == 200
            assert (await request(lagging, method='POST'))[0] == 302
            assert not e.participant_dropout_active(player(lagging, 23))
            clock.now = released['deadline_ts'] + 10.1
            for _ in range(50):
                status, content, error = await request(codes['I0'][0])
                assert status == 200, error
                if 'data-ready="1"' in content:
                    break
                await asyncio.sleep(.03)
            else:
                raise AssertionError('current H/HA round failed to settle')
            assert j.state(sessions['I0'], 23)['settled']
            assert not e.participant_dropout_active(player(lagging, 23)), 'settlement must not reopen recovery'
            for code in codes['I0']:
                p = player(code, 23)
                assert p.group.results_ready and p.total_cost >= 0
                if code == lagging:
                    assert p.decision_source == e.DECISION_SOURCE_DISCONNECT_AUTO
                    assert p.departure_minute == prior_minute and p.dropout_event == 'disconnect'
                else:
                    assert p.decision_source == e.DECISION_SOURCE_MANUAL and p.departure_minute == 474
            # The late actor recovers through real callbacks, without editing page indices.
            for _ in range(6):
                p = player(lagging, 23).participant
                name = _get_session_lookups(sessions['I0'])[p._index_in_pages].page_class.__name__
                if name == 'Decision':
                    break
                assert name in {'RecoveryGate', 'ResultsSync', 'Results'}, name
                assert (await request(lagging))[0] == 200
                assert (await request(lagging, method='POST'))[0] == 302
            assert not e.participant_dropout_active(player(lagging, 24))
            token = await render_token(lagging)
            status, result = await control_request(project_asgi.app, token, {'kind': 'ack'})
            assert status == 200 and result['acknowledged'], result
            print('real timeout task POST -> redirected Decision GET; absence, proxy, settlement and next-round recovery OK', flush=True)

        asyncio.run(exercise())


def run_real_timeout_task(code, page_index, application, *, ensure=False):
    import uvicorn
    observed = []
    response_errors = []
    async def track(scope, receive, send):
        if scope['type'] == 'http':
            observed.append((scope['method'], scope['path']))
        body = bytearray()
        async def inspect(message):
            if '/Decision/' in scope.get('path', '') and message['type'] == 'http.response.body':
                body.extend(message.get('body', b''))
                if not message.get('more_body'):
                    for needle in (b'data-control-token', b'data-shell-token',
                                   b'actual_capacity', b'id="otree-live"'):
                        if needle in body:
                            response_errors.append((scope['path'], needle.decode()))
            await send(message)
        return await application(scope, receive, inspect)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    assert port != 18000
    server = uvicorn.Server(uvicorn.Config(track, host='127.0.0.1', port=port,
        log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        while not server.started:
            if not thread.is_alive() or time.monotonic() - started > 5:
                raise AssertionError('isolated TCP service did not start')
            time.sleep(.01)
        from otree.database import db
        from otree import tasks
        if ensure:
            from otree.models import Participant
            tasks.ensure_pages_visited(-1,
                participant_pks=[Participant.objects_get(code=code).id], page_index=page_index)
        else:
            tasks.submit_expired_url(-1, participant_code=code, page_index=page_index)
        db.commit()
        source = r'''
import os
from pathlib import Path
from otree.database import engine
assert engine.url.get_backend_name() == 'sqlite'
assert Path(engine.url.database).resolve() == Path.cwd() / 'db.sqlite3'
from otree.main import setup
setup()
try:
    from project_timeout_worker import install_timeout_transport
except ModuleNotFoundError as exc:
    if exc.name != 'project_timeout_worker': raise
else:
    install_timeout_transport(int(os.environ['ISOLATED_TIMEOUT_PORT']))
from otree import tasks
def stop_after_iteration(seconds): raise SystemExit(0)
tasks.sleep = stop_after_iteration
worker = tasks.Worker(int(os.environ['ISOLATED_TIMEOUT_PORT']))
# End only the constructor's stale-task cleanup transaction. PostgreSQL allows
# the task HTTP write in parallel; an uncommitted SQLite DELETE would instead
# lock the disposable business file before the real task loop even starts.
from otree.database import db
db.commit()
worker.listen()
'''
        result = subprocess.run([sys.executable, '-B', '-c', source],
            env=dict(os.environ, ISOLATED_TIMEOUT_PORT=str(port)),
            cwd=Path.cwd(), text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Traceback' not in result.stderr, result.stdout + result.stderr
        assert not response_errors, response_errors
        print(result.stdout.strip(), flush=True)
        return observed
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive(), 'isolated TCP thread was not stopped'


async def check_admin_or_results(scenario, sessions, codes, player, request,
                                 app, helper, e, runtime):
    from otree.database import db
    from otree.models import Participant
    if scenario == 'unsettled_results':
        code = codes['I0'][0]
        before = player(code, 22).participant._index_in_pages
        status, content, error = await request(code)
        assert status == 200 and '尚未完成结算' in content, (status, error)
        assert 'class="otree-btn-next' not in content, 'unsettled Results must not offer an advance button'
        assert 'class="result-shell"' not in content, 'unsettled Results must not render the cost panel'
        status, _, error = await request(code, method='POST')
        assert status == 200, error
        assert player(code, 22).participant._index_in_pages == before
        assert not player(code, 22).group.results_ready
        from otree import constants
        from otree.views.abstract import ADMIN_SECRET_CODE
        status, content, error = await request(code, method='POST', data={
            constants.timeout_happened: True, constants.admin_secret_code: ADMIN_SECRET_CODE})
        assert status == 409 and '尚未完成结算' in content, (status, error)
        assert player(code, 22).participant._index_in_pages == before
        return
    from itsdangerous import TimestampSigner
    from otree import middleware
    from otree.common import AUTH_COOKIE_NAME, AUTH_COOKIE_VALUE, CSRF_TOKEN_NAME
    payload = {AUTH_COOKIE_NAME: AUTH_COOKIE_VALUE, CSRF_TOKEN_NAME: 'isolated-csrf'}
    value = TimestampSigner(middleware._SECRET).sign(base64.b64encode(json.dumps(payload).encode())).decode()
    async def authenticated(scope, receive, send):
        scope = dict(scope, headers=[*scope['headers'], (b'cookie', f'session={value}'.encode())])
        return await app(scope, receive, send)
    for condition in sessions:
        before = [(p, player(p, 22).participant._index_in_pages) for p in codes[condition]]
        body = urlencode({CSRF_TOKEN_NAME: 'isolated-csrf', 'advancemode': 'all'}).encode()
        path = f'/AdvanceSession/{sessions[condition]}'
        status, content, error = await helper.http_request(app, path, method='POST', body=body)
        assert status == 302 and '不能绕过' not in content, (status, error)
        status, content, error = await helper.http_request(authenticated, path, method='POST', body=body)
        assert status == 409 and '不能绕过同步' in content, (status, error)
        assert [(p, player(p, 22).participant._index_in_pages) for p in codes[condition]] == before
        assert all(not player(p, 22).group.results_ready for p in codes[condition])
        status, content, error = await helper.http_request(authenticated, f'/SessionMonitor/{sessions[condition]}')
        assert status == 200 and '不能绕过同步' in content, (status, error)
        status, _, error = await helper.http_request(authenticated, path, method='POST', body=b'')
        assert status == 403, (status, error)
    # Legacy Sessions still delegate to the installed native implementation.
    from otree.models import Session
    from otree.views.admin import AdvanceSession
    from starlette.responses import Response
    db.new_session()
    legacy = Session.objects_get(code=sessions['I1'])
    legacy.vars[e.ROOM_RELEASE_PROTOCOL_VAR] = 1
    db.commit()
    with patch.object(AdvanceSession, 'post', return_value=Response('native-legacy')) as native:
        path = f'/AdvanceSession/{sessions["I1"]}'
        status, content, error = await helper.http_request(authenticated, path, method='POST', body=body)
        assert status == 200 and content == 'native-legacy', (status, error)
        native.assert_called_once()
        # An individually selected protocol-2 actor cannot be advanced via a legacy URL.
        selected = urlencode({CSRF_TOKEN_NAME: 'isolated-csrf',
                              'selected_participant': codes['I0'][0]}).encode()
        status, _, error = await helper.http_request(authenticated, path, method='POST', body=selected)
        assert status == 409, (status, error)
        assert native.call_count == 1
