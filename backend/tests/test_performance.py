import asyncio
import hashlib
import json
from pathlib import Path
from time import perf_counter

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.examples import demo_process
from app.main import create_app
from app.models import Ambiguity, AmbiguityAnalysis, ProcessDefinition, ProcessPreparation, AuditResult
from app.performance_cache import ResultCache
from app.pipeline import generate_valid
from app.services.llm.factory import get_llm_provider
from app.services.llm.performance import compact_process
from app.services.llm.mock_provider import MockLLMProvider
from app.services.llm.router import LLMRouter
from app.services.llm.base import ProviderError
from app.xml_validation import validate_document
from app.models import ProcessPatch, ModificationPreparation

ROOT = Path(__file__).resolve().parents[2]


def settings(**kw):
    return Settings(_env_file=None, primary_llm_provider='multiai', primary_llm_api_key='private-key',
        primary_llm_model='gpt-6.1-sol', primary_llm_base_url='https://primary.test/v1',
        llm_performance_enabled=True, llm_combined_enabled=True, llm_fallback_enabled=False,
        **kw)


def ready(process=None):
    return ProcessPreparation(status='ready', analysis=AmbiguityAnalysis(), process=process or demo_process(0))


def transport(monkeypatch, handle):
    original = httpx.AsyncClient
    clients = []
    def make(**kw):
        client = original(transport=httpx.MockTransport(handle), **kw)
        clients.append(client)
        return client
    monkeypatch.setattr(httpx, 'AsyncClient', make)
    return clients


def answer(value):
    return httpx.Response(200, json={'usage': {'prompt_tokens': 120, 'completion_tokens': 90},
        'choices': [{'message': {'content': value.model_dump_json()}, 'finish_reason': 'stop'}]})


def test_combined_ready_one_call_full_validation(monkeypatch):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        assert calls[-1]['response_format']['json_schema']['name'] == 'ProcessPreparation'
        return answer(ready())
    clients = transport(monkeypatch, handle)
    app = create_app(settings())
    with TestClient(app) as client:
        result = client.post('/api/process/generate', json={'text': 'Оператор обрабатывает заявку.'})
        assert result.status_code == 200
        assert len(calls) == 1
        assert not [i for i in validate_document(result.json()['xml'], complete_di=True) if i.severity == 'error']
        report = app.state.performance_profiles[result.headers['X-Request-ID']]
        assert report['attempt_count'] == 1
        assert report['input_tokens'] == 120 and report['output_tokens'] == 90
        assert report['corrective_retry_count'] == 0
        assert report['serialization_ms'] > 0
        assert 'private-key' not in json.dumps(report)
    assert clients[0].is_closed


def test_combined_critical_has_no_graph_or_builder(monkeypatch):
    q = Ambiguity(id='order', question='Проверки параллельны?', type='unclear_parallelism')
    result = ProcessPreparation(status='clarification_required', analysis=AmbiguityAnalysis(ambiguities=[q]), process=None)
    transport(monkeypatch, lambda request: answer(result))
    monkeypatch.setattr('app.pipeline.build_bpmn', lambda p: pytest.fail('critical must not build a graph'))
    with TestClient(create_app(settings())) as client:
        data = client.post('/api/process/generate', json={'text': 'Юрист и инженер проверяют заявку.'}).json()
    assert data['process'] is None and data['xml'] is None
    assert data['preflight']['analysis']['ambiguities'][0]['id'] == 'order'
    assert data['timings']['total_ms'] > 0


@pytest.mark.parametrize('status,analysis,process', [
    ('ready', AmbiguityAnalysis(), None),
    ('ready', AmbiguityAnalysis(ambiguities=[Ambiguity(id='q', question='Кто?', type='unclear_participant')]), demo_process(0)),
    ('clarification_required', AmbiguityAnalysis(), None),
    ('clarification_required', AmbiguityAnalysis(ambiguities=[Ambiguity(id='q', question='Кто?', type='unclear_participant')]), demo_process(0))])
def test_preparation_gate_rejects_inconsistent_status(status, analysis, process):
    with pytest.raises(ValidationError):
        ProcessPreparation(status=status, analysis=analysis, process=process)


def test_invalid_json_one_corrective_before_fallback(monkeypatch):
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'invalid'}}]}) if len(calls) == 1 else answer(ready())
    transport(monkeypatch, handle)
    with TestClient(create_app(settings(llm_max_retries=2))) as client:
        result = client.post('/api/process/generate', json={'text': 'Оператор обрабатывает заявку.'})
    assert result.status_code == 200 and len(calls) == 2
    assert 'Исправь' in calls[1]['messages'][-1]['content']


@pytest.mark.parametrize('failure', ['timeout', 429, 500, 502, 503, 504])
def test_transport_error_immediate_fallback(monkeypatch, failure):
    primary_calls = []
    def handle(request):
        primary_calls.append(request.url.host)
        if failure == 'timeout':
            raise httpx.ReadTimeout('private-key', request=request)
        return httpx.Response(failure, text='private-key')
    transport(monkeypatch, handle)
    router = get_llm_provider(settings())
    class Backup(MockLLMProvider):
        name = 'backup'
        supports_preparation = True
        combined_enabled = True
        async def prepare_process(self, *args):
            self.attempt_count += 1
            return ready()
    router.fallback = Backup()
    with TestClient(create_app(settings(), router)) as client:
        response = client.post('/api/process/generate', json={'text': 'Оператор обрабатывает заявку.'})
    assert response.status_code == 200
    assert primary_calls == ['primary.test']
    assert response.json()['metadata']['fallback_used']
    assert 'private-key' not in response.text


def test_deadline_reserves_fallback_and_does_not_reset_on_retry():
    class Slow(MockLLMProvider):
        name = 'slow'
        async def parse_process(self, *args):
            await asyncio.sleep(1)
            return demo_process(0)
    class Fast(MockLLMProvider):
        async def parse_process(self, *args):
            return demo_process(0)
    async def check():
        router = LLMRouter(Slow(), Fast(), budget=0.05, fallback_reserve=0.02)
        result = await router.parse_process('Текст')
        deadline = router._deadline
        assert result.nodes and router.metadata.fallback_used
        await router.parse_process('Текст')
        assert router._deadline == deadline
    asyncio.run(check())


@pytest.mark.parametrize('kind', ['warning', 'doctor'])
def test_warnings_never_request_corrective(monkeypatch, kind):
    calls = []
    async def call(correction):
        calls.append(correction)
        return demo_process(0)
    if kind == 'warning':
        from app.models import Issue
        monkeypatch.setattr('app.pipeline.validate_semantics', lambda p: [Issue(severity='warning', source='BUSINESS_LOGIC', message='Рекомендация')])
    result = asyncio.run(generate_valid(call))
    assert result['xml'] and calls == ['']


def test_cache_hit_session_prompt_answers_and_config_isolation():
    async def check():
        cache = ResultCache(capacity=2)
        calls = 0
        async def compute():
            nonlocal calls
            calls += 1
            return {'xml': '<validated/>', 'process': {'value': calls}}
        first = await cache.get_or_compute('session-a', 'generate', {'answers': 'a'}, 'v1', compute)
        first['process']['value'] = 999
        cached = await cache.get_or_compute('session-a', 'generate', {'answers': 'a'}, 'v1', compute)
        assert cached['process']['value'] == 1 and calls == 1
        await cache.get_or_compute('session-b', 'generate', {'answers': 'a'}, 'v1', compute)
        await cache.get_or_compute('session-a', 'generate', {'answers': 'a'}, 'v2', compute)
        await cache.get_or_compute('session-a', 'generate', {'answers': 'b'}, 'v2', compute)
        assert calls == 4 and len(cache.entries) == 2
    asyncio.run(check())


def test_failures_are_not_cached_and_singleflight_deduplicates():
    async def check():
        cache = ResultCache()
        calls = 0
        async def compute():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return {'xml': '<validated/>'}
        results = await asyncio.gather(*(cache.get_or_compute('session', 'generate', {}, 'v1', compute) for _ in range(3)))
        assert calls == 1 and len(results) == 3
        async def bad():
            nonlocal calls
            calls += 1
            raise ProviderError('timeout')
        for _ in range(2):
            with pytest.raises(ProviderError):
                await cache.get_or_compute('session', 'generate', {}, 'failed', bad)
        assert calls == 3 and not cache.inflight
        assert not any('failed' in key for key in cache.entries)
    asyncio.run(check())


def test_operation_specific_budgets_reasoning_and_connection_reuse(monkeypatch):
    bodies = []
    def handle(request):
        body = json.loads(request.content)
        bodies.append(body)
        name = body['response_format']['json_schema']['name']
        return answer(AmbiguityAnalysis() if name == 'AmbiguityAnalysis' else AuditResult() if name == 'AuditResult' else demo_process(0))
    clients = transport(monkeypatch, handle)
    router = get_llm_provider(settings(llm_parse_max_tokens=5000, llm_modify_max_tokens=4500,
        llm_parse_reasoning_effort='medium', llm_clarify_reasoning_effort='low', llm_doctor_reasoning_effort='medium'))
    async def check():
        await router.analyze_ambiguities('Текст')
        await router.parse_process('Текст')
        await router.modify_process(demo_process(0), 'Изменить название')
        await router.audit_process(demo_process(0))
        await router.close()
    asyncio.run(check())
    assert [b['max_tokens'] for b in bodies] == [1500, 5000, 4500, 3000]
    assert bodies[0]['reasoning_effort'] == 'low' and bodies[1]['reasoning_effort'] == 'medium'
    assert len(clients) == 1
    assert 'JSON Schema:' not in bodies[0]['messages'][0]['content']


def test_compact_representation_preserves_all_canonical_business_data():
    process = demo_process(0)
    compact = compact_process(process)
    assert ProcessDefinition.model_validate(compact) == process
    assert len(json.dumps(compact, ensure_ascii=False, separators=(',', ':'))) < len(process.model_dump_json())


def test_performance_full_graph_prompts_keep_every_existing_bpmn_rule():
    from app.services.llm.common import performance_prompt, prompt_text
    extraction = prompt_text('extraction')
    assert performance_prompt('extraction') == extraction
    assert extraction in performance_prompt('corrective')
    assert performance_prompt('modification') == prompt_text('modification')
    assert performance_prompt('clarification') == prompt_text('clarification')


def test_singleflight_retention_is_bounded_under_unique_concurrent_requests():
    async def check():
        cache = ResultCache(max_inflight=1)
        release = asyncio.Event()
        async def slow():
            await release.wait()
            return {'xml': '<validated/>'}
        first = asyncio.create_task(cache.get_or_compute('session', 'generate', {'text': 'first'}, 'v1', slow))
        await asyncio.sleep(0)
        second = asyncio.create_task(cache.get_or_compute('session', 'generate', {'text': 'second'}, 'v1', slow))
        await asyncio.sleep(0)
        assert len(cache.inflight) == 1
        release.set()
        await asyncio.gather(first, second)
        assert not cache.inflight
    asyncio.run(check())


def test_env_reload_preserves_transport_until_existing_request_finishes(monkeypatch):
    class Tracked(MockLLMProvider):
        closed = 0
        def new_request(self):
            return self
        async def close(self):
            self.closed += 1
    active = [settings()]
    created = []
    monkeypatch.setattr('app.main.Settings', lambda: active[0])
    def make(_):
        selected = Tracked()
        created.append(selected)
        return selected
    monkeypatch.setattr('app.main.get_llm_provider', make)
    app = create_app()
    route = next(r for r in app.routes if r.path == '/api/process/generate')
    dependency = route.dependant.dependencies[0].call
    async def check():
        first = dependency()
        _, before = await anext(first)
        active[0] = active[0].model_copy(update={'primary_llm_model': 'replacement-model'})
        second = dependency()
        _, after = await anext(second)
        assert before is created[0] and after is created[1]
        assert before.closed == 0
        await first.aclose()
        assert before.closed == 1 and after.closed == 0
        await second.aclose()
        assert not app.state.retired_providers
    asyncio.run(check())


def test_patch_changes_only_declared_elements_and_preserves_every_id():
    previous = demo_process(0)
    changed = previous.nodes[1].model_copy(update={'name': 'Проверить комплектность'})
    patch = ProcessPatch(nodes=[changed])
    output = patch.apply(previous)
    assert [n.id for n in output.nodes] == [n.id for n in previous.nodes]
    assert output.nodes[1].name == 'Проверить комплектность'
    assert output.flows == previous.flows and output.participants == previous.participants
    assert output.nodes[:1] + output.nodes[2:] == previous.nodes[:1] + previous.nodes[2:]
    assert previous.nodes[1].name != output.nodes[1].name


@pytest.mark.parametrize('patch', [ProcessPatch(remove_node_ids=['missing']),
    ProcessPatch(nodes=[demo_process(0).nodes[0], demo_process(0).nodes[0]]),
    ProcessPatch(remove_node_ids=[demo_process(0).nodes[0].id], nodes=[demo_process(0).nodes[0]])])
def test_patch_rejects_unknown_duplicate_or_conflicting_edits(patch):
    with pytest.raises(ValueError):
        patch.apply(demo_process(0))


def test_patch_cannot_mutate_graph_while_asking_critical_question():
    patch = ProcessPatch(nodes=[demo_process(0).nodes[0]], ambiguities=[Ambiguity(id='q', question='Кто?', type='unclear_participant')])
    with pytest.raises(ValueError):
        patch.apply(demo_process(0))


def test_corrective_patch_cannot_drop_business_actions_but_explicit_modify_can():
    previous = demo_process(0)
    task = next(n for n in previous.nodes if n.type.endswith('task'))
    patch = ProcessPatch(remove_node_ids=[task.id])
    with pytest.raises(ValueError, match='Corrective'):
        patch.apply(previous, allow_business_removal=False)
    assert task.id not in {n.id for n in patch.apply(previous).nodes}


def test_explicit_unknown_is_only_a_cheap_schema_signal(monkeypatch):
    calls = []
    q = Ambiguity(id='q', question='Кто проверяет?', type='unclear_participant')
    def handle(request):
        body = json.loads(request.content)
        calls.append(body['response_format']['json_schema']['name'])
        return answer(AmbiguityAnalysis(ambiguities=[q]))
    transport(monkeypatch, handle)
    with TestClient(create_app(settings())) as client:
        result = client.post('/api/process/generate', json={'text': 'Исполнитель повторной проверки не указан.'})
    assert result.status_code == 200
    assert calls == ['AmbiguityAnalysis']
    assert result.json()['process'] is None and result.json()['xml'] is None
    assert result.json()['metadata']['attempts'] == 1


def test_long_role_names_preserve_business_graph_and_complete_di():
    from app.bpmn import build_bpmn
    process = demo_process(0)
    for index, role in enumerate(process.participants):
        role.name = ('Руководитель подразделения эксплуатации энергетического оборудования ' * 2) + str(index)
    xml = build_bpmn(process)
    assert all(role.name in xml for role in process.participants)
    assert not [i for i in validate_document(xml, complete_di=True) if i.severity == 'error']


def test_modify_patch_combined_gate_one_call_and_canonical_result(monkeypatch):
    previous = demo_process(0)
    changed = previous.nodes[1].model_copy(update={'name': 'Проверить комплектность'})
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        assert calls[-1]['response_format']['json_schema']['name'] == 'ModificationPreparation'
        return answer(ModificationPreparation(status='ready', analysis=AmbiguityAnalysis(), patch=ProcessPatch(nodes=[changed])))
    transport(monkeypatch, handle)
    with TestClient(create_app(settings(llm_patch_enabled=True))) as client:
        result = client.post('/api/process/modify', json={'process': previous.model_dump(), 'command': 'Переименуй проверку в Проверить комплектность'})
    assert result.status_code == 200 and len(calls) == 1
    process = ProcessDefinition.model_validate(result.json()['process'])
    assert {n.id for n in process.nodes} == {n.id for n in previous.nodes}
    assert process.nodes[1].name == 'Проверить комплектность'
    assert result.json()['changes'] and result.json()['xml']


def test_corrective_uses_patch_schema_and_does_not_repeat_business_extraction(monkeypatch):
    process = demo_process(0)
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        assert calls[-1]['response_format']['json_schema']['name'] == 'ProcessPatch'
        assert 'не возвращай весь процесс' in calls[-1]['messages'][0]['content'].lower()
        assert 'irrelevant-original-description' not in calls[-1]['messages'][1]['content']
        return answer(ProcessPatch())
    transport(monkeypatch, handle)
    router = get_llm_provider(settings(llm_patch_enabled=True))
    async def check():
        result = await router.parse_process('irrelevant-original-description', json.dumps({'previous_result': compact_process(process), 'errors': [{'code': 'SOME_ERROR'}]}))
        await router.close()
        return result
    assert asyncio.run(check()) == process


@pytest.mark.parametrize('model', [ProcessDefinition, ProcessPatch, ProcessPreparation, ModificationPreparation])
def test_compact_wire_is_lossless_and_keeps_schema_constraints(model):
    from tools.experimental_wire import wire_model
    from app.services.llm.openai_compatible_provider import strict_schema
    value = demo_process(0) if model is ProcessDefinition else ProcessPatch(nodes=[demo_process(0).nodes[1]]) if model is ProcessPatch else ready() if model is ProcessPreparation else ModificationPreparation(status='ready', analysis=AmbiguityAnalysis(), patch=ProcessPatch())
    transport_model = wire_model(model)
    encoded = transport_model.model_validate(value.model_dump()).model_dump_json(by_alias=True)
    decoded = transport_model.model_validate_json(encoded)
    assert model.model_validate(decoded.model_dump()) == value
    schema = strict_schema(transport_model)
    for definition in schema.get('$defs', {}).values():
        if definition.get('type') == 'object':
            assert set(definition['required']) == set(definition['properties'])
            assert definition['additionalProperties'] is False


@pytest.mark.parametrize('fixture', json.loads((ROOT / 'examples/performance/golden.json').read_text(encoding='utf-8')))
def test_golden_business_invariants_and_complete_local_validation(fixture):
    raw = json.loads((ROOT / fixture['path']).read_text(encoding='utf-8'))
    source = ProcessDefinition.model_validate(raw.get('process', raw))
    async def resolved(_):
        return source
    started = perf_counter()
    result = asyncio.run(generate_valid(resolved))
    assert perf_counter() - started < 3  # Local work only; never asserts real API latency.
    output = result['process']
    assert len(output.nodes) == fixture['nodes']
    assert len(output.pools) == fixture['pools']
    assert len(output.message_flows) == fixture['message_flows']
    assert sorted(set(n.type for n in output.nodes)) == fixture['node_types']
    # Manifest records the existing normalized export baseline. Technical wait
    # placeholders may already become message starts/event gateways, so compare
    # the complete baseline topology and evidence, not the pre-normalization DTO.
    # Fingerprints were frozen by the ORIGINAL commit's pipeline, not generated
    # by the implementation under test. All names, roles, evidence, IDs, conditions,
    # messages and ambiguities must match that complete business baseline.
    canonical = json.dumps(output.model_dump(mode='json'), sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    assert hashlib.sha256(canonical.encode()).hexdigest() == fixture['normalized_sha256']
    assert not [i for i in validate_document(result['xml'], complete_di=True) if i.severity == 'error']
