import asyncio
import httpx
import pytest
from starlette.responses import JSONResponse
from app.config import Settings
from app.main import create_app
from app.security import RequestGuard
from app.services.llm.mock_provider import MockLLMProvider

CODE = 'test-workspace-code-not-a-provider-key'

def settings(**overrides):
    return Settings(_env_file=None, llm_provider='mock', api_limits_enabled=True, **overrides)

def test_production_fails_closed_without_access_code():
    with pytest.raises(ValueError, match='PULSE_ACCESS_TOKEN'):
        create_app(settings(app_env='production'), MockLLMProvider())

def test_shared_access_protects_process_api_and_check_but_not_health():
    class NoCalls(MockLLMProvider):
        async def parse_process(self, *args):
            raise AssertionError('unauthorized request reached the provider')
    app = create_app(settings(pulse_access_token=CODE), NoCalls())
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
            health = await c.get('/api/health')
            assert health.status_code == 200 and health.json()['access_required']
            for path in ('generate', 'clarify', 'modify', 'audit', 'import', 'bpmn'):
                r = await c.post('/api/process/' + path, json={})
                assert r.status_code == 401 and CODE not in r.text
            assert (await c.get('/api/access/check')).status_code == 401
            assert (await c.get('/api/access/check', headers={'Authorization': 'Bearer wrong'})).status_code == 401
            r = await c.get('/api/access/check', headers={'Authorization': 'Bearer ' + CODE})
            assert r.status_code == 200 and CODE not in r.text
    asyncio.run(check())

@pytest.mark.parametrize(('overrides', 'code'), [
    ({'api_requests_per_minute': 1}, 'REQUEST_RATE_LIMIT'),
    ({'api_requests_per_day': 1}, 'DAILY_OPERATION_LIMIT'),
])
def test_operation_windows_count_generate_clarify_modify_and_audit(overrides, code):
    calls = []
    async def inner(scope, receive, send):
        calls.append(scope['path'])
        await JSONResponse({'ok': True})(scope, receive, send)
    guard = RequestGuard(inner, settings=settings(**overrides))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url='http://test') as c:
            assert (await c.post('/api/process/generate', json={})).status_code == 200
            for path in ('clarify', 'modify', 'audit'):
                r = await c.post('/api/process/' + path, json={})
                assert r.status_code == 429 and r.json()['code'] == code
                assert int(r.headers['Retry-After']) > 0
            assert (await c.post('/api/process/import', json={})).status_code == 200
    asyncio.run(check())
    assert calls == ['/api/process/generate', '/api/process/import']

def test_concurrent_operation_limit_and_slot_release():
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        async def inner(scope, receive, send):
            entered.set()
            await release.wait()
            await JSONResponse({'ok': True})(scope, receive, send)
        guard = RequestGuard(inner, settings=settings(api_max_concurrent=1))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url='http://test') as c:
            first = asyncio.create_task(c.post('/api/process/generate', json={}))
            await entered.wait()
            blocked = await c.post('/api/process/modify', json={})
            assert blocked.status_code == 429 and blocked.json()['code'] == 'CAPACITY_LIMIT'
            release.set()
            assert (await first).status_code == 200
            assert guard.active == 0
            assert (await c.post('/api/process/modify', json={})).status_code == 200
    asyncio.run(check())

def test_failed_operation_releases_capacity():
    async def inner(*args): raise RuntimeError('simulated failure')
    guard = RequestGuard(inner, settings=settings())
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url='http://test') as c:
            with pytest.raises(RuntimeError): await c.post('/api/process/generate', json={})
        assert guard.active == 0
    asyncio.run(check())

@pytest.mark.parametrize('chunked', [False, True])
def test_body_size_checks_actual_chunks_and_declared_length(chunked):
    async def inner(*args): raise AssertionError('oversized body reached the app')
    guard = RequestGuard(inner, settings=settings(api_max_body_bytes=1024))
    async def chunks():
        yield b'a' * 600
        yield b'b' * 600
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url='http://test') as c:
            r = await c.post('/api/process/import', content=chunks() if chunked else b'a' * 1200)
            assert r.status_code == 413 and r.json()['code'] == 'REQUEST_TOO_LARGE'
    asyncio.run(check())

def test_disabled_limits_do_not_disable_access_or_size_guard():
    async def inner(scope, receive, send): await JSONResponse({'ok': True})(scope, receive, send)
    guard = RequestGuard(inner, settings=Settings(_env_file=None, api_limits_enabled=False, pulse_access_token=CODE))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url='http://test') as c:
            assert (await c.post('/api/process/generate', json={})).status_code == 401
    asyncio.run(check())

@pytest.mark.parametrize('overrides', [dict(pulse_access_token='short'), dict(pulse_access_token=CODE, api_limits_enabled=False)])
def test_production_requires_strong_code_and_enabled_limits(overrides):
    with pytest.raises(ValueError, match='at least 32'):
        create_app(Settings(_env_file=None, app_env='production', **overrides), MockLLMProvider())

def test_settings_repr_does_not_expose_provider_or_workspace_secrets():
    s = Settings(_env_file=None, pulse_access_token=CODE, llm_api_key='base-secret', primary_llm_api_key='primary-secret', fallback_llm_api_key='fallback-secret')
    assert all(secret not in repr(s) for secret in (CODE, 'base-secret', 'primary-secret', 'fallback-secret'))
