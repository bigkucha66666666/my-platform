"""Capture decision POST arrival before oTree's global HTTP transaction lock.

Only pending requests are process-local.  Release/deadline state belongs in
Session.vars; the deployment must use the project ASGI entrypoint with one
worker before this middleware can be relied on for formal rounds.
"""
from contextvars import ContextVar
import json
import re
import secrets
from threading import Lock
import time
from urllib.parse import parse_qs


_DECISION_POST_PATH = re.compile(
    r'^/p/([^/]+)/dynamic_bottleneck_round/Decision/[0-9]+/?$'
)
_PARTICIPANT_PATH = re.compile(r'^/p/([^/]+)/dynamic_bottleneck_round/')
_PAGE_INDEX = re.compile(
    r'^/p/[^/]+/dynamic_bottleneck_round/([^/]+)/([0-9]+)/?$'
)
_ingress_ts = ContextVar('dynamic_decision_ingress_ts', default=None)
_page_ingress_ts = ContextVar('dynamic_page_ingress_ts', default=None)
_ack_ingress_ts = ContextVar('dynamic_shell_ack_ingress_ts', default=None)
_ack_request_id = ContextVar('dynamic_shell_ack_request_id', default=None)
_shell_token = ContextVar('dynamic_shell_get_token', default=None)
_decision_form_choices = ContextVar('dynamic_decision_form_choices', default=None)
_pending_lock = Lock()
_pending = {}
_failed = {}
_pending_acks = {}
_completed = {}
_completed_decision_posts = {}
_completed_shell_responses = {}
_middleware_active = False


def ingress_middleware_active():
    return _middleware_active


def request_ingress_ts():
    """Trusted request-arrival timestamp for the current Decision POST."""
    return _ingress_ts.get()


def remember_decision_preexisting_choice(participant_code, round_number, preexisting):
    """Snapshot before form population; repeated queries retain the first value.

    The middleware installs a fresh dictionary per HTTP request. oTree copies
    the context into its synchronous worker; both copies share this dictionary.
    No model attribute or participant/session variable is persisted.
    """
    choices = _decision_form_choices.get()
    if choices is None:
        return bool(preexisting)
    return choices.setdefault((participant_code, int(round_number)), bool(preexisting))


def decision_preexisting_choice(participant_code, round_number):
    choices = _decision_form_choices.get()
    return bool(choices and choices.get((participant_code, int(round_number)), False))


def page_ingress_ts():
    """Trusted arrival time for the current participant page GET/POST."""
    return _page_ingress_ts.get()


def shell_ack_ingress_ts():
    """Trusted WebSocket frame arrival time, before oTree's Live lock."""
    return _ack_ingress_ts.get()


def current_decision_shell_token():
    """Unique token for the current Decision GET, embedded only in its HTML."""
    return _shell_token.get()


def completed_shell_response_ts(participant_code, *, page_index, shell_token):
    if not shell_token or page_index is None:
        return None
    with _pending_lock:
        return _completed_shell_responses.get(
            (participant_code, page_index, shell_token)
        )


def finish_shell_ack():
    request_id = _ack_request_id.get()
    if request_id is None:
        return
    with _pending_lock:
        for code, requests in tuple(_pending_acks.items()):
            requests.pop(request_id, None)
            if not requests:
                _pending_acks.pop(code, None)
    _ack_request_id.set(None)


def pending_shell_acks(participant_code, *, page_index, deadline_ts):
    with _pending_lock:
        return tuple(
            timestamp for timestamp, request_page_index in
            _pending_acks.get(participant_code, {}).values()
            if (page_index is None or request_page_index == page_index)
            and timestamp < float(deadline_ts)
        )


def pending_decision_posts(participant_code, *, deadline_ts):
    """In-flight POSTs admitted before the shared decision deadline."""
    with _pending_lock:
        return tuple(
            timestamp for timestamp, is_decision_post
            in _pending.get(participant_code, {}).values()
            if is_decision_post and timestamp < float(deadline_ts)
        )


def pending_participant_requests(participant_code, *, before_ts=float('inf')):
    with _pending_lock:
        return tuple(
            timestamp for timestamp, _ in _pending.get(participant_code, {}).values()
            if timestamp < float(before_ts)
        )


def failed_participant_requests(participant_code, *, after_ts=0,
                                before_ts=float('inf'), page_index=None):
    with _pending_lock:
        return tuple(
            timestamp for timestamp, request_page_index in _failed.get(participant_code, ())
            if float(after_ts) <= timestamp < float(before_ts)
            and (page_index is None or request_page_index == page_index)
        )


def completed_participant_requests_crossing(participant_code, *,
                                            after_ts, deadline_ts):
    """Requests that reached the server before cutoff but finished after it."""
    with _pending_lock:
        return tuple(
            (started, finished) for started, finished, _page_index, _page_name,
            _method in
            _completed.get(participant_code, ())
            if float(after_ts) <= started < float(deadline_ts) <= finished
        )


def completed_decision_response_ts(participant_code, *, page_index, method,
                                   ingress_ts=None):
    """Completed Decision response, optionally matched to the accepted POST."""
    if page_index is None or method not in {'GET', 'POST'}:
        return None
    with _pending_lock:
        if method == 'POST' and ingress_ts is not None:
            return _completed_decision_posts.get(
                (participant_code, page_index, float(ingress_ts))
            )
        completed = (
            finished for _started, finished, request_page_index, page_name,
            request_method in _completed.get(participant_code, ())
            if request_page_index == page_index
            and page_name == 'Decision' and request_method == method
        )
        return max(completed, default=None)


def completed_decision_posts_overrun(participant_code, *, page_index,
                                     deadline_ts, drain_limit_seconds,
                                     ingress_ts=None):
    """A pre-deadline POST completed only after the permitted queue drain."""
    if page_index is None or drain_limit_seconds is None:
        return False
    with _pending_lock:
        return any(
            code == participant_code and index == page_index
            and started < float(deadline_ts)
            and finished > float(deadline_ts) + float(drain_limit_seconds)
            and (ingress_ts is None or started == float(ingress_ts))
            for (code, index, started), finished
            in _completed_decision_posts.items()
        )


class DecisionIngressMiddleware:
    def __init__(self, app):
        global _middleware_active
        self.app = app
        _middleware_active = True

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'websocket' and scope.get('path') == '/live':
            query = parse_qs(scope.get('query_string', b'').decode('utf-8', 'replace'))
            if query.get('page_name') != ['Decision']:
                return await self.app(scope, receive, send)
            code = (query.get('participant_code') or [None])[0]
            try:
                page_index = int((query.get('page_index') or [''])[0])
            except ValueError:
                return await self.app(scope, receive, send)
            if not code:
                return await self.app(scope, receive, send)
            owned_acks = set()

            async def receive_observed():
                message = await receive()
                _ack_ingress_ts.set(None)
                _ack_request_id.set(None)
                if message.get('type') == 'websocket.receive':
                    try:
                        data = json.loads(message.get('text', ''))
                    except (TypeError, ValueError):
                        data = None
                    if isinstance(data, dict) and data.get('kind') == 'ack':
                        timestamp = time.time()
                        request_id = object()
                        owned_acks.add(request_id)
                        with _pending_lock:
                            _pending_acks.setdefault(code, {})[request_id] = (
                                timestamp, page_index
                            )
                        _ack_ingress_ts.set(timestamp)
                        _ack_request_id.set(request_id)
                return message

            try:
                return await self.app(scope, receive_observed, send)
            finally:
                with _pending_lock:
                    requests = _pending_acks.get(code)
                    if requests is not None:
                        for request_id in owned_acks:
                            requests.pop(request_id, None)
                        if not requests:
                            _pending_acks.pop(code, None)
        if scope.get('type') != 'http':
            return await self.app(scope, receive, send)
        path = scope.get('path', '')
        match = _PARTICIPANT_PATH.match(path)
        if match is None:
            return await self.app(scope, receive, send)
        code = match.group(1)
        is_decision_post = bool(
            scope.get('method') == 'POST' and _DECISION_POST_PATH.match(path)
        )
        is_decision_get = bool(
            scope.get('method') == 'GET' and _DECISION_POST_PATH.match(path)
        )
        timestamp = time.time()
        match_index = _PAGE_INDEX.match(path)
        page_name = match_index.group(1) if match_index else None
        page_index = int(match_index.group(2)) if match_index else None
        request_id = object()
        with _pending_lock:
            _pending.setdefault(code, {})[request_id] = (timestamp, is_decision_post)
        token = _ingress_ts.set(timestamp if is_decision_post else None)
        page_token = _page_ingress_ts.set(timestamp)
        shell_token_value = secrets.token_hex(12) if is_decision_get else None
        shell_token_context = _shell_token.set(shell_token_value)
        form_context = _decision_form_choices.set(
            {} if is_decision_get or is_decision_post else None
        )
        if is_decision_post:
            scope['dynamic_decision_ingress_ts'] = timestamp
        response_status = None
        raised = False

        async def send_observed(message):
            nonlocal response_status
            if message.get('type') == 'http.response.start':
                response_status = message.get('status')
            await send(message)

        try:
            return await self.app(scope, receive, send_observed)
        except BaseException:
            raised = True
            raise
        finally:
            _ingress_ts.reset(token)
            _page_ingress_ts.reset(page_token)
            _shell_token.reset(shell_token_context)
            _decision_form_choices.reset(form_context)
            completed_ts = time.time()
            with _pending_lock:
                completed = _completed.setdefault(code, [])
                completed.append((
                    timestamp, completed_ts, page_index, page_name,
                    scope.get('method'),
                ))
                if is_decision_post:
                    _completed_decision_posts[(code, page_index, timestamp)] = completed_ts
                if (is_decision_get and not raised
                        and response_status is not None and response_status < 400):
                    _completed_shell_responses[
                        (code, page_index, shell_token_value)
                    ] = completed_ts
                if len(completed) > 512:
                    del completed[:-512]
                if raised or (response_status is not None and response_status >= 500):
                    _failed.setdefault(code, []).append((timestamp, page_index))
                requests = _pending.get(code)
                if requests is not None:
                    requests.pop(request_id, None)
                    if not requests:
                        _pending.pop(code, None)
