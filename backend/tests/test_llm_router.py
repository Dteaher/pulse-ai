import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.examples import demo_process
from app.main import create_app
from app.services.llm.factory import get_llm_provider


def settings():
    return Settings(_env_file=None, llm_provider='openai_compatible',
        llm_model='openai/gpt-oss-120b:free', llm_base_url='https://primary.test/v1', llm_api_key='primary-secret',
        fallback_llm_provider='yandex', fallback_llm_model='gpt://folder/backup',
        fallback_llm_base_url='https://backup.test/v1', fallback_llm_api_key='backup-secret')


def install(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def success():
    return httpx.Response(200, json={'choices': [{'message': {'content': demo_process(0).model_dump_json()}}]})


@pytest.mark.parametrize('failure', ['success', 'timeout', 'network', 429, 500, 502, 503, 504, 'model', 'temporary'])
def test_router_failover_and_metadata(monkeypatch, failure):
    calls = []
    def handler(request):
        calls.append((request.url.host, json.loads(request.content)))
        if request.url.host == 'primary.test':
            if failure == 'timeout':
                raise httpx.ReadTimeout('private-timeout-details', request=request)
            if failure == 'network':
                raise httpx.ConnectError('private-network-details', request=request)
            if failure == 'model':
                return httpx.Response(404, json={'error': 'No endpoints found for model'})
            if failure == 'temporary':
                return httpx.Response(400, json={'error': 'temporarily unavailable'})
            if isinstance(failure, int):
                return httpx.Response(failure, text='private-api-details')
        return success()
    install(monkeypatch, handler)
    response = TestClient(create_app(settings())).post('/api/process/generate', json={'text': 'Описание заявки'})
    assert response.status_code == 200, response.text
    fallback = failure != 'success'
    assert response.json()['metadata'] == {
        'provider_used': 'yandex' if fallback else 'openai_compatible',
        'model_used': 'gpt://folder/backup' if fallback else 'openai/gpt-oss-120b:free',
        'fallback_used': fallback,
        'attempts': 2 if fallback else 1,
    }
    assert len(calls) == (2 if fallback else 1)
    if fallback:
        assert calls[0][1]['messages'] == calls[1][1]['messages']
    assert 'secret' not in response.text
    assert 'private-' not in response.text


@pytest.mark.parametrize('corrects', [True, False])
def test_invalid_response_retries_primary_before_fallback(monkeypatch, corrects):
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'primary.test' and (not corrects or len(calls) == 1):
            # Valid JSON, invalid ProcessDefinition exercises Pydantic as well.
            return httpx.Response(200, json={'choices': [{'message': {'content': '{"id":42}'}}]})
        return success()
    install(monkeypatch, handler)
    router = get_llm_provider(settings())
    assert asyncio.run(router.parse_process('Описание')).nodes
    assert calls == (['primary.test', 'primary.test'] if corrects else ['primary.test'] * 3 + ['backup.test'])
    assert router.metadata.fallback_used is not corrects


def test_both_down_safe_error_and_next_request_tries_primary(monkeypatch):
    calls = []
    down = True
    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(503, text='stack trace secret private payload') if down else success()
    install(monkeypatch, handler)
    client = TestClient(create_app(settings()))
    response = client.post('/api/process/generate', json={'text': 'Описание заявки'})
    assert response.status_code == 502
    assert 'AI-моделей' in response.json()['detail']
    assert all(word not in response.text for word in ['secret', 'stack trace', 'private'])
    assert calls == ['primary.test', 'backup.test']
    down = False
    response = client.post('/api/process/generate', json={'text': 'Описание заявки'})
    assert response.status_code == 200
    assert response.json()['metadata']['fallback_used'] is False
    assert calls[-1] == 'primary.test'
