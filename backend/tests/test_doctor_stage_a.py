import json
from pathlib import Path
import pytest
from lxml import etree as E
from fastapi.testclient import TestClient
from app.bpmn import build_bpmn, import_process, parse_xml, NS
from app.models import ProcessDefinition
from app.config import Settings
from app.main import create_app
from app.doctor import doctor_rules, diagnostic_payload
from app.formal_messages import add_formal_messages
from app.xml_validation import validate_document
from app.services.llm.mock_provider import MockLLMProvider

ROOT=Path(__file__).resolve().parents[2]
def golden(case):
    return ProcessDefinition.model_validate_json((ROOT/f'examples/llm-benchmark/{case}.json').read_text(encoding='utf8'))

@pytest.mark.parametrize('case',['simple','medium','complex'])
def test_doctor_keeps_legal_models_and_diagnostic_contract(case):
    p=golden(case);xml=build_bpmn(p)
    assert not any(i.severity=='error' for i in doctor_rules(xml))
    for i in doctor_rules(xml):
        d=diagnostic_payload(i)
        assert d['category'] in {'notation','execution','business','readability'}
        assert all(k in d for k in ['code','severity','element_ids','description','reason','suggestion','confidence'])
    assert build_bpmn(p)==xml

@pytest.mark.parametrize('case',['medium','complex'])
def test_formal_messages_stable_unique_references_roundtrip(case):
    p=golden(case);plain=build_bpmn(p);xml=add_formal_messages(plain)
    assert xml==add_formal_messages(plain)
    root=parse_xml(xml)
    assert len(root.findall('bpmn:message',NS))==len(p.message_flows)
    assert not any(i.severity=='error' for i in validate_document(xml,complete_di=True))
    assert import_process(xml,p)==p
    assert add_formal_messages(build_bpmn(import_process(xml,p)))==xml
    assert all(e.get('isExecutable')=='false' for e in root.findall('bpmn:process',NS))
    assert not parse_xml(plain).findall('bpmn:message',NS)

def test_doctor_no_paid_calls_and_timer_is_partial_not_failure():
    class NeverAudit(MockLLMProvider):
        async def audit_process(self,*a,**kw):
            pytest.fail('deterministic Doctor called LLM')
    s=Settings(_env_file=None,llm_provider='mock',primary_llm_provider='mock',llm_fallback_enabled=False,api_limits_enabled=False)
    with TestClient(create_app(s,provider=NeverAudit())) as c:
        xml=build_bpmn(golden('simple'))
        assert c.post('/api/process/audit',json={'xml':xml}).json()['llm_audit'] is False
        root=parse_xml(xml)
        E.SubElement(root.find('.//bpmn:startEvent',NS),'{'+NS['bpmn']+'}timerEventDefinition')
        report=c.post('/api/process/audit',json={'xml':E.tostring(root,encoding='unicode')}).json()
        assert report['technical']['BPMN XML / XSD'] is True
        assert report['technical']['Граф / связи / события / шлюзы'] is None
        assert 'частичная' in report['notice']

def test_execution_diagnostics_are_opt_in_and_never_invent_expressions():
    xml=build_bpmn(golden('simple'))
    assert not any(i.category=='execution' for i in doctor_rules(xml))
    rules=doctor_rules(xml,execution_preparation=True)
    assert any(i.code=='EXECUTION_CONDITION_NOT_FORMAL' for i in rules)
    assert any(i.code=='EXECUTION_XOR_NO_DEFAULT' for i in rules)
    assert all(i.severity!='error' for i in rules)

def test_formal_broken_message_ref_is_error():
    root=parse_xml(add_formal_messages(build_bpmn(golden('medium'))))
    root.find('.//bpmn:messageFlow',NS).set('messageRef','Missing')
    assert any(i.code=='BROKEN_REFERENCE' for i in validate_document(E.tostring(root,encoding='unicode')))

def test_cycle_with_exit_is_not_error():
    rules=doctor_rules(build_bpmn(golden('complex')))
    cycles=[i for i in rules if i.code=='DOCTOR_LOOP_WITH_EXIT']
    assert len(cycles)==3 and all(i.severity=='warning' for i in cycles)

@pytest.mark.parametrize('ref',['sourceRef','targetRef','messageRef'])
def test_wrong_reference_type_even_outside_subset(ref):
    root=parse_xml(build_bpmn(golden('medium')))
    event=root.find('.//bpmn:startEvent',NS)
    E.SubElement(event,'{'+NS['bpmn']+'}timerEventDefinition')
    if ref=='messageRef':
        element=root.find('.//bpmn:messageFlow',NS)
    else:element=root.find('.//bpmn:sequenceFlow',NS)
    element.set(ref,root.find('bpmn:process',NS).get('id'))
    assert any(i.code=='BROKEN_REFERENCE' for i in validate_document(E.tostring(root,encoding='unicode')))

def independent_parallel_xml():
    root=E.Element('{'+NS['bpmn']+'}definitions',nsmap=NS,targetNamespace='https://test.local')
    proc=E.SubElement(root,'{'+NS['bpmn']+'}process',id='p',isExecutable='false')
    for id,kind in [('s','startEvent'),('g','parallelGateway'),('a','task'),('b','task'),('ea','endEvent'),('eb','endEvent')]:
        E.SubElement(proc,'{'+NS['bpmn']+'}'+kind,id=id)
    for i,(source,target) in enumerate([('s','g'),('g','a'),('g','b'),('a','ea'),('b','eb')]):
        E.SubElement(proc,'{'+NS['bpmn']+'}sequenceFlow',id='f'+str(i),sourceRef=source,targetRef=target)
    return E.tostring(root,encoding='unicode')

def test_parallel_separate_ends_are_not_doctor_errors():
    xml=independent_parallel_xml()
    assert not any(i.severity=='error' for i in validate_document(xml))
    s=Settings(_env_file=None,llm_provider='mock',primary_llm_provider='mock',llm_fallback_enabled=False,api_limits_enabled=False)
    with TestClient(create_app(s)) as c:
        report=c.post('/api/process/audit',json={'xml':xml}).json()
        assert not any(i['severity']=='error' for i in report['issues'])
        assert any(i['code']=='DOCTOR_PARALLEL_SEPARATE_ENDS' for i in report['issues'])

def test_terminate_parallel_is_risk_not_proven_error():
    root=parse_xml(independent_parallel_xml())
    E.SubElement(root.find('.//bpmn:endEvent',NS),'{'+NS['bpmn']+'}terminateEventDefinition')
    rules=doctor_rules(E.tostring(root,encoding='unicode'))
    assert any(i.code=='DOCTOR_TERMINATE_PARALLEL_RISK' and i.severity=='warning' for i in rules)

def test_timeout_boundary_suppresses_wait_advice():
    root=parse_xml(build_bpmn(golden('medium')))
    wait=root.find('.//bpmn:receiveTask',NS)
    before=doctor_rules(E.tostring(root,encoding='unicode'))
    assert any(i.code=='DOCTOR_MESSAGE_WAIT_NO_TIMEOUT' and wait.get('id') in i.node_ids for i in before)
    boundary=E.SubElement(wait.getparent(),'{'+NS['bpmn']+'}boundaryEvent',id='timer',attachedToRef=wait.get('id'))
    E.SubElement(boundary,'{'+NS['bpmn']+'}timerEventDefinition')
    after=doctor_rules(E.tostring(root,encoding='unicode'))
    assert not any(i.code=='DOCTOR_MESSAGE_WAIT_NO_TIMEOUT' and wait.get('id') in i.node_ids for i in after)

def test_formal_message_renaming_is_not_silently_lost():
    root=parse_xml(add_formal_messages(build_bpmn(golden('medium'))))
    root.find('bpmn:message',NS).set('name','Changed payload semantics')
    with pytest.raises(ValueError):import_process(E.tostring(root,encoding='unicode'))

@pytest.mark.parametrize('enabled',[False,True])
def test_formal_flag_applies_to_pipeline_and_export(enabled):
    import asyncio
    from app.pipeline import generate_valid
    p=golden('medium')
    async def supplied(_):return p.model_copy(deep=True)
    result=asyncio.run(generate_valid(supplied,max_retries=0,formal_messages=enabled))
    assert bool(parse_xml(result['xml']).findall('bpmn:message',NS)) is enabled
    s=Settings(_env_file=None,llm_provider='mock',primary_llm_provider='mock',llm_fallback_enabled=False,api_limits_enabled=False,bpmn_formal_messages_enabled=enabled)
    with TestClient(create_app(s)) as c:
        result=c.post('/api/process/bpmn',json={'process':p.model_dump()})
        assert result.status_code==200
        assert bool(parse_xml(result.json()['xml']).findall('bpmn:message',NS)) is enabled

def test_large_external_process_reports_analysis_limit():
    root=parse_xml(independent_parallel_xml());proc=root.find('bpmn:process',NS)
    for i in range(151):E.SubElement(proc,'{'+NS['bpmn']+'}task',id='large'+str(i))
    rules=doctor_rules(E.tostring(root,encoding='unicode'))
    assert len(rules)==1 and rules[0].code=='DOCTOR_ANALYSIS_LIMIT' and rules[0].severity=='info'

def test_closed_scc_reports_risk_without_inventing_timer():
    root=parse_xml(independent_parallel_xml());proc=root.find('bpmn:process',NS)
    for flow in list(proc.findall('bpmn:sequenceFlow',NS)):
        if flow.get('sourceRef')=='g' and flow.get('targetRef')=='b':proc.remove(flow)
        elif flow.get('sourceRef')=='a':flow.set('targetRef','g')
    rules=doctor_rules(E.tostring(root,encoding='unicode'))
    assert any(i.code=='DOCTOR_CLOSED_CYCLE' and i.severity=='warning' for i in rules)
