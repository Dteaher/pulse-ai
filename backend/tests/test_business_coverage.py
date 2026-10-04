import asyncio
import json
import httpx
import pytest
from app.business_coverage import CoverageGap, CoverageReport, coverage_issues
from app.examples import demo_process
from app.models import Flow, Node, Ambiguity
from app.pipeline import generate_valid
from app.services.llm.base import ProviderError
from app.services.llm.multiai_provider import MultiAIProvider
from app.services.llm.router import LLMRouter

SOURCE = 'Клиент отправляет заявку. Менеджер проверяет заявку. Менеджер исправляет документы. После проверки менеджер принимает решение и уведомляет клиента.'

def corrected_process():
    p=demo_process(0)
    p.nodes.append(Node(id='FixDocuments', type='user_task', name='Исправить документы', participant_id='Manager', source_text='Менеджер исправляет документы.'))
    f=next(f for f in p.flows if f.source=='Check')
    original=f.target; f.target='FixDocuments'
    p.flows.append(Flow(id='AfterFix',source='FixDocuments',target=original))
    return p

def gap(**changes):
    return CoverageGap(kind='missing_action',source_excerpt='Менеджер исправляет документы.',message='Пропущено исправление документов менеджером.',suggested_fix='Добавить исправление после проверки заявки.',**changes)

@pytest.mark.parametrize('bad', ['quote','reference'])
def test_unverifiable_business_feedback_is_rejected(bad):
    g=gap(node_ids=['invented'] if bad=='reference' else [])
    if bad=='quote':g.source_excerpt='Клиент оплачивает договор.'
    with pytest.raises(ProviderError):coverage_issues(CoverageReport(gaps=[g]),demo_process(0),SOURCE)

@pytest.mark.parametrize('success',[True,False])
def test_wire_coverage_drives_bounded_corrective_and_rechecks(monkeypatch,success):
    bodies=[]; generation=0
    def handle(request):
        nonlocal generation
        b=json.loads(request.content); bodies.append(b)
        kind=b['response_format']['json_schema']['name']
        if kind=='CoverageReport':
            payload=json.loads(b['messages'][1]['content'])
            assert payload['source_text']==SOURCE
            assert payload['source_text']!=payload['process']['description']
            complete=any(n['id']=='FixDocuments' for n in payload['process']['nodes'])
            result=CoverageReport(gaps=[] if complete else [gap(node_ids=['Check'])])
        else:
            generation+=1
            if generation>1:
                correction=json.loads(b['messages'][1]['content'])
                assert correction['business_context']['source_text']==SOURCE
                assert correction['errors'][0]['code']=='BUSINESS_MISSING_ACTION'
            result=corrected_process() if success and generation>1 else demo_process(0)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':result.model_dump_json()}}]})
    async def run():
        p=MultiAIProvider('test','https://test.invalid/v1','test',max_retries=0)
        p.business_coverage_enabled=True;p.performance_enabled=True
        p._clients[asyncio.get_running_loop()]=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        router=LLMRouter(p,budget=30)
        async def call(correction):return await router.parse_process(SOURCE,correction)
        try:
            if success:
                result=await generate_valid(call,provider=router,source_text=SOURCE,max_retries=1)
                assert result['xml'] and any(n.id=='FixDocuments' for n in result['process'].nodes)
            else:
                with pytest.raises(ProviderError) as e:await generate_valid(call,provider=router,source_text=SOURCE,max_retries=1)
                assert e.value.diagnostics[0].code=='BUSINESS_MISSING_ACTION'
        finally:await router.close()
    asyncio.run(run())
    assert [b['response_format']['json_schema']['name'] for b in bodies]==['ProcessDefinition','CoverageReport','ProcessDefinition','CoverageReport']


def test_critical_question_does_not_trigger_coverage_or_guessing():
    p=demo_process(0)
    p.ambiguities=[Ambiguity(id='q',question='Каков неизвестный исход?',type='unclear_end',severity='critical')]
    class Reviewer:
        business_coverage_enabled=True
        async def check_business_coverage(self,*args):pytest.fail('critical question bypassed')
    async def call(_):return p
    result=asyncio.run(generate_valid(call,provider=Reviewer(),source_text=SOURCE))
    assert result['xml'] is None and result['ambiguities']


def test_answer_is_accepted_as_evidence():
    g=gap()
    issues=coverage_issues(CoverageReport(gaps=[g]),demo_process(0),'Описание без ответа.',{'q':g.source_excerpt})
    assert issues[0].severity=='error'

@pytest.mark.parametrize('fixed', [True, False])
def test_reviewer_evidence_correction_is_bounded_and_revalidated(fixed):
    from app.services.llm.common import checked_coverage
    calls=[]
    async def call(correction):
        calls.append(correction)
        return CoverageReport(gaps=[gap(node_ids=['Check'] if fixed and len(calls)>1 else ['invented'])])
    if fixed:
        result=asyncio.run(checked_coverage(call,demo_process(0),SOURCE,{},2))
        assert result.gaps[0].node_ids==['Check']
    else:
        with pytest.raises(ProviderError):asyncio.run(checked_coverage(call,demo_process(0),SOURCE,{},2))
    assert len(calls)==2 and calls[1]


def test_create_endpoint_runs_coverage_against_request_and_returns_repaired_bpmn():
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    from app.services.llm.mock_provider import MockLLMProvider
    class CoverageFixture(MockLLMProvider):
        business_coverage_enabled=True
        def __init__(self):self.count=0;self.sources=[]
        async def parse_process(self,text,correction=''):
            self.count+=1
            if self.count>1:
                assert json.loads(correction)['business_context']['source_text']==SOURCE
            return corrected_process() if self.count>1 else demo_process(0)
        async def check_business_coverage(self,p,source_text,answers=None):
            self.sources.append(source_text)
            return CoverageReport(gaps=[] if any(n.id=='FixDocuments' for n in p.nodes) else [gap(node_ids=['Check'])])
    provider=CoverageFixture()
    with TestClient(create_app(Settings(_env_file=None,llm_business_coverage_enabled=True),provider)) as client:
        response=client.post('/api/process/generate',json={'text':SOURCE})
    assert response.status_code==200 and response.json()['xml']
    assert provider.sources==[SOURCE,SOURCE] and provider.count==2
