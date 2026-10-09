"""Protocol 2 acceptance against real frozen oTree, isolated per process."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import re
import unittest
from urllib.parse import urlencode
PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Loading this standalone helper must not import the application package before
# chdir: oTree 5 binds its SQLite engine at import time.
helper_spec = importlib.util.spec_from_file_location('room_framework_helpers',
    Path(__file__).with_name('test_room_release_framework.py'))
helper = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(helper)
check_dual_session_live_flow = helper.check_dual_session_live_flow
http_request = helper.http_request
LiveConnection = helper.LiveConnection


async def control_request(app, token, data):
    messages = []
    supplied = False
    async def receive():
        nonlocal supplied
        if not supplied:
            supplied = True
            return {'type': 'http.request', 'body': json.dumps(dict(data, token=token)).encode(),
                    'more_body': False}
        return {'type': 'http.disconnect'}
    async def send(message):
        messages.append(message)
    await app({'type': 'http', 'method': 'POST', 'path': '/dynamic-room-sync',
        'headers': [(b'host', b'testserver'), (b'origin', b'http://testserver'),
                    (b'content-type', b'application/json')],
        'scheme': 'http', 'server': ('testserver', 80)}, receive, send)
    status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
    body = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
    return status, json.loads(body)


def run_scenario(scenario):
    with tempfile.TemporaryDirectory(prefix='sync-framework-') as directory:
        os.chdir(directory)
        os.environ['DYNAMIC_ROOM_SYNC_DB'] = str(Path(directory) / 'journal.sqlite3')
        import otree
        expected_source = os.environ.get('ROOM_SYNC_EXPECTED_OTREE_SOURCE')
        if expected_source:
            assert Path(otree.__file__).resolve() == Path(expected_source).resolve()
        print(f'isolated oTree {otree.__version__}: {otree.__file__}; cwd={directory}', flush=True)
        for name in ('_static', '_rooms', 'access_gate', 'dynamic_bottleneck_round',
                     'dynamic_bottleneck_survey', 'payment_info', 'route_choice', 'single_bottleneck'):
            Path(name).symlink_to(PROJECT_ROOT / name, target_is_directory=True)
        if scenario == 'edges':
            check_edges()
        elif scenario == 'decision_failure':
            check_edges(failure=True)
        elif scenario == 'late_pending':
            check_edges(pending=True)
        elif scenario == 'receipt_race':
            check_edges(race=True)
        elif scenario in {'control_failure', 'control_fault_race', 'control_anomaly_unavailable',
                          'worker_overrun_after_native_block'}:
            check_edges(control_fault=scenario)
        elif scenario == 'room':
            check_room_consumers()
        elif scenario == 'transaction':
            check_transaction_and_pending()
        elif scenario in {'settlement_write_failure', 'settlement_control_race'}:
            check_transaction_and_pending(final_fault=scenario)
        elif scenario == 'suspended':
            check_suspended_flow()
        elif scenario == 'export':
            check_readonly_export()
        else:
            check_dual_session_live_flow(conditions=('I0',) if scenario == 'mixed' else ('I0', 'I1'),
                round_numbers=tuple(range(1, 34)) if scenario == 'full_flow' else (4,),
                full_flow=scenario == 'full_flow', mixed_conditions=scenario == 'mixed')


def check_edges(*, failure=False, pending=False, race=False, control_fault=None):
    from otree.main import setup
    setup()
    import project_asgi
    import dynamic_bottleneck_round as e
    from dynamic_bottleneck_round import sync_runtime as runtime
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.models import Participant, Session
    from otree.session import create_session
    session = create_session('dynamic_bottleneck_round_demo', num_participants=3)
    session_code = session.code
    codes = [p.code for p in session.get_participants()]
    index = next(i for i, info in _get_session_lookups(session.code).items()
                 if info.round_number == 1 and info.page_class.__name__ == 'Decision')
    for p in session.get_participants():
        p._index_in_pages = index
        p.visited = True
        p.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
        p._last_page_timestamp = int(time.time())
    db.commit()
    def player(code):
        db.new_session()
        return e.Player.objects_get(participant=Participant.objects_get(code=code), round_number=1)
    def arm(deadline):
        j = runtime.get_journal()
        with j._connection(write=True) as conn:
            record, original = j._load(conn, session_code, 1)
            record['state'].update(release_ts=deadline - 60, deadline_ts=deadline, phase='RELEASED')
            j._save(conn, session_code, 1, record, original)
    async def post(code, minute):
        return await http_request(project_asgi.app,
            f'/p/{code}/dynamic_bottleneck_round/Decision/{index}', method='POST',
            body=urlencode({'departure_minute': minute}).encode())
    async def exercise():
        tokens = []
        for code in codes:
            status, content, error = await http_request(project_asgi.app,
                f'/p/{code}/dynamic_bottleneck_round/Decision/{index}')
            assert status == 200, (status, error)
            tokens.append(re.search(r'data-control-token="([^"]+)"', content).group(1))
        for token in tokens:
            status, result = await control_request(project_asgi.app, token, {'kind': 'ack'})
            assert status == 200 and result['acknowledged'], result
        arm(time.time() + 55)
        if control_fault == 'worker_overrun_after_native_block':
            import concurrent.futures
            import threading
            from unittest.mock import patch
            j = runtime.get_journal()
            deadline = time.time() + .5
            arm(deadline)
            with j._connection(write=True) as conn:
                record, original = j._load(conn, session_code, 1)
                record['state']['drain_limit_seconds'] = .1
                j._save(conn, session_code, 1, record, original)
            native_entered, release_native = threading.Event(), threading.Event()
            original_snapshot, original_exchange = e.sync_integration.snapshot, j.exchange
            def blocked_snapshot(p, **kwargs):
                native_entered.set()
                assert release_native.wait(4), 'native pause was not released'
                return original_snapshot(p, **kwargs)
            def slow_exchange(*args, **kwargs):
                time.sleep(max(0, deadline + .15 - time.time()))
                return original_exchange(*args, **kwargs)
            def control_worker():
                assert native_entered.wait(2), 'native readiness did not start'
                try:
                    return asyncio.run(control_request(project_asgi.app, tokens[1],
                        {'kind': 'decision', 'departure_minute': 474}))
                finally:
                    release_native.set()
            ref = player(codes[0])
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool, \
                    patch.object(e.sync_integration, 'snapshot', blocked_snapshot), \
                    patch.object(j, 'exchange', side_effect=slow_exchange):
                control_future = pool.submit(control_worker)
                assert not e.set_results(ref.group), 'worker anomaly must stop resumed native settlement'
                control_status, control_result = control_future.result(4)
            assert control_status == 503, control_result
            receipt = j.receipt(session_code, 1, codes[1])
            assert receipt['kind'] == 'manual' and receipt['departure_minute'] == 474, receipt
            assert receipt['ingress_ts'] < deadline, receipt
            state = j.state(session_code, 1)
            assert state['technical_anomaly'] == 'decision_control_queue_overrun', state
            assert not state['settled'] and not ref.group.results_ready
            metadata = e.room_release_export_metadata(ref)
            assert metadata['room_release_exclude_behavior']
            assert metadata['room_release_technical_anomaly'] == 'decision_control_queue_overrun'
            return
        if control_fault:
            from unittest.mock import patch
            j = runtime.get_journal()
            def fail_request():
                async def request():
                    with patch.object(j, 'exchange', side_effect=RuntimeError('storage failed')):
                        status, result = await control_request(project_asgi.app, tokens[1],
                            {'kind': 'decision', 'departure_minute': 474})
                    assert status == 503, result
                # The native callback and control request use distinct threads.
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(lambda: asyncio.run(request())).result(5)
            if control_fault == 'control_fault_race':
                original_receipt = e.sync_integration.receipt
                injected = False
                def read_then_fail(p):
                    nonlocal injected
                    result = original_receipt(p)
                    if not injected:
                        injected = True
                        fail_request()
                    return result
                arm(time.time() - 11)
                ref = player(codes[0])
                with patch.object(e.sync_integration, 'receipt', read_then_fail):
                    assert not e.set_results(ref.group), 'fault after readiness snapshot must block automatic fill'
                assert injected
            else:
                if control_fault == 'control_anomaly_unavailable':
                    with patch.object(j, 'mark_anomaly', side_effect=RuntimeError('anomaly write unavailable')):
                        fail_request()
                        arm(time.time() - 11)
                        ref = player(codes[0])
                        assert not e.set_results(ref.group), 'RAM safety latch must block while anomaly cannot persist'
                        assert not any(e.player_has_departure_choice(p) for p in ref.group.get_players())
                else:
                    fail_request()
                    arm(time.time() - 11)
                    ref = player(codes[0])
                assert not e.set_results(ref.group), 'storage recovery must not heal a lost decision'
            assert not ref.group.results_ready
            assert not any(e.player_has_departure_choice(p) for p in ref.group.get_players())
            s = j.state(session_code, 1)
            assert s['technical_anomaly'] == 'control_persistence_failure', s
            assert not s['settled']
            metadata = e.room_release_export_metadata(ref)
            assert metadata['room_release_exclude_behavior']
            assert metadata['room_release_technical_anomaly'] == 'control_persistence_failure'
            return
        if race:
            from unittest.mock import patch
            j = runtime.get_journal()
            deadline = j.state(session_code, 1)['deadline_ts']
            original_receipt = e.sync_integration.receipt
            payload = runtime.verify_control_token(tokens[1])
            injected = False
            def receipt_after_first_import(p):
                nonlocal injected
                r = original_receipt(p)
                if p.participant.code == codes[1] and not injected:
                    injected = True
                    # Real canonical Journal exchange commits after the first
                    # import read but before the final pending/drain sweep.
                    j.exchange(session_code, 1, codes[1], payload['shell_token'],
                        kind='decision', departure_minute=474, now_ts=deadline - .5,
                        processing_ts=deadline + 10.5)
                return r
            ref = player(codes[0])
            with patch.object(e.sync_integration, 'receipt', receipt_after_first_import), \
                    patch.object(e.sync_integration.time, 'time', return_value=deadline + 11):
                assert e.sync_integration.ready_for_results(ref.group)
            chosen = next(p for p in ref.group.get_players() if p.participant.code == codes[1])
            assert chosen.decision_source == e.DECISION_SOURCE_MANUAL, chosen.decision_source
            assert chosen.departure_minute == 474
            assert chosen.participant.vars['last_manual_departure_minute'] == 474
            assert injected
            return
        if failure:
            from unittest.mock import patch
            def fail_native(player, values):
                raise RuntimeError('native Decision failure after release')
            with patch.object(e.Decision, 'error_message', fail_native):
                status, _, error = await post(codes[1], 474)
            assert status == 500, (status, error)
            ref = player(codes[0])
            assert not e.set_results(ref.group), 'native Decision failure must not become valid automatic data'
            s = runtime.get_journal().state(session_code, 1)
            assert s['technical_anomaly'] == 'decision_page_http_failure', s
            arm(time.time() - 11)
            assert not e.set_results(ref.group)
            assert not any(e.player_has_departure_choice(p) for p in ref.group.get_players())
            return
        if pending:
            arm(time.time() - 11)
            payload = runtime.verify_control_token(tokens[1])
            ticket = runtime.register_pending(payload, 'decision', time.time())
            try:
                ref = player(codes[0])
                assert not e.set_results(ref.group), 'late control persistence blocks automatic classification'
                s = runtime.get_journal().state(session_code, 1)
                assert s['technical_anomaly'] == 'decision_control_queue_overrun', s
                assert not any(e.player_has_departure_choice(p) for p in ref.group.get_players())
            finally:
                runtime.finish_pending(ticket)
            return
        status, content, _ = await post(codes[0], 999)
        assert status == 200 and '有效出发时间' in content
        assert not e.player_has_departure_choice(player(codes[0]))
        status, content, _ = await post(codes[0], 474)
        assert status == 200 and '服务器选择确认' in content
        status, r = await control_request(project_asgi.app, tokens[0],
            {'kind': 'decision', 'departure_minute': 474})
        assert status == 200 and r['receipt']['departure_minute'] == 474, r
        status, r = await control_request(project_asgi.app, tokens[0],
            {'kind': 'decision', 'departure_minute': 475})
        assert status == 200 and r['receipt']['departure_minute'] == 474
        status, _, error = await post(codes[0], 999)
        assert status == 302, (status, error)
        assert player(codes[0]).departure_minute == 474
        status, _, _ = await post(codes[0], 475)
        assert status == 302 and player(codes[0]).departure_minute == 474
        status, result = await control_request(project_asgi.app, tokens[0], {'kind': 'operative'})
        assert status == 200, result
        metadata = e.room_release_export_metadata(player(codes[0]))
        assert metadata['room_release_protocol'] == 2
        assert metadata['room_release_receipt_kind'] == 'manual'
        assert metadata['room_release_post_ingress_ts'] > metadata['room_release_receipt_ingress_ts']
        assert metadata['room_release_post_response_completed_ts']
        assert metadata['room_release_operative_ts']
        arm(time.time() - 1)
        status, r = await control_request(project_asgi.app, tokens[1],
            {'kind': 'decision', 'departure_minute': 475})
        assert status == 200 and r['decision_kind'] == 'late'
        status, _, error = await post(codes[1], 475)
        assert status == 302, (status, error)
        late = player(codes[1])
        assert not e.player_has_departure_choice(late), 'late choice waits for drain'
        assert not e.set_results(late.group), 'direct settlement cannot bypass drain'
        db.commit()
        arm(time.time() - 11)
        late = player(codes[1])
        assert e.set_results(late.group)
        assert late.decision_source == e.DECISION_SOURCE_TIMEOUT_AUTO, late.decision_source
        assert player(codes[2]).decision_source == e.DECISION_SOURCE_DISCONNECT_AUTO
    asyncio.run(exercise())


def check_room_consumers():
    from otree.main import setup
    setup()
    import project_asgi
    from otree.room import ROOM_DICT
    from otree.common import AUTH_COOKIE_NAME, AUTH_COOKIE_VALUE
    from otree.middleware import _SECRET
    import itsdangerous
    import base64
    admin_cookie = itsdangerous.TimestampSigner(str(_SECRET)).sign(base64.b64encode(
        json.dumps({AUTH_COOKIE_NAME: AUTH_COOKIE_VALUE}).encode())).decode()
    async def exercise():
        admin = LiveConnection(project_asgi.app, '', '', 0)
        admin.scope.update(path='/room_without_session/demo_room', raw_path=b'/room_without_session/demo_room', query_string=b'')
        admin.scope['headers'].append((b'cookie', ('session=' + admin_cookie).encode()))
        await admin.connect()
        init = await asyncio.wait_for(admin.output.get(), 10)
        assert json.loads(init['text'])['status'] == 'init', init
        participant = LiveConnection(project_asgi.app, '', '', 0)
        participant.scope.update(path='/wait_for_session_in_room', raw_path=b'/wait_for_session_in_room',
            query_string=urlencode({'room_name': 'demo_room', 'tab_unique_id': 'test-tab'}).encode())
        await participant.connect()
        added = await asyncio.wait_for(admin.output.get(), 10)
        assert json.loads(added['text'])['status'] == 'add_participant', added
        assert ROOM_DICT['demo_room'].present_count == 1
        await participant.close()
        removed = await asyncio.wait_for(admin.output.get(), 10)
        assert json.loads(removed['text'])['status'] == 'remove_participant'
        assert ROOM_DICT['demo_room'].present_count == 0
        await admin.close()
        from otree.session import create_session
        from otree.database import db
        from otree.channels import utils as channel_utils
        session = create_session('dynamic_bottleneck_round_demo', num_participants=1)
        p = session.get_participants()[0]
        p.visited = True
        db.commit()
        monitor = LiveConnection(project_asgi.app, '', '', 0)
        path = f'/session_monitor/{session.code}'
        monitor.scope.update(path=path, raw_path=path.encode(), query_string=b'')
        monitor.scope['headers'].append((b'cookie', ('session=' + admin_cookie).encode()))
        await monitor.connect()
        data = json.loads((await asyncio.wait_for(monitor.output.get(), 10))['text'])
        assert 'rows' in data and len(data['rows']) == 1, data
        # Exercise the production synchronous broadcaster from a worker loop.
        await asyncio.to_thread(channel_utils.sync_group_send,
            group=channel_utils.session_monitor_group_name(session.code),
            data={'bridge_probe': True})
        data = json.loads((await asyncio.wait_for(monitor.output.get(), 10))['text'])
        assert data == {'bridge_probe': True}, data
        await monitor.close()
    asyncio.run(exercise())


def check_transaction_and_pending(*, final_fault=None):
    from otree.main import setup
    setup()
    import project_asgi
    import dynamic_bottleneck_round as e
    from dynamic_bottleneck_round import sync_runtime as runtime
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.models import Participant, Session
    from otree.session import create_session
    from unittest.mock import patch
    session = create_session('dynamic_bottleneck_round_demo', num_participants=2,
        modified_session_config_fields={'cohort_size': 1})
    code = session.code
    codes = [p.code for p in session.get_participants()]
    index = next(i for i, info in _get_session_lookups(code).items()
        if info.round_number == 1 and info.page_class.__name__ == 'Decision')
    for p in session.get_participants():
        p._index_in_pages = index
        p.visited = True
        p.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
        p._last_page_timestamp = int(time.time())
    db.commit()
    def groups():
        db.new_session()
        return list(e.Group.objects_filter(session=Session.objects_get(code=code), round_number=1))
    def window(deadline):
        j = runtime.get_journal()
        with j._connection(write=True) as conn:
            record, original = j._load(conn, code, 1)
            record['state'].update(release_ts=deadline - 60, deadline_ts=deadline, phase='RELEASED')
            j._save(conn, code, 1, record, original)
    async def exercise():
        tokens = []
        for participant in codes:
            status, content, error = await http_request(project_asgi.app,
                f'/p/{participant}/dynamic_bottleneck_round/Decision/{index}')
            assert status == 200, (status, error)
            tokens.append(re.search(r'data-control-token="([^"]+)"', content).group(1))
        for token in tokens:
            assert (await control_request(project_asgi.app, token, {'kind': 'ack'}))[0] == 200
        window(time.time() + 50)
        for token in tokens:
            assert (await control_request(project_asgi.app, token,
                {'kind': 'decision', 'departure_minute': 474}))[0] == 200
        # Import valid receipts via the real native callbacks, preserving two
        # different groups for the Session-wide barrier.
        for participant in codes:
            status, _, error = await http_request(project_asgi.app,
                f'/p/{participant}/dynamic_bottleneck_round/Decision/{index}', method='POST',
                body=b'departure_minute=999')
            assert status == 302, (status, error)
        assert len(groups()) == 2
        assert not any(g.results_ready for g in groups()), 'results were not prepared by native Decision POST'
        path = f'/p/{codes[0]}/dynamic_bottleneck_round/ResultsSync/{index + 2}'
        from sqlalchemy import event
        from sqlalchemy.orm import Session as ORMSession
        def fail_commit(orm_session):
            if any(isinstance(obj, e.Group) and obj.results_ready for obj in orm_session.identity_map.values()):
                raise RuntimeError('intentional business commit failure')
        event.listen(ORMSession, 'before_commit', fail_commit)
        try:
            status, _, error = await http_request(project_asgi.app, path)
        finally:
            event.remove(ORMSession, 'before_commit', fail_commit)
        assert status == 500, (status, error)
        assert not any(g.results_ready for g in groups()), f'native failure rolled back business results: {error}'
        assert not runtime.get_journal().state(code, 1)['settled'], 'rollback never settles journal'
        # A valid incoming control write blocks even all-manual early settlement.
        payload = runtime.verify_control_token(tokens[1])
        ticket = runtime.register_pending(payload, 'decision', time.time())
        try:
            assert not e.set_results(groups()[0])
            assert not any(g.results_ready for g in groups())
        finally:
            runtime.finish_pending(ticket)
        if final_fault:
            j = runtime.get_journal()
            if final_fault == 'settlement_write_failure':
                with patch.object(j, 'mark_settled', side_effect=RuntimeError('settlement storage failed')):
                    await http_request(project_asgi.app, path)
                expected = 'control_settlement_failure'
            else:
                from dynamic_bottleneck_round.decision_ingress import _success_callbacks
                injected = False
                def fail_during_commit(orm_session):
                    nonlocal injected
                    if ('sync-settled', code, 1) in (_success_callbacks.get() or {}):
                        injected = True
                        runtime.latch_control_fault(payload, 'control_persistence_failure')
                event.listen(ORMSession, 'before_commit', fail_during_commit)
                try:
                    await http_request(project_asgi.app, path)
                finally:
                    event.remove(ORMSession, 'before_commit', fail_during_commit)
                assert injected, 'fault injected after final settlement callback was scheduled'
                expected = 'control_persistence_failure'
            assert all(g.results_ready for g in groups()), 'native commit is already complete'
            s = j.state(code, 1)
            assert s['technical_anomaly'] == expected, s
            assert not s['settled'], 'late fault cannot certify healthy settlement'
            assert runtime.control_fault(code, 1) == expected
            for token in tokens:
                identity = runtime.verify_control_token(token)
                assert j.receipt(code, 1, identity['participant_code'])['departure_minute'] == 474
            return
        status, content, error = await http_request(project_asgi.app, path)
        assert status == 200 and 'data-ready="1"' in content, (status, error)
        assert all(g.results_ready for g in groups())
        assert runtime.get_journal().state(code, 1)['settled']
        # Session identity must reject a freshly initialized/replaced journal.
        identity_player = groups()[0].get_players()[0]
        current = identity_player.session
        marker = current.vars[e.sync_integration.IDENTITY_VAR]
        current.vars[e.sync_integration.IDENTITY_VAR] = '0' * 32
        try:
            try:
                e.room_release_state(identity_player)
            except RuntimeError:
                pass
            else:
                raise AssertionError('journal identity mismatch accepted')
        finally:
            current.vars[e.sync_integration.IDENTITY_VAR] = marker
        db.rollback()
    asyncio.run(exercise())


def check_suspended_flow():
    from otree.main import setup
    setup()
    import project_asgi
    import dynamic_bottleneck_round as e
    from dynamic_bottleneck_round import sync_runtime as runtime
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.models import Participant, Session
    from otree.session import create_session
    async def exercise(count, suspended):
        session = create_session('dynamic_bottleneck_round_demo', num_participants=count)
        code = session.code
        participants = session.get_participants()
        codes = [p.code for p in participants]
        index = next(i for i, info in _get_session_lookups(code).items()
            if info.round_number == 1 and info.page_class.__name__ == 'Decision')
        for pos, p in enumerate(participants):
            p._index_in_pages = index
            p.visited = True
            p.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
            p._last_page_timestamp = int(time.time())
            if pos < suspended:
                p.vars.update(dropout_suspended=True, dropout_active=True, dropout_reason='timeout')
        db.commit()
        for participant in codes:
            status, content, error = await http_request(project_asgi.app,
                f'/p/{participant}/dynamic_bottleneck_round/Decision/{index}')
            if participant in codes[:suspended]:
                assert status == 302, (status, error)
                db.new_session()
                p = Participant.objects_get(code=participant)
                player = e.Player.objects_get(participant=p, round_number=1)
                assert player.decision_source == e.DECISION_SOURCE_SUSPENDED_AUTO
                assert 'RecoveryGate' in p._url_i_should_be_on()
                status, _, error = await http_request(project_asgi.app, p._url_i_should_be_on(),
                    method='POST', body=b'')
                assert status == 302, (status, error)
            else:
                assert status == 200, (status, error)
                token = re.search(r'data-control-token="([^"]+)"', content).group(1)
                assert (await control_request(project_asgi.app, token, {'kind': 'ack'}))[0] == 200
        s = runtime.get_journal().state(code, 1)
        assert s['expected_codes'] == codes[suspended:]
        assert s['decision_page_index'] == index
        # An empty eligible cohort needs no shell ACK; the original 60s
        # clock can still be armed and settled from existing proxy choices.
        if count == suspended:
            path = f'/p/{codes[0]}/dynamic_bottleneck_round/ResultsSync/{index + 2}'
            status, content, error = await http_request(project_asgi.app, path)
            assert status == 200, (status, error)
            s = runtime.get_journal().state(code, 1)
            assert s['release_ts'] and s['deadline_ts'] - s['release_ts'] == 60
            with runtime.get_journal()._connection(write=True) as conn:
                record, original = runtime.get_journal()._load(conn, code, 1)
                record['state'].update(release_ts=time.time() - 1, deadline_ts=time.time() + 59)
                runtime.get_journal()._save(conn, code, 1, record, original)
            status, content, error = await http_request(project_asgi.app, path)
            assert status == 200 and 'data-ready="1"' in content, (status, error)
            assert runtime.get_journal().state(code, 1)['settled']
    async def run():
        await exercise(2, 1)
        await exercise(2, 2)
    asyncio.run(run())


def check_readonly_export():
    from otree.main import setup
    setup()
    import project_asgi
    import dynamic_bottleneck_round as e
    from dynamic_bottleneck_round import sync_runtime as runtime
    from otree.database import db
    from otree.lookup import _get_session_lookups
    from otree.models import Participant, Session
    from otree.session import create_session
    session = create_session('dynamic_bottleneck_round_demo', num_participants=1)
    code, participant_code = session.code, session.get_participants()[0].code
    def arm(number):
        db.new_session()
        participant = Participant.objects_get(code=participant_code)
        index = next(i for i, info in _get_session_lookups(code).items()
            if info.round_number == number and info.page_class.__name__ == 'Decision')
        participant._index_in_pages = index
        participant.visited = True
        participant.time_started_utc = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
        participant._last_page_timestamp = int(time.time())
        db.commit()
        return f'/p/{participant_code}/dynamic_bottleneck_round/Decision/{index}'
    async def exercise():
        status, _, error = await http_request(project_asgi.app, arm(4))
        assert status == 200, (status, error)
        j = runtime.get_journal()
        before = j.state(code, 4)
        db.new_session()
        current = Session.objects_get(code=code)
        players = list(e.Player.objects_filter(session=current))
        future = [p for p in players if p.round_number > 4]
        rows = list(e.custom_export(players))
        assert len(rows) == 31, 'original formal export includes30 rounds and header'
        for p in future:
            row = dict(zip(e.EXPORT_HEADERS, e.export_row_for_player(p)))
            assert row['room_release_protocol'] == 2
            assert row['room_release_prepare_started_ts'] == '', 'export must not start a future prepare clock'
        assert current._vars[e.sync_integration.ROUNDS_VAR] == [4]
        with j._connection() as connection:
            assert connection.execute('SELECT count(*) FROM sync_rounds').fetchone()[0] == 1
        assert j.state(code, 4) == before
        fresh_get = time.time()
        status, _, error = await http_request(project_asgi.app, arm(5))
        assert status == 200, (status, error)
        next_state = j.state(code, 5)
        assert next_state['prepare_started_ts'] >= fresh_get
        assert not next_state['ack_ts_by_code'] and next_state['release_ts'] is None
        assert j.state(code, 4) == before
    asyncio.run(exercise())


class RoomSyncFrameworkTests(unittest.TestCase):
    def run_check(self, scenario):
        import otree
        env = dict(os.environ, PYTHONPATH=str(PROJECT_ROOT) + os.pathsep + os.environ.get('PYTHONPATH', ''), DATABASE_URL='sqlite:///db.sqlite3',
            OTREE_IN_MEMORY='', DYNAMIC_ROOM_RELEASE_PROTOCOL='2',
            DYNAMIC_ROOM_RELEASE_DRAIN_SECONDS='10', ROOM_SYNC_EXPECTED_OTREE_SOURCE=otree.__file__)
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--scenario', scenario],
            env=env, capture_output=True, text=True, timeout=700 if scenario == 'full_flow' else 150)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_v2_get_ack_receipt_native_post_and_h_ha_results(self):
        self.run_check('smoke')

    def test_canonical_native_validation_late_and_direct_settlement_drain(self):
        self.run_check('edges')

    def test_mixed_group_information_privacy(self):
        self.run_check('mixed')

    def test_actual_room_consumers_through_business_bridge(self):
        self.run_check('room')

    def test_real_business_rollback_commit_pending_and_identity_guard(self):
        self.run_check('transaction')

    def test_suspended_proxy_recovery_and_zero_eligible_flow(self):
        self.run_check('suspended')

    def test_native_decision_failure_excludes_automatic_data(self):
        self.run_check('decision_failure')

    def test_late_pending_control_write_blocks_automatic_classification(self):
        self.run_check('late_pending')

    def test_worker_overrun_after_native_block_keeps_manual_receipt_and_excludes_round(self):
        self.run_check('worker_overrun_after_native_block')

    def test_future_round_exports_do_not_start_prepare_clocks(self):
        self.run_check('export')

    def test_receipt_committed_after_initial_import_wins_final_drain_sweep(self):
        self.run_check('receipt_race')

    def test_control_persistence_failure_blocks_native_automatic_settlement(self):
        self.run_check('control_failure')

    def test_control_fault_during_receipt_import_blocks_automatic_fill(self):
        self.run_check('control_fault_race')

    def test_control_fault_blocks_until_anomaly_write_recovers(self):
        self.run_check('control_anomaly_unavailable')

    def test_control_fault_arriving_during_native_commit_prevents_healthy_settlement(self):
        self.run_check('settlement_control_race')

    def test_failed_settlement_marker_keeps_durable_fault_and_manual_receipts(self):
        self.run_check('settlement_write_failure')

    @unittest.skipUnless(os.environ.get('RUN_ROOM_SYNC_FULL_FLOW') == '1', 'explicit acceptance')
    def test_full_flow(self):
        self.run_check('full_flow')

    def test_flow_remains_33_rounds(self):
        import dynamic_bottleneck_round as experiment
        self.assertEqual((experiment.C.WARMUP_ROUNDS, experiment.C.FORMAL_ROUNDS,
                          experiment.C.NUM_ROUNDS), (3, 30, 33))

    def test_v1_receipt_columns_remain_blank(self):
        import dynamic_bottleneck_round as experiment
        from types import SimpleNamespace
        player = SimpleNamespace(round_number=1,
            session=SimpleNamespace(vars={experiment.ROOM_RELEASE_PROTOCOL_VAR: 1}),
            participant=SimpleNamespace(code='legacy', vars={}))
        values = experiment.room_release_export_metadata(player)
        self.assertEqual(values.get('room_release_receipt_ingress_ts'), '')
        self.assertEqual(values.get('room_release_receipt_kind'), '')


if __name__ == '__main__':
    if '--scenario' in sys.argv:
        run_scenario(sys.argv[-1])
    else:
        unittest.main()
