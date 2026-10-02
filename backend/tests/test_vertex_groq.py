import asyncio
import json
from types import SimpleNamespace
import httpx
import pytest
from google.genai import errors
from fastapi.testclient import TestClient
from app.config import Settings
from app.examples import demo_process
from app.main import create_app
from app.models import AuditResult, ClarificationResult, ModificationResult
from app.services.llm.factory import get_llm_provider
from app.services.llm.vertex_gemini_provider import VertexGeminiProvider
from app.services.llm.common import prompt_text, validated_json
from app.models import ProcessDefinition
from app.bpmn import validate_xml


def configuration(**overrides):
    values = dict(_env_file=None, primary_llm_provider='groq', primary_llm_model='openai/gpt-oss-120b',
        primary_llm_base_url='https://api.groq.com/openai/v1', primary_llm_api_key='groq-secret',
        fallback_llm_provider='vertex_gemini', fallback_llm_model='gemini-2.5-flash',
        fallback_llm_api_key='cloud-secret', llm_max_tokens=8000)
    values.update(overrides)
    return Settings(**values)


def mock_vertex(monkeypatch, content=None, failure=None):
    calls = []
    initializations = []
    class AsyncClient:
        models = None
        def __init__(self):
            self.models = SimpleNamespace(generate_content=self.generate)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def generate(self, **kwargs):
            calls.append(kwargs)
            if failure: raise failure
            text = content(len(calls)) if callable(content) else content or demo_process(0).model_dump_json()
            return SimpleNamespace(text=text, candidates=[])
    class Client:
        def __init__(self, **kwargs):
            initializations.append(kwargs)
            self.aio = AsyncClient()
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr('app.services.llm.vertex_gemini_provider.genai.Client', Client)
    return calls, initializations


@pytest.mark.parametrize('status', [200, 401, 429, 500, 502, 503, 504, 'timeout', 'network'])
def test_groq_vertex_failover_same_pipeline(monkeypatch, status):
    calls, initializations = mock_vertex(monkeypatch)
    original = httpx.AsyncClient
    def handle(request):
        assert request.url.host == 'api.groq.com'
        body = json.loads(request.content)
        assert body['model'] == 'openai/gpt-oss-120b'
        assert body['max_tokens'] == 8000
        if status == 'timeout': raise httpx.ReadTimeout('secret', request=request)
        if status == 'network': raise httpx.ConnectError('secret', request=request)
        if status != 200: return httpx.Response(status, text='private SDK stack')
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process(0).model_dump_json()}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    response = TestClient(create_app(configuration())).post('/api/process/generate', json={'text': 'Описание процесса'})
    assert response.status_code == 200, response.text
    result = response.json()
    backup = status != 200
    assert result['metadata'] == {'provider_used': 'vertex_gemini' if backup else 'groq',
        'model_used': 'gemini-2.5-flash' if backup else 'openai/gpt-oss-120b',
        'fallback_used': backup, 'attempts': 2 if backup else 1}
    validate_xml(result['xml'])
    assert result['process'] == demo_process(0).model_dump()
    assert 'secret' not in response.text
    if backup:
        init = initializations[0]
        assert init['vertexai'] is True
        assert init['api_key'] == 'cloud-secret'
        assert init['http_options'].timeout == 90000
        assert init['http_options'].retry_options.attempts == 1
        assert calls[0]['config'].response_mime_type == 'application/json'
        assert set(calls[0]['config'].response_schema['properties']) == set(ProcessDefinition.model_fields)
        assert calls[0]['config'].response_schema['properties']['flows']['items']['properties']['condition']['nullable']
        assert calls[0]['config'].system_instruction.startswith(prompt_text('extraction'))


def test_vertex_corrective_retry_and_wrapper_extraction(monkeypatch):
    calls, _ = mock_vertex(monkeypatch, lambda count: 'invalid' if count == 1 else '```json\n'+demo_process(0).model_dump_json()+'\n```')
    provider = VertexGeminiProvider('cloud-key', 'gemini-2.5-flash', max_retries=1)
    assert asyncio.run(provider.parse_process('Описание')) == demo_process(0)
    assert provider.attempt_count == 2
    assert len(calls) == 2
    assert 'Исправь' in calls[1]['contents'][0].parts[0].text


def test_vertex_all_result_types(monkeypatch):
    values = iter([demo_process(0).model_dump_json(), demo_process(0).model_dump_json(), AuditResult().model_dump_json()])
    mock_vertex(monkeypatch, lambda count: next(values))
    provider = VertexGeminiProvider('cloud-key', 'gemini-2.5-flash')
    assert isinstance(asyncio.run(provider.clarify_process(demo_process(0), {})), ClarificationResult)
    assert isinstance(asyncio.run(provider.modify_process(demo_process(0), 'Обновить')), ModificationResult)
    assert isinstance(asyncio.run(provider.audit_process(demo_process(0))), AuditResult)


def test_vertex_both_down_and_info_safe(monkeypatch):
    mock_vertex(monkeypatch, failure=errors.APIError(503, {'error': {'message': 'cloud-secret private-error'}}))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(lambda request: httpx.Response(500)), **kw))
    client = TestClient(create_app(configuration()))
    info = client.get('/api/llm/info')
    assert info.json() == {'primary': {'provider': 'groq', 'model': 'openai/gpt-oss-120b'},
        'fallback': {'enabled': True, 'provider': 'vertex_gemini', 'model': 'gemini-2.5-flash'}}
    assert 'secret' not in info.text
    result = client.post('/api/process/generate', json={'text': 'Описание процесса'})
    assert result.status_code == 502
    assert result.json()['detail'] == 'Не удалось получить ответ от AI-моделей. Попробуйте ещё раз позже.'
    assert 'secret' not in result.text


def test_disabled_fallback_and_hyphen_alias():
    assert get_llm_provider(configuration(llm_fallback_enabled=False)).fallback is None
    assert get_llm_provider(configuration(fallback_llm_provider='vertex-gemini')).fallback.name == 'vertex_gemini'


def test_multiple_json_objects_rejected():
    with pytest.raises(ValueError):
        validated_json('{} {}', ProcessDefinition)


@pytest.mark.parametrize('corrects', [True, False])
def test_groq_invalid_json_retries_before_vertex(monkeypatch, corrects):
    calls, _ = mock_vertex(monkeypatch)
    original = httpx.AsyncClient
    attempts = []
    def handler(request):
        attempts.append(request)
        value = demo_process(0).model_dump_json() if corrects and len(attempts) == 2 else 'invalid JSON'
        return httpx.Response(200, json={'choices': [{'message': {'content': value}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    response = TestClient(create_app(configuration())).post('/api/process/generate', json={'text': 'Описание процесса'})
    assert response.status_code == 200
    metadata = response.json()['metadata']
    assert metadata['provider_used'] == ('groq' if corrects else 'vertex_gemini')
    assert metadata['fallback_used'] is (not corrects)
    assert metadata['attempts'] == (2 if corrects else 4)
    assert len(calls) == (0 if corrects else 1)
