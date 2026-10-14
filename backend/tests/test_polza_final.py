import asyncio
import json
from time import monotonic

import httpx
import pytest

from app.config import Settings
from app.examples import demo_process
from app.business_coverage import CoverageGap, CoverageReport, coverage_issues
from app.services.llm.factory import get_llm_provider
from app.services.llm.base import ProviderError


def config(**kw):
    return Settings(_env_file=None, primary_llm_provider='polza', primary_llm_model='openai/gpt-6-luna',
        primary_llm_api_key='primary-private', fallback_llm_provider='multiai', fallback_llm_model='gpt-6-sol',
        fallback_llm_api_key='backup-private', llm_performance_enabled=True, **kw)


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_generation_and_coverage_provenance_are_separate(monkeypatch):
    def handler(request):
        body=json.loads(request.content)
        coverage=body['response_format']['json_schema']['name']=='CoverageReport'
        if coverage and request.url.host=='polza.ai':
            return httpx.Response(429)
        result=CoverageReport() if coverage else demo_process(0)
        return httpx.Response(200,json={'model':'gpt-6-sol' if coverage else 'actual-luna',
            'usage':{'prompt_tokens':10,'completion_tokens':20,'cost_rub':0.1},
            'choices':[{'message':{'content':result.model_dump_json()}}]})
    transport(monkeypatch,handler)
    async def run():
        router=get_llm_provider(config())
        try:
            process=await router.parse_process('Оператор проверяет заявку.')
            await router.check_business_coverage(process,'Оператор проверяет заявку.')
            metadata=router.metadata
            assert metadata.provider_requested=='polza'
            assert metadata.model_requested=='openai/gpt-6-luna'
            assert metadata.provider_used=='polza' and metadata.model_used=='actual-luna'
            assert metadata.business_coverage_provider_used=='multiai'
            assert metadata.business_coverage_model_used=='gpt-6-sol'
            assert metadata.fallback_reason=='http_429'
            assert metadata.cost_rub is None  # Unknown failed-call usage is not a fake zero.
            assert 'private' not in metadata.model_dump_json()
        finally: await router.close()
    asyncio.run(run())


def test_success_metadata_actual_usage(monkeypatch):
    transport(monkeypatch,lambda r:httpx.Response(200,json={'model':'actual-model',
        'usage':{'prompt_tokens':12,'completion_tokens':34,'cost_rub':0.056},
        'choices':[{'message':{'content':demo_process(0).model_dump_json()}}]}))
    async def run():
        router=get_llm_provider(config())
        try:
            await router.parse_process('Оператор проверяет заявку.')
            assert router.metadata.input_tokens==12
            assert router.metadata.output_tokens==34
            assert router.metadata.cost_rub==0.056
            assert router.metadata.model_used=='actual-model'
            assert router.metadata.fallback_reason is None
        finally: await router.close()
    asyncio.run(run())


def test_expired_deadline_never_contacts_backup(monkeypatch):
    calls=[]
    transport(monkeypatch,lambda request:calls.append(request.url.host) or httpx.Response(503))
    async def run():
        router=get_llm_provider(config())
        router._deadline=monotonic()-1
        try:
            with pytest.raises(ProviderError): await router.parse_process('Текст')
            assert not calls
        finally: await router.close()
    asyncio.run(run())


def test_local_profile_is_explicit_and_does_not_override_test_settings(tmp_path,monkeypatch):
    local=tmp_path/'local.env'; local.write_text('PRIMARY_LLM_PROVIDER=polza\nPRIMARY_LLM_MODEL=test-model\n')
    monkeypatch.setenv('PULSE_ENV_FILE',str(local))
    assert Settings().primary_llm_provider=='polza'
    assert Settings().primary_llm_model=='test-model'
    assert Settings(_env_file=None).primary_llm_provider==''


@pytest.mark.parametrize('provider',['polza','multiai'])
@pytest.mark.parametrize('kind',['missing_action','missing_role','missing_condition','missing_message','wrong_relationship'])
def test_coverage_golden_categories_and_valid_representation(monkeypatch,provider,kind):
    source='Оператор исправляет документы и повторяет проверку.'
    report=CoverageReport(gaps=[CoverageGap(kind=kind,source_excerpt=source,message='Нарушена логика',suggested_fix='Восстановить действие и возврат',node_ids=['Check'])])
    result=report
    def handler(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':result.model_dump_json()}}]})
    transport(monkeypatch,handler)
    async def run():
        s=config().model_copy(update={'primary_llm_provider':provider,'primary_llm_base_url':'https://test.invalid/v1','llm_fallback_enabled':False})
        router=get_llm_provider(s)
        try:
            reviewed=await router.check_business_coverage(demo_process(0),source)
            assert coverage_issues(reviewed,demo_process(0),source)[0].code=='BUSINESS_'+kind.upper()
            nonlocal result
            result=CoverageReport()
            assert (await router.check_business_coverage(demo_process(0),source)).gaps==[]
        finally: await router.close()
    asyncio.run(run())



def test_deadline_is_not_disabled_by_performance_switch():
    router=get_llm_provider(
        config().model_copy(update={'llm_performance_enabled':False,'llm_request_budget':45}))
    assert router.budget==45



def test_empty_optional_profile_values_use_auto_and_inherited_reasoning(tmp_path):
    profile=tmp_path/'profile.env'
    profile.write_text('LLM_STRUCTURED_OUTPUT=\nLLM_BUSINESS_COVERAGE_REASONING_EFFORT=\nLLM_REASONING_EFFORT=low\n')
    settings=Settings(_env_file=profile)
    assert settings.llm_structured_output is None
    assert settings.llm_business_coverage_reasoning_effort is None
    assert settings.llm_reasoning_effort=='low'



def test_small_remaining_budget_cancels_both_operations_without_reset(monkeypatch):
    events=[]
    async def handler(request):
        events.append(request.url.host)
        await asyncio.sleep(1)
        return httpx.Response(200,json={'choices':[{'message':{'content':demo_process(0).model_dump_json()}}]})
    transport(monkeypatch,handler)
    async def run():
        router=get_llm_provider(config())
        router.budget=0.03
        router.fallback_reserve=0.01
        started=monotonic()
        try:
            with pytest.raises(ProviderError): await router.parse_process('Текст')
            assert monotonic()-started<0.5
            assert router._deadline<=started+0.05
            assert len(events)<=2
        finally: await router.close()
    asyncio.run(run())



@pytest.mark.parametrize('invalid',[True,-1,1.5,float('nan'),float('inf'),'12'])
def test_untrusted_usage_never_breaks_bpmn_metadata(invalid):
    router=get_llm_provider(config())
    router._metrics=[{'input_tokens':invalid,'output_tokens':invalid,'cost_rub':None}]
    assert router.metadata.input_tokens is None
    assert router.metadata.output_tokens is None
    assert router.metadata.cost_rub is None
