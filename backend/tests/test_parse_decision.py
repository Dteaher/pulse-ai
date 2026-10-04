import asyncio
import json
from pathlib import Path
import pytest
import httpx
from pydantic import ValidationError
from app.models import ProcessDefinition
from app.services.llm.decision import ParseDecision
from app.services.llm.openai_compatible_provider import strict_schema, OpenAICompatibleProvider
from app.services.llm.vertex_gemini_provider import vertex_schema
from app.services.llm.router import LLMRouter
from app.preflight import prepare

ROOT = Path(__file__).resolve().parents[2]
PROCESS = json.loads((ROOT / 'examples/pipeline-resilience/simple-response.json').read_text(encoding='utf-8'))['process']
QUESTION = {'id': 'parallel', 'question': 'Проверки параллельны?', 'type': 'unclear_parallelism', 'severity': 'critical'}


@pytest.mark.parametrize('result', [
    {'status': 'ready', 'process': PROCESS, 'analysis': {}},
    {'status': 'ready', 'analysis': {}},
    {'status': 'clarification_required', 'analysis': {'ambiguities': []}},
    {'status': 'clarification_required', 'analysis': {'ambiguities': [QUESTION]}, 'process': PROCESS},
    {'status': 'something', 'process': PROCESS},
])
def test_decision_rejects_mixed_empty_or_wrong_branch(result):
    with pytest.raises(ValidationError):
        ParseDecision.model_validate({'result': result})


def test_ready_never_accepts_critical_questions():
    p = ProcessDefinition.model_validate(PROCESS).model_copy(deep=True)
    from app.models import Ambiguity
    p.ambiguities = [Ambiguity.model_validate(QUESTION)]
    with pytest.raises(ValidationError):
        ParseDecision.model_validate({'result': {'status': 'ready', 'process': p.model_dump()}})


def test_native_and_vertex_schema_exclusive_tagged_variants():
    native = strict_schema(ParseDecision)
    assert 'anyOf' in native['properties']['result']
    assert 'discriminator' not in json.dumps(native)
    vertex = vertex_schema(ParseDecision)
    assert len(vertex['properties']['result']['anyOf']) == 2
    assert vertex['properties']['result']['anyOf'][0]['properties']['status']['enum'] == ['ready']
    assert vertex['properties']['result']['anyOf'][1]['properties']['status']['enum'] == ['clarification_required']


@pytest.mark.parametrize('result', [{'status': 'ready', 'process': PROCESS}, {'status': 'clarification_required', 'analysis': {'ambiguities': [QUESTION]}}])
def test_new_combined_gate_one_native_call_same_preflight_contract(result):
    async def run():
        provider = OpenAICompatibleProvider('unit', 'https://unit.invalid/v1', 'unit', max_retries=0)
        provider.combined_parse_enabled = True
        provider.performance_enabled = True
        calls = []
        async def transport(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'result': result})}}]})
        provider._clients[asyncio.get_running_loop()] = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        state, timings, process = await prepare(LLMRouter(provider), 'example')
        assert len(calls) == 1 and 'combined_preparation_ms' in timings
        if result['status'] == 'ready':
            assert process == ProcessDefinition.model_validate(PROCESS) and not state.analysis.ambiguities
        else:
            assert process is None and state.analysis.ambiguities[0].id == 'parallel'
        await provider.close()
    asyncio.run(run())
