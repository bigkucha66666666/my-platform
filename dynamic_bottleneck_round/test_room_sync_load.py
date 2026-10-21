"""Small real TCP transport acceptance; all application imports occur in a temp child.

This is a journal/bridge load test, not a distributed browser or ORM benchmark.
The business fixture deliberately blocks its single event loop synchronously.
Only the transport's trusted test clock is replaced at the cutoff boundary.
"""
import asyncio
import json
import math
import os
from pathlib import Path
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def summary(samples):
    ordered = sorted(samples)
    return dict(samples=len(ordered), median_ms=round(statistics.median(ordered) * 1000, 3),
                p95_ms=round(ordered[math.ceil(len(ordered) * .95) - 1] * 1000, 3),
                max_ms=round(max(ordered) * 1000, 3))


async def acceptance(*, session_count=2):
    # run_child has already changed cwd before these imports (oTree 5 isolation).
    import otree
    import uvicorn
    from dynamic_bottleneck_round import sync_runtime as runtime
    from dynamic_bottleneck_round import sync_transport as transport
    from dynamic_bottleneck_round.sync_journal import SyncJournal
    from dynamic_bottleneck_round.room_release import DECISION_SECONDS

    assert DECISION_SECONDS == 60
    assert session_count in (1, 2)
    sessions = [('loadA', 'I0'), ('loadB', 'I1')][:session_count]
    actor_count = 40 * session_count

    runtime.reset_runtime()
    journal = runtime.get_journal()
    started, release = threading.Event(), threading.Event()
    clock = SimpleNamespace(value=None)
    completed = []
    tokens, shells, identities = {}, {}, {}
    samples = {kind: [] for kind in ('ack', 'status', 'decision', 'native', 'replay', 'late')}
    now = time.time()
    for session, condition in sessions:
        codes = [f'{session}_{index:02d}' for index in range(40)]
        journal.ensure_round(session, 1, expected_codes=codes, now_ts=now,
            decision_page_index=9, drain_limit_seconds=10, information_condition=condition,
            allowed_minutes=[474, 475], actual_capacity_display='test_capacity',
            capacity_level_label='test_level', boot_id=runtime.PROCESS_BOOT_ID)
        for index, code in enumerate(codes):
            shell = f'{len(shells) + 1:024x}'
            journal.register_shell(session, 1, code, shell, now_ts=now, page_index=9)
            shells[code], identities[code] = shell, (session, 1)
            tokens[code] = runtime.sign_control_token(session, 1, code, shell, 9)

    async def business(scope, receive, send):
        if scope['path'] == '/slow':
            started.set()
            if not release.wait(20):
                raise AssertionError('test did not release synchronous business block')
        else:
            code = scope['path'].rsplit('/', 1)[-1]
            await receive()
            receipt = journal.receipt(*identities[code], code)
            assert receipt['kind'] == 'manual' and receipt['departure_minute'] == 474
            assert clock.value > journal.state(*identities[code])['deadline_ts']
            completed.append((code, receipt, clock.value, threading.get_ident()))
        await send({'type': 'http.response.start', 'status': 204,
                    'headers': [(b'content-length', b'0')]})
        await send({'type': 'http.response.body', 'body': b''})

    bridge = transport.ThreadedASGI(business)
    app = transport.SyncControlASGI(bridge, workers=2)
    sock = socket.socket()
    server = server_task = slow_task = None
    native_tasks = []
    writers = set()
    try:
        sock.bind(('127.0.0.1', 0))
        sock.listen(128)
        sock.setblocking(False)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
            loop='asyncio', http='h11', ws='none', lifespan='off', log_level='critical',
            access_log=False, workers=1))
        # Owned test server shares this loop; it never installs process signal handlers.
        server.install_signal_handlers = lambda: None
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        for unused in range(200):
            if server.started:
                break
            if server_task.done():
                await server_task
                raise AssertionError('loopback server failed to start')
            await asyncio.sleep(.01)
        assert server.started

        async def request(path, payload=None, *, category=None, discard_reply=False):
            begin = time.monotonic()
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writers.add(writer)
            try:
                body = json.dumps(payload or {}).encode()
                host = f'127.0.0.1:{port}'
                header = (f'POST {path} HTTP/1.1\r\nHost: {host}\r\n'
                    f'Origin: http://{host}\r\nContent-Type: application/json\r\n'
                    f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode()
                writer.write(header + body)
                await writer.drain()
                if discard_reply:
                    return None
                headers = (await reader.readuntil(b'\r\n\r\n')).decode('ascii')
                status = int(headers.split(' ', 2)[1])
                fields = dict(line.split(': ', 1) for line in headers.split('\r\n')[1:] if ': ' in line)
                if fields.get('Transfer-Encoding', '').lower() == 'chunked':
                    chunks = []
                    while True:
                        length = int((await reader.readline()).strip(), 16)
                        if not length:
                            await reader.readline()
                            break
                        chunks.append(await reader.readexactly(length))
                        assert await reader.readexactly(2) == b'\r\n'
                    response = b''.join(chunks)
                elif 'Content-Length' in fields:
                    response = await reader.readexactly(int(fields['Content-Length']))
                else:
                    response = await reader.read()
                if category:
                    samples[category].append(time.monotonic() - begin)
                return status, json.loads(response) if response else None
            finally:
                writer.close()
                await writer.wait_closed()
                writers.discard(writer)

        async def control(code, kind, category, **extra):
            status, body = await request('/dynamic-room-sync',
                dict(token=tokens[code], kind=kind, **extra), category=category)
            assert status == 200, (code, kind, status, body)
            return body

        slow_task = asyncio.create_task(request('/slow'))
        assert await asyncio.to_thread(started.wait, 2)
        # Separately release each Session to verify they do not share a barrier/T.
        a_codes = [code for code in tokens if code.startswith('loadA')]
        b_codes = [code for code in tokens if code.startswith('loadB')]
        await asyncio.wait_for(asyncio.gather(*(control(c, 'ack', 'ack') for c in a_codes)), 5)
        assert journal.state('loadA', 1)['release_ts'] is not None
        if session_count == 2:
            assert journal.state('loadB', 1)['release_ts'] is None
            await asyncio.wait_for(asyncio.gather(*(control(c, 'ack', 'ack') for c in b_codes)), 5)
            original_releases = [journal.state(s, 1)['release_ts'] for s, unused in sessions]
            assert original_releases[0] != original_releases[1]
        snapshots = await asyncio.wait_for(asyncio.gather(
            *(control(c, 'status', 'status') for c in tokens)), 5)
        assert all('actual_capacity_display' not in body for body in snapshots)
        assert not slow_task.done() and not completed

        # Boundary fixture keeps a full 60-second T/T+60 window; no production constant changes.
        # Both Sessions have independent journal records, deliberately armed to the same clock.
        for session, unused in sessions:
            with journal._connection(write=True) as connection:
                record, original = journal._load(connection, session, 1)
                record['state'].update(release_ts=1941.0, deadline_ts=2001.0, phase='ARMED')
                journal._save(connection, session, 1, record, original)
        clock.value = 2000.0  # complete-body arrivals at exactly T+59
        native_tasks = [asyncio.create_task(request('/native/' + c, category='native')) for c in tokens]
        with patch.object(transport, 'time', SimpleNamespace(time=lambda: clock.value)):
            decisions = await asyncio.wait_for(asyncio.gather(
                *(control(c, 'decision', 'decision', departure_minute=474) for c in tokens)), 5)
            assert all(body['decision_kind'] == 'manual' for body in decisions)
            assert all(bool(body.get('actual_capacity_display')) == c.startswith('loadB')
                       for c, body in zip(tokens, decisions))
            assert not slow_task.done() and not completed
            reopened = SyncJournal(journal.path)
            canonical = {c: reopened.receipt(*identities[c], c) for c in tokens}
            assert len(canonical) == actor_count and all(r == dict(kind='manual',
                departure_minute=474, ingress_ts=2000.0) for r in canonical.values())
            clock.value = 2002.0  # T+61: business can now complete only after cutoff
            release.set()
            assert (await asyncio.wait_for(slow_task, 5))[0] == 204
            native_results = await asyncio.wait_for(asyncio.gather(*native_tasks), 5)
            assert all(status == 204 for status, body in native_results)
            assert len(completed) == actor_count and len({row[3] for row in completed}) == 1
            assert completed[0][3] != threading.get_ident()
            # Every retry opens a fresh TCP socket, sends a different second value,
            # and receives the original receipt. Lost response is replayable too.
            await request('/dynamic-room-sync', dict(token=tokens[a_codes[0]],
                kind='decision', departure_minute=475), discard_reply=True)
            retries = await asyncio.wait_for(asyncio.gather(
                *(control(c, 'decision', 'replay', departure_minute=475) for c in tokens)), 5)
            assert all(body['receipt'] == canonical[c] for c, body in zip(tokens, retries))
            assert all(reopened.receipt(*identities[c], c) == canonical[c] for c in tokens)

            # A distinct round has no prior manual receipt: a real late body must be late.
            late_code, late_shell = 'late_actor', 'f' * 24
            journal.ensure_round('loadA', 2, expected_codes=[late_code], now_ts=1900.0,
                decision_page_index=19, drain_limit_seconds=10, information_condition='I1',
                allowed_minutes=[474, 475], actual_capacity_display='test_capacity',
                capacity_level_label='test_level', boot_id=runtime.PROCESS_BOOT_ID)
            journal.register_shell('loadA', 2, late_code, late_shell, now_ts=1900.0, page_index=19)
            journal.exchange('loadA', 2, late_code, late_shell, kind='ack', now_ts=1901.0)
            late_token = runtime.sign_control_token('loadA', 2, late_code, late_shell, 19)
            late_state = journal.state('loadA', 2)
            clock.value = late_state['deadline_ts'] + .001
            status, late = await request('/dynamic-room-sync', dict(token=late_token,
                kind='decision', departure_minute=474), category='late')
            assert status == 200 and late['decision_kind'] == 'late'
            assert journal.receipt('loadA', 2, late_code)['departure_minute'] is None
        assert all(not journal.state(session, 1)['technical_anomaly'] for session, unused in sessions)
        for category, expected in [('ack', actor_count), ('status', actor_count),
                                    ('decision', actor_count), ('native', actor_count),
                                    ('replay', actor_count), ('late', 1)]:
            assert len(samples[category]) == expected, (category, samples[category])
        result = dict(otree_version=otree.__version__, otree_source=otree.__file__,
            transport='owned uvicorn loopback TCP, stdlib asyncio streams',
            business='single ThreadedASGI synchronous blocking fixture; no ORM commit benchmark',
            workers=dict(business=1, control=2), sessions=session_count, actors_per_session=40,
            boundary=dict(arrival_ts=2000.0, release_ts=1941.0, deadline_ts=2001.0,
                business_completion_ts=2002.0, test_clock=True, production_seconds=60),
            completeness=dict(durable_manual=actor_count, native_after_cutoff=actor_count,
                canonical_replays=actor_count, lost_response_replayed=1, genuine_late=1),
            timings={kind: summary(values) for kind, values in samples.items()})
    finally:
        release.set()
        if slow_task is not None:
            await asyncio.gather(slow_task, *native_tasks, return_exceptions=True)
        for writer in tuple(writers):
            writer.close()
        if server is not None:
            server.should_exit = True
        if server_task is not None:
            await asyncio.wait_for(server_task, 10)
        await app.aclose()
        sock.close()
        assert bridge.thread is None or not bridge.thread.is_alive()
        runtime.reset_runtime()
    result['cleanup'] = 'owned server task, socket, business thread and executor stopped'
    print('ROOM_SYNC_LOAD ' + json.dumps(result, sort_keys=True), flush=True)


def legacy_on_v2_child():
    """Real frozen marker-1 Sessions retain native Live under the v2 wrapper."""
    os.environ['DYNAMIC_ROOM_RELEASE_PROTOCOL'] = '2'
    os.environ['DYNAMIC_ROOM_RELEASE_DRAIN_SECONDS'] = '10'
    from otree.main import setup
    setup()
    import otree
    import project_asgi
    import dynamic_bottleneck_round as experiment
    from dynamic_bottleneck_round import sync_runtime as runtime
    from dynamic_bottleneck_round import test_room_release_framework as helper
    from otree.models import Session
    from otree.database import db
    runtime.get_journal()
    from timeout_navigation import TimeoutOriginMiddleware
    assert isinstance(project_asgi.app, TimeoutOriginMiddleware)
    assert isinstance(project_asgi.app.app, __import__(
        'dynamic_bottleneck_round.sync_transport', fromlist=['SyncControlASGI']).SyncControlASGI)
    os.environ['DYNAMIC_ROOM_RELEASE_PROTOCOL'] = '1'
    original_request = helper.http_request
    checked_shells = []

    async def checked_request(application, path, **kwargs):
        result = await original_request(application, path, **kwargs)
        if '/Decision/' in path and kwargs.get('method', 'GET') == 'GET':
            assert result[0] == 200, result
            assert not re.search(r'data-control-token="[^"]+"', result[1])
            checked_shells.append(path)
        return result

    try:
        # Standard initialization already happened before importing project_asgi.
        with patch('otree.main.setup'), patch.object(helper, 'http_request', checked_request):
            helper.check_dual_session_live_flow(round_numbers=(4,))
        db.new_session()
        sessions = list(Session.objects_filter())
        assert len(sessions) == 2
        assert all(s.vars[experiment.ROOM_RELEASE_PROTOCOL_VAR] == 1 for s in sessions)
        assert len(checked_shells) == 81
    finally:
        asyncio.run(project_asgi.app.aclose())
        runtime.reset_runtime()
    print('ROOM_SYNC_LEGACY_ON_V2 ' + json.dumps(dict(otree_version=otree.__version__,
        otree_source=otree.__file__, sessions=2, human_manual=80, decision_gets=81,
        protocol_marker=1, wrapper_protocol=2, native_live=True, fast_tokens=0,
        paid_provider='mocked', rl='real', cleanup='business bridge and executor stopped')),
        flush=True)


def run_child(*, legacy=False, session_count=2):
    with tempfile.TemporaryDirectory(prefix='room-sync-load-') as directory:
        os.chdir(directory)  # Must precede any application/oTree import.
        os.environ['DATABASE_URL'] = 'sqlite:///db.sqlite3'
        os.environ['OTREE_IN_MEMORY'] = ''
        # Tripwire is checked BEFORE imports: inherited external URLs must never
        # be passed to oTree, even during a deliberately failing regression.
        assert os.environ.get('DATABASE_URL') == 'sqlite:///db.sqlite3', 'database isolation failed before imports'
        assert os.environ.get('OTREE_IN_MEMORY') == '', 'memory mode isolation failed before imports'
        os.environ['DYNAMIC_ROOM_SYNC_DB'] = str(Path(directory) / 'journal.sqlite3')
        # oTree imports validate its static directory, even without ORM setup.
        for name in (('_static', '_rooms', 'access_gate', 'dynamic_bottleneck_round',
                      'dynamic_bottleneck_survey', 'payment_info', 'route_choice',
                      'single_bottleneck') if legacy else ('_static',)):
            Path(name).symlink_to(PROJECT_ROOT / name, target_is_directory=True)
        from otree.database import engine, IN_MEMORY
        assert not IN_MEMORY and engine.url.get_backend_name() == 'sqlite'
        assert Path(engine.url.database).resolve() == Path(directory, 'db.sqlite3').resolve()
        print('ROOM_SYNC_DATABASE_ISOLATION ' + json.dumps(dict(backend='sqlite',
            database='db.sqlite3', in_memory=False, temporary_directory=True,
            inherited_external_url_overridden=True)), flush=True)
        if legacy:
            legacy_on_v2_child()
        else:
            asyncio.run(acceptance(session_count=session_count))


class RoomSyncLoadTests(unittest.TestCase):
    def run_isolated(self, argument):
        env = os.environ.copy()
        inherited = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = os.pathsep.join([inherited, str(PROJECT_ROOT)]).strip(os.pathsep)
        # Safe adversarial inherited configuration. run_child must overwrite it
        # before importing any app/oTree module; the tripwire cannot connect it.
        env.update(DATABASE_URL='postgresql://isolation.invalid/never_connect', OTREE_IN_MEMORY='1')
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), argument],
            cwd=tempfile.gettempdir(), env=env, capture_output=True, text=True, timeout=45)
        print(completed.stdout, end='')
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return completed.stdout

    def test_two_sessions_80_loopback_control_and_deadline_receipts(self):
        output = self.run_isolated('--child')
        self.assertIn('"durable_manual": 80', output)
        self.assertIn('"cleanup":', output)

    def test_one_session_40_loopback_control_and_deadline_receipts(self):
        output = self.run_isolated('--single-child')
        self.assertIn('"durable_manual": 40', output)
        self.assertIn('"sessions": 1', output)
        self.assertIn('"cleanup":', output)

    def test_marker1_native_live_sessions_under_protocol2_wrapper(self):
        output = self.run_isolated('--legacy-child')
        self.assertIn('"human_manual": 80', output)
        self.assertIn('"native_live": true', output)


if __name__ == '__main__':
    if '--single-child' in sys.argv:
        run_child(session_count=1)
    elif '--child' in sys.argv:
        run_child()
    elif '--legacy-child' in sys.argv:
        run_child(legacy=True)
    else:
        unittest.main()
