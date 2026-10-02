import ast
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.examples import TEXTS, demo_process, ambiguous_process
from app.models import ProcessDefinition, ClarificationResult, ModificationResult, AuditResult
from app.services.llm.base import LLMProvider, ProviderError
from app.services.llm.factory import get_llm_provider
from app.services.llm.mock_provider import MockLLMProvider
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.services.llm.yandex_provider import YandexProvider
from app.services.llm.router import LLMRouter
from app.pipeline import generate_valid
from app.validator import validate_process
from app.bpmn import validate_xml
from app.main import create_app


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize('kind', ['mock', 'openai_compatible', 'yandex'])
def test_same_process_same_pipeline_and_xml(monkeypatch, kind):
    process = demo_process(1)
    transport(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {'content': process.model_dump_json()}}]}))
    settings = Settings(_env_file=None, llm_provider=kind, llm_api_key='test-key',
                        llm_model='gpt://test-folder/test-model' if kind == 'yandex' else 'test-model',
                        llm_base_url='https://example.test/v1')
    provider: LLMProvider = get_llm_provider(settings)
    result = asyncio.run(generate_valid(lambda c: provider.parse_process(TEXTS[1], c)))
    assert isinstance(result['process'], ProcessDefinition)
    assert result['process'] == process
    assert validate_process(result['process']) == []
    validate_xml(result['xml'])
    reference = asyncio.run(generate_valid(lambda c: MockLLMProvider().parse_process(TEXTS[1], c)))
    assert result['xml'] == reference['xml']


@pytest.mark.parametrize('kind', ['mock', 'openai_compatible', 'yandex'])
def test_all_operation_result_contracts(monkeypatch, kind):
    mock = MockLLMProvider()
    initial = ambiguous_process()
    answers = {a.id: a.suggested_answers[0] for a in initial.ambiguities}
    command = 'После проверки документов добавь согласование руководителем'
    clarified = asyncio.run(mock.clarify_process(initial, answers))
    modified = asyncio.run(mock.modify_process(demo_process(1), command))
    responses = iter([clarified.process.model_dump_json(), modified.process.model_dump_json(), AuditResult().model_dump_json()])
    transport(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {'content': next(responses)}}]}))
    if kind == 'mock':
        provider = mock
    else:
        cls = YandexProvider if kind == 'yandex' else OpenAICompatibleProvider
        provider = cls('key', 'https://example.test/v1', 'gpt://folder/model' if kind == 'yandex' else 'model')
    assert isinstance(asyncio.run(provider.clarify_process(initial, answers)), ClarificationResult)
    assert isinstance(asyncio.run(provider.modify_process(demo_process(1), command)), ModificationResult)
    assert isinstance(asyncio.run(provider.audit_process(demo_process(1))), AuditResult)


@pytest.mark.parametrize('invalid', ['not JSON', '{"unexpected":true}', '{"id":42}'])
def test_provider_corrects_invalid_json_before_return(monkeypatch, invalid):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        value = invalid if len(calls) == 1 else demo_process(0).model_dump_json()
        return httpx.Response(200, json={'choices': [{'message': {'content': value}}]})
    transport(monkeypatch, handler)
    result = asyncio.run(OpenAICompatibleProvider('key', 'https://example.test/v1', 'model').parse_process('Описание'))
    assert isinstance(result, ProcessDefinition)
    assert len(calls) == 2
    assert len(calls[1]['messages']) == 3


def test_provider_exhausted_json_retry_returns_safe_error(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'secret-invalid-output'}}]})
    transport(monkeypatch, handler)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(OpenAICompatibleProvider('key', 'https://example.test/v1', 'model').audit_process(demo_process(0)))
    assert len(calls) == 3
    assert caught.value.retryable
    assert 'secret-invalid-output' not in str(caught.value)


def test_auto_format_negotiation_stays_inside_provider(monkeypatch):
    formats = []
    def handler(request):
        body = json.loads(request.content)
        formats.append(body.get('response_format', {}).get('type'))
        if len(formats) < 3:
            return httpx.Response(400, json={'error': 'response_format is unsupported'})
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process(0).model_dump_json()}}]})
    transport(monkeypatch, handler)
    assert asyncio.run(OpenAICompatibleProvider('key', 'https://example.test/v1', 'model').parse_process('Описание')).nodes
    assert formats == ['json_schema', 'json_object', None]


def test_strict_mode_never_silently_downgrades(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={'error': 'json_schema is unsupported'})
    transport(monkeypatch, handler)
    with pytest.raises(ProviderError):
        asyncio.run(OpenAICompatibleProvider('key', 'https://example.test/v1', 'model', True).parse_process('Описание'))
    assert len(calls) == 1


@pytest.mark.parametrize('status', [429, 500, 503, 401, 403])
def test_optional_fallback_policy(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'primary.test':
            return httpx.Response(status)
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process(0).model_dump_json()}}]})
    transport(monkeypatch, handler)
    provider = get_llm_provider(Settings(_env_file=None, llm_provider='openai_compatible',
        llm_api_key='key', llm_model='primary', llm_base_url='https://primary.test/v1',
        fallback_llm_provider='yandex', fallback_llm_api_key='backup-key',
        fallback_llm_model='gpt://folder/backup', fallback_llm_base_url='https://backup.test/v1'))
    if status == 403:
        with pytest.raises(ProviderError):
            asyncio.run(provider.parse_process('Описание'))
        assert calls == ['primary.test']
    else:
        assert asyncio.run(provider.parse_process('Описание')).nodes
        assert calls == ['primary.test', 'backup.test']


def test_fallback_is_optional_and_mock_never_used_as_reserve():
    assert get_llm_provider(Settings(_env_file=None, llm_provider='mock')).fallback is None
    with pytest.raises(ProviderError):
        get_llm_provider(Settings(_env_file=None, fallback_llm_provider='mock'))


def test_safe_info_and_primary_override():
    settings = Settings(_env_file=None, llm_provider='mock', primary_llm_provider='openai_compatible',
                        llm_model='openai/gpt-oss-120b:free', llm_api_key='never-return-key',
                        llm_base_url='https://example.test/private-endpoint')
    response = TestClient(create_app(settings)).get('/api/llm/info')
    assert response.json()['primary'] == {'provider': 'openai_compatible', 'model': 'openai/gpt-oss-120b:free'}
    assert 'never-return-key' not in response.text
    assert 'private-endpoint' not in response.text


def test_settings_use_dotenv_not_process_environment(monkeypatch, tmp_path):
    path = tmp_path / '.env'
    path.write_text('LLM_PROVIDER=mock\nLLM_MODEL=file-model', encoding='utf-8')
    monkeypatch.setenv('LLM_PROVIDER', 'yandex')
    monkeypatch.setenv('LLM_MODEL', 'environment-model')
    settings = Settings(_env_file=path)
    assert (settings.llm_provider, settings.llm_model) == ('mock', 'file-model')


def test_business_modules_do_not_import_concrete_providers():
    root = Path(__file__).parents[1] / 'app'
    forbidden = ('openai_provider', 'openai_compatible_provider', 'yandex_provider', 'mock_provider', 'fallback_provider', 'httpx', 'openai')
    for relative in ['main.py', 'pipeline.py', 'models.py', 'validator.py', 'bpmn.py']:
        tree = ast.parse((root / relative).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            modules = [node.module or ''] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names] if isinstance(node, ast.Import) else []
            assert not any(module.split('.')[-1] in forbidden for module in modules), relative
