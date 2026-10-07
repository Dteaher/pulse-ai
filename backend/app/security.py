"""Single-instance demo controls, not user identity, billing or enterprise RBAC."""
import asyncio
from collections import deque
from hmac import compare_digest
from time import monotonic

from starlette.responses import JSONResponse


class RequestGuard:
    def __init__(self, app, *, settings):
        self.app = app
        self.settings = settings
        self.lock = asyncio.Lock()
        self.window = deque()
        self.day_started = monotonic()
        self.day_count = 0
        self.active = 0

    async def reject(self, scope, receive, send, status, message, code, retry_after=None):
        headers = {'Retry-After': str(retry_after)} if retry_after is not None else {}
        response = JSONResponse({'human_message': message, 'detail': message, 'code': code}, status_code=status, headers=headers)
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        protected = scope['type'] == 'http' and ((scope['method'] == 'POST' and scope['path'].startswith('/api/process/')) or scope['path'] == '/api/access/check')
        if not protected:
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        token = self.settings.pulse_access_token
        if token and not compare_digest(headers.get(b'authorization', b''), ('Bearer ' + token).encode()):
            return await self.reject(scope, receive, send, 401, 'Для этой рабочей области нужен код доступа.', 'ACCESS_REQUIRED')
        if scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        limit = self.settings.api_max_body_bytes
        try:
            declared = int(headers.get(b'content-length', b'0'))
        except ValueError:
            declared = limit + 1
        if declared > limit:
            return await self.reject(scope, receive, send, 413, 'Файл или запрос слишком большой для этой рабочей области.', 'REQUEST_TOO_LARGE')
        # Count actual chunks too: Content-Length is not a trustworthy size check.
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > limit:
                return await self.reject(scope, receive, send, 413, 'Файл или запрос слишком большой для этой рабочей области.', 'REQUEST_TOO_LARGE')
            chunks.append(chunk)
            if not message.get('more_body', False):
                break
        body = b''.join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        expensive = scope['path'] in {
            '/api/process/generate', '/api/process/clarify', '/api/process/modify', '/api/process/audit',
        }
        if not expensive or not self.settings.api_limits_enabled:
            return await self.app(scope, replay, send)
        now = monotonic()
        async with self.lock:
            while self.window and self.window[0] <= now - 60:
                self.window.popleft()
            if now - self.day_started >= 86400:
                self.day_started, self.day_count = now, 0
            error = None
            if self.active >= self.settings.api_max_concurrent:
                error = ('Сейчас обрабатываются другие запросы. Повторите чуть позже.', 'CAPACITY_LIMIT', 5)
            elif len(self.window) >= self.settings.api_requests_per_minute:
                error = ('Слишком много запросов. Подождите перед следующей операцией.', 'REQUEST_RATE_LIMIT', max(1, int(self.window[0] + 60 - now) + 1))
            elif self.day_count >= self.settings.api_requests_per_day:
                error = ('Лимит операций этой рабочей области исчерпан. Обратитесь к владельцу.', 'DAILY_OPERATION_LIMIT', max(1, int(self.day_started + 86400 - now) + 1))
            else:
                self.active += 1
                self.window.append(now)
                self.day_count += 1
        if error:
            return await self.reject(scope, receive, send, 429, *error[:2], retry_after=error[2])
        try:
            await self.app(scope, replay, send)
        finally:
            async with self.lock:
                self.active -= 1
