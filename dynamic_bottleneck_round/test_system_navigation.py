"""Authenticated timeout requests must never impersonate browser readiness."""
import asyncio
import unittest
from unittest.mock import patch
from urllib.request import Request

from timeout_navigation import (TimeoutOriginMiddleware, build_timeout_opener,
                                is_timeout_navigation, make_timeout_headers)

SECRET = 'isolated-secret-' + 'x' * 48
TARGET = '/p/abc/dynamic_bottleneck_round/Decision/91?x=1'


class TimeoutOriginTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, middleware, headers, *, method='GET', path=TARGET,
                     client=('127.0.0.1', 1)):
        async def receive():
            return {'type': 'http.request', 'body': b''}
        async def send(message):
            pass
        path, _, query = path.partition('?')
        return await middleware({'type': 'http', 'method': method, 'path': path,
            'raw_path': path.encode(), 'query_string': query.encode(),
            'headers': [(k.encode(), v.encode()) for k, v in headers.items()],
            'client': client}, receive, send)

    async def test_signature_replay_method_path_expiry_and_untrusted_headers(self):
        seen = []
        async def app(scope, receive, send):
            seen.append(is_timeout_navigation())
        middleware = TimeoutOriginMiddleware(app, secret=SECRET)
        headers = make_timeout_headers(SECRET, 'GET', TARGET)
        await self.invoke(middleware, headers)
        await self.invoke(middleware, headers)  # replay
        await self.invoke(middleware, make_timeout_headers(SECRET, 'POST', TARGET))
        await self.invoke(middleware, make_timeout_headers(SECRET, 'GET', TARGET + 'bad'))
        await self.invoke(middleware, make_timeout_headers(SECRET, 'GET', TARGET, now=1))
        await self.invoke(middleware, {'x-project-timeout-authorization': 'forged',
                                    'user-agent': 'Python-urllib/3.10'})
        await self.invoke(middleware, {})  # localhost is still a real browser
        self.assertEqual(seen, [True, False, False, False, False, False, False])
        self.assertFalse(is_timeout_navigation())

    async def test_context_copies_to_threads_and_resets_after_failure_and_cancel(self):
        async def app(scope, receive, send):
            self.assertTrue(is_timeout_navigation())
            self.assertTrue(await asyncio.to_thread(is_timeout_navigation))
            raise RuntimeError('fixture')
        middleware = TimeoutOriginMiddleware(app, secret=SECRET)
        with self.assertRaises(RuntimeError):
            await self.invoke(middleware, make_timeout_headers(SECRET, 'GET', TARGET))
        self.assertFalse(is_timeout_navigation())
        reached = asyncio.Event()
        async def wait(scope, receive, send):
            reached.set()
            await asyncio.Future()
        middleware = TimeoutOriginMiddleware(wait, secret=SECRET)
        task = asyncio.create_task(self.invoke(middleware, make_timeout_headers(SECRET, 'GET', TARGET)))
        await reached.wait()
        self.assertFalse(is_timeout_navigation())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(is_timeout_navigation())

    async def test_concurrent_browser_and_worker_have_separate_contexts(self):
        values = []
        async def app(scope, receive, send):
            before = is_timeout_navigation()
            await asyncio.sleep(.01)
            values.append((before, is_timeout_navigation()))
        middleware = TimeoutOriginMiddleware(app, secret=SECRET)
        await asyncio.gather(self.invoke(middleware, {}), self.invoke(middleware,
            make_timeout_headers(SECRET, 'GET', TARGET)))
        self.assertCountEqual(values, [(True, True), (False, False)])

    async def test_no_secret_never_trusts_a_header(self):
        async def app(scope, receive, send):
            self.assertFalse(is_timeout_navigation())
        await self.invoke(TimeoutOriginMiddleware(app, secret=''),
                          make_timeout_headers(SECRET, 'GET', TARGET))

    async def test_explicit_shutdown_reaches_control_workers(self):
        from unittest.mock import AsyncMock
        child = AsyncMock()
        await TimeoutOriginMiddleware(child, secret=SECRET).aclose()
        child.aclose.assert_awaited_once_with()


class TimeoutTransportTests(unittest.TestCase):
    def test_non_http_requests_are_blocked_before_any_external_handler(self):
        opener = build_timeout_opener(19999, SECRET)
        with self.assertRaises(ValueError):
            opener.open('file:///nonexistent-project-timeout-test')
        with patch('urllib.request.FTPHandler.ftp_open', side_effect=AssertionError('network handler reached')):
            with self.assertRaises(ValueError):
                opener.open('ftp://example.com/')

    def test_redirect_request_is_freshly_signed_and_cross_origin_is_rejected(self):
        opener = build_timeout_opener(19999, SECRET)
        signer = next(h for h in opener.handlers if hasattr(h, 'timeout_signer'))
        first = signer.http_request(Request('http://127.0.0.1:19999' + TARGET, data=b'x=1'))
        second = signer.http_request(Request('http://127.0.0.1:19999' + TARGET))
        self.assertNotEqual(first.get_header('X-project-timeout-authorization'),
                            second.get_header('X-project-timeout-authorization'))
        with self.assertRaises(ValueError):
            signer.http_request(Request('http://example.com/' , data=b'x=1'))

    def test_real_threaded_business_loop_preserves_origin_and_ingress_is_skipped(self):
        from dynamic_bottleneck_round.sync_transport import ThreadedASGI
        from dynamic_bottleneck_round.decision_ingress import (
            DecisionIngressMiddleware, current_decision_shell_token,
            pending_participant_requests, failed_participant_requests,
            page_ingress_ts)
        code = 'system-origin-test'
        path = f'/p/{code}/dynamic_bottleneck_round/Decision/91'
        async def business(scope, receive, send):
            self.assertTrue(is_timeout_navigation())
            self.assertTrue(await asyncio.to_thread(is_timeout_navigation))
            self.assertIsNone(page_ingress_ts())
            self.assertIsNone(current_decision_shell_token())
            self.assertFalse(pending_participant_requests(code))
            raise RuntimeError('internal request failure')
        threaded = ThreadedASGI(business)
        app = TimeoutOriginMiddleware(DecisionIngressMiddleware(threaded), secret=SECRET)
        async def exercise():
            async def receive(): return {'type': 'http.request', 'body': b''}
            async def send(message): pass
            with self.assertRaises(RuntimeError):
                await app({'type': 'http', 'method': 'GET', 'path': path,
                    'headers': [(k.encode(), v.encode()) for k, v in
                        make_timeout_headers(SECRET, 'GET', path).items()]}, receive, send)
            self.assertFalse(failed_participant_requests(code))
            self.assertFalse(is_timeout_navigation())
        try:
            asyncio.run(exercise())
        finally:
            asyncio.run(threaded.aclose())
