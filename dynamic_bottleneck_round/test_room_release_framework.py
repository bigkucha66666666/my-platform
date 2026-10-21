"""Run real oTree HTTP callbacks with frozen models in a disposable database.

Each check uses a fresh process because standard ORM initialization changes
model classes globally. No project or production database is opened.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlencode


PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def http_request(application, path, *, method='GET', body=b''):
    messages = []
    completed = asyncio.Event()
    supplied = False

    async def receive():
        nonlocal supplied
        if not supplied:
            supplied = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await completed.wait()
        return {'type': 'http.disconnect'}

    async def send(message):
        messages.append(message)
        if message['type'] == 'http.response.body' and not message.get('more_body'):
            completed.set()

    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': method, 'scheme': 'http', 'path': path,
        'raw_path': path.encode(), 'root_path': '', 'query_string': b'',
        'server': ('testserver', 80), 'client': ('127.0.0.1', 12345),
        'headers': [(b'host', b'testserver'),
                    (b'content-type', b'application/x-www-form-urlencoded')],
    }
    error = None
    try:
        await asyncio.wait_for(application(scope, receive, send), timeout=20)
    except Exception as exc:
        error = repr(exc)
    start = next((m for m in messages if m['type'] == 'http.response.start'), {})
    content = b''.join(m.get('body', b'') for m in messages
                       if m['type'] == 'http.response.body').decode('utf-8', 'replace')
    return start.get('status'), content, error


class LiveConnection:
    """Actual oTree Live consumer with in-memory ASGI transport queues."""
    def __init__(self, application, code, session_code, page_index):
        self.application = application
        self.input = asyncio.Queue()
        self.output = asyncio.Queue()
        self.scope = {
            'type': 'websocket', 'asgi': {'version': '3.0'}, 'scheme': 'ws',
            'path': '/live', 'raw_path': b'/live', 'root_path': '',
            'query_string': urlencode({
                'participant_code': code, 'session_code': session_code,
                'page_index': page_index, 'page_name': 'Decision',
            }).encode(),
            'server': ('testserver', 80), 'client': ('127.0.0.1', 12345),
            'headers': [(b'host', b'testserver')], 'subprotocols': [],
        }
        self.task = None

    async def connect(self):
        await self.input.put({'type': 'websocket.connect'})
        self.task = asyncio.create_task(self.application(
            self.scope, self.input.get, self.output.put,
        ))
        assert (await asyncio.wait_for(self.output.get(), 10))['type'] == 'websocket.accept'
        return self

    async def exchange(self, data):
        await self.input.put({'type': 'websocket.receive', 'text': json.dumps(data)})
        message = await asyncio.wait_for(self.output.get(), 20)
        assert message['type'] == 'websocket.send', message
        response = json.loads(message['text'])
        if 'otree_success' in response:  # oTree 6 envelope; 5 sends payload directly.
            assert response['otree_success'], response
            return response['live_method_payload']
        return response

    async def close(self):
        await self.input.put({'type': 'websocket.disconnect', 'code': 1000})
        await asyncio.wait_for(self.task, 10)


def check_frozen_decision_get():
    from otree.main import setup
    setup()
    import project_asgi
    import dynamic_bottleneck_round as experiment
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.session import create_session

    session = create_session('dynamic_bottleneck_round_demo', num_participants=1)
    index = next(index for index, info in _get_session_lookups(session.code).items()
                 if info.round_number == 1 and info.page_class.__name__ == 'Decision')
    participant = session.get_participants()[0]
    participant._index_in_pages = index
    participant.visited = True
    participant.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
    participant._last_page_timestamp = int(time.time())
    participant._last_request_timestamp = int(time.time())
    db.commit()
    player = experiment.Player.objects_get(session=session, round_number=1)
    assert player._is_frozen and hasattr(player, '_setattr_fields')
    path = f'/p/{participant.code}/dynamic_bottleneck_round/Decision/{index}'
    status, content, error = asyncio.run(http_request(project_asgi.app, path))
    assert status == 200, f'Decision GET returned {status}; {error}'
    assert 'room-release-gate' in content
    assert '_room_release_choice_preexisting' not in player.__dict__
    print('frozen Decision GET: 200', flush=True)


def check_real_decision_posts():
    from otree.main import setup
    setup()
    import project_asgi
    import dynamic_bottleneck_round as experiment
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.models import Participant, Session
    from otree.session import create_session
    from otree.middleware import lock2

    session = create_session('dynamic_bottleneck_round_demo', num_participants=6)
    session_code = session.code
    codes = [p.code for p in session.get_participants()]

    def arm(code, round_number=1):
        db.new_session()
        participant = Participant.objects_get(code=code)
        index = next(index for index, info in _get_session_lookups(session_code).items()
                     if info.round_number == round_number
                     and info.page_class.__name__ == 'Decision')
        participant._index_in_pages = index
        participant.visited = True
        participant.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
        participant._last_page_timestamp = int(time.time())
        participant._last_request_timestamp = int(time.time())
        participant._timeout_expiration_time = None
        participant._timeout_page_index = 0
        db.commit()
        return f'/p/{code}/dynamic_bottleneck_round/Decision/{index}'

    def player(code, round_number=1):
        db.new_session()
        return experiment.Player.objects_get(participant=Participant.objects_get(code=code),
                                             round_number=round_number)

    def window(*, deadline=None, round_number=1):
        reference = player(codes[0], round_number)
        state = experiment.room_release_state(reference)
        # Controlled boundary fixtures always retain the real 60-second window.
        deadline = time.time() + 59 if deadline is None else deadline
        state.update(release_ts=deadline - 60, deadline_ts=deadline, phase='RELEASED')
        state['ack_ts_by_code'] = {code: state['prepare_started_ts'] for code in codes}
        reference.session.vars[f'{experiment.ROOM_RELEASE_ROUND_PREFIX}{round_number}'] = state
        for group in reference.subsession.get_groups():
            group.round_started = True
            group.round_started_at_ts = state['release_ts']
            group.decision_deadline_ts = deadline
        db.commit()

    async def post(path, minute):
        from urllib.parse import urlencode
        return await http_request(project_asgi.app, path, method='POST',
                                  body=urlencode({'departure_minute': minute}).encode())

    async def exercise():
        paths = [arm(code) for code in codes]
        for path in paths:
            status, _, error = await http_request(project_asgi.app, path)
            assert status == 200, (status, error)
        window()
        status, _, error = await post(paths[0], 474)
        assert status == 302, (status, error)
        chosen = player(codes[0])
        assert chosen.decision_source == 'manual' and chosen.departure_minute == 474
        status, _, error = await post(paths[0], 475)
        assert status == 302, (status, error)
        assert player(codes[0]).departure_minute == 474

        status, content, error = await post(paths[1], 999)
        assert status == 200 and '有效出发时间' in content, (status, error)
        assert not experiment.player_has_departure_choice(player(codes[1]))
        status, _, error = await post(paths[1], 475)
        assert status == 302, (status, error)
        assert player(codes[1]).decision_source == 'manual'

        automatic = player(codes[2])
        experiment.set_player_departure_choice(automatic, 473,
                                               experiment.DECISION_SOURCE_TIMEOUT_AUTO)
        db.commit()
        status, _, error = await post(paths[2], 476)
        assert status == 302, (status, error)
        automatic = player(codes[2])
        assert automatic.departure_minute == 473
        assert automatic.decision_source == experiment.DECISION_SOURCE_TIMEOUT_AUTO

        window(deadline=time.time() + 61)
        status, content, error = await post(paths[3], 474)
        assert status == 200 and '尚未放行' in content, (status, error)
        assert not experiment.player_has_departure_choice(player(codes[3]))
        window()
        status, _, error = await post(paths[3], 474)
        assert status == 302 and player(codes[3]).decision_source == 'manual', error

        window(deadline=time.time() - 1)
        status, _, error = await post(paths[4], 474)
        assert status == 302, (status, error)
        assert player(codes[4]).decision_source == experiment.DECISION_SOURCE_TIMEOUT_AUTO

        window(deadline=time.time() + .15)
        await lock2.acquire()
        try:
            queued = asyncio.create_task(post(paths[5], 474))
            await asyncio.sleep(.25)
        finally:
            lock2.release()
        status, _, error = await queued
        assert status == 302, (status, error)
        delayed = player(codes[5])
        assert delayed.decision_source == 'manual'
        prefix = f'{experiment.ROOM_RELEASE_ROUND_PREFIX}1:'
        audit = delayed.participant.vars
        assert audit[prefix + 'post_processing_ts'] > audit[prefix + 'post_ingress_ts'] + .1

        next_path = arm(codes[2], 2)
        status, _, error = await http_request(project_asgi.app, next_path)
        assert status == 200, (status, error)
        window(round_number=2)
        status, _, error = await post(next_path, 474)
        assert status == 302 and player(codes[2], 2).decision_source == 'manual', error
        assert all('_room_release_choice_preexisting' not in p.__dict__
                   for p in experiment.Player.objects_filter(
                       session=Session.objects_get(code=session_code)))
    asyncio.run(exercise())
    print('real frozen HTTP POST: manual, invalid retry, stale, early, late, queued, next round',
          flush=True)


def check_dual_session_live_flow(conditions=('I0', 'I1'), *, round_numbers=(4,),
                                 full_flow=False, mixed_conditions=False):
    from otree.main import setup
    setup()
    import project_asgi
    import re
    from unittest.mock import patch
    import dynamic_bottleneck_round as experiment
    from dynamic_bottleneck_round.agents import prefetch_coordinator
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.models import Participant, Session
    from otree.session import create_session

    prefetch_coordinator.CACHE_ROOT = Path.cwd() / 'agent_cache'
    entries = []
    session_codes = []
    for condition in conditions:
        session = create_session('dynamic_bottleneck_round_prod', num_participants=40,
            modified_session_config_fields={
                'group_treatment_spec': ('G01:H-I0;G02:HA-I1' if mixed_conditions else
                    f'G01:H-{condition};G02:HA-{condition}'),
                'capacity_information_condition': condition,
                'api_agent_mode': 'active', 'api_agent_count_per_group': 10,
                'rl_agent_enabled': 1, 'rl_agent_count_per_group': 10,
            })
        session_codes.append(session.code)
        index = next(index for index, info in _get_session_lookups(session.code).items()
                     if info.round_number == round_numbers[0]
                     and info.page_class.__name__ == 'Decision')
        for participant in session.get_participants():
            participant.vars['access_granted'] = True
            participant.visited = True
            participant._index_in_pages = index
            participant._last_page_timestamp = int(time.time())
            participant.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
            own_condition = participant.vars['dynamic_bottleneck_information_condition'] if mixed_conditions else condition
            entries.append((participant.code, session.code, index, own_condition))
        db.commit()

    def mock_provider(*, config, choice_set):
        # Only the external paid provider is replaced; H/HA, RL, records and
        # group settlement run through the production application.
        return experiment.AgentChoice(
            departure_slot=min(choice_set.valid_slots()),
            decision_source='deepseek_api', fallback_used=False,
            reason='framework_test_provider',
        )

    async def exercise_round(round_number):
        shells = await asyncio.gather(*(http_request(project_asgi.app,
            f'/p/{code}/dynamic_bottleneck_round/Decision/{index}')
            for code, _, index, _ in entries))
        assert all(status == 200 for status, _, _ in shells), [
            (status, error) for status, _, error in shells if status != 200]
        tokens = [re.search(r'data-shell-token="([0-9a-f]{24})"', content).group(1)
                  for _, content, _ in shells]
        protocol2 = os.environ.get('DYNAMIC_ROOM_RELEASE_PROTOCOL') == '2'
        if protocol2:
            controls = [re.search(r'data-control-token="([^"]+)"', content).group(1)
                        for _, content, _ in shells]
            from dynamic_bottleneck_round.test_room_sync_framework import control_request
            class ControlConnection:
                def __init__(self, token):
                    self.token = token
                async def exchange(self, data):
                    status, result = await control_request(project_asgi.app, self.token, data)
                    assert status == 200, result
                    return result
                async def close(self):
                    pass
            connections = [ControlConnection(token) for token in controls]
        else:
            connections = await asyncio.gather(*(LiveConnection(
                project_asgi.app, code, session, index).connect()
                for code, session, index, _ in entries))
        try:
            acknowledgments = await asyncio.gather(*(connection.exchange({
                'kind': 'ack', 'shell_token': token,
            }) for connection, token in zip(connections, tokens)))
            assert all(status['acknowledged'] for status in acknowledgments)
            statuses = await asyncio.gather(*(c.exchange({'kind': 'status'})
                                               for c in connections))
            releases = {}
            for entry, status in zip(entries, statuses):
                assert not status['technical_anomaly'], status
                releases.setdefault(entry[1], set()).add(status['release_ts'])
                assert 'actual_capacity_display' not in status
            assert all(len(values) == 1 and None not in values for values in releases.values())
            # Refresh and duplicate ACK must retain the same scheduled T.
            first_code, _, first_index, _ = entries[0]
            status, _, error = await http_request(project_asgi.app,
                f'/p/{first_code}/dynamic_bottleneck_round/Decision/{first_index}')
            assert status == 200, (status, error)
            duplicated = await connections[0].exchange({'kind': 'ack', 'shell_token': tokens[0]})
            assert duplicated['release_ts'] in releases[entries[0][1]]
            await asyncio.sleep(max(0, max(next(iter(t)) for t in releases.values())
                                    - time.time() + .05))
            statuses = await asyncio.gather(*(c.exchange({'kind': 'status'})
                                               for c in connections))
            for (_, _, _, condition), status in zip(entries, statuses):
                assert status['server_now_ts'] >= status['release_ts']
                assert bool(status.get('actual_capacity_display')) == (condition == 'I1')
            await connections[0].close()
            code, session, index, _ = entries[0]
            connections[0] = (ControlConnection(controls[0]) if protocol2 else
                await LiveConnection(project_asgi.app, code, session, index).connect())
            recovered = await connections[0].exchange({'kind': 'ack', 'shell_token': tokens[0]})
            assert recovered['release_ts'] in releases[session]
            if protocol2:
                receipts = await asyncio.gather(*(c.exchange({'kind': 'decision',
                    'departure_minute': 474}) for c in connections))
                assert all(r['decision_received'] for r in receipts)
            posts = await asyncio.gather(*(http_request(project_asgi.app,
                f'/p/{code}/dynamic_bottleneck_round/Decision/{index}', method='POST',
                body=urlencode({'departure_minute': 474}).encode())
                for code, _, index, _ in entries))
            assert all(status == 302 for status, _, _ in posts), [
                (status, error) for status, _, error in posts if status != 302]
            for _ in range(20):
                results = await asyncio.gather(*(http_request(project_asgi.app,
                    f'/p/{code}/dynamic_bottleneck_round/ResultsSync/{index + 2}')
                    for code, _, index, _ in entries))
                assert all(status == 200 for status, _, _ in results)
                if all('data-ready="1"' in content for _, content, _ in results):
                    break
                await asyncio.sleep(.05)
            else:
                raise AssertionError('H/HA results barrier did not become ready')
            db.new_session()
            for session_code in session_codes:
                session = Session.objects_get(code=session_code)
                state = (experiment.room_release_state(
                    experiment.Player.objects_filter(session=session, round_number=round_number)[0])
                    if protocol2 else session.vars[f'{experiment.ROOM_RELEASE_ROUND_PREFIX}{round_number}'])
                assert not state['technical_anomaly']
                groups = list(experiment.Group.objects_filter(session=session,
                                                              round_number=round_number))
                assert [len(g.get_players()) for g in groups] == [30, 10]
                assert all(g.results_ready for g in groups)
                assert {g.round_started_at_ts for g in groups} == {state['release_ts']}
                assert {g.decision_deadline_ts for g in groups} == {state['deadline_ts']}
                expected_seconds = experiment.round_timing_for_session(
                    session, round_number)['decision_seconds']
                assert state['deadline_ts'] - state['release_ts'] == expected_seconds
                assert all(p.decision_source == 'manual' for g in groups for p in g.get_players())
                assert len(experiment.active_virtual_decisions_for_group(groups[0])) == 0
                actors = experiment.active_virtual_decisions_for_group(groups[1])
                assert len(actors) == 20
                assert sum(r['actor_type'] == experiment.API_AGENT_TYPE_DEEPSEEK for r in actors) == 10
                assert len(experiment.independent_rl_records_for_group(groups[1])) == 10
        finally:
            await asyncio.gather(*(c.close() for c in connections), return_exceptions=True)

    async def advance_to(next_round=None):
        # Walk real GET/POST callbacks, not model index rewrites between rounds.
        for _ in range(12):
            db.new_session()
            pending, updated = [], []
            for code, session_code, _, condition in entries:
                participant = Participant.objects_get(code=code)
                if participant._index_in_pages > participant._max_page_index:
                    assert next_round is None
                    continue
                index = participant._index_in_pages
                info = _get_session_lookups(session_code)[index]
                name = info.page_class.__name__
                if name == 'Decision' and info.round_number == next_round:
                    updated.append((code, session_code, index, condition))
                    continue
                assert name in {'ResultsSync', 'Results', 'FormalStart',
                                'UnderstandingAndStrategy', 'DecisionExperience',
                                'SurveyComplete', 'PaymentInfo'}, name
                values = {}
                if name == 'UnderstandingAndStrategy':
                    values = {'service_rate_understanding': 'fewer_pass_more_queue',
                              'predecision_capacity_access': ('exact_current_rate'
                                  if condition == 'I1' else 'distribution_then_reveal'),
                              'primary_decision_basis': ('current_rate'
                                  if condition == 'I1' else 'distribution')}
                elif name == 'DecisionExperience':
                    import dynamic_bottleneck_survey as survey
                    values = {field: 4 for field in survey.PAGE_TWO_FIELDS}
                    if participant.vars['dynamic_bottleneck_treatment_group'] == 'HA':
                        values.update({field: 3 for field in survey.AGENT_FIELDS})
                pending.append((code, name, participant._url_i_should_be_on(), values))
            if not pending:
                if next_round is not None:
                    assert len(updated) == len(entries)
                    entries[:] = updated
                return
            gets = await asyncio.gather(*(http_request(project_asgi.app, path)
                                         for _, _, path, _ in pending))
            assert all(status == 200 for status, _, _ in gets), gets
            for (_, name, _, values), (_, content, _) in zip(pending, gets):
                if name == 'DecisionExperience':
                    assert ('name="agent_changed_strategy"' in content) == (
                        'agent_changed_strategy' in values)
            posts = await asyncio.gather(*(http_request(project_asgi.app, path,
                method='POST', body=urlencode(values).encode())
                for _, _, path, values in pending))
            assert all(status == 302 for status, _, _ in posts), posts
        raise AssertionError('HTTP flow did not reach the next decision/end')

    async def exercise():
        for position, round_number in enumerate(round_numbers):
            await exercise_round(round_number)
            if full_flow:
                print(f'80 humans: round {round_number}/33, manual and H/HA results OK', flush=True)
                next_round = (round_numbers[position + 1]
                              if position + 1 < len(round_numbers) else None)
                await advance_to(next_round)
        if full_flow:
            from decimal import Decimal, ROUND_HALF_UP
            from payment_info.cash_payment import cash_rule_for_version, RANK_RULE_VERSION
            import dynamic_bottleneck_survey as survey
            db.new_session()
            for code, _, _, _ in entries:
                participant = Participant.objects_get(code=code)
                players = list(experiment.Player.objects_filter(participant=participant))
                assert len(players) == 33 and all(p.decision_source == 'manual' for p in players)
                cost = sum((Decimal(str(p.total_cost)) for p in players if p.round_number > 3),
                           Decimal(0))
                runtime = participant.vars
                assert runtime[experiment.SETTLED_FORMAL_ROUNDS_VAR] == 30
                assert Decimal(runtime[experiment.TOTAL_COST_DECIMAL_VAR]) == cost
                rule = cash_rule_for_version(participant.session.config['cash_payment_rule'])
                if rule.version == RANK_RULE_VERSION:
                    final = runtime[experiment.RANKING_RESULTS_VAR]['33']
                    assert final['completed_rounds'] == 30
                    assert Decimal(final['total_cost']) == cost
                    mid = Decimal(final['rank']) + Decimal(final['tied_count'] - 1) / 2
                    count = final['actor_count']
                    rank_cents = (Decimal(300) * (count - mid) / (count - 1)
                                  if count > 1 else Decimal(150))
                    cost_cents = max(Decimal(0), 1200 - cost)
                    cents = 1500 + sum(int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
                                       for value in (cost_cents, rank_cents))
                else:
                    cap = Decimal(rule.max_bonus_cents) / 100
                    bonus = max(Decimal(0), min(cap, cap - cost / 30 / rule.cost_divisor))
                    cents = 1500 + int((100 * bonus).quantize(Decimal(1), rounding=ROUND_HALF_UP))
                assert runtime['dynamic_bottleneck_cash_payment_cents'] == cents
                # Currency.__str__ adds the display unit; compare its numeric
                # Decimal value, not participant-facing formatted text.
                assert Decimal(participant.payoff) == Decimal(cents - 1500)
                assert runtime['finished']
                response = survey.Player.objects_get(participant=participant)
                assert response.decision_confidence == 4
                assert response.field_maybe_none('agent_changed_strategy') == (
                    3 if runtime['dynamic_bottleneck_treatment_group'] == 'HA' else None)
            print('2640 manual choices; 80 H/HA surveys, 30-round costs and cash audited', flush=True)

    with patch.object(experiment, 'choose_agent_departure', side_effect=mock_provider):
        asyncio.run(exercise())
    print(f'{len(entries)} GET/ACK/manual POST, H/HA results, {conditions}, refresh/reconnect',
          flush=True)


def run_isolated_check(scenario):
    with tempfile.TemporaryDirectory(prefix='room-release-framework-') as directory:
        os.chdir(directory)
        for name in ('_static', '_rooms', 'access_gate', 'dynamic_bottleneck_round',
                     'dynamic_bottleneck_survey', 'payment_info',
                     'route_choice', 'single_bottleneck'):
            Path(name).symlink_to(PROJECT_ROOT / name, target_is_directory=True)
        if scenario == 'frozen_get':
            check_frozen_decision_get()
        elif scenario == 'http_posts':
            check_real_decision_posts()
        elif scenario == 'dual_live':
            check_dual_session_live_flow()
        elif scenario == 'single_live':
            check_dual_session_live_flow(('I1',))
        elif scenario == 'full_flow':
            check_dual_session_live_flow(round_numbers=tuple(range(1, 34)), full_flow=True)
        elif scenario == 'asgi_guard':
            try:
                import project_asgi
            except RuntimeError as exc:
                assert 'project_prodserver.py' in str(exc)
            else:
                raise AssertionError('Uninitialized ASGI entrypoint was accepted')
        elif scenario == 'launcher':
            from unittest.mock import patch
            import project_prodserver
            with patch('project_prodserver.subprocess.Popen'), patch('project_prodserver.Server'):
                project_prodserver.main(['127.0.0.1:19999'])
            import otree.common
            import dynamic_bottleneck_round as experiment
            import project_asgi
            assert otree.common.USE_TIMEOUT_WORKER
            assert hasattr(experiment.Player, '_setattr_fields')
        else:
            raise ValueError(f'Unknown framework check: {scenario}')


class RoomReleaseFrameworkTests(unittest.TestCase):
    def run_check(self, scenario):
        env = os.environ.copy()
        env.update(PYTHONPATH=str(PROJECT_ROOT) + os.pathsep + env.get('PYTHONPATH', ''),
                   DATABASE_URL='sqlite:///db.sqlite3',
                   OTREE_IN_MEMORY='', DYNAMIC_ROOM_RELEASE_PROTOCOL='1',
                   DYNAMIC_ROOM_RELEASE_DRAIN_SECONDS='10')
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), '--framework-check', scenario],
            env=env, capture_output=True, text=True,
            timeout=600 if scenario == 'full_flow' else 120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_frozen_player_can_render_real_decision_get(self):
        self.run_check('frozen_get')

    def test_asgi_entrypoint_rejects_missing_framework_initialization(self):
        self.run_check('asgi_guard')

    def test_production_launcher_performs_real_standard_initialization(self):
        self.run_check('launcher')

    def test_real_post_validation_and_stale_choice_protection(self):
        self.run_check('http_posts')

    def test_live_40_humans_and_ha_settlement_through_real_consumer(self):
        self.run_check('single_live')

    def test_live_80_humans_and_ha_settlement_through_real_consumer(self):
        self.run_check('dual_live')

    @unittest.skipUnless(os.environ.get('RUN_ROOM_RELEASE_FULL_FLOW') == '1',
                         'explicit full-flow acceptance; no Chrome processes')
    def test_full_33_round_http_flow_surveys_and_cash(self):
        self.run_check('full_flow')


if __name__ == '__main__':
    if '--framework-check' in sys.argv:
        run_isolated_check(sys.argv[-1])
    else:
        unittest.main()
