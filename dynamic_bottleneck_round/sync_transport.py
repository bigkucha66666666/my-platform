"""Separate HTTP control lane and one thread/event-loop for all oTree work."""
import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
import contextvars
import json
import re
import threading
import time
from urllib.parse import urlsplit

from . import sync_runtime as runtime
from .sync_journal import SyncRequestRejected
from .decision_ingress import (pending_participant_requests, pending_shell_acks,
    failed_participant_requests, completed_participant_requests_crossing)

_admission_ticket = contextvars.ContextVar('control_admission_ticket', default=None)


class ControlIdentityRejected(PermissionError):
    pass


class ThreadedASGI:
    """Keep oTree's original global lock and all business ASGI on one loop."""
    def __init__(self, app):
        self.app = app
        self.loop = None
        self.thread = None
        self._start_lock = threading.Lock()
        self._closed = False
        self._http_futures = set()
        self._http_lock = threading.Lock()

    def _start(self):
        with self._start_lock:
            if self._closed:
                raise RuntimeError('business loop is closed')
            if self.thread is not None:
                return
            ready = threading.Event()
            errors = []
            def run():
                try:
                    self.loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self.loop)
                except BaseException as error:
                    errors.append(error)
                    ready.set()
                    return
                ready.set()
                try:
                    self.loop.run_forever()
                finally:
                    pending = asyncio.all_tasks(self.loop)
                    for task in pending:
                        task.cancel()
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                    self.loop.run_until_complete(self.loop.shutdown_default_executor())
                    self.loop.close()
            self.thread = threading.Thread(target=run, name='room-business', daemon=True)
            self.thread.start()
            if not ready.wait(5) or errors:
                raise RuntimeError('business loop failed to start')

    async def __call__(self, scope, receive, send):
        self._start()
        owner_loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        async def on_owner(callback, *args):
            # oTree sync_group_send uses asyncio.run in arbitrary worker threads.
            # Always bridge from the caller's CURRENT loop, never a captured one.
            if asyncio.get_running_loop() is owner_loop:
                return await callback(*args)
            async def capture():
                result = await callback(*args)
                return result, contextvars.copy_context()
            future = asyncio.run_coroutine_threadsafe(capture(), owner_loop)
            result, updated_context = await asyncio.wrap_future(future)
            # Ingress middleware records Live ACK time while receive executes.
            # Return those context changes into the actual business task.
            for variable, value in updated_context.items():
                variable.set(value)
            return result
        async def business():
            return await self.app(scope, lambda: on_owner(receive),
                                  lambda message: on_owner(send, message))
        future = context.run(asyncio.run_coroutine_threadsafe, business(), self.loop)
        if scope['type'] == 'http':
            with self._http_lock:
                self._http_futures.add(future)
            def http_finished(completed):
                with self._http_lock:
                    self._http_futures.discard(completed)
            future.add_done_callback(http_finished)
        wrapped = asyncio.wrap_future(future)
        try:
            if scope['type'] == 'http':
                return await asyncio.shield(wrapped)
            return await wrapped
        except asyncio.CancelledError:
            if scope['type'] == 'http':
                # Cancelling oTree's await of a synchronous ORM callback could
                # release its global lock while that callback is still running.
                # Drain HTTP work before ingress middleware releases ownership.
                while not wrapped.done():
                    try:
                        await asyncio.shield(wrapped)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if wrapped.done() and not wrapped.cancelled():
                    wrapped.exception()
            else:
                future.cancel()
            raise

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        with self._http_lock:
            pending_http = tuple(self._http_futures)
        if pending_http:
            await asyncio.gather(*(asyncio.shield(asyncio.wrap_future(future))
                                   for future in pending_http), return_exceptions=True)
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            await asyncio.to_thread(self.thread.join, 10)
            if self.thread.is_alive():
                raise RuntimeError('business loop did not stop')


class SyncControlASGI:
    MAX_BODY = 16384
    BODY_TIMEOUT = 5

    def __init__(self, app, *, workers=4, max_pending=128):
        self.app = app
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='room-control')
        self._slots = threading.BoundedSemaphore(max_pending)
        self._flush_lock = threading.Lock()
        self._fault_flush_scheduled = False
        self._closed = False
        runtime.set_control_transport_active(True)
        self._accepted = 0
        self._drained = threading.Event()
        self._drained.set()

    def _schedule_fault_flush(self):
        # At most one extra queued/running persistence job beyond admission slots.
        # This function is safe on the main loop and in a completion callback.
        if not runtime.pending_fault_rounds():
            return
        with self._flush_lock:
            if self._closed or self._fault_flush_scheduled:
                return
            self._fault_flush_scheduled = True
            try:
                self.executor.submit(self._flush_faults)
            except RuntimeError:
                self._fault_flush_scheduled = False

    def _flush_faults(self):
        attempted = set(runtime.pending_fault_rounds())
        try:
            for session, number in attempted:
                runtime.persist_control_fault(session, number)
        finally:
            with self._flush_lock:
                self._fault_flush_scheduled = False
            # Only new rounds arriving during this pass need another pass.
            # Failed writes stay dirty for the next admission/capacity event;
            # they must not cause a self-rescheduling retry loop.
            if set(runtime.pending_fault_rounds()) - attempted:
                self._schedule_fault_flush()

    def _shutdown_workers(self):
        # Drain accepted writes and the owned coalesced job, then give any fault
        # added during shutdown one final best-effort worker-side persistence.
        # Successors are submitted by completed heads. Keep the executor open
        # until every accepted entry (including actor queues) has finished.
        self._drained.wait()
        self.executor.shutdown(wait=True, cancel_futures=False)
        for session, number in runtime.pending_fault_rounds():
            runtime.persist_control_fault(session, number)

    async def _response(self, send, status, body):
        encoded = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
        await send({'type': 'http.response.start', 'status': status, 'headers': [
            (b'content-type', b'application/json'), (b'cache-control', b'no-store')]})
        await send({'type': 'http.response.body', 'body': encoded})

    @staticmethod
    def _origin(scope):
        headers = scope.get('headers', [])
        origins = [value.decode('ascii') for key, value in headers if key.lower() == b'origin']
        hosts = [value.decode('ascii') for key, value in headers if key.lower() == b'host']
        if len(origins) != 1 or len(hosts) != 1:
            return False
        origin = urlsplit(origins[0])
        return (origin.scheme == scope.get('scheme', 'http') and origin.netloc == hosts[0]
                and not origin.path and not origin.query and not origin.fragment
                and origin.username is None)

    async def _body(self, receive):
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return None
            if message['type'] != 'http.request':
                raise ValueError('invalid request')
            body.extend(message.get('body', b''))
            if len(body) > self.MAX_BODY:
                raise OverflowError
            if not message.get('more_body', False):
                # No await after a complete body: validate and give valid work
                # its pending ticket before wait_for relays back to the caller.
                return self._admit_body(body)

    def _admit_body(self, body):
        queue_rejected = False
        # The native final commit uses this same short registry boundary. A
        # complete valid body becomes pending before either side can close it.
        with runtime.control_body_boundary():
            arrival = time.time()
            try:
                data = json.loads(body)
                if not isinstance(data, dict) or data.get('kind') not in {'ack', 'status', 'operative', 'decision'}:
                    raise ValueError
                if data['kind'] == 'decision' and type(data.get('departure_minute')) is not int:
                    raise ValueError
            except (ValueError, TypeError, UnicodeError, RecursionError):
                return {'status': 400, 'body': {'error': 'invalid_json_request'}}
            if not runtime.journal_initialized():
                return {'status': 503, 'body': {'technical_anomaly': 'control_unavailable'}}
            try:
                payload = runtime.verify_control_token(data.get('token'))
            except ValueError:
                return {'status': 403, 'body': {'error': 'invalid_control_token'}}
            try:
                runtime.validate_control_request(payload, data)
            except PermissionError:
                return {'status': 403, 'body': {'error': 'invalid_control_identity'}}
            except ValueError:
                return {'status': 400, 'body': {'error': 'control_request_rejected'}}
            if self._closed or not self._slots.acquire(blocking=False):
                runtime.latch_control_fault(payload, 'control_queue_admission_failure')
                queue_rejected = True
            else:
                ticket = None
                try:
                    ticket = runtime.register_pending(payload, data['kind'], arrival)
                    future = Future()
                    dispatch = lambda: self._submit_admitted(ticket, payload, data, arrival, future)
                    runnable = runtime.admit_control_work(ticket, payload, data, arrival, dispatch)
                    self._accepted += 1
                    self._drained.clear()
                except Exception:
                    runtime.latch_control_fault(payload, 'control_queue_admission_failure')
                    if ticket is not None:
                        runtime.finish_pending(ticket)
                    self._slots.release()
                    queue_rejected = True
        if queue_rejected:
            self._schedule_fault_flush()
            return {'status': 503, 'body': {'technical_anomaly': 'control_unavailable'}}
        if runnable is not None:
            runnable()
        return {'future': future, 'payload': payload, 'data': data}

    def _submit_admitted(self, ticket, payload, data, arrival, result):
        def run():
            context = _admission_ticket.set(ticket)
            try:
                return self._exchange(payload, data, arrival)
            finally:
                _admission_ticket.reset(context)
        def completed(work):
            # _exchange installs faults before this pending release. No executor
            # submission, callbacks, or disk work inside the registry boundary.
            with runtime.control_body_boundary():
                successor = runtime.finish_pending(ticket)
                self._slots.release()
                self._accepted -= 1
                if not self._accepted:
                    self._drained.set()
            try:
                result.set_result(work.result())
            except Exception as error:
                result.set_exception(error)
            if successor is not None:
                successor()
            self._schedule_fault_flush()
        try:
            work = self.executor.submit(run)
        except Exception as error:
            runtime.latch_control_fault(payload, 'control_queue_admission_failure')
            work = Future()
            work.set_exception(error)
        work.add_done_callback(completed)

    def _retain_admitted(self, payload, data, arrival):
        ticket = _admission_ticket.get()
        if data['kind'] == 'decision' and runtime.admitted_before_fault(ticket, payload, data, arrival):
            runtime.get_journal().retain_admitted_decision(
                payload, data, arrival, admission_ticket=ticket)

    def _exchange(self, payload, data, arrival):
        session, number = payload['session_code'], payload['round_number']
        try:
            reason = runtime.persist_control_fault(session, number)
            if reason:
                self._retain_admitted(payload, data, arrival)
                return {'technical_anomaly': reason}
            result = self._exchange_registered(payload, data, arrival)
            reason = runtime.persist_control_fault(session, number)
            return {'technical_anomaly': reason} if reason else result
        except (ControlIdentityRejected, SyncRequestRejected):
            raise
        except Exception:
            # Runs on the persistence worker even when HTTP has been cancelled.
            # Install the interlock before the done callback releases pending.
            runtime.latch_control_fault(payload, 'control_persistence_failure')
            runtime.persist_control_fault(session, number)
            try:
                # The failed head still owns its live admission capability.
                # Preserve its evidence before a successor can retain another
                # value; an unavailable audit must not replace this failure.
                self._retain_admitted(payload, data, arrival)
            except Exception:
                pass
            raise

    def _exchange_registered(self, payload, data, arrival):
        journal = runtime.get_journal()
        session, number, code = (payload[key] for key in ('session_code', 'round_number', 'participant_code'))
        # Authenticate the registered shell before any state advancement.
        with journal._connection() as connection:
            row = connection.execute('SELECT page_index FROM sync_shells WHERE session_code=? '
                'AND round_number=? AND participant_code=? AND shell_token=?',
                (session, number, code, payload['shell_token'])).fetchone()
        if row != (payload['page_index'],):
            raise ControlIdentityRejected('unregistered control identity')
        state = journal.state(session, number)
        page = state['decision_page_index']
        if page != payload['page_index']:
            raise ControlIdentityRejected('invalid control page')
        deadline = state['prepare_deadline_ts']
        pending = set(runtime.pending_control_codes(session, number, kind='ack', before_ts=deadline))
        failed, crossed = [], []
        for expected in state['expected_codes']:
            if (pending_participant_requests(expected, before_ts=deadline, page_index=page)
                    or pending_shell_acks(expected, page_index=page, deadline_ts=deadline)):
                pending.add(expected)
            if failed_participant_requests(expected, page_index=page):
                failed.append(expected)
            if completed_participant_requests_crossing(expected,
                    after_ts=state['prepare_started_ts'], deadline_ts=deadline, page_index=page):
                crossed.append(expected)
        reason = runtime.control_fault(session, number) or state['technical_anomaly']
        if reason:
            runtime.latch_control_fault(payload, reason)
            self._retain_admitted(payload, data, arrival)
            return {'technical_anomaly': reason}
        try:
            result, manual_overrun_limit = journal.exchange(session, number, code, payload['shell_token'],
                kind=data['kind'], now_ts=arrival, processing_ts=time.time(),
                departure_minute=data.get('departure_minute'), pending_codes=pending,
                failed_codes=failed, crossed_codes=crossed,
                _include_manual_overrun_limit=True)
        except SyncRequestRejected:
            # A fault can land after the precheck but before the transaction.
            # Retain trusted evidence even when that transaction rejects manual.
            reason = runtime.control_fault(session, number) or journal.state(session, number)['technical_anomaly']
            if not reason:
                raise
            runtime.latch_control_fault(payload, reason)
            self._retain_admitted(payload, data, arrival)
            return {'technical_anomaly': reason}
        # Replay/status can succeed after an anomaly lands inside exchange.
        # Adopt that durable snapshot before the worker releases its admission;
        # _exchange persists the interlock through the same completion path.
        reason = result.get('technical_anomaly')
        if reason:
            runtime.latch_control_fault(payload, reason)
            self._retain_admitted(payload, data, arrival)
        # The journal's processing sample precedes its SQLite UPDATE/commit.
        # Keep this worker's pending ticket until a late commit is faulted.
        if manual_overrun_limit is not None and time.time() > manual_overrun_limit:
            runtime.latch_control_fault(payload, 'decision_control_queue_overrun')
            runtime.persist_control_fault(session, number)
        return result

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            started = False
            failure_sent = False
            async def lifecycle_send(message):
                nonlocal started, failure_sent
                if message['type'] == 'lifespan.startup.complete':
                    started = True
                elif message['type'] in {'lifespan.startup.failed', 'lifespan.shutdown.failed'}:
                    if failure_sent:
                        return
                    failure_sent = True
                    # Starlette includes a full traceback in its failure event.
                    message = {'type': message['type'],
                               'message': 'Room synchronization lifecycle failed'}
                await send(message)
            try:
                await asyncio.get_running_loop().run_in_executor(self.executor, runtime.get_journal)
                return await self.app(scope, receive, lifecycle_send)
            except Exception:
                phase = 'shutdown' if started else 'startup'
                await lifecycle_send({'type': 'lifespan.' + phase + '.failed'})
                return
            finally:
                await self.aclose()
        if scope['type'] != 'http' or scope.get('path') != '/dynamic-room-sync':
            return await self.app(scope, receive, send)
        if scope.get('method') != 'POST':
            return await self._response(send, 405, {'error': 'method_not_allowed'})
        try:
            valid_origin = self._origin(scope)
        except (ValueError, UnicodeError):
            valid_origin = False
        if not valid_origin:
            return await self._response(send, 403, {'error': 'invalid_origin'})
        try:
            received = await asyncio.wait_for(self._body(receive), self.BODY_TIMEOUT)
            if received is None:
                return
        except OverflowError:
            return await self._response(send, 413, {'error': 'body_too_large'})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return await self._response(send, 400, {'error': 'invalid_json_request'})
        except asyncio.TimeoutError:
            return await self._response(send, 408, {'error': 'incomplete_request'})
        except Exception:
            return await self._response(send, 503, {'technical_anomaly': 'control_unavailable'})
        if 'status' in received:
            return await self._response(send, received['status'], received['body'])
        future, payload, data = (received[key] for key in ('future', 'payload', 'data'))
        try:
            wrapped = asyncio.wrap_future(future)
            # Retrieve failures even when the response waiter disconnects.
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            try:
                # A cancelled HTTP waiter must not cancel accepted queued work.
                result = await asyncio.shield(wrapped)
            except ControlIdentityRejected:
                return await self._response(send, 403, {'error': 'invalid_control_identity'})
            except SyncRequestRejected:
                return await self._response(send, 400, {'error': 'control_request_rejected'})
            reason = runtime.control_fault(payload['session_code'], payload['round_number'])
            if reason:
                return await self._response(send, 503, {'technical_anomaly': reason})
            clock_id, clock_token = data.get('clock_id'), data.get('clock_token')
            if (type(clock_id) is int and 0 < clock_id <= 9007199254740991
                    and isinstance(clock_token, str) and re.fullmatch(r'[0-9a-f]{32}', clock_token)):
                result.update(clock_id=clock_id, clock_token=clock_token)
            return await self._response(send, 200, result)
        except Exception:
            return await self._response(send, 503, {'technical_anomaly': 'control_unavailable'})

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        try:
            child = self.app
            while child is not None:
                if isinstance(child, ThreadedASGI):
                    await child.aclose()
                    break
                child = getattr(child, 'app', None)
        finally:
            await asyncio.to_thread(self._shutdown_workers)
            runtime.set_control_transport_active(False)
