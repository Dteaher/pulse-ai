import asyncio,json
from pathlib import Path
import pytest
from lxml import etree as E
from app.models import Node,Flow,MessageFlow
from app.notation import polish_process
from app.validator import validate_process
from app.semantics import validate_semantics
from app.bpmn import build_bpmn,import_process,validate_xml,NS
from app.changes import changes_between,preserve_ids
from app.pipeline import generate_valid
from test_controlled_loops import contract_process
from test_clarify_modify import Scripted,client


def event_contract():
    p=contract_process()
    reply=next(n for n in p.nodes if n.id=='Reply');reply.type='exclusive_gateway';reply.decision_basis='event'
    for f in p.flows:
        if f.source=='Reply':f.condition=f.name
    return polish_process(p)


def test_external_message_wait_is_event_based_and_preserves_correction_loop():
    p=event_contract();reply=next(n for n in p.nodes if n.id=='Reply')
    assert reply.type=='event_based_gateway' and reply.decision_basis=='event'
    assert all(not f.condition and not f.is_default for f in p.flows if f.source=='Reply')
    assert not validate_process(p) and not validate_semantics(p)
    assert any(f.source=='Resubmit' and f.target=='Merge_Reply' for f in p.flows)
    assert any(f.source=='Correction' and f.target=='Merge' for f in p.flows)
    assert len(p.message_flows)==4


@pytest.mark.parametrize('fault,code',[('task','INVALID_EVENT_GATEWAY_SUCCESSOR'),('condition','EVENT_GATEWAY_HAS_CONDITION'),('default','EVENT_GATEWAY_HAS_CONDITION'),('join','INVALID_EVENT_GATEWAY_TOPOLOGY'),('shared','SHARED_EVENT_GATEWAY_SUCCESSOR'),('mixed','MIXED_EVENT_GATEWAY_SUCCESSORS'),('data','EVENT_GATEWAY_DATA_DECISION')])
def test_event_gateway_strict_rules(fault,code):
    p=event_contract();gate=next(n for n in p.nodes if n.id=='Reply');branch=next(f for f in p.flows if f.source=='Reply')
    if fault=='task':next(n for n in p.nodes if n.id==branch.target).type='user_task'
    if fault=='condition':branch.condition='Да'
    if fault=='default':branch.is_default=True
    if fault=='join':p.flows=[f for f in p.flows if f.target!='Reply']
    if fault=='shared':p.flows.append(Flow(id='Shared',source='Submit',target=branch.target))
    if fault=='mixed':
        n=next(n for n in p.nodes if n.id==branch.target);n.type='intermediate_event';n.event_definition='message'
    if fault=='data':gate.decision_basis='data'
    assert any(i.code==code for i in validate_process(p))
    with pytest.raises(ValueError):build_bpmn(p)


def test_event_message_catch_successors_xml_and_fresh_roundtrip():
    p=event_contract()
    for f in p.flows:
        if f.source=='Reply':
            n=next(n for n in p.nodes if n.id==f.target);n.type='intermediate_event';n.event_definition='message'
    xml=build_bpmn(p);validate_xml(xml)
    root=E.fromstring(xml.encode())
    assert len(root.findall('.//bpmn:intermediateCatchEvent/bpmn:messageEventDefinition',NS))==2
    assert len(root.findall('.//bpmn:eventBasedGateway',NS))==1
    fresh=import_process(xml)
    assert {n.id:(n.type,n.event_definition,n.decision_basis) for n in fresh.nodes}=={n.id:(n.type,n.event_definition,n.decision_basis) for n in p.nodes}
    assert import_process(xml,p)==p


def test_event_gateway_conditions_do_not_enter_xml():
    p=event_contract();root=E.fromstring(build_bpmn(p).encode())
    outs={f.id for f in p.flows if f.source=='Reply'}
    assert all(el.find('bpmn:conditionExpression',NS) is None for el in root.findall('.//bpmn:sequenceFlow',NS) if el.get('id') in outs)


def none_start_receive(shared=False):
    p=contract_process();start=next(n for n in p.nodes if n.id=='CompanyStart');start.event_definition='none'
    receiver=Node(id='InitialReceive',type='receive_task',name='Получить заявку',participant_id='Operator',source_text='Заявка поступает от внешнего участника')
    p.nodes.append(receiver)
    next(f for f in p.flows if f.source=='CompanyStart').target=receiver.id
    p.flows.append(Flow(id='InitialToWork',source=receiver.id,target='Merge'))
    p.message_flows[0].target=receiver.id
    if shared:
        next(f for f in p.flows if f.source=='Return').target=receiver.id
        p.flows=[f for f in p.flows if f.source!='Correction'];p.nodes=[n for n in p.nodes if n.id!='Correction']
        p.message_flows[2].target=receiver.id
    return p


@pytest.mark.parametrize('shared',[False,True])
def test_incoming_message_starts_process_without_losing_repeat_receive(shared):
    before=none_start_receive(shared);p=polish_process(before)
    start=next(n for n in p.nodes if n.id=='CompanyStart')
    assert start.event_definition=='message'
    assert p.message_flows[0].target==start.id
    assert ('InitialReceive' in {n.id for n in p.nodes})==shared
    if shared:
        assert p.message_flows[2].target=='InitialReceive'
        assert any(f.source=='Return' and f.target=='InitialReceive' for f in p.flows)
    assert not validate_process(p) and not validate_semantics(p)
    xml=build_bpmn(p);validate_xml(xml)
    assert E.fromstring(xml.encode()).find('.//bpmn:startEvent[@id="CompanyStart"]/bpmn:messageEventDefinition',NS) is not None
    assert import_process(xml,p)==p
    assert before.message_flows[0].target=='InitialReceive'  # Input is immutable.


def test_message_start_requires_collaboration_message():
    p=event_contract();p.message_flows=[m for m in p.message_flows if m.target!='CompanyStart']
    assert any(i.code=='MESSAGE_START_WITHOUT_MESSAGE' for i in validate_process(p))


def test_message_definition_cannot_be_attached_to_a_task():
    p=event_contract();next(n for n in p.nodes if n.id=='Check').event_definition='message'
    assert any(i.code=='INVALID_EVENT_DEFINITION' for i in validate_process(p))


def test_data_decisions_stay_xor_and_alternative_paths_merge_explicitly():
    before=contract_process();before.nodes=[n for n in before.nodes if n.id!='FinalMerge'];before.flows=[f for f in before.flows if f.source!='FinalMerge']
    for f in before.flows:
        if f.target=='FinalMerge':f.target='Send'
    p=polish_process(before)
    assert all(next(n for n in p.nodes if n.id==i).type=='exclusive_gateway' for i in ('Amount','Approved','Merge_Send'))
    assert {f.source for f in p.flows if f.target=='Merge_Send'}=={'Amount','Approved'}
    assert not validate_process(p) and not validate_semantics(p)
    assert any(f.source=='Resend' and f.target=='ApprovalMerge' for f in p.flows)
    assert polish_process(p)==p


def test_received_data_is_not_confused_with_waiting_for_future_messages():
    p=contract_process();reply=next(n for n in p.nodes if n.id=='Reply');reply.type='exclusive_gateway';reply.decision_basis='data'
    for f in p.flows:
        if f.source=='Reply':f.condition=f.name
    result=polish_process(p)
    assert next(n for n in result.nodes if n.id=='Reply').type=='exclusive_gateway'
    assert not validate_semantics(result)


def test_explicit_event_wait_on_xor_has_precise_semantic_error():
    p=contract_process();reply=next(n for n in p.nodes if n.id=='Reply');reply.type='exclusive_gateway';reply.decision_basis='event'
    assert any(i.code=='MESSAGE_WAIT_USES_XOR' for i in validate_semantics(p))


def test_pipeline_clarify_modify_changes_and_ids_preserve_event_contract():
    from app.models import Ambiguity
    p=event_contract();base=p.model_copy(deep=True)
    p.ambiguities=[Ambiguity(id='Check',question='Уточните проверку')]
    r=client(Scripted([base])).post('/api/process/clarify',json={'process':p.model_dump(),'answers':{'Check':'Проверять все документы'}})
    assert r.status_code==200 and r.json()['xml']
    candidate=base.model_copy(deep=True);next(n for n in candidate.nodes if n.id=='Check').name='Проверить комплект документов'
    r=client(Scripted([candidate])).post('/api/process/modify',json={'process':base.model_dump(),'instruction':'Уточни подпись проверки'})
    assert r.status_code==200,r.text
    after=r.json()['process']
    assert next(n for n in after['nodes'] if n['id']=='Reply')['type']=='event_based_gateway'
    assert next(n for n in after['nodes'] if n['id']=='CompanyStart')['event_definition']=='message'
    assert preserve_ids(base,candidate).message_flows==base.message_flows
    assert any(c.type=='node_changed' and 'Check' in c.element_ids for c in changes_between(base,candidate).changes)


def test_event_definition_and_decision_basis_are_visible_in_changeset():
    before=contract_process();after=before.model_copy(deep=True)
    next(n for n in after.nodes if n.id=='CompanyStart').event_definition='none'
    next(n for n in after.nodes if n.id=='Reply').decision_basis='event'
    assert {'CompanyStart','Reply'} <= {i for c in changes_between(before,after).changes for i in c.element_ids}


def test_layout_labels_are_disjoint_and_loops_have_distinct_corridors():
    p=event_contract();root=E.fromstring(build_bpmn(p).encode())
    node_ids={n.id for n in p.nodes}
    boxes=[]
    for el in root.findall('.//bpmndi:BPMNShape',NS):
        if el.get('bpmnElement') in node_ids:
            b=el.find('dc:Bounds',NS);boxes.append(tuple(float(b.get(k)) for k in ('x','y','width','height')))
    labels=[tuple(float(b.get(k)) for k in ('x','y','width','height')) for b in root.findall('.//bpmndi:BPMNLabel/dc:Bounds',NS)]
    def overlaps(a,b):
        x,y,w,h=a;tx,ty,tw,th=b;return x<tx+tw and tx<x+w and y<ty+th and ty<y+h
    assert all(not overlaps(a,b) for a in labels for b in boxes)
    assert all(not overlaps(a,b) for i,a in enumerate(labels) for b in labels[i+1:])
    assert any(f.source=='Resend' and f.target=='ApprovalMerge' for f in p.flows)


def test_native_event_gateway_loop_gets_explicit_merge_without_retry():
    p=contract_process()
    p.nodes=[n for n in p.nodes if n.id!='Wait'];p.flows=[f for f in p.flows if f.source!='Wait']
    for f in p.flows:
        if f.target=='Wait':f.target='Reply'
    provider=Scripted([p])
    result=asyncio.run(generate_valid(lambda c:provider.parse_process(p.description,c)))
    assert result['attempts']==1 and result['xml']
    assert not validate_process(result['process'])
    assert next(n for n in result['process'].nodes if n.id=='Merge_Reply').type=='exclusive_gateway'
    assert len(provider.calls)==1


def test_notation_never_discards_broken_references_or_duplicate_ids():
    p=contract_process();p.flows[0].target='Missing'
    assert polish_process(p)==p
    p=contract_process();p.nodes.append(p.nodes[0].model_copy(deep=True))
    assert polish_process(p)==p
