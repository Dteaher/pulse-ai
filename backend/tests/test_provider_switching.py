import json

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.examples import demo_process
from app.main import create_app


def test_switch_api_and_model_without_restarting(monkeypatch, tmp_path):
    env_file = tmp_path / '.env'
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr('app.main.Settings', lambda: Settings(_env_file=env_file))
    env_file.write_text('LLM_PROVIDER=mock', encoding='utf-8')
    client = TestClient(create_app())
    calls = []
    original = httpx.AsyncClient

    def handler(request):
        calls.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process(0).model_dump_json()}}]})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    configurations = [
        ('openai_compatible', 'LLM_API_KEY=compatible-secret\nLLM_BASE_URL=https://custom.example/v1\nLLM_MODEL=custom-model',
         'https://custom.example/v1/chat/completions', 'Bearer compatible-secret', 'custom-model'),
        ('yandex', 'LLM_API_KEY=yandex-secret\nYANDEX_FOLDER_ID=folder\nLLM_MODEL=yandexgpt/latest',
         'https://ai.api.cloud.yandex.net/v1/chat/completions', 'Api-Key yandex-secret', 'gpt://folder/yandexgpt/latest'),
        ('openai', 'LLM_API_KEY=openai-secret\nLLM_MODEL=another-model',
         'https://api.openai.com/v1/chat/completions', 'Bearer openai-secret', 'another-model'),
    ]
    for name, config, endpoint, auth, model in configurations:
        env_file.write_text(f'LLM_PROVIDER={name}\n{config}', encoding='utf-8')
        health = client.get('/api/health')
        assert health.json()['provider'] == name
        assert health.json()['configured'] is True
        assert 'secret' not in health.text
        response = client.post('/api/process/generate', json={'text': 'Описание заявки'})
        assert response.status_code == 200, response.text
        assert 'BPMNDiagram' in response.json()['xml']
        url, headers, body = calls[-1]
        assert (url, headers['authorization'], body['model']) == (endpoint, auth, model)
        if name == 'yandex':
            assert headers['openai-project'] == 'folder'
    assert len(calls) == 3


def test_invalid_runtime_config_is_safe_and_recoverable(monkeypatch, tmp_path):
    env_file = tmp_path / '.env'
    monkeypatch.delenv('LLM_PROVIDER', raising=False)
    monkeypatch.delenv('LLM_MAX_TOKENS', raising=False)
    monkeypatch.setattr('app.main.Settings', lambda: Settings(_env_file=env_file))
    env_file.write_text('LLM_PROVIDER=mock', encoding='utf-8')
    client = TestClient(create_app())
    env_file.write_text('LLM_PROVIDER=mock\nLLM_MAX_TOKENS=secret-invalid-value', encoding='utf-8')
    response = client.get('/api/health')
    assert response.status_code == 502
    assert 'secret-invalid-value' not in response.text
    # Local BPMN operations remain available while the API settings are repaired.
    assert client.post('/api/process/bpmn', json={'process': demo_process(0).model_dump()}).status_code == 200
    env_file.write_text('LLM_PROVIDER=mock', encoding='utf-8')
    assert client.get('/api/health').status_code == 200


def test_explicit_settings_remain_fixed(monkeypatch):
    client = TestClient(create_app(Settings(_env_file=None, llm_provider='mock')))
    monkeypatch.setattr('app.main.Settings', lambda: (_ for _ in ()).throw(AssertionError('must stay fixed')))
    assert client.get('/api/health').json()['provider'] == 'mock'


def test_model_is_stable_during_graph_correction(monkeypatch, tmp_path):
    # The temporary file owns this scenario, regardless of CI/hosting variables.
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    env_file = tmp_path / '.env'
    env_file.write_text('LLM_PROVIDER=openai_compatible\nLLM_API_KEY=test-key\nLLM_MODEL=first-model\nLLM_BASE_URL=https://first.test/v1', encoding='utf-8')
    monkeypatch.setattr('app.main.Settings', lambda: Settings(_env_file=env_file))
    client = TestClient(create_app())
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append((request.url.host, json.loads(request.content)['model']))
        process = demo_process(0)
        if len(calls) == 1:
            # Change .env while the first operation is still processing.
            env_file.write_text('LLM_PROVIDER=openai_compatible\nLLM_API_KEY=next-key\nLLM_MODEL=next-model\nLLM_BASE_URL=https://next.test/v1', encoding='utf-8')
            process.flows[0].target = 'UnknownNode'
        return httpx.Response(200, json={'choices': [{'message': {'content': process.model_dump_json()}}]})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    assert client.post('/api/process/generate', json={'text': 'Описание заявки'}).status_code == 200
    assert calls == [('first.test', 'first-model'), ('first.test', 'first-model')]
    assert client.post('/api/process/generate', json={'text': 'Описание заявки'}).status_code == 200
    assert calls[-1] == ('next.test', 'next-model')
