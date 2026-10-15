import asyncio
import json
import httpx
import pytest
from app.config import Settings
from app.examples import demo_process
from app.models import ProcessDefinition
from app.services.llm.factory import get_llm_provider
from app.services.llm.openai_compatible_provider import strict_schema
from app.services.llm.base import ProviderError


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('invalid', [False, True])
def test_fallback_schema_is_isolated_and_validation_remains(monkeypatch, enabled, invalid):
    bodies = []
    original = httpx.AsyncClient
    def handle(request):
        bodies.append(json.loads(request.content))
        process = demo_process().model_dump()
        if invalid:
            process['pools'] = [{'id': 'pool', 'name': 'Pool',
                'nodes': process.pop('nodes'), 'flows': process.pop('flows')}]
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(process)}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    settings = Settings(_env_file=None, primary_llm_provider='polza', primary_llm_api_key='test-primary',
        primary_llm_model='primary', fallback_llm_provider='multiai', fallback_llm_api_key='test-fallback',
        fallback_llm_model='fallback', llm_fallback_enabled=True, fallback_llm_schema_in_prompt=enabled,
        llm_max_retries=0, llm_structured_output=True, fallback_llm_structured_output=True)
    router = get_llm_provider(settings)
    async def run():
        try:
            for provider in (router.primary, router.fallback):
                if invalid:
                    with pytest.raises(ProviderError) as exc:
                        await provider.parse_process('Golden text')
                    assert exc.value.reason == 'invalid_response'
                else:
                    assert await provider.parse_process('Golden text') == demo_process()
        finally:
            await router.primary.close()
            await router.fallback.close()
    asyncio.run(run())
    assert '\nJSON Schema:\n' not in bodies[0]['messages'][0]['content']
    prompt = bodies[1]['messages'][0]['content']
    assert ('\nJSON Schema:\n' in prompt) == enabled
    if enabled:
        assert json.loads(prompt.split('\nJSON Schema:\n', 1)[1]) == strict_schema(ProcessDefinition)
    assert all(b['response_format']['type'] == 'json_schema' for b in bodies)
    assert router.primary.schema_in_prompt is False
