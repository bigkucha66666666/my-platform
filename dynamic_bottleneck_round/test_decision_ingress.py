"""Check that the admission clock is sampled before the oTree app handles POST."""
import asyncio
import unittest
import dynamic_bottleneck_round.decision_ingress as ingress

from dynamic_bottleneck_round.decision_ingress import (
    DecisionIngressMiddleware,
    pending_decision_posts,
    pending_participant_requests,
    failed_participant_requests,
    request_ingress_ts,
    shell_ack_ingress_ts,
    pending_shell_acks,
    finish_shell_ack,
    completed_participant_requests_crossing,
)


class DecisionIngressTests(unittest.TestCase):
    def test_concurrent_requests_for_same_player_have_independent_snapshots(self):
        async def exercise():
            first_ready, second_ready = asyncio.Event(), asyncio.Event()

            async def first(scope, receive, send):
                ingress.remember_decision_preexisting_choice('SAME_FORM', 4, True)
                first_ready.set()
                await second_ready.wait()
                self.assertTrue(ingress.decision_preexisting_choice('SAME_FORM', 4))

            async def second(scope, receive, send):
                await first_ready.wait()
                ingress.remember_decision_preexisting_choice('SAME_FORM', 4, False)
                second_ready.set()
                self.assertFalse(ingress.decision_preexisting_choice('SAME_FORM', 4))

            scope = {'type': 'http', 'method': 'POST',
                     'path': '/p/SAME_FORM/dynamic_bottleneck_round/Decision/9'}
            await asyncio.gather(
                DecisionIngressMiddleware(first)(scope.copy(), None, None),
                DecisionIngressMiddleware(second)(scope.copy(), None, None),
            )
            self.assertFalse(ingress.decision_preexisting_choice('SAME_FORM', 4))
        asyncio.run(exercise())

    def test_failed_request_cleans_its_form_snapshot(self):
        async def failing(scope, receive, send):
            ingress.remember_decision_preexisting_choice('FAILED_FORM', 4, True)
            raise RuntimeError('intentional test failure')

        async def exercise():
            with self.assertRaisesRegex(RuntimeError, 'intentional test failure'):
                await DecisionIngressMiddleware(failing)(
                    {'type': 'http', 'method': 'GET',
                     'path': '/p/FAILED_FORM/dynamic_bottleneck_round/Decision/9'},
                    None, None,
                )
            self.assertFalse(ingress.decision_preexisting_choice('FAILED_FORM', 4))
        asyncio.run(exercise())

    def test_form_snapshot_survives_thread_handoff_and_resets_between_requests(self):
        from starlette.concurrency import run_in_threadpool
        observations = []

        async def send(message):
            return None

        async def exercise():
            for code, initially_chosen in (('FORM_A', True), ('FORM_B', False),
                                           ('FORM_A', False)):
                def worker():
                    first = ingress.remember_decision_preexisting_choice(
                        code, 4, initially_chosen,
                    )
                    # A repeated field query after WTForms population must
                    # retain the request's original snapshot.
                    repeated = ingress.remember_decision_preexisting_choice(
                        code, 4, True,
                    )
                    observations.append((first, repeated,
                        ingress.decision_preexisting_choice(code, 4),
                        ingress.decision_preexisting_choice(code, 5)))

                async def downstream(scope, receive, send):
                    await run_in_threadpool(worker)
                    # Context copies share this request's mutable container.
                    self.assertEqual(ingress.decision_preexisting_choice(code, 4),
                                     initially_chosen)
                await DecisionIngressMiddleware(downstream)(
                    {'type': 'http', 'method': 'POST',
                     'path': f'/p/{code}/dynamic_bottleneck_round/Decision/9'},
                    None, send,
                )
                self.assertFalse(ingress.decision_preexisting_choice(code, 4))

        asyncio.run(exercise())
        self.assertEqual(observations, [
            (True, True, True, False),
            (False, False, False, False),
            (False, False, False, False),
        ])

    def test_shell_completion_is_linked_to_the_acknowledged_get_instance(self):
        from unittest.mock import patch
        tokens = []

        async def downstream(scope, receive, send):
            tokens.append(ingress.current_decision_shell_token())
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        async def send(message):
            return None

        for started, finished in ((100.0, 101.0), (102.0, 103.0)):
            with patch('dynamic_bottleneck_round.decision_ingress.time.time',
                       side_effect=[started, finished]):
                asyncio.run(DecisionIngressMiddleware(downstream)(
                    {'type': 'http', 'method': 'GET',
                     'path': '/p/AUDIT_SHELL/dynamic_bottleneck_round/Decision/9'},
                    None, send,
                ))
        self.assertNotEqual(tokens[0], tokens[1])
        self.assertEqual(ingress.completed_shell_response_ts(
            'AUDIT_SHELL', page_index=9, shell_token=tokens[0],
        ), 101.0)
        self.assertEqual(ingress.completed_shell_response_ts(
            'AUDIT_SHELL', page_index=9, shell_token=tokens[1],
        ), 103.0)

    def test_decision_response_completion_is_queryable_by_page_and_method(self):
        from unittest.mock import patch

        async def downstream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        async def send(message):
            return None

        for method, started, completed in (
            ('GET', 100.0, 101.0), ('POST', 110.0, 112.0),
        ):
            with patch('dynamic_bottleneck_round.decision_ingress.time.time',
                       side_effect=[started, completed]):
                asyncio.run(DecisionIngressMiddleware(downstream)(
                    {'type': 'http', 'method': method,
                     'path': '/p/AUDIT_COMPLETION/dynamic_bottleneck_round/Decision/9'},
                    None, send,
                ))
        self.assertTrue(hasattr(ingress, 'completed_decision_response_ts'))
        self.assertEqual(ingress.completed_decision_response_ts(
            'AUDIT_COMPLETION', page_index=9, method='GET'), 101.0)
        self.assertEqual(ingress.completed_decision_response_ts(
            'AUDIT_COMPLETION', page_index=9, method='POST'), 112.0)
        self.assertIsNone(ingress.completed_decision_response_ts(
            'AUDIT_COMPLETION', page_index=10, method='POST'))

    def test_accepted_post_completion_is_matched_to_its_ingress(self):
        from unittest.mock import patch

        async def downstream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 302, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        async def send(message):
            return None

        for started, finished in ((166.9, 181.0), (182.0, 183.0)):
            with patch('dynamic_bottleneck_round.decision_ingress.time.time',
                       side_effect=[started, finished]):
                asyncio.run(DecisionIngressMiddleware(downstream)(
                    {'type': 'http', 'method': 'POST',
                     'path': '/p/AUDIT_MATCH/dynamic_bottleneck_round/Decision/9'},
                    None, send,
                ))
        self.assertEqual(ingress.completed_decision_response_ts(
            'AUDIT_MATCH', page_index=9, method='POST', ingress_ts=166.9,
        ), 181.0)
        self.assertIsNone(ingress.completed_decision_response_ts(
            'AUDIT_MATCH', page_index=9, method='POST', ingress_ts=170,
        ))
        self.assertTrue(ingress.completed_decision_posts_overrun(
            'AUDIT_MATCH', page_index=9, deadline_ts=167,
            drain_limit_seconds=10, ingress_ts=166.9,
        ))

    def test_decision_post_is_registered_until_response_completion(self):
        seen = []
        gate = asyncio.Event()

        async def downstream(scope, receive, send):
            seen.append(request_ingress_ts())
            self.assertEqual(len(pending_decision_posts('P001', deadline_ts=1e20)), 1)
            self.assertEqual(len(pending_participant_requests('P001')), 1)
            await gate.wait()
            await send({'type': 'http.response.start', 'status': 302, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        async def exercise():
            app = DecisionIngressMiddleware(downstream)
            async def send(message):
                return None
            scope = {
                'type': 'http', 'method': 'POST',
                'path': '/p/P001/dynamic_bottleneck_round/Decision/9',
            }
            task = asyncio.create_task(app(scope, None, send))
            await asyncio.sleep(0)
            self.assertEqual(len(pending_decision_posts('P001', deadline_ts=1e20)), 1)
            gate.set()
            await task
            self.assertEqual(pending_decision_posts('P001', deadline_ts=1e20), ())
            self.assertEqual(pending_participant_requests('P001'), ())

        asyncio.run(exercise())
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], float)

    def test_other_pages_are_not_registered(self):
        async def downstream(scope, receive, send):
            self.assertIsNone(request_ingress_ts())
            self.assertEqual(pending_decision_posts('P001', deadline_ts=1e20), ())

        asyncio.run(DecisionIngressMiddleware(downstream)(
            {'type': 'http', 'method': 'GET',
             'path': '/p/P001/dynamic_bottleneck_round/Decision/9'},
            None, None,
        ))

    def test_other_participant_page_is_tracked_for_no_show_classification(self):
        async def downstream(scope, receive, send):
            self.assertIsNone(request_ingress_ts())
            self.assertEqual(len(pending_participant_requests('P002')), 1)

        asyncio.run(DecisionIngressMiddleware(downstream)(
            {'type': 'http', 'method': 'GET',
             'path': '/p/P002/dynamic_bottleneck_round/RoundStartSync/9'},
            None, None,
        ))
        self.assertEqual(pending_participant_requests('P002'), ())

    def test_ingress_time_survives_starlette_middleware_and_threadpool(self):
        from starlette.applications import Starlette
        from starlette.middleware.base import BaseHTTPMiddleware
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route
        from starlette.concurrency import run_in_threadpool

        async def endpoint(request):
            timestamp = await run_in_threadpool(request_ingress_ts)
            return PlainTextResponse(str(timestamp))

        class PassThrough(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                return await call_next(request)

        downstream = Starlette(routes=[Route(
            '/p/{participant_code}/dynamic_bottleneck_round/Decision/{page_index}',
            endpoint, methods=['POST'],
        )])
        downstream.add_middleware(PassThrough)
        app = DecisionIngressMiddleware(downstream)

        async def exercise():
            sent = []
            request_delivered = False

            async def receive():
                nonlocal request_delivered
                if not request_delivered:
                    request_delivered = True
                    return {'type': 'http.request', 'body': b'', 'more_body': False}
                await asyncio.sleep(0)
                return {'type': 'http.disconnect'}

            async def send(message):
                sent.append(message)

            await app({
                'type': 'http', 'method': 'POST', 'path':
                '/p/P003/dynamic_bottleneck_round/Decision/9',
                'root_path': '', 'scheme': 'http', 'query_string': b'',
                'headers': [], 'server': ('testserver', 80), 'client': ('testclient', 1),
            }, receive, send)
            body = b''.join(item.get('body', b'') for item in sent)
            self.assertGreater(float(body), 0)

        asyncio.run(exercise())

    def test_server_error_is_distinguishable_from_human_no_show(self):
        async def downstream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 500, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        async def send(message):
            return None

        asyncio.run(DecisionIngressMiddleware(downstream)(
            {'type': 'http', 'method': 'GET',
             'path': '/p/ERROR_001/dynamic_bottleneck_round/Decision/9'},
            None, send,
        ))
        self.assertEqual(len(failed_participant_requests('ERROR_001')), 1)

    def test_live_ack_timestamp_is_captured_before_downstream_lock(self):
        async def downstream(scope, receive, send):
            message = await receive()
            self.assertEqual(message['type'], 'websocket.receive')
            stamped = shell_ack_ingress_ts()
            self.assertIsInstance(stamped, float)
            self.assertEqual(len(pending_shell_acks('ACK001', page_index=9,
                                                    deadline_ts=1e20)), 1)
            finish_shell_ack()
            self.assertEqual(pending_shell_acks('ACK001', page_index=9,
                                                deadline_ts=1e20), ())

        async def receive():
            return {'type': 'websocket.receive', 'text': '{"kind":"ack"}'}

        asyncio.run(DecisionIngressMiddleware(downstream)(
            {'type': 'websocket', 'path': '/live', 'query_string':
             b'participant_code=ACK001&page_name=Decision&page_index=9'},
            receive, None,
        ))

    def test_failed_initial_decision_get_is_found_after_state_rollback(self):
        async def downstream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 500, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        async def send(message):
            return None

        asyncio.run(DecisionIngressMiddleware(downstream)(
            {'type': 'http', 'method': 'GET',
             'path': '/p/FAILED_FIRST/dynamic_bottleneck_round/Decision/99'},
            None, send,
        ))
        self.assertEqual(len(failed_participant_requests(
            'FAILED_FIRST', page_index=99,
        )), 1)
        self.assertEqual(failed_participant_requests(
            'FAILED_FIRST', page_index=100,
        ), ())

    def test_completed_queued_page_remains_visible_after_pending_is_cleared(self):
        from unittest.mock import patch
        async def downstream(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})

        async def send(message):
            return None

        with patch('dynamic_bottleneck_round.decision_ingress.time.time',
                   side_effect=[100.0, 162.0]):
            asyncio.run(DecisionIngressMiddleware(downstream)(
                {'type': 'http', 'method': 'GET',
                 'path': '/p/SLOW_PRIOR/dynamic_bottleneck_round/RoundStartSync/8'},
                None, send,
            ))
        self.assertEqual(pending_participant_requests('SLOW_PRIOR'), ())
        self.assertEqual(completed_participant_requests_crossing(
            'SLOW_PRIOR', after_ts=100, deadline_ts=160), ((100.0, 162.0),))


if __name__ == '__main__':
    unittest.main()
