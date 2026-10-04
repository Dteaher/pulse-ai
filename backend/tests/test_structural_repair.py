import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.models import ProcessDefinition, Issue
from app.notation import polish_process
from app.pipeline import generate_valid
from app.repair import RepairPatch, apply_repair, repair_context, quality_fingerprint
from app.repair import assert_preservation
from app.services.llm.mock_provider import MockLLMProvider
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.services.llm.router import LLMRouter
from app.telemetry import profile, PerformanceProfile
from app.validator import validate_process
from app.semantics import validate_semantics

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def graphs():
    original = polish_process(ProcessDefinition.model_validate(json.loads((ROOT / 'examples/pipeline-resilience/exact-complex-polished-response.json').read_text(encoding='utf-8'))['process']))
    broken = original.model_copy(deep=True)
    flow = next(f for f in broken.flows if f.target == 'o_join_checks')
    broken.flows.append(flow.model_copy(update={'id': 'injected_duplicate'}))
    errors = [e for e in validate_process(broken) + validate_semantics(broken) if e.severity == 'error']
    return original, broken, errors


def valid_patch():
    return RepairPatch.model_validate({'operations': [{'op': 'remove_duplicate_flow', 'element_id': 'injected_duplicate'}]})


def test_repair_exact_six_roles_loops_messages_evidence(graphs):
    original, broken, errors = graphs
    before = quality_fingerprint(broken)
    fixed = apply_repair(broken, errors, valid_patch())
    assert len(fixed.participants) == 6 and len(fixed.pools) == 2
    assert quality_fingerprint(fixed) == quality_fingerprint(original)
    assert quality_fingerprint(broken) == before  # Transactional, caller never mutated.


@pytest.mark.parametrize('operation', [
    {'op': 'delete_task', 'element_id': 'o_decide'},
    {'op': 'rename', 'element_id': 'o_decide'},
    {'op': 'add_gateway', 'element_id': 'o_decide', 'participant_id': 'p_operator', 'gateway_type': 'exclusive_gateway'},
    {'op': 'change_gateway_type', 'element_id': 'o_decide', 'gateway_type': 'exclusive_gateway'},
    {'op': 'change_gateway_type', 'element_id': 'o_docs_gateway', 'gateway_type': 'parallel_gateway'},
    {'op': 'remove_duplicate_flow', 'element_id': 'o09'},
    {'op': 'remove_duplicate_flow', 'element_id': 'o11'},
    {'op': 'redirect_target', 'element_id': 'c01', 'target': 'o_join_checks'},
    {'op': 'redirect_target', 'element_id': 'o11', 'target': 'unknown'},
    {'op': 'add_flow', 'element_id': 'new', 'source': 'o_join_checks', 'target': 'unrelated'},
    {'op': 'clear_event_condition', 'element_id': 'o04'},
    {'op': 'sequence_to_message', 'element_id': 'o11'},
])
def test_unrelated_destructive_unknown_and_condition_operations_rejected(graphs, operation):
    _, broken, errors = graphs
    snapshot = quality_fingerprint(broken)
    with pytest.raises((ValueError, ValidationError)):
        apply_repair(broken, errors, RepairPatch.model_validate({'operations': [operation]}))
    assert quality_fingerprint(broken) == snapshot


def test_context_local_bounded_and_unsupported_bypass(graphs):
    _, broken, errors = graphs
    context = repair_context(broken, errors)
    assert len(context['nodes']) < len(broken.nodes)
    assert 'description' not in context
    assert all('source_text' in n for n in context['nodes'])
    assert repair_context(broken, [Issue(code='SAME_POOL_MESSAGE_FLOW', severity='error', message='bad')]) is None


@pytest.mark.parametrize('field', ['participants', 'nodes', 'flows', 'message_flows', 'assumptions'])
def test_final_fingerprint_rejects_hidden_post_normalization_mutation(graphs, field):
    _, broken, errors = graphs
    fixed = apply_repair(broken, errors, valid_patch())
    if field == 'participants':
        fixed.participants[0].name = 'Merged actors'
    elif field == 'nodes':
        fixed.nodes[1].source_text = 'Dropped business evidence'
    elif field == 'flows':
        fixed.flows[0].name = 'Unrelated flow name'
    elif field == 'message_flows':
        fixed.message_flows.pop()
    else:
        from app.models import Assumption
        fixed.assumptions.append(Assumption(id='new', text='Unrequested business assumption'))
    with pytest.raises(ValueError):
        assert_preservation(broken, fixed, valid_patch())


def test_guard_rejects_breaking_business_cycle_before_full_validation(graphs):
    _, broken, _ = graphs
    correction = next(n.id for n in broken.nodes if n.name == 'Исправить документы')
    cyclic = next(f for f in broken.flows if f.source == correction)
    target = next(n.id for n in broken.nodes if n.type == 'end_event' and n.participant_id == 'p_client')
    errors = [Issue(code='DEAD_END', severity='error', message='Bad link', node_id=correction, node_ids=[target])]
    patch = RepairPatch.model_validate({'operations': [{'op': 'redirect_target', 'element_id': cyclic.id, 'target': target}]})
    with pytest.raises(ValueError, match='loop'):
        apply_repair(broken, errors, patch)


class RepairProvider(MockLLMProvider):
    patch_corrective_enabled = True
    supports_structural_repair = True
    def __init__(self, patch):
        self.patch = patch
        self.repair_calls = 0
    async def repair_process(self, context):
        self.repair_calls += 1
        return self.patch


def test_pipeline_patch_passes_full_xml_di_without_full_regeneration(graphs):
    original, broken, _ = graphs
    async def run():
        provider = RepairProvider(valid_patch())
        calls = []
        async def parse(correction):
            calls.append(correction)
            return broken
        measured = PerformanceProfile()
        token = profile.set(measured)
        try:
            result = await generate_valid(parse, provider=provider)
            assert result['xml'] and quality_fingerprint(result['process']) == quality_fingerprint(original)
            assert len(calls) == 1 and provider.repair_calls == 1
            assert measured.patch_success_count == 1 and measured.graph_corrective_count == 0
        finally:
            profile.reset(token)
    asyncio.run(run())


def test_pipeline_rejected_patch_uses_existing_full_corrective(graphs):
    original, broken, _ = graphs
    async def run():
        bad_patch = RepairPatch.model_validate({'operations': [{'op': 'redirect_target', 'element_id': 'o11', 'target': 'o_join_checks'}]})
        provider = RepairProvider(bad_patch)
        calls = []
        async def parse(correction):
            calls.append(correction)
            return broken if not correction else original
        measured = PerformanceProfile()
        token = profile.set(measured)
        try:
            result = await generate_valid(parse, provider=provider)
            assert result['xml'] and len(calls) == 2
            assert 'previous_result' in calls[-1]
            assert measured.patch_rejected_quality_count == 1 and measured.graph_corrective_count == 1
        finally:
            profile.reset(token)
    asyncio.run(run())


def test_flag_off_does_not_call_patch(graphs):
    original, broken, _ = graphs
    async def run():
        provider = RepairProvider(valid_patch())
        provider.patch_corrective_enabled = False
        async def parse(correction):
            return original if correction else broken
        assert (await generate_valid(parse, provider=provider))['xml']
        assert provider.repair_calls == 0
    asyncio.run(run())


def test_native_repair_adapter_validates_and_retries_its_own_json(graphs):
    _, broken, errors = graphs
    async def run():
        provider = OpenAICompatibleProvider('test-only', 'https://unit.invalid/v1', 'unit', max_retries=1)
        bodies = []
        async def transport(request):
            body = json.loads(request.content)
            bodies.append(body)
            result = {'operations': []} if len(bodies) == 1 else valid_patch().model_dump()
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        provider._clients[asyncio.get_running_loop()] = client
        provider.performance_enabled = True
        router = LLMRouter(provider)
        patch = await router.repair_process(repair_context(broken, errors))
        assert patch == valid_patch() and len(bodies) == 2
        assert bodies[0]['response_format']['json_schema']['name'] == 'RepairPatch'
        assert bodies[0]['max_tokens'] == 1600
        assert 'previous_result' not in bodies[0]['messages'][1]['content']
        await provider.close()
    asyncio.run(run())


def test_vertex_repair_uses_same_patch_contract_and_pipeline(monkeypatch, graphs):
    from test_vertex_failover import mock_vertex
    from app.services.llm.vertex_gemini_provider import VertexGeminiProvider
    calls, _ = mock_vertex(monkeypatch, valid_patch().model_dump_json())
    _, broken, errors = graphs
    provider = VertexGeminiProvider('unit', 'gemini-2.5-flash', max_retries=0)
    async def run():
        patch = await provider.repair_process(repair_context(broken, errors))
        candidate = apply_repair(broken, errors, patch)
        async def resolved(_):
            return candidate
        assert (await generate_valid(resolved, max_retries=0))['xml']
        assert patch == valid_patch()
    asyncio.run(run())
    assert len(calls) == 1
    assert 'operations' in calls[0]['config'].response_schema['properties']
