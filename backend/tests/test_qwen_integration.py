"""Deterministic provider/pipeline regression, no paid API in unit tests."""
import asyncio
import json
from pathlib import Path
import httpx
import pytest
from app.config import Settings
from app.examples import demo_process
from app.models import ProcessDefinition
from app.pipeline import generate_valid
from app.services.llm.factory import get_llm_provider
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.services.llm.base import ProviderError
from app.services.llm.raw_debug import save_raw
from app.xml_validation import validate_document
from app.repair import quality_fingerprint
from tools.quality_oracle import quality


def provider(monkeypatch,handler,**settings):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    s=Settings(_env_file=None,primary_llm_provider='multiai',primary_llm_model='qwen-test',
               primary_llm_api_key='private-key',llm_fallback_enabled=False,llm_performance_enabled=True,
               llm_max_tokens=8000,llm_max_retries=1,**settings)
    return get_llm_provider(s).primary


@pytest.mark.parametrize('content',[None,'','{"participants":['])
def test_length_never_parses_or_normal_corrects_content(monkeypatch,content):
    calls=[]
    p=provider(monkeypatch,lambda r:(calls.append(r) or httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':content}}]})))
    monkeypatch.setattr('app.services.llm.openai_compatible_provider.validated_json',lambda *a:pytest.fail('truncated output parsed'))
    with pytest.raises(ProviderError) as e: asyncio.run(p.parse_process('Описание'))
    assert e.value.reason=='output_truncated' and len(calls)==1


@pytest.mark.parametrize('repeat',[False,True])
def test_controlled_budget_retry_once_and_never_json_corrective(monkeypatch,repeat):
    bodies=[]
    def handle(request):
        bodies.append(json.loads(request.content))
        truncated=len(bodies)==1 or repeat
        return httpx.Response(200,json={'choices':[{'finish_reason':'length' if truncated else 'stop',
            'message':{'content':'{"broken":' if truncated else demo_process().model_dump_json()}}]})
    p=provider(monkeypatch,handle,llm_output_retry_enabled=True)
    if repeat:
        with pytest.raises(ProviderError) as e:asyncio.run(p.parse_process('Описание'))
        assert e.value.reason=='output_truncated'
    else:assert asyncio.run(p.parse_process('Описание'))==demo_process()
    assert [b['max_tokens'] for b in bodies]==[8000,12000]
    assert bodies[0]['messages']==bodies[1]['messages']


def test_output_retry_respects_remaining_deadline(monkeypatch):
    async def run():
        p=OpenAICompatibleProvider('key','https://test/v1','test',timeout=.04,max_tokens=8000)
        p.output_retry_enabled=True
        calls=0
        async def once(*a):
            nonlocal calls
            calls+=1
            if calls==1:
                await asyncio.sleep(.02)
                raise ProviderError('truncated',reason='output_truncated')
            await asyncio.sleep(.1)
            pytest.fail('deadline ignored')
        p._request_once=once
        with pytest.raises(ProviderError) as e:await p.parse_process('Text')
        assert e.value.reason=='timeout' and calls==2
    asyncio.run(run())


def test_parse_override_on_wire_and_other_operations_unchanged(monkeypatch):
    budgets=[]
    def handle(request):
        b=json.loads(request.content);budgets.append(b['max_tokens'])
        name=b['response_format']['json_schema']['name']
        content='{"ambiguities":[],"assumptions":[],"superseded_assumption_ids":[]}' if name=='AmbiguityAnalysis' else '{"issues":[]}' if name=='AuditResult' else demo_process().model_dump_json()
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':content}}]})
    p=provider(monkeypatch,handle,llm_parse_max_tokens=16000,llm_clarify_max_tokens=1500,llm_modify_max_tokens=4500,llm_doctor_max_tokens=3000)
    async def run():
        await p.parse_process('Text');await p.analyze_ambiguities('Text');await p.modify_process(demo_process(),'Rename');await p.audit_process(demo_process());await p.close()
    asyncio.run(run())
    assert budgets==[16000,1500,4500,3000]


@pytest.mark.parametrize('omit', [False, True])
def test_primary_token_limit_can_be_omitted_without_changing_validation(monkeypatch, omit):
    bodies = []
    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': demo_process().model_dump_json()}}]})
    p = provider(monkeypatch, handle, primary_llm_omit_token_limit=omit)
    async def run():
        assert await p.parse_process('Text') == demo_process()
        await p.close()
    asyncio.run(run())
    assert ('max_tokens' in bodies[0]) is not omit
    assert 'max_completion_tokens' not in bodies[0]
    assert bodies[0]['response_format']['type'] == 'json_schema'


def test_omitted_limit_does_not_repeat_same_unbounded_request(monkeypatch):
    calls = []
    p = provider(monkeypatch, lambda r: (calls.append(r) or httpx.Response(200,
        json={'choices': [{'finish_reason': 'length', 'message': {'content': ''}}]})),
        primary_llm_omit_token_limit=True, llm_output_retry_enabled=True)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(p.parse_process('Text'))
    assert exc.value.reason == 'output_truncated'
    assert len(calls) == 1


def test_empty_truncation_debug_has_priority(tmp_path,monkeypatch):
    monkeypatch.setattr('app.services.llm.raw_debug.DEBUG_DIR',tmp_path)
    path=save_raw(provider='test',model='test',status=200,duration_ms=1,body={'max_tokens':8000},
        response={'choices':[{'finish_reason':'length','message':{'content':''}}]},model_class=ProcessDefinition)
    raw=json.loads(path.read_text(encoding='utf-8'))
    assert raw['analysis']['classification']=='OUTPUT_TRUNCATED' and raw['response_length']==0


@pytest.mark.parametrize('name',['medium','exact-complex-polished'])
def test_frozen_golden_same_qwen_contract_and_full_pipeline(monkeypatch,name):
    root=Path(__file__).resolve().parents[2]
    p=ProcessDefinition.model_validate(json.loads((root/f'examples/pipeline-resilience/{name}-response.json').read_text(encoding='utf-8'))['process'])
    adapter=provider(monkeypatch,lambda r:httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':p.model_dump_json()}}]}))
    async def run():return await generate_valid(lambda _:adapter.parse_process('Fixture transport'),max_retries=0)
    r=asyncio.run(run())
    assert r['xml'] and not [i for i in validate_document(r['xml'],complete_di=True) if i.severity=='error']
    if name=='exact-complex-polished':assert quality(r['process'],'complex')['passed']
    assert quality_fingerprint(p)==quality_fingerprint(ProcessDefinition.model_validate_json(p.model_dump_json()))


def test_minimal_prompt_hint_is_opt_in_not_a_model_id_branch(monkeypatch):
    bodies=[]
    p=provider(monkeypatch,lambda r:(bodies.append(json.loads(r.content)) or httpx.Response(200,json={'choices':[{'message':{'content':demo_process().model_dump_json()}}]})))
    p.prompt_hints=('Different IDs for pool and participant.',)
    asyncio.run(p.parse_process('Text'))
    assert bodies[0]['messages'][0]['content'].endswith(p.prompt_hints[0])
    assert not OpenAICompatibleProvider('key','https://test','arbitrary').prompt_hints


@pytest.mark.parametrize('critical',[True,False])
def test_clarify_and_explicit_actor_gate_without_building_graph(monkeypatch,critical):
    from app.preflight import prepare
    from app.models import AmbiguityAnalysis,Ambiguity
    questions=[Ambiguity(id='actor',question='Кто повторяет проверку?',type='unclear_participant'),
               Ambiguity(id='parallel',question='Параллельно или последовательно?',type='unclear_parallelism')] if critical else []
    analysis=AmbiguityAnalysis(ambiguities=questions)
    p=provider(monkeypatch,lambda r:httpx.Response(200,json={'choices':[{'message':{'content':analysis.model_dump_json()}}]}))
    state,_,graph=asyncio.run(prepare(p,'Не указан проверяющий' if critical else 'Руководитель принимает решение. Юридический отдел и служба безопасности одновременно проверяют заявку.'))
    assert graph is None and bool(state.analysis.ambiguities)==critical


def test_golden_roles_actions_parallel_messages_and_fingerprint():
    from tools.qwen_quality import complex_quality,semantic_fingerprint
    root=Path(__file__).resolve().parents[2]
    p=ProcessDefinition.model_validate(json.loads((root/'examples/pipeline-resilience/exact-complex-polished-response.json').read_text(encoding='utf-8'))['process'])
    assert complex_quality(p)['passed']
    renamed=p.model_copy(deep=True)
    for n in renamed.nodes:n.name=n.name.replace('Проверить документы','Проверка документов')
    assert semantic_fingerprint(p)==semantic_fingerprint(renamed)
    merged=p.model_copy(deep=True)
    next(r for r in merged.participants if r.name=='Руководитель').name='Менеджер'
    assert not complex_quality(merged)['passed']
    swapped=p.model_copy(deep=True)
    next(m for m in swapped.message_flows if 'отказ' in m.name.casefold()).name='Договор'
    next(n for n in swapped.nodes if 'уведомление об отказе' in n.name.casefold()).name='Получить договор'
    assert not complex_quality(swapped)['passed']


def test_revised_contract_message_accepts_correct_synonym_but_not_initial_contract():
    from tools.qwen_quality import complex_quality
    root=Path(__file__).resolve().parents[2]
    p=ProcessDefinition.model_validate(json.loads((root/'examples/pipeline-resilience/exact-complex-polished-response.json').read_text(encoding='utf-8'))['process'])
    m=next(m for m in p.message_flows if 'измен' in m.name.casefold())
    m.name='Исправленный договор'
    target=next(n for n in p.nodes if n.id==m.target)
    target.name='Получить исправленный договор'
    assert complex_quality(p)['passed']
    target.name='Получить первоначальный договор'
    assert not complex_quality(p)['invariants']['message_business_relationships']
