import asyncio
import json
import httpx
import pytest
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.services.llm.yandex_provider import YandexProvider
from app.services.llm.base import ProviderError
from app.examples import demo_process


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_openai_structured_contract(monkeypatch):
    p = demo_process()
    def handler(request):
        assert str(request.url) == 'https://api.openai.com/v1/chat/completions'
        body = json.loads(request.content)
        assert body['model'] == 'test-model'
        assert body['max_completion_tokens'] == 16000
        assert body['response_format']['json_schema']['strict'] is True
        assert body['response_format']['json_schema']['schema']['additionalProperties'] is False
        assert 'tools' not in body
        return httpx.Response(200, json={'choices': [{'message': {'content': p.model_dump_json()}}]})
    install_transport(monkeypatch, handler)
    result = asyncio.run(OpenAIProvider('test-key', 'https://api.openai.com/v1', 'test-model').parse_process('Описание процесса'))
    assert result == p


def test_compatible_json_mode_explicit(monkeypatch):
    def handler(request):
        assert json.loads(request.content)['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process().model_dump_json()}}]})
    install_transport(monkeypatch, handler)
    result = asyncio.run(OpenAICompatibleProvider('key', 'https://example.com/v1', 'gpt-oss-120b', False).parse_process('Описание'))
    assert result.nodes


@pytest.mark.parametrize('code', [401, 403, 429, 400, 500])
def test_safe_provider_errors(monkeypatch, code):
    install_transport(monkeypatch, lambda request: httpx.Response(code, text='sensitive-provider-response'))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(OpenAIProvider('secret-test-key', 'https://api.openai.com/v1', 'model').parse_process('Описание'))
    assert 'secret-test-key' not in str(caught.value)
    assert 'sensitive-provider-response' not in str(caught.value)


def test_refusal(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {'refusal': 'No'}}]}))
    with pytest.raises(ProviderError):
        asyncio.run(OpenAIProvider('key', 'https://api.openai.com/v1', 'model').parse_process('Описание'))


def test_yandex_auth_contract(monkeypatch):
    def handler(request):
        assert request.headers['Authorization'] == 'Api-Key test-key'
        assert request.headers['OpenAI-Project'] == 'test-folder'
        assert json.loads(request.content)['model'] == 'gpt://test-folder/yandexgpt/latest'
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process().model_dump_json()}}]})
    install_transport(monkeypatch, handler)
    provider = YandexProvider('test-key', 'https://ai.api.cloud.yandex.net/v1', 'gpt://test-folder/yandexgpt/latest', folder_id='test-folder')
    assert asyncio.run(provider.parse_process('Описание')).nodes


def test_truncated_output_is_actionable(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]}))
    with pytest.raises(ProviderError, match='LLM_MAX_TOKENS'):
        asyncio.run(OpenAIProvider('key', 'https://api.openai.com/v1', 'model').parse_process('Описание'))


def test_removed_free_model_is_clear_and_never_silently_paid(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(404, json={'error': {'message': 'This model is unavailable for free. The paid version is available now'}}))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(OpenAICompatibleProvider('key', 'https://openrouter.ai/api/v1', 'openai/gpt-oss-120b:free').parse_process('Описание'))
    assert 'больше недоступна бесплатно' in str(caught.value)
    assert not caught.value.retryable
