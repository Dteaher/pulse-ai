import asyncio
import json

import httpx
import pytest

from app.bpmn import build_bpmn, validate_xml
from app.config import Settings
from app.examples import demo_process
from app.services.llm.base import ProviderError
from app.services.llm.factory import get_llm_provider
from app.services.llm.multiai_provider import MultiAIProvider
from app.validator import validate_process


@pytest.mark.parametrize('effort', [None, 'low'])
def test_multiai_uses_shared_validated_pipeline(monkeypatch, effort):
    original = httpx.AsyncClient

    def handle(request):
        assert str(request.url) == 'https://multiai.test/v1/chat/completions'
        body = json.loads(request.content)
        assert body['model'] == 'gpt-6.1-sol'
        assert body['response_format']['type'] == 'json_schema'
        if effort is None:
            assert 'reasoning_effort' not in body
        else:
            assert body['reasoning_effort'] == effort
        return httpx.Response(200, json={'choices': [{'message': {
            'content': demo_process(0).model_dump_json()}}]})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(
        transport=httpx.MockTransport(handle), **kw))
    router = get_llm_provider(Settings(_env_file=None,
        primary_llm_provider='multiai', primary_llm_api_key='test-secret',
        primary_llm_model='gpt-6.1-sol', primary_llm_base_url='https://multiai.test/v1',
        llm_fallback_enabled=False, llm_reasoning_effort=effort))
    process = asyncio.run(router.parse_process('Описание процесса'))
    assert process == demo_process(0)
    assert not [issue for issue in validate_process(process) if issue.severity == 'error']
    validate_xml(build_bpmn(process))
    assert router.metadata.provider_used == 'multiai'
    assert router.metadata.model_used == 'gpt-6.1-sol'
    assert not router.metadata.fallback_used
    assert isinstance(router.primary, MultiAIProvider)
    assert 'test-secret' not in json.dumps(router.connection_info)


def test_reasoning_option_does_not_leak_to_other_providers():
    settings = Settings(_env_file=None, primary_llm_provider='openai_compatible',
                        llm_reasoning_effort='low', llm_fallback_enabled=False)
    assert get_llm_provider(settings).primary.request_options() == {}


def test_retired_provider_is_not_created():
    with pytest.raises(ProviderError, match='Неизвестный'):
        get_llm_provider(Settings(_env_file=None, primary_llm_provider='groq',
                                 llm_fallback_enabled=False))
