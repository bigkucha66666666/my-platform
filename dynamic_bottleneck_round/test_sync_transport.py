"""Control tests only touch disposable journals, never oTree's ORM database."""
import asyncio
import contextvars
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import sqlite3
from unittest.mock import patch

try:
    from . import sync_runtime as runtime
    from .sync_transport import SyncControlASGI, ThreadedASGI
except ImportError:
    runtime = SyncControlASGI = ThreadedASGI = None


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assertIsNotNone(SyncControlASGI, 'isolated control transport is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'DYNAMIC_ROOM_SYNC_DB': str(Path(self.temp.name) / 'journal.sqlite3')})
        self.env.start()
        self.addCleanup(self.env.stop)
        runtime.reset_runtime()
        self.addCleanup(runtime.reset_runtime)
        # Each disposable journal reuses participant p; old HTTP failures from
        # cancellation tests belong to their previous fixture, not this round.
        from . import decision_ingress
        failures = patch.dict(decision_ingress._failed, {}, clear=True)
        failures.start()
        self.addCleanup(failures.stop)
        self.journal = runtime.get_journal()
        now = time.time()
        self.journal.ensure_round('s', 1, expected_codes=['p'], now_ts=now,
            decision_page_index=9, drain_limit_seconds=10, information_condition='I0',
            allowed_minutes=[474], boot_id=runtime.PROCESS_BOOT_ID)
        self.shell = 'a' * 24
        self.journal.register_shell('s', 1, 'p', self.shell, now_ts=now, page_index=9)
        self.token = runtime.sign_control_token('s', 1, 'p', self.shell, 9)
        async def business(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 204})
            await send({'type': 'http.response.body', 'body': b''})
        self.bridge = ThreadedASGI(business)
        self.app = SyncControlASGI(self.bridge)
        self.addAsyncCleanup(self.app.aclose)

    async def request(self, data=None, *, path='/dynamic-room-sync', method='POST', headers=None, messages=None):
        queue = asyncio.Queue()
        for item in messages or [{'type': 'http.request', 'body': json.dumps(data or {'token': self.token, 'kind': 'status'}).encode()}]:
            queue.put_nowait(item)
        output = []
        async def send(message):
            output.append(message)
        scope = {'type': 'http', 'method': method, 'path': path, 'scheme': 'http',
                 'headers': headers if headers is not None else [(b'host', b'testserver'), (b'origin', b'http://testserver'), (b'content-type', b'application/json')]}
        await self.app(scope, queue.get, send)
        return output

    async def test_control_survives_genuine_synchronous_business_block(self):
        started, release = threading.Event(), threading.Event()
        async def blocking(scope, receive, send):
            started.set()
            release.wait(3)
            await send({'type': 'http.response.start', 'status': 204})
            await send({'type': 'http.response.body', 'body': b''})
        self.bridge.app = blocking
        task = asyncio.create_task(self.request(path='/slow'))
        try:
            await asyncio.wait_for(asyncio.to_thread(started.wait, 1), 1.2)
            self.assertTrue(started.is_set())
            output = await asyncio.wait_for(self.request(), .5)
            self.assertEqual(output[0]['status'], 200, output[-1]['body'])
        finally:
            release.set()
            await task

    async def test_decision_commit_overrun_faults_before_pending_clears_while_business_blocked(self):
        self.arm_decision()
        deadline = time.time() + .4
        with self.journal._connection(write=True) as connection:
            record, original = self.journal._load(connection, 's', 1)
            record['state'].update(deadline_ts=deadline, drain_limit_seconds=.1)
            self.journal._save(connection, 's', 1, record, original)
        business_entered, release_business = threading.Event(), threading.Event()
        commit_entered, release_commit = threading.Event(), threading.Event()
        async def blocked_business(scope, receive, send):
            business_entered.set()
            release_business.wait(3)
            await send({'type': 'http.response.start', 'status': 204})
            await send({'type': 'http.response.body', 'body': b''})
        self.bridge.app = blocked_business
        original_connection = self.journal._connection
        delayed = False
        @contextmanager
        def delayed_commit(*, write=False):
            nonlocal delayed
            with original_connection(write=write) as connection:
                yield connection
                if write and not delayed:
                    delayed = True
                    commit_entered.set()
                    release_commit.wait(3)
        business_task = asyncio.create_task(self.request(path='/blocked'))
        try:
            await asyncio.to_thread(business_entered.wait, 1)
            self.assertTrue(business_entered.is_set())
            with patch.object(self.journal, '_connection', side_effect=delayed_commit):
                decision_task = asyncio.create_task(self.request({
                    'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
                try:
                    await asyncio.to_thread(commit_entered.wait, 1)
                    self.assertTrue(commit_entered.is_set())
                    self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ('p',))
                    await asyncio.sleep(max(0, deadline + .15 - time.time()))
                finally:
                    release_commit.set()
                await decision_task
        finally:
            release_business.set()
            await business_task
        self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ())
        self.assertEqual(self.journal.receipt('s', 1, 'p')['kind'], 'manual')
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'],
                         'decision_control_queue_overrun')
        self.assertFalse(runtime.close_control_round('s', 1))

    async def test_complete_body_owns_pending_before_wait_for_relay(self):
        self.arm_decision()
        body_completed, release_relay = asyncio.Event(), asyncio.Event()
        worker_entered, release_worker = threading.Event(), threading.Event()
        original_wait_for = asyncio.wait_for
        original_exchange = self.journal.exchange
        async def paused_relay(awaitable, timeout):
            result = await original_wait_for(awaitable, timeout)
            body_completed.set()
            await release_relay.wait()
            return result
        def slow_exchange(*args, **kwargs):
            worker_entered.set()
            release_worker.wait(3)
            return original_exchange(*args, **kwargs)
        with patch.object(asyncio, 'wait_for', side_effect=paused_relay), \
                patch.object(self.journal, 'exchange', side_effect=slow_exchange):
            request = asyncio.create_task(self.request({
                'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
            try:
                await body_completed.wait()
                await asyncio.to_thread(worker_entered.wait, 1)
                self.assertTrue(worker_entered.is_set())
                self.assertFalse(runtime.close_control_round('s', 1),
                    'complete valid body must own the final settlement boundary')
            finally:
                release_relay.set()
                release_worker.set()
            response = await request
        self.assertEqual(response[0]['status'], 200)
        self.assertEqual(self.journal.receipt('s', 1, 'p')['kind'], 'manual')

    async def test_invalid_complete_body_never_owns_pending_or_faults_round(self):
        self.arm_decision()
        body_completed, release_relay = asyncio.Event(), asyncio.Event()
        original_wait_for = asyncio.wait_for
        async def paused_relay(awaitable, timeout):
            result = await original_wait_for(awaitable, timeout)
            body_completed.set()
            await release_relay.wait()
            return result
        with patch.object(asyncio, 'wait_for', side_effect=paused_relay):
            request = asyncio.create_task(self.request({
                'token': self.token, 'kind': 'decision', 'departure_minute': 999}))
            try:
                await body_completed.wait()
                self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ())
                self.assertTrue(runtime.close_control_round('s', 1))
            finally:
                release_relay.set()
            response = await request
        self.assertEqual(response[0]['status'], 400)
        self.assertFalse(self.journal.state('s', 1)['technical_anomaly'])

    async def test_cancelled_relay_after_complete_body_keeps_worker_owned_decision(self):
        self.arm_decision()
        body_completed, release_relay = asyncio.Event(), asyncio.Event()
        worker_entered, release_worker = threading.Event(), threading.Event()
        original_wait_for, original_exchange = asyncio.wait_for, self.journal.exchange
        async def paused_relay(awaitable, timeout):
            result = await original_wait_for(awaitable, timeout)
            body_completed.set()
            await release_relay.wait()
            return result
        def slow_exchange(*args, **kwargs):
            worker_entered.set()
            release_worker.wait(3)
            return original_exchange(*args, **kwargs)
        with patch.object(asyncio, 'wait_for', side_effect=paused_relay), \
                patch.object(self.journal, 'exchange', side_effect=slow_exchange):
            request = asyncio.create_task(self.request({
                'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
            try:
                await body_completed.wait()
                await asyncio.to_thread(worker_entered.wait, 1)
                self.assertTrue(worker_entered.is_set())
                self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ('p',))
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await request
                self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ('p',))
            finally:
                release_relay.set()
                release_worker.set()
            for unused in range(100):
                if not runtime.pending_control_codes('s', 1, kind='decision'):
                    break
                await asyncio.sleep(.01)
        self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ())
        self.assertEqual(self.journal.receipt('s', 1, 'p')['kind'], 'manual')

    async def test_rejects_bad_signatures_origin_method_and_body(self):
        for kwargs, status in [({'data': {'token': self.token + 'x', 'kind': 'status'}}, 403),
                ({'headers': [(b'host', b'testserver'), (b'origin', b'https://evil.test')]}, 403),
                ({'method': 'GET'}, 405),
                ({'messages': [{'type': 'http.request', 'body': b'x' * 17000}]}, 413),
                ({'messages': [{'type': 'http.request', 'body': b'{'}]}, 400),
                ({'messages': [{'type': 'invalid'}]}, 400),
                ({'messages': [{'type': 'http.request', 'body': b'{', 'more_body': True}, {'type': 'http.disconnect'}]}, None)]:
            with self.subTest(kwargs=str(kwargs)[:35]):
                output = await asyncio.wait_for(self.request(**kwargs), 1)
                if status is None:
                    self.assertEqual(output, [])
                else:
                    self.assertEqual(output[0]['status'], status)
                    self.assertNotIn(self.token.encode(), output[-1]['body'])

    async def test_registered_identity_and_strict_token_types(self):
        token = runtime.sign_control_token('s', 1, 'p', 'b' * 24, 9)
        self.assertEqual((await self.request({'token': token, 'kind': 'status'}))[0]['status'], 403)
        with self.assertRaises(ValueError):
            runtime.sign_control_token('s', True, 'p', self.shell, 9)
        payload = runtime.verify_control_token(self.token)
        self.assertEqual(payload['journal_id'], runtime.journal_identity())
        self.assertNotIn('capacity', str(payload))

    async def test_no_capacity_leak_and_clock_pair_echo(self):
        output = await self.request({'token': self.token, 'kind': 'status', 'clock_id': 3, 'clock_token': 'b' * 32})
        body = json.loads(output[-1]['body'])
        self.assertNotIn('actual_capacity_display', body)
        self.assertEqual((body.get('clock_id'), body.get('clock_token')), (3, 'b' * 32), body)
        self.assertIn((b'cache-control', b'no-store'), output[0]['headers'])

    async def test_context_and_single_thread_and_worker_loop_send(self):
        marker = contextvars.ContextVar('test_marker', default=None)
        marker.set('request-context')
        threads = []
        async def business(scope, receive, send):
            threads.append(threading.get_ident())
            self.assertEqual(marker.get(), 'request-context')
            await receive()
            def worker():
                asyncio.run(send({'type': 'http.response.start', 'status': 201}))
            await asyncio.to_thread(worker)
            await send({'type': 'http.response.body', 'body': b'ok'})
        self.bridge.app = business
        for unused in range(2):
            self.assertEqual((await self.request(path='/business'))[0]['status'], 201)
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())

    async def test_pending_persists_after_http_cancel_until_worker_finishes(self):
        entered, release = threading.Event(), threading.Event()
        original = self.journal.exchange
        def slow(*args, **kwargs):
            entered.set()
            release.wait(3)
            return original(*args, **kwargs)
        with patch.object(self.journal, 'exchange', side_effect=slow):
            task = asyncio.create_task(self.request({'token': self.token, 'kind': 'ack'}))
            try:
                await asyncio.to_thread(entered.wait, 1)
                self.assertTrue(entered.is_set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(runtime.pending_control_codes('s', 1, kind='ack'), ('p',))
            finally:
                release.set()
            for unused in range(100):
                if not runtime.pending_control_codes('s', 1, kind='ack'):
                    break
                await asyncio.sleep(.01)
            self.assertEqual(runtime.pending_control_codes('s', 1, kind='ack'), ())

    async def test_application_error_is_sanitized(self):
        with patch.object(self.journal, 'exchange', side_effect=RuntimeError(self.token)):
            output = await self.request()
        self.assertEqual(output[0]['status'], 503)
        self.assertNotIn(self.token.encode(), output[-1]['body'])

    def arm_decision(self):
        now = time.time()
        with self.journal._connection(write=True) as connection:
            record, original = self.journal._load(connection, 's', 1)
            record['state'].update(release_ts=now - 59, deadline_ts=now + 1,
                                   phase='RELEASED', ack_ts_by_code={'p': now - 60})
            self.journal._save(connection, 's', 1, record, original)

    def allow_second_choice(self):
        with self.journal._connection(write=True) as connection:
            record, original = self.journal._load(connection, 's', 1)
            record['private']['allowed_minutes'].append(475)
            self.journal._save(connection, 's', 1, record, original)
        self.token = runtime.sign_control_token('s', 1, 'p', self.shell, 9)

    async def assert_first_arrival(self, *, refresh=False, cancel=False, fail=False):
        self.arm_decision()
        self.allow_second_choice()
        second_token = self.token
        if refresh:
            shell = 'b' * 24
            self.journal.register_shell('s', 1, 'p', shell, now_ts=time.time(), page_index=9)
            second_token = runtime.sign_control_token('s', 1, 'p', shell, 9)
        entered, release = threading.Event(), threading.Event()
        registered = asyncio.Event()
        loop = asyncio.get_running_loop()
        original = self.app._exchange_registered
        register = runtime.register_pending
        calls = []
        def slow(payload, data, arrival):
            calls.append(data['departure_minute'])
            if data['departure_minute'] == 474:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('test release timeout')
                if fail:
                    raise RuntimeError('first persistence failed')
            return original(payload, data, arrival)
        def observe(*args):
            ticket = register(*args)
            if len(runtime._pending) == 2:
                loop.call_soon_threadsafe(registered.set)
            return ticket
        # Identical server timestamps must still use registration order.
        arrival = time.time()
        with patch.object(self.app, '_exchange_registered', side_effect=slow), \
                patch.object(runtime, 'register_pending', side_effect=observe), \
                patch('dynamic_bottleneck_round.sync_transport.time.time', return_value=arrival):
            first = asyncio.create_task(self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = asyncio.create_task(self.request({'token': second_token, 'kind': 'decision', 'departure_minute': 475}))
            try:
                await asyncio.wait_for(registered.wait(), 2)
                # An independent executor sentinel proves free workers can run.
                await asyncio.wrap_future(self.app.executor.submit(lambda: None))
                await asyncio.sleep(0)
                self.assertFalse(second.done(), 'later arrival must not confirm before the first')
                self.assertEqual(calls, [474], 'later persistence must not start before the first')
                if cancel:
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
            finally:
                release.set()
                responses = await asyncio.gather(first, second, return_exceptions=True)
        if fail:
            self.assertTrue(all(response[0]['status'] == 503 for response in responses))
            self.assertIsNone(self.journal.receipt('s', 1, 'p'))
            self.assertFalse(runtime.close_control_round('s', 1))
            audit = self.journal.audit('s', 1, 'p')['admitted_decision']
            self.assertIsNotNone(audit, 'first failed admission should retain evidence when storage is writable')
            self.assertEqual(audit['departure_minute'], 474, 'later queued value must not replace first failed evidence')
            self.assertEqual(audit['ingress_ts'], arrival)
            self.assertEqual(audit['kind'], 'manual')
            self.assertEqual(audit['shell_token'], self.shell)
        else:
            for response in responses[1:] if cancel else responses:
                self.assertEqual(json.loads(response[-1]['body'])['receipt']['departure_minute'], 474)
            self.assertEqual(self.journal.receipt('s', 1, 'p')['departure_minute'], 474)
        self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ())

    async def test_first_registered_decision_wins_equal_timestamp(self):
        await self.assert_first_arrival()

    async def test_refreshed_shell_shares_first_arrival_order(self):
        await self.assert_first_arrival(refresh=True)

    async def test_cancelled_first_request_keeps_order_and_canonical(self):
        await self.assert_first_arrival(cancel=True)

    async def test_first_persistence_failure_never_confirms_second_value(self):
        await self.assert_first_arrival(fail=True)

    async def test_secondary_audit_failure_preserves_original_error_and_fault(self):
        self.arm_decision()
        observed = []
        original = self.app._exchange
        def exchange(*args):
            try:
                return original(*args)
            except Exception as error:
                observed.append(str(error))
                raise
        with patch.object(self.app, '_exchange', side_effect=exchange), \
                patch.object(self.app, '_exchange_registered', side_effect=RuntimeError('first persistence failed')), \
                patch.object(self.app, '_retain_admitted', side_effect=RuntimeError('secondary audit failed')) as retain:
            output = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(output[0]['status'], 503)
        self.assertEqual(retain.call_count, 1, 'failure path must attempt admitted audit before releasing ticket')
        self.assertEqual(observed, ['first persistence failed'])
        self.assertIsNone(self.journal.receipt('s', 1, 'p'))
        self.assertTrue(runtime.control_fault('s', 1))
        self.assertFalse(runtime.close_control_round('s', 1))
        self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ())

    async def test_pre_fault_admitted_decision_retains_audit_without_receipt(self):
        self.arm_decision()
        entered, release = threading.Event(), threading.Event()
        original = self.app._exchange
        def paused(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test release timeout')
            return original(*args, **kwargs)
        with patch.object(self.app, '_exchange', side_effect=paused):
            request = asyncio.create_task(self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            arrival = next(iter(runtime._pending.values()))[-1]
            runtime.latch_control_fault(runtime.verify_control_token(self.token), 'decision_control_queue_overrun')
            runtime.persist_control_fault('s', 1)
            release.set()
            output = await request
        self.assertEqual(output[0]['status'], 503)
        self.assertIsNone(self.journal.receipt('s', 1, 'p'))
        audit = self.journal.audit('s', 1, 'p').get('admitted_decision')
        self.assertIsNotNone(audit, 'pre-fault server admission must survive later fault')
        self.assertEqual(audit['departure_minute'], 474)
        self.assertEqual(audit['ingress_ts'], arrival)
        self.assertEqual(audit['kind'], 'manual')
        from .sync_journal import SyncJournal
        self.assertEqual(SyncJournal(self.journal.path).audit('s', 1, 'p')['admitted_decision'], audit)
        self.assertFalse(runtime.close_control_round('s', 1))
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'], 'decision_control_queue_overrun')

    async def test_fault_already_in_journal_does_not_grant_pre_fault_audit(self):
        self.arm_decision()
        self.journal.mark_anomaly('s', 1, 'decision_control_queue_overrun')
        response = await self.request({'token': self.token, 'kind': 'decision',
            'departure_minute': 474, 'admitted_before_fault': True, 'admission_ticket': 'forged'})
        self.assertTrue(json.loads(response[-1]['body'])['technical_anomaly'])
        self.assertIsNone(self.journal.audit('s', 1, 'p').get('admitted_decision'))
        self.assertIsNone(self.journal.receipt('s', 1, 'p'))
        self.assertFalse(runtime.close_control_round('s', 1), 'durable anomaly must retain close interlock')

    async def test_fault_between_worker_precheck_and_transaction_retains_admitted_audit(self):
        self.arm_decision()
        original = self.journal.exchange
        payload = runtime.verify_control_token(self.token)
        def fault_then_exchange(*args, **kwargs):
            runtime.latch_control_fault(payload, 'decision_control_queue_overrun')
            runtime.persist_control_fault('s', 1)
            return original(*args, **kwargs)
        with patch.object(self.journal, 'exchange', side_effect=fault_then_exchange):
            response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(response[0]['status'], 503)
        self.assertIsNone(self.journal.receipt('s', 1, 'p'))
        self.assertIsNotNone(self.journal.audit('s', 1, 'p')['admitted_decision'])

    async def assert_successful_exchange_fault_interlock(self, kind):
        self.arm_decision()
        await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        canonical = self.journal.receipt('s', 1, 'p')
        original = self.journal.exchange
        observed = []
        def fault_then_exchange(*args, **kwargs):
            self.journal.mark_anomaly('s', 1, 'decision_control_queue_overrun')
            result = original(*args, **kwargs)
            observed.append(result[0]['technical_anomaly'])
            return result
        boundary = []
        finish = runtime.finish_pending
        def observe_finish(ticket):
            boundary.append(runtime.control_fault('s', 1))
            return finish(ticket)
        with patch.object(self.journal, 'exchange', side_effect=fault_then_exchange), \
                patch.object(runtime, 'finish_pending', side_effect=observe_finish):
            response = await self.request({'token': self.token, 'kind': kind, 'departure_minute': 474})
        self.assertEqual(observed, ['decision_control_queue_overrun'])
        closed = runtime.close_control_round('s', 1)
        self.assertEqual((boundary, closed), (['decision_control_queue_overrun'], False),
                         'successful snapshot must interlock pending release and closure')
        self.assertEqual(response[0]['status'], 503)
        self.assertEqual(self.journal.receipt('s', 1, 'p'), canonical)
        self.assertEqual(boundary, ['decision_control_queue_overrun'],
                         'successful snapshot must install interlock before pending release')
        self.assertFalse(closed)
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'], 'decision_control_queue_overrun')
        audit = self.journal.audit('s', 1, 'p').get('admitted_decision')
        if kind == 'decision':
            self.assertEqual(audit['departure_minute'], 474)
            self.assertEqual(audit['kind'], 'manual')
        else:
            self.assertIsNone(audit)

    async def test_successful_replay_snapshot_fault_interlocks_before_pending_release(self):
        await self.assert_successful_exchange_fault_interlock('decision')

    async def test_successful_status_snapshot_fault_interlocks_before_pending_release(self):
        await self.assert_successful_exchange_fault_interlock('status')

    async def test_faulted_round_rejects_json_audit_capability_and_direct_probe(self):
        self.arm_decision()
        payload = runtime.verify_control_token(self.token)
        runtime.latch_control_fault(payload, 'decision_control_queue_overrun')
        data = {'token': self.token, 'kind': 'decision', 'departure_minute': 474,
                'admitted_before_fault': True, 'admission_ticket': 'forged'}
        self.assertEqual((await self.request(data))[0]['status'], 503)
        self.app._exchange_registered(payload, data, time.time())
        self.assertIsNone(self.journal.audit('s', 1, 'p').get('admitted_decision'))

    async def test_early_and_invalid_requests_do_not_own_later_manual(self):
        self.arm_decision()
        self.allow_second_choice()
        state = self.journal.state('s', 1)
        with patch('dynamic_bottleneck_round.sync_transport.time.time', return_value=state['release_ts'] - 1):
            response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(response[0]['status'], 400)
        self.assertEqual((await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': -1}))[0]['status'], 400)
        response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 475})
        self.assertEqual(json.loads(response[-1]['body'])['receipt']['departure_minute'], 475)
        self.assertFalse(runtime._decision_queues)
        self.assertFalse(runtime._admitted_requests)

    async def test_actor_queue_does_not_block_other_actor_or_session(self):
        self.arm_decision()
        tokens = []
        for session, code in [('s', 'q'), ('other', 'p')]:
            if session == 'other':
                self.journal.ensure_round(session, 1, expected_codes=[code], now_ts=time.time(),
                    decision_page_index=9, drain_limit_seconds=10, information_condition='I0',
                    allowed_minutes=[474], boot_id=runtime.PROCESS_BOOT_ID)
            else:
                with self.journal._connection(write=True) as connection:
                    record, original = self.journal._load(connection, session, 1)
                    record['state']['expected_codes'].append(code)
                    self.journal._save(connection, session, 1, record, original)
            self.journal.register_shell(session, 1, code, 'b' * 24, now_ts=time.time(), page_index=9)
            with self.journal._connection(write=True) as connection:
                record, original = self.journal._load(connection, session, 1)
                record['state'].update(release_ts=time.time()-1, deadline_ts=time.time()+59,
                                      phase='RELEASED', ack_ts_by_code={code: time.time()-2})
                if session == 's':
                    record['state']['ack_ts_by_code']['p'] = time.time()-2
                self.journal._save(connection, session, 1, record, original)
            tokens.append(runtime.sign_control_token(session, 1, code, 'b' * 24, 9))
        entered, release = threading.Event(), threading.Event()
        original = self.app._exchange_registered
        def pause(payload, data, arrival):
            if payload['session_code'] == 's' and payload['participant_code'] == 'p':
                entered.set()
                release.wait(5)
            return original(payload, data, arrival)
        with patch.object(self.app, '_exchange_registered', side_effect=pause):
            first = asyncio.create_task(self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            try:
                for token in tokens:
                    response = await asyncio.wait_for(self.request({'token': token, 'kind': 'decision', 'departure_minute': 474}), 2)
                    self.assertEqual(json.loads(response[-1]['body'])['receipt']['kind'], 'manual')
            finally:
                release.set()
                await first

    async def test_actor_queue_uses_admission_slots_and_shutdown_drains_successors(self):
        self.arm_decision()
        self.app._slots = threading.BoundedSemaphore(2)
        entered, release = threading.Event(), threading.Event()
        original = self.app._exchange_registered
        def pause(*args):
            entered.set()
            release.wait(5)
            return original(*args)
        with patch.object(self.app, '_exchange_registered', side_effect=pause):
            first = self.app._admit_body(json.dumps({'token': self.token, 'kind': 'decision', 'departure_minute': 474}).encode())
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = self.app._admit_body(json.dumps({'token': self.token, 'kind': 'decision', 'departure_minute': 474}).encode())
            self.assertEqual(len(runtime._pending), 2)
            self.assertFalse(second['future'].done())
            rejected = self.app._admit_body(json.dumps({'token': self.token, 'kind': 'decision', 'departure_minute': 474}).encode())
            self.assertEqual(rejected['status'], 503)
            closing = asyncio.create_task(self.app.aclose())
            release.set()
            await asyncio.wait_for(closing, 3)
        self.assertTrue(first['future'].done())
        self.assertTrue(second['future'].done())
        self.assertFalse(runtime._pending)
        self.assertFalse(runtime._decision_queues)
        self.assertFalse(runtime._admitted_requests)
        self.assertTrue(self.app._slots.acquire(blocking=False))
        self.assertTrue(self.app._slots.acquire(blocking=False))
        self.app._slots.release()
        self.app._slots.release()

    async def test_scheduler_admission_error_releases_ticket_and_slot(self):
        self.arm_decision()
        with patch.object(runtime, 'admit_control_work', side_effect=RuntimeError('queue bookkeeping failed')):
            response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        pending = runtime.pending_control_codes('s', 1, kind='decision')
        # Remove leaked tickets only after capturing the behavior, so a RED
        # admission-cleanup test cannot contaminate subsequent fixtures.
        for ticket in tuple(runtime._pending):
            runtime.finish_pending(ticket)
        self.assertEqual(response[0]['status'], 503)
        self.assertEqual(pending, ())
        self.assertFalse(runtime.close_control_round('s', 1))

    async def test_control_scheduler_never_does_sql_or_submit_inside_boundary(self):
        self.arm_decision()
        owner = threading.get_ident()
        connection, submit = self.journal._connection, self.app.executor.submit
        @contextmanager
        def checked_connection(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), owner)
            self.assertFalse(runtime._lock._is_owned())
            with connection(*args, **kwargs) as value:
                yield value
        def checked_submit(*args, **kwargs):
            self.assertFalse(runtime._lock._is_owned())
            return submit(*args, **kwargs)
        with patch.object(self.journal, '_connection', side_effect=checked_connection), \
                patch.object(self.app.executor, 'submit', side_effect=checked_submit):
            response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(response[0]['status'], 200)

    async def test_failed_control_persistence_leaves_durable_fault_before_pending_clears(self):
        await self.assert_failed_control('decision')

    async def test_failed_ack_persistence_leaves_durable_fault(self):
        await self.assert_failed_control('ack')

    async def test_failed_status_persistence_leaves_durable_fault(self):
        await self.assert_failed_control('status')

    async def test_failed_operative_persistence_leaves_durable_fault(self):
        await self.assert_failed_control('operative')

    async def assert_failed_control(self, kind):
        self.arm_decision()
        with patch.object(self.journal, 'exchange', side_effect=RuntimeError('storage failed')):
            output = await self.request({'token': self.token, 'kind': kind, 'departure_minute': 474})
        self.assertEqual(output[0]['status'], 503)
        self.assertEqual(runtime.pending_control_codes('s', 1, kind=kind), ())
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'], 'control_persistence_failure')
        self.assertIsNone(self.journal.receipt('s', 1, 'p'))

    async def test_cancelled_failed_worker_still_latches_fault(self):
        self.arm_decision()
        entered, release = threading.Event(), threading.Event()
        def fail(*args, **kwargs):
            entered.set()
            release.wait(3)
            raise RuntimeError('storage failed')
        with patch.object(self.journal, 'exchange', side_effect=fail):
            task = asyncio.create_task(self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
            try:
                await asyncio.to_thread(entered.wait, 1)
                self.assertTrue(entered.is_set())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
            await self.app.aclose()
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'], 'control_persistence_failure')

    async def test_overload_authenticates_and_validates_before_latching_fault(self):
        self.arm_decision()
        occupied = 0
        while self.app._slots.acquire(blocking=False):
            occupied += 1
        try:
            invalid_identity = runtime.sign_control_token('s', 1, 'p', 'b' * 24, 9)
            for token, minute in ((self.token + 'x', 474), (invalid_identity, 474), (self.token, 999)):
                response = await self.request({'token': token, 'kind': 'decision', 'departure_minute': minute})
                self.assertIn(response[0]['status'], (400, 403))
                self.assertFalse(self.journal.state('s', 1)['technical_anomaly'])
            response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
            self.assertEqual(response[0]['status'], 503)
        finally:
            for unused in range(occupied):
                self.app._slots.release()
        # The next real worker also flushes a queue-admission fault.
        await self.request()
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'], 'control_queue_admission_failure')

    async def test_invalid_choices_and_settled_replay_failure_preserve_history(self):
        self.arm_decision()
        response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 999})
        self.assertEqual(response[0]['status'], 400)
        self.assertFalse(self.journal.state('s', 1)['technical_anomaly'])
        response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        canonical = json.loads(response[-1]['body'])['receipt']
        self.journal.mark_settled('s', 1)
        before = self.journal.state('s', 1)
        with patch.object(self.journal, 'exchange', side_effect=RuntimeError('replay failure')):
            response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(response[0]['status'], 503)
        self.assertEqual(self.journal.state('s', 1), before)
        self.assertEqual(self.journal.receipt('s', 1, 'p'), canonical)

    async def test_anomaly_storage_failure_retains_interlock_without_blocking_control_loop(self):
        self.arm_decision()
        entered, release = threading.Event(), threading.Event()
        def unavailable(*args):
            entered.set()
            release.wait(3)
            raise RuntimeError('anomaly storage unavailable')
        with patch.object(self.journal, 'exchange', side_effect=RuntimeError('receipt storage unavailable')), \
                patch.object(self.journal, 'mark_anomaly', side_effect=unavailable):
            task = asyncio.create_task(self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
            try:
                await asyncio.to_thread(entered.wait, 1)
                self.assertTrue(entered.is_set())
                # Registry operations must remain responsive during SQLite work.
                payload = runtime.verify_control_token(self.token)
                other = runtime.register_pending(payload, 'status', time.time())
                runtime.finish_pending(other)
                self.assertEqual(runtime.control_fault('s', 1), 'control_persistence_failure')
                self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ('p',))
            finally:
                release.set()
                output = await task
            self.assertEqual(output[0]['status'], 503)
            self.assertEqual(runtime.control_fault('s', 1), 'control_persistence_failure')
        await self.request()
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'], 'control_persistence_failure')

    async def test_final_control_boundary_rejects_active_fault_and_preserves_settled_replay(self):
        self.arm_decision()
        payload = runtime.verify_control_token(self.token)
        ticket = runtime.register_pending(payload, 'decision', time.time())
        self.assertFalse(runtime.close_control_round('s', 1))
        runtime.finish_pending(ticket)
        runtime.latch_control_fault(payload, 'control_persistence_failure')
        self.assertFalse(runtime.close_control_round('s', 1))
        await self.request()
        self.assertFalse(self.journal.state('s', 1)['settled'])

    async def test_storage_permission_error_is_not_mistaken_for_invalid_identity(self):
        self.arm_decision()
        with patch.object(self.journal, 'exchange', side_effect=PermissionError('storage access denied')):
            output = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(output[0]['status'], 503)
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'], 'control_persistence_failure')

    async def test_storage_value_error_is_not_mistaken_for_invalid_choice(self):
        self.arm_decision()
        with patch.object(self.journal, 'exchange', side_effect=ValueError('corrupt persisted record')):
            output = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(output[0]['status'], 503)
        self.assertEqual(self.journal.state('s', 1)['technical_anomaly'], 'control_persistence_failure')

    async def test_fault_after_successful_native_control_boundary_is_only_replay_failure(self):
        self.arm_decision()
        response = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        canonical = json.loads(response[-1]['body'])['receipt']
        self.assertTrue(runtime.close_control_round('s', 1))
        with patch.object(self.journal, 'exchange', side_effect=RuntimeError('replay failed')):
            output = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(output[0]['status'], 503)
        self.assertFalse(runtime.control_fault('s', 1))
        self.journal.mark_settled('s', 1)
        self.assertFalse(self.journal.state('s', 1)['technical_anomaly'])
        self.assertEqual(self.journal.receipt('s', 1, 'p'), canonical)

    async def test_retained_fault_prevents_healthy_status_and_new_receipt(self):
        self.arm_decision()
        with patch.object(self.journal, 'mark_anomaly', side_effect=RuntimeError('anomaly write failed')):
            with patch.object(self.journal, 'exchange', side_effect=RuntimeError('receipt failed')):
                self.assertEqual((await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474}))[0]['status'], 503)
            for kind in ('status', 'decision', 'ack', 'operative'):
                output = await self.request({'token': self.token, 'kind': kind, 'departure_minute': 474})
                self.assertEqual(output[0]['status'], 503, kind)
                body = json.loads(output[-1]['body'])
                self.assertEqual(body['technical_anomaly'], 'control_persistence_failure')
                self.assertNotIn('receipt', body)
                self.assertIsNone(self.journal.receipt('s', 1, 'p'))

    async def test_fault_during_authentication_prevents_new_control_receipt(self):
        self.arm_decision()
        payload = runtime.verify_control_token(self.token)
        original = self.journal.state
        def read_then_fault(*args, **kwargs):
            result = original(*args, **kwargs)
            runtime.latch_control_fault(payload, 'control_persistence_failure')
            return result
        with patch.object(self.journal, 'state', side_effect=read_then_fault), \
                patch.object(self.journal, 'mark_anomaly', side_effect=RuntimeError('anomaly write failed')):
            output = await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474})
        self.assertEqual(output[0]['status'], 503)
        self.assertIsNone(self.journal.receipt('s', 1, 'p'))

    async def test_fault_after_worker_result_is_checked_before_http_response(self):
        self.arm_decision()
        payload = runtime.verify_control_token(self.token)
        original = runtime.finish_pending
        def finish_then_fault(ticket):
            original(ticket)
            runtime.latch_control_fault(payload, 'control_persistence_failure')
        with patch.object(runtime, 'finish_pending', side_effect=finish_then_fault):
            output = await self.request()
        self.assertEqual(output[0]['status'], 503)
        self.assertEqual(json.loads(output[-1]['body'])['technical_anomaly'], 'control_persistence_failure')

    async def overload_from_another_session(self, *, close=False, fail_flush=False):
        await self.app.aclose()
        self.app = SyncControlASGI(self.bridge, workers=1, max_pending=1)
        self.addAsyncCleanup(self.app.aclose)
        self.arm_decision()
        now = time.time()
        self.journal.ensure_round('other', 1, expected_codes=['q'], now_ts=now,
            decision_page_index=9, drain_limit_seconds=10, information_condition='I0',
            allowed_minutes=[474], boot_id=runtime.PROCESS_BOOT_ID)
        self.journal.register_shell('other', 1, 'q', self.shell, now_ts=now, page_index=9)
        token = runtime.sign_control_token('other', 1, 'q', self.shell, 9)
        entered, release, flushed = threading.Event(), threading.Event(), threading.Event()
        original_exchange, original_mark = self.journal.exchange, self.journal.mark_anomaly
        attempts = []
        def busy(*args, **kwargs):
            if args[0] == 'other':
                entered.set()
                release.wait(3)
            return original_exchange(*args, **kwargs)
        def mark(*args):
            attempts.append(threading.get_ident())
            try:
                if fail_flush:
                    raise RuntimeError('flush failed')
                return original_mark(*args)
            finally:
                flushed.set()
        with patch.object(self.journal, 'exchange', side_effect=busy), patch.object(self.journal, 'mark_anomaly', side_effect=mark):
            task = asyncio.create_task(self.request({'token': token, 'kind': 'status'}))
            try:
                await asyncio.to_thread(entered.wait, 1)
                self.assertTrue(entered.is_set())
                for unused in range(20):
                    self.assertEqual((await self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474}))[0]['status'], 503)
                # One coalesced flush job at most, despite twenty rejections.
                self.assertLessEqual(self.app.executor._work_queue.qsize(), 1)
                shutdown = asyncio.create_task(self.app.aclose()) if close else None
                if shutdown:
                    await asyncio.sleep(.01)
                    self.assertFalse(shutdown.done())
            finally:
                release.set()
                await task
            if shutdown:
                await shutdown
                self.assertTrue(all(not t.is_alive() for t in self.app.executor._threads))
            await asyncio.to_thread(flushed.wait, 1)
            self.assertTrue(flushed.is_set(), 'no affected-round request should be needed to flush its fault')
            await asyncio.sleep(.03)
            self.assertLessEqual(len(attempts), 3, 'failed flush must not spin')
            self.assertNotIn(threading.get_ident(), attempts, 'SQLite never runs on the main event loop')
            self.assertEqual(runtime.control_fault('s', 1), 'control_queue_admission_failure')
            self.assertEqual(self.journal.state('s', 1)['technical_anomaly'],
                '' if fail_flush else 'control_queue_admission_failure')
            self.assertFalse(self.journal.state('other', 1)['technical_anomaly'])

    async def test_overload_fault_flushes_after_other_session_releases_worker(self):
        await self.overload_from_another_session()

    async def test_overload_flush_failure_retains_guard_without_spinning(self):
        await self.overload_from_another_session(fail_flush=True)

    async def test_shutdown_drains_owned_overload_flush(self):
        await self.overload_from_another_session(close=True)

    async def test_fault_added_during_flush_is_not_lost_or_spun(self):
        now = time.time()
        self.journal.ensure_round('second', 1, expected_codes=['q'], now_ts=now,
            decision_page_index=9, drain_limit_seconds=10, information_condition='I0',
            allowed_minutes=[474], boot_id=runtime.PROCESS_BOOT_ID)
        self.journal.register_shell('second', 1, 'q', self.shell, now_ts=now, page_index=9)
        token = runtime.sign_control_token('second', 1, 'q', self.shell, 9)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = self.journal.mark_anomaly
        def mark(*args):
            if args[0] == 's':
                entered.set()
                release.wait(3)
            result = original(*args)
            if args[0] == 'second':
                finished.set()
            return result
        count = 0
        while self.app._slots.acquire(blocking=False):
            count += 1
        with patch.object(self.journal, 'mark_anomaly', side_effect=mark):
            try:
                self.assertEqual((await self.request())[0]['status'], 503)
                await asyncio.to_thread(entered.wait, 1)
                self.assertTrue(entered.is_set())
                self.assertEqual((await self.request({'token': token, 'kind': 'status'}))[0]['status'], 503)
            finally:
                release.set()
                for unused in range(count):
                    self.app._slots.release()
            await asyncio.to_thread(finished.wait, 1)
            self.assertTrue(finished.is_set())
        self.assertEqual(self.journal.state('second', 1)['technical_anomaly'], 'control_queue_admission_failure')

    async def test_authorization_ceiling_refuses_new_identity_preserves_live_and_resets(self):
        other_shell = 'b' * 24
        self.journal.register_shell('s', 1, 'p', other_shell, now_ts=time.time(), page_index=9)
        with patch.object(runtime, '_MAX_CONTROLS', 1):
            with self.assertRaisesRegex(RuntimeError, 'authorization capacity exhausted'):
                runtime.sign_control_token('s', 1, 'p', other_shell, 9)
            self.assertEqual(runtime.sign_control_token('s', 1, 'p', self.shell, 9), self.token)
            self.assertEqual((await self.request())[0]['status'], 200)
            runtime.reset_runtime()
            token = runtime.sign_control_token('s', 1, 'p', other_shell, 9)
            self.assertEqual((await self.request({'token': token, 'kind': 'status'}))[0]['status'], 200)

    async def test_lifespan_is_forwarded_and_threads_close(self):
        events = []
        async def business(scope, receive, send):
            while True:
                event = await receive()
                events.append(event['type'])
                await send({'type': event['type'] + '.complete'})
                if event['type'] == 'lifespan.shutdown':
                    return
        self.bridge.app = business
        queue = asyncio.Queue()
        queue.put_nowait({'type': 'lifespan.startup'})
        output = []
        async def send(event):
            output.append(event)
            if event['type'] == 'lifespan.startup.complete':
                queue.put_nowait({'type': 'lifespan.shutdown'})
        await asyncio.wait_for(self.app({'type': 'lifespan'}, queue.get, send), 2)
        self.assertEqual(events, ['lifespan.startup', 'lifespan.shutdown'])
        self.assertFalse(self.bridge.thread.is_alive())
        self.assertFalse(runtime.control_transport_active())

    async def test_business_disconnect_and_exception_do_not_hang(self):
        async def business(scope, receive, send):
            event = await receive()
            self.assertEqual(event['type'], 'http.disconnect')
            raise RuntimeError('business failure')
        self.bridge.app = business
        with self.assertRaisesRegex(RuntimeError, 'business failure'):
            await asyncio.wait_for(self.request(path='/business', messages=[{'type': 'http.disconnect'}]), 1)

    async def test_journal_identity_survives_reopen_and_rejects_replacement(self):
        identity = runtime.journal_identity()
        runtime.reset_runtime()
        self.assertEqual(runtime.journal_identity(), identity)
        replacement = str(Path(self.temp.name) / 'replacement.sqlite3')
        with patch.dict(os.environ, {'DYNAMIC_ROOM_SYNC_DB': replacement}):
            runtime.reset_runtime()
            with self.assertRaises(ValueError):
                runtime.verify_control_token(self.token)

    async def test_queued_ack_is_registered_before_its_worker_starts(self):
        await self.app.aclose()
        self.app = SyncControlASGI(self.bridge, workers=1)
        entered, release = threading.Event(), threading.Event()
        def occupied():
            entered.set()
            release.wait(3)
        future = self.app.executor.submit(occupied)
        await asyncio.to_thread(entered.wait, 1)
        task = asyncio.create_task(self.request({'token': self.token, 'kind': 'ack'}))
        decision = asyncio.create_task(self.request({'token': self.token, 'kind': 'decision', 'departure_minute': 474}))
        try:
            await asyncio.sleep(.03)
            self.assertEqual(runtime.pending_control_codes('s', 1, kind='ack'), ('p',))
            self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ('p',))
            self.assertEqual(runtime.pending_control_codes('s', 2, kind='decision'), ())
            decision.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await decision
            await asyncio.sleep(.01)
            self.assertEqual(runtime.pending_control_codes('s', 1, kind='decision'), ('p',))
            release_timestamp = time.time()
        finally:
            release.set()
            await task
            await asyncio.wrap_future(future)
            await self.app.aclose()
        self.assertLess(self.journal.state('s', 1)['ack_ts_by_code']['p'], release_timestamp)

    async def test_websocket_receive_context_reaches_business_live_method(self):
        from .decision_ingress import DecisionIngressMiddleware, shell_ack_ingress_ts, finish_shell_ack
        observed = []
        async def business(scope, receive, send):
            await receive()
            observed.append(shell_ack_ingress_ts())
            finish_shell_ack()
            await send({'type': 'websocket.close', 'code': 1000})
        self.bridge.app = business
        app = DecisionIngressMiddleware(self.bridge)
        async def receive():
            return {'type': 'websocket.receive', 'text': '{"kind":"ack"}'}
        async def send(message):
            pass
        await app({'type': 'websocket', 'path': '/live',
                   'query_string': b'page_name=Decision&participant_code=p&page_index=9'}, receive, send)
        self.assertIsInstance(observed[0], float)

    async def test_cancelled_http_keeps_ingress_and_lock_until_business_finishes(self):
        from .decision_ingress import DecisionIngressMiddleware, pending_participant_requests
        entered, release = threading.Event(), threading.Event()
        finished = threading.Event()
        async def business(scope, receive, send):
            entered.set()
            try:
                await asyncio.to_thread(release.wait, 3)
            finally:
                finished.set()
        self.bridge.app = business
        self.app.app = DecisionIngressMiddleware(self.bridge)
        task = asyncio.create_task(self.request(path='/p/p/dynamic_bottleneck_round/Decision/9'))
        try:
            await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(.03)
            self.assertFalse(task.done())
            self.assertFalse(finished.is_set())
            self.assertTrue(pending_participant_requests('p', page_index=9))
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(finished.is_set())
        self.assertFalse(pending_participant_requests('p', page_index=9))

    async def test_initialization_failure_sends_sanitized_lifespan_failure(self):
        output = []
        async def send(message):
            output.append(message)
        async def receive():
            return {'type': 'lifespan.startup'}
        with patch.object(runtime, 'get_journal', side_effect=RuntimeError(self.token)):
            await self.app({'type': 'lifespan'}, receive, send)
        self.assertEqual(output[0]['type'], 'lifespan.startup.failed')
        self.assertNotIn(self.token, output[0]['message'])

    async def test_runtime_initialization_closes_every_sqlite_connection(self):
        runtime.reset_runtime()
        connections = []
        original = sqlite3.connect
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            connections.append(connection)
            return connection
        with patch('dynamic_bottleneck_round.sync_runtime.sqlite3.connect', side_effect=connect):
            runtime.get_journal()
        self.assertGreaterEqual(len(connections), 3)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute('SELECT 1')

    async def test_runtime_restart_marks_unsettled_round_once(self):
        runtime.reset_runtime()
        from .sync_journal import SyncJournal
        original = SyncJournal.ensure_boot
        boots = []
        def boot(journal, boot_id):
            boots.append(boot_id)
            return original(journal, boot_id)
        with patch.object(runtime, 'PROCESS_BOOT_ID', 'new-process'), patch.object(SyncJournal, 'ensure_boot', boot):
            journal = runtime.get_journal()
            self.assertIs(journal, runtime.get_journal())
        self.assertEqual(boots, ['new-process'])
        self.assertEqual(journal.state('s', 1)['technical_anomaly'], 'worker_restarted_during_round')

    async def test_pending_current_get_prevents_absent_exclusion(self):
        from .decision_ingress import DecisionIngressMiddleware
        self.journal.ensure_round('prepare', 1, expected_codes=['p', 'q'], now_ts=100,
            decision_page_index=91, drain_limit_seconds=10, information_condition='I0',
            allowed_minutes=[474], boot_id=runtime.PROCESS_BOOT_ID)
        self.journal.register_shell('prepare', 1, 'p', self.shell, now_ts=101, page_index=91)
        self.journal.exchange('prepare', 1, 'p', self.shell, kind='ack', now_ts=102)
        token = runtime.sign_control_token('prepare', 1, 'p', self.shell, 91)
        entered, release = asyncio.Event(), asyncio.Event()
        async def business(scope, receive, send):
            entered.set()
            await release.wait()
        with patch('dynamic_bottleneck_round.decision_ingress.time.time', return_value=219):
            task = asyncio.create_task(DecisionIngressMiddleware(business)(
                {'type': 'http', 'method': 'GET',
                 'path': '/p/q/dynamic_bottleneck_round/Decision/91'}, None, None))
            await entered.wait()
        try:
            with patch('dynamic_bottleneck_round.sync_transport.time.time', return_value=221):
                output = await self.request({'token': token, 'kind': 'status'})
            self.assertEqual(output[0]['status'], 200)
            state = self.journal.state('prepare', 1)
            self.assertEqual(state['excluded_codes'], [])
            self.assertIsNone(state['release_ts'])
        finally:
            release.set()
            await task

    async def test_full_body_time_and_incomplete_body_timeout(self):
        body = json.dumps({'token': self.token, 'kind': 'ack'}).encode()
        queue = asyncio.Queue()
        queue.put_nowait({'type': 'http.request', 'body': body[:20], 'more_body': True})
        output = []
        async def send(message):
            output.append(message)
        scope = {'type': 'http', 'method': 'POST', 'path': '/dynamic-room-sync', 'scheme': 'http',
                 'headers': [(b'host', b'testserver'), (b'origin', b'http://testserver')]}
        task = asyncio.create_task(self.app(scope, queue.get, send))
        await asyncio.sleep(.01)
        completed_after = time.time()
        queue.put_nowait({'type': 'http.request', 'body': body[20:]})
        await task
        self.assertGreaterEqual(self.journal.state('s', 1)['ack_ts_by_code']['p'], completed_after)
        self.app.BODY_TIMEOUT = .01
        output.clear()
        queue.put_nowait({'type': 'http.request', 'body': b'{', 'more_body': True})
        await self.app(scope, queue.get, send)
        self.assertEqual(output[0]['status'], 408)

    async def test_signature_verification_never_uses_sqlite_or_stat(self):
        with patch.object(runtime, 'get_journal', side_effect=AssertionError('no initialization')), \
                patch.object(runtime, '_check_file', side_effect=AssertionError('no stat')):
            self.assertEqual(runtime.verify_control_token(self.token)['participant_code'], 'p')

    async def test_close_drains_http_before_stopping_business_loop(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        async def business(scope, receive, send):
            entered.set()
            try:
                await asyncio.to_thread(release.wait, 2)
            finally:
                finished.set()
        self.bridge.app = business
        request = asyncio.create_task(self.request(path='/business'))
        await asyncio.to_thread(entered.wait, 1)
        closing = asyncio.create_task(self.app.aclose())
        try:
            await asyncio.sleep(.02)
            self.assertFalse(finished.is_set())
        finally:
            release.set()
            await asyncio.gather(request, closing, return_exceptions=True)
        self.assertFalse(request.cancelled())
        self.assertIsNone(request.exception())
        self.assertFalse(self.bridge.thread.is_alive())

    async def test_lost_journal_fails_technically_without_recreating_file(self):
        path = Path(self.journal.path)
        path.unlink()
        output = await self.request()
        self.assertEqual(output[0]['status'], 503)
        self.assertIn('technical_anomaly', json.loads(output[-1]['body']))
        self.assertFalse(path.exists())

    async def test_excessively_nested_json_is_rejected_without_trace(self):
        output = await self.request(messages=[{
            'type': 'http.request', 'body': b'[' * 1100 + b'0' + b']' * 1100}])
        self.assertEqual(output[0]['status'], 400)

    async def test_real_starlette_lifespan_failure_is_sanitized_exactly_once(self):
        from starlette.applications import Starlette
        for phase in ('startup', 'shutdown'):
            with self.subTest(phase=phase):
                sentinel = 'PRIVATE_LIFESPAN_SENTINEL'
                async def failing_handler():
                    raise RuntimeError(sentinel)
                business = Starlette(**{'on_' + phase: [failing_handler]})
                app = SyncControlASGI(ThreadedASGI(business))
                self.addAsyncCleanup(app.aclose)
                queue = asyncio.Queue()
                queue.put_nowait({'type': 'lifespan.startup'})
                queue.put_nowait({'type': 'lifespan.shutdown'})
                output = []
                async def send(message):
                    output.append(message)
                await app({'type': 'lifespan'}, queue.get, send)
                failures = [message for message in output if message['type'].endswith('.failed')]
                self.assertTrue(all(sentinel not in message.get('message', '') for message in failures),
                                'downstream traceback must not be forwarded')
                self.assertEqual(len(failures), 1)
                self.assertEqual(failures[0]['type'], 'lifespan.' + phase + '.failed')

    async def test_unexpected_receive_error_is_sanitized_but_cancellation_propagates(self):
        sentinel = 'PRIVATE_RECEIVE_SENTINEL'
        scope = {'type': 'http', 'method': 'POST', 'path': '/dynamic-room-sync', 'scheme': 'http',
                 'headers': [(b'host', b'testserver'), (b'origin', b'http://testserver')]}
        output = []
        async def send(message):
            output.append(message)
        async def broken_receive():
            raise RuntimeError(sentinel)
        try:
            await self.app(scope, broken_receive, send)
        except RuntimeError:
            self.fail('unexpected receive exception escaped the control error boundary')
        self.assertEqual(output[0]['status'], 503)
        self.assertIn('technical_anomaly', json.loads(output[-1]['body']))
        self.assertNotIn(sentinel.encode(), output[-1]['body'])
        output.clear()
        async def cancelled_receive():
            raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.app(scope, cancelled_receive, send)
        self.assertEqual(output, [])


if __name__ == '__main__':
    unittest.main()
