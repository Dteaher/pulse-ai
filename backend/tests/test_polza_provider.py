import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.examples import demo_process
from app.services.llm.factory import get_llm_provider
from app.services.llm.performance import operation_budget
from app.services.llm.polza_provider import PolzaProvider


def configured(**kwargs):
    return Settings(_env_file=None, primary_llm_provider='polza',
        primary_llm_api_key='test-secret', primary_llm_model='openai/gpt-6-luna',
        llm_performance_enabled=True, llm_reasoning_effort='medium',
        llm_fallback_enabled=False, **kwargs)


def test_factory_polza_defaults_and_separate_coverage_reasoning():
    provider = get_llm_provider(configured(llm_doctor_reasoning_effort='low')).primary
    assert isinstance(provider, PolzaProvider)
    assert provider.base_url == 'https://polza.ai/api/v1'
    provider._operation = 'business_coverage'
    assert provider.request_options() == {'reasoning': {'effort': 'medium'}}
    provider._operation = 'audit'
    assert provider.request_options() == {'reasoning': {'effort': 'low'}}


@pytest.mark.parametrize('operation,canonical', [
    ('preparation_v2', 'preparation'), ('preparation_patch', 'modification'),
    ('modification_patch', 'modification'), ('corrective_patch', 'corrective')])
def test_operation_aliases_share_policy(operation, canonical):
    provider = get_llm_provider(configured(llm_parse_reasoning_effort='low',
        llm_modify_reasoning_effort='high', llm_corrective_reasoning_effort='medium')).primary
    provider._operation = operation
    assert operation_budget(provider, operation, {}) == operation_budget(provider, canonical, {})
    expected = provider.reasoning_by_operation[canonical]
    assert provider.request_options() == {'reasoning': {'effort': expected}}


def test_real_wire_contract_is_validated_without_external_call(monkeypatch):
    bodies = []
    original = httpx.AsyncClient
    def handle(request):
        bodies.append(json.loads(request.content))
        assert request.url == 'https://polza.ai/api/v1/chat/completions'
        return httpx.Response(200, json={'model': 'openai/gpt-6-luna',
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'cost_rub': 0.01},
            'choices': [{'message': {'content': demo_process(0).model_dump_json()}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    async def run():
        provider = get_llm_provider(configured()).primary
        try:
            result = await provider.parse_process('Оператор обрабатывает заявку.')
            assert result == demo_process(0)
        finally:
            await provider.close()
    asyncio.run(run())
    assert bodies[0]['reasoning'] == {'effort': 'medium'}
    assert 'reasoning_effort' not in bodies[0]
    assert bodies[0]['response_format']['type'] == 'json_schema'
    assert 'max_tokens' in bodies[0]


@pytest.mark.parametrize('failure', ['timeout', 'network', 429, 500, 502, 503, 504])
def test_polza_to_multiai_failover(monkeypatch, failure):
    original = httpx.AsyncClient
    calls = []
    def handle(request):
        calls.append(request.url.host)
        if request.url.host == 'polza.ai':
            if failure == 'timeout':
                raise httpx.ReadTimeout('timeout', request=request)
            if failure == 'network':
                raise httpx.ConnectError('network', request=request)
            return httpx.Response(failure, text='temporary failure')
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process(0).model_dump_json()}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    config = configured().model_copy(update={'llm_fallback_enabled': True,
        'fallback_llm_provider': 'multiai', 'fallback_llm_model': 'gpt-5.5',
        'fallback_llm_api_key': 'backup-test-secret'})
    async def run():
        router = get_llm_provider(config)
        try:
            assert await router.parse_process('Оператор обрабатывает заявку.') == demo_process(0)
            assert router.metadata.provider_used == 'multiai'
            assert router.metadata.fallback_used is True
        finally:
            await router.close()
    asyncio.run(run())
    assert calls == ['polza.ai', 'multiai.store']


@pytest.mark.parametrize('cancel_owner', [True, False])
def test_cancel_releases_cache_and_waiter_does_not_cancel_owner(cancel_owner):
    from app.performance_cache import ResultCache
    async def run():
        cache = ResultCache()
        started = asyncio.Event()
        release = asyncio.Event()
        async def compute():
            started.set()
            await release.wait()
            return {'xml': '<validated/>'}
        owner = asyncio.create_task(cache.get_or_compute('s', 'generate', {}, 'v', compute))
        await started.wait()
        waiter = asyncio.create_task(cache.get_or_compute('s', 'generate', {}, 'v', compute))
        await asyncio.sleep(0)
        (owner if cancel_owner else waiter).cancel()
        if cancel_owner:
            for task in (owner, waiter):
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert not cache.entries
        else:
            with pytest.raises(asyncio.CancelledError):
                await waiter
            release.set()
            assert await owner == {'xml': '<validated/>'}
        assert not cache.inflight
    asyncio.run(run())


@pytest.mark.parametrize('first_failure', ['unsupported_schema', 'invalid_json'])
def test_polza_format_negotiation_and_corrective_retry(monkeypatch, first_failure):
    original = httpx.AsyncClient
    bodies = []
    def handle(request):
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            if first_failure == 'unsupported_schema':
                return httpx.Response(400, text='response_format json_schema not supported')
            return httpx.Response(200, json={'choices': [{'message': {'content': '{invalid json'}}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process(0).model_dump_json()}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    async def run():
        router = get_llm_provider(configured())
        try:
            assert await router.parse_process('Оператор обрабатывает заявку.') == demo_process(0)
            assert router.metadata.fallback_used is False
        finally:
            await router.close()
    asyncio.run(run())
    assert len(bodies) == 2
    assert bodies[0]['response_format']['type'] == 'json_schema'
    assert bodies[1]['response_format']['type'] == ('json_object' if first_failure == 'unsupported_schema' else 'json_schema')
