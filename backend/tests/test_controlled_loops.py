import asyncio,json,logging
from pathlib import Path
import pytest
from app.models import ProcessDefinition,Node,Flow,MessageFlow
from app.validator import validate_process
from app.semantics import validate_semantics
from app.quality import validate_labels
from app.bpmn import build_bpmn,validate_xml,import_process
from app.pipeline import generate_valid
from test_collaboration import collaboration_example
from test_clarify_modify import Scripted,client


def contract_process():
    p=collaboration_example()
    p.name='Заключение договора';p.description=(Path(__file__).resolve().parents[2]/'examples/controlled-loops/input.txt').read_text(encoding='utf8')
    p.nodes=[n for n in p.nodes if n.id not in {'ReceiveNotice','ClientNo','Decide','Approved','Contract','Reject','CompanyNo'}]
    p.flows=[f for f in p.flows if f.source not in {'Decide','Approved','Contract','Reject','ReceiveNotice'} and f.target not in {'Decide','Approved','Contract','Reject','CompanyNo','ClientNo','ReceiveNotice'}]
    p.message_flows=[f for f in p.message_flows if f.source!='Reject']
    specs=[('Price','user_task','Определить стоимость договора','Manager'),('Amount','exclusive_gateway','Стоимость больше 500 000 рублей?','Manager'),
    ('ApprovalMerge','exclusive_gateway','Передача на согласование','Head'),('Approve','user_task','Согласовать договор','Head'),('Approved','exclusive_gateway','Договор согласован?','Head'),
    ('Rework','user_task','Доработать договор','Manager'),('Resend','user_task','Отправить на повторное согласование','Manager'),('FinalMerge','exclusive_gateway','Подготовка отправки договора','Manager')]
    p.nodes += [Node(id=i,type=t,name=n,participant_id=r) for i,t,n,r in specs]
    edges=[('Join','Price',''),('Price','Amount',''),('Amount','ApprovalMerge','Стоимость > 500000'),('Amount','FinalMerge','Стоимость <= 500000'),('ApprovalMerge','Approve',''),('Approve','Approved',''),('Approved','Rework','Не согласовано'),('Rework','Resend',''),('Resend','ApprovalMerge',''),('Approved','FinalMerge','Согласовано'),('FinalMerge','Send','')]
    p.flows += [Flow(id=f'ContractFlow{i}',source=a,target=b,name=c,condition=c or None) for i,(a,b,c) in enumerate(edges)]
    return p


def test_complete_contract_loops_api_xml_and_roundtrip():
    p=contract_process()
    assert not validate_process(p)
    assert not any(i.severity=='error' for i in validate_semantics(p))
    response=client(Scripted([p])).post('/api/process/generate',json={'text':p.description})
    assert response.status_code==200,response.text
    xml=response.json()['xml'];validate_xml(xml)
    restored=import_process(xml,p)
    from app.notation import polish_process
    assert restored==polish_process(p)
    assert len(restored.pools)==2 and len(restored.message_flows)==4
    assert any(f.source=='Resend' and f.target=='ApprovalMerge' for f in restored.flows)
    assert any(f.source=='Correction' and f.target=='Merge' for f in restored.flows)


def test_mixed_xor_with_controlled_return_is_valid():
    p=contract_process()
    # The external reply decision accepts the first send and repeated send.
    next(n for n in p.nodes if n.id=='Reply').type='exclusive_gateway'
    next(n for n in p.nodes if n.id=='Reply').decision_basis='data'
    for f in p.flows:
        if f.source=='Reply':f.condition=f.name
    next(f for f in p.flows if f.source=='Resubmit').target='Reply'
    assert not validate_process(p)
    assert not any(i.severity=='error' for i in validate_semantics(p))



@pytest.mark.parametrize('fault,code',[('cross','CROSS_POOL_SEQUENCE_FLOW'),('message','SAME_POOL_MESSAGE_FLOW'),('orphan','UNREACHABLE_NODE'),('join','PARALLEL_JOIN_MISMATCH'),('reference','BROKEN_REFERENCE')])
def test_exact_errors_and_corrective_feedback(fault,code):
    good=contract_process();bad=good.model_copy(deep=True)
    if fault=='cross':bad.flows[0].target='Check'
    if fault=='message':bad.message_flows.append(MessageFlow(id='BadMessage',source='Resend',target='Approve'))
    if fault=='orphan':bad.flows=[f for f in bad.flows if f.target!='Check']
    if fault=='join':
        bad.nodes=[n for n in bad.nodes if n.id!='Join'];bad.flows=[f for f in bad.flows if f.source!='Join']
        for f in bad.flows:
            if f.target=='Join':f.target='Price'
    if fault=='reference':bad.flows[0].target='Missing'
    calls=[]
    async def call(c):calls.append(c);return bad if len(calls)==1 else good
    result=asyncio.run(generate_valid(call))
    assert result['xml'] and result['attempts']==2
    errors=json.loads(calls[1])['errors']
    found=next(i for i in errors if i['code']==code)
    if fault in {'cross','message','reference'}:assert found['flow_id']
    if fault=='orphan':assert found['process_id']=='CompanyPool'
    if fault=='join':assert found['gateway_id']=='Fork'


def test_message_does_not_replace_local_sequence_or_fix_unreachable_node():
    p=contract_process();p.flows=[f for f in p.flows if f.target!='ReceiveContract']
    errors=validate_process(p)
    assert any(i.code=='UNREACHABLE_NODE' and i.process_id=='ClientPool' and 'ReceiveContract' in i.node_ids for i in errors)
    assert not any(i.code=='UNREACHABLE_NODE' and i.process_id=='CompanyPool' for i in errors)


def test_cosmetics_warn_without_retry_or_blocking_xml():
    p=contract_process();next(n for n in p.nodes if n.id=='Price').name='Менеджер определяет стоимость договора'
    next(n for n in p.nodes if n.id=='Amount').name=''
    calls=[]
    async def call(c):calls.append(c);return p
    result=asyncio.run(generate_valid(call))
    assert result['xml'] and len(calls)==1
    assert {'LABEL_STYLE','EMPTY_GATEWAY_LABEL'} <= {i.code for i in result['semantic_warnings']}


def test_development_logs_exact_remaining_errors_and_provider(caplog):
    p=contract_process();bad=p.model_copy(deep=True);bad.message_flows.append(MessageFlow(id='Bad',source='Resend',target='Approve'))
    provider=Scripted([bad,p]);provider.name='openai_compatible';provider.model='gpt-6.1-sol'
    with caplog.at_level(logging.INFO,logger='pulse.validation'):
        asyncio.run(generate_valid(lambda c:provider.parse_process(p.description,c),provider=provider,development=True))
    assert 'SAME_POOL_MESSAGE_FLOW' in caplog.text and 'corrective_retry=1' in caplog.text
    assert 'provider=openai_compatible model=gpt-6.1-sol' in caplog.text
    assert 'errors=[]' in caplog.text


def test_graph_exhaustion_fails_over_only_after_two_same_provider_repairs():
    from app.services.llm.router import LLMRouter
    good=contract_process();bad=good.model_copy(deep=True)
    bad.message_flows.append(MessageFlow(id='Bad',source='Resend',target='Approve'))
    primary=Scripted([bad,bad,bad]);fallback=Scripted([good])
    primary.name='openai_compatible';fallback.name='vertex_gemini'
    router=LLMRouter(primary,fallback)
    result=asyncio.run(generate_valid(lambda c:router.parse_process(good.description,c),provider=router))
    # Resilience policy: initial graph + one focused correction, then fallback.
    assert result['xml'] and result['attempts']==3
    assert len(primary.calls)==2 and len(fallback.calls)==1
    assert router.metadata.fallback_used
    assert 'SAME_POOL_MESSAGE_FLOW' in fallback.calls[0][2]


def test_alternative_approval_paths_cannot_merge_with_parallel_join():
    p=contract_process();next(n for n in p.nodes if n.id=='FinalMerge').type='parallel_gateway'
    assert any(i.code=='PARALLEL_JOIN_MISMATCH' for i in validate_semantics(p))


@pytest.mark.parametrize('source_type,target_type',[('send_task','receive_task'),('task','intermediate_event'),('intermediate_event','task')])
def test_message_activity_event_endpoints_between_pools(source_type,target_type):
    p=contract_process()
    next(n for n in p.nodes if n.id=='Send').type=source_type
    next(n for n in p.nodes if n.id=='ReceiveContract').type=target_type
    if target_type=='intermediate_event':
        next(n for n in p.nodes if n.id=='ReceiveContract').event_definition='message'
    reply=next(n for n in p.nodes if n.id=='Reply');reply.type='exclusive_gateway';reply.decision_basis='data'
    for f in p.flows:
        if f.source=='Reply':f.condition=f.name
    if source_type=='intermediate_event':
        # None Throw cannot send a message; Message Throw is outside the typed subset.
        assert any(i.code=='INVALID_MESSAGE_ENDPOINT' and i.source=='BPMN_SPEC' for i in validate_process(p))
        with pytest.raises(ValueError): build_bpmn(p)
        return
    assert not validate_process(p)
    validate_xml(build_bpmn(p))


def test_controlled_xor_self_return_has_exit_but_closed_cycle_is_invalid():
    p=contract_process()
    p.flows.append(Flow(id='RepeatDecision',source='Amount',target='Amount',condition='Стоимость ещё уточняется',name='Уточняется'))
    assert not validate_process(p)
    assert not any(i.severity=='error' for i in validate_semantics(p))
    p.flows=[f for f in p.flows if f.source!='Amount' or f.target=='Amount']
    assert any(i.code=='NO_PATH_TO_END' and 'Amount' in i.node_ids for i in validate_process(p))
