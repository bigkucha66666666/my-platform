"""Per-boot authenticated navigation by the native oTree timeout worker.

No database or experiment imports: this boundary runs before the business lock.
IP addresses and User-Agent never establish internal origin.
"""
from contextvars import ContextVar
import hashlib
import hmac
import logging
import os
import secrets
from threading import Lock
import time
from urllib.parse import urlsplit
from urllib.request import BaseHandler, ProxyHandler, build_opener

HEADER = 'x-project-timeout-authorization'
_internal = ContextVar('project_timeout_navigation', default=False)
log = logging.getLogger(__name__)


def is_timeout_navigation():
    return _internal.get()


def make_timeout_headers(secret, method, target, *, now=None):
    stamp = str(int((time.time() if now is None else now) * 1000))
    nonce = secrets.token_hex(16)
    message = '\n'.join(('v1', method.upper(), target, stamp, nonce)).encode()
    signature = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return {HEADER: '.'.join(('v1', stamp, nonce, signature))}


class TimeoutOriginMiddleware:
    def __init__(self, app, *, secret=None):
        self.app = app
        self.secret = os.environ.get('PROJECT_TIMEOUT_WORKER_SECRET', '') if secret is None else secret
        self.seen = {}
        self.lock = Lock()

    def authenticated(self, scope):
        if not self.secret or scope.get('type') != 'http':
            return False
        values = [v for k, v in scope.get('headers', []) if k.lower() == HEADER.encode()]
        if len(values) != 1 or len(values[0]) > 256:
            return False
        try:
            version, stamp, nonce, signature = values[0].decode('ascii').split('.')
            now = time.time()
            timestamp = int(stamp) / 1000
            if (version != 'v1' or not -5 <= now - timestamp <= 30
                    or len(nonce) != 32 or len(signature) != 64
                    or any(c not in '0123456789abcdef' for c in nonce + signature)):
                return False
            target = scope.get('raw_path', scope['path'].encode()).decode('ascii')
            query = scope.get('query_string', b'')
            if query:
                target += '?' + query.decode('ascii')
            message = '\n'.join(('v1', scope['method'].upper(), target, stamp, nonce)).encode()
            expected = hmac.new(self.secret.encode(), message, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return False
        except (ValueError, UnicodeError, KeyError):
            return False
        with self.lock:
            self.seen = {key: expiry for key, expiry in self.seen.items() if expiry >= now}
            if nonce in self.seen or len(self.seen) >= 10000:
                return False
            self.seen[nonce] = timestamp + 30
        return True

    async def __call__(self, scope, receive, send):
        internal = self.authenticated(scope)
        token = _internal.set(internal)
        try:
            if internal:
                log.info('navigation_origin=timeout_worker method=%s path=%s',
                         scope.get('method'), scope.get('path'))
            return await self.app(scope, receive, send)
        finally:
            _internal.reset(token)

    async def aclose(self):
        close = getattr(self.app, 'aclose', None)
        if close is not None:
            await close()


class _TimeoutSigner(BaseHandler):
    handler_order = 100  # before urllib's HTTPHandler, also on each redirect
    timeout_signer = True

    def __init__(self, port, secret):
        self.port, self.secret = int(port), secret

    def validate_origin(self, request):
        url = urlsplit(request.full_url)
        if (url.scheme != 'http' or url.hostname != '127.0.0.1'
                or url.port != self.port or url.username or url.password):
            raise ValueError('Timeout navigation cannot leave its local server origin')
        return url

    def default_open(self, request):
        # Covers protocols lacking an http_request processor (file/ftp too).
        self.validate_origin(request)
        return None

    def http_request(self, request):
        url = self.validate_origin(request)
        request.remove_header(HEADER.capitalize())
        target = url.path or '/'
        if url.query:
            target += '?' + url.query
        for key, value in make_timeout_headers(self.secret, request.get_method(), target).items():
            # Not propagated by HTTPRedirectHandler; the redirected GET is signed afresh.
            request.add_unredirected_header(key, value)
        return request

    https_request = http_request


def build_timeout_opener(port, secret):
    if len(secret) < 32:
        raise RuntimeError('Missing per-boot timeout worker secret; use project_prodserver.py')
    return build_opener(ProxyHandler({}), _TimeoutSigner(port, secret))
