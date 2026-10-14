"""Hand-authored business graphs: no providers, network, or model responses."""
import asyncio
import pytest
from lxml import etree as E
from app.models import ProcessDefinition, Participant, Pool, Node, Flow, MessageFlow
from app.notation import polish_process
from app.validator import validate_process
from app.semantics import validate_semantics
from app.bpmn import build_bpmn, NS
from app.pipeline import generate_valid
from app.xml_validation import validate_document
from app.logic_audit import compare_process_xml, process_diff, require_xml_fidelity
from app.diagnostics import PipelineFailure


def graph(specs, edges, name='Эталон'):
    return ProcessDefinition(id='Reference', name=name,
        participants=[Participant(id='Analyst', name='Аналитик')],
        nodes=[Node(id=i, type=t, name=label, participant_id='Analyst') for i,t,label in specs],
        flows=[Flow(id=f'F{i}', source=s, target=t, condition=c or None, name=c)
               for i,(s,t,c) in enumerate(edges)])


def reference(case):
    s=('Start','start_event','Начало'); e=('End','end_event','Процесс завершён')
    a=('A','user_task','Проверить заявку'); b=('B','user_task','Подготовить результат')
    if case == 'sequence':
        return graph([s,a,b,e],[('Start','A',''),('A','B',''),('B','End','')])
    if case in ('xor', 'rejection'):
        return graph([s,a,('X','exclusive_gateway','Заявка одобрена?'),
            ('Accept','user_task','Оформить договор'),('Reject','user_task','Сообщить об отказе'),
            ('YesEnd','end_event','Заявка одобрена'),('NoEnd','end_event','Заявка отклонена')],
            [('Start','A',''),('A','X',''),('X','Accept','Одобрено'),('X','Reject','Отклонено'),
             ('Accept','YesEnd',''),('Reject','NoEnd','')])
    if case == 'and':
        return graph([s,('Fork','parallel_gateway','Запустить проверки'),a,b,
            ('Join','parallel_gateway','Дождаться обеих проверок'),e],
            [('Start','Fork',''),('Fork','A',''),('Fork','B',''),('A','Join',''),('B','Join',''),('Join','End','')])
    if case == 'nested_xor_and':
        p=reference('and'); p.nodes += [Node(id=i,type=t,name=n,participant_id='Analyst') for i,t,n in
            [('Decision','exclusive_gateway','Требуется дополнительная проверка?'),
             ('Extra','user_task','Выполнить дополнительную проверку'),
             ('Merge','exclusive_gateway','Объединить альтернативы')]]
        next(f for f in p.flows if f.source=='A').target='Decision'
        p.flows += [Flow(id=i,source=s,target=t,condition=c or None,name=c) for i,s,t,c in
            [('D1','Decision','Extra','Нужна проверка'),('D2','Decision','Merge','Проверка не нужна'),
             ('D3','Extra','Merge',''),('D4','Merge','Join','')]]
        return p
    if case == 'nested_and_xor':
        p=reference('and'); p.nodes += [Node(id=i,type=t,name=n,participant_id='Analyst') for i,t,n in
            [('Decision','exclusive_gateway','Выбрать способ обработки'),('Skip','user_task','Обработать вручную'),
             ('Merge','exclusive_gateway','Объединить способы')]]
        next(f for f in p.flows if f.source=='Start').target='Decision'
        next(f for f in p.flows if f.source=='Join').target='Merge'
        p.flows += [Flow(id=i,source=s,target=t,condition=c or None,name=c) for i,s,t,c in
            [('D1','Decision','Fork','Две проверки'),('D2','Decision','Skip','Ручная обработка'),
             ('D3','Skip','Merge',''),('D4','Merge','End','')]]
        return p
    if case in ('documents_loop','contract_loop'):
        label='документы' if case=='documents_loop' else 'договор'
        return graph([s,('Merge','exclusive_gateway','Повторная проверка'),
            ('Check','user_task','Проверить '+label),('Decision','exclusive_gateway','Нужна доработка?'),
            ('Rework','user_task','Доработать '+label),e],
            [('Start','Merge',''),('Merge','Check',''),('Check','Decision',''),
             ('Decision','Rework','Требуется доработка'),('Rework','Merge',''),('Decision','End','Готово')])
    if case=='two_pools':
        return ProcessDefinition(id='Reference',name='Обмен заявкой',
            pools=[Pool(id='ClientPool',name='Клиент',kind='external'),Pool(id='CompanyPool',name='Компания')],
            participants=[Participant(id='Client',name='Клиент',kind='external',pool_id='ClientPool'),
                          Participant(id='Operator',name='Оператор',pool_id='CompanyPool')],
            nodes=[Node(id=i,type=t,name=n,participant_id=r,event_definition=d) for i,t,n,r,d in
                [('ClientStart','start_event','Начало','Client','none'),('Send','send_task','Отправить заявку','Client','none'),
                 ('ClientEnd','end_event','Заявка отправлена','Client','none'),
                 ('CompanyStart','start_event','Заявка получена','Operator','message'),
                 ('Check','user_task','Проверить заявку','Operator','none'),('CompanyEnd','end_event','Проверка завершена','Operator','none')]],
            flows=[Flow(id=f'F{i}',source=s,target=t) for i,(s,t) in enumerate(
                [('ClientStart','Send'),('Send','ClientEnd'),('CompanyStart','Check'),('Check','CompanyEnd')])],
            message_flows=[MessageFlow(id='Request',source='Send',target='CompanyStart',name='Заявка')])
    raise AssertionError(case)


CASES=('sequence','xor','and','nested_xor_and','nested_and_xor','documents_loop','contract_loop','rejection','two_pools')


@pytest.mark.parametrize('case',CASES)
def test_reference_graph_is_unchanged_through_normalizer_validator_builder(case):
    p=reference(case); snapshot=p.model_dump()
    normalized=polish_process(p)
    assert normalized==p
    assert not [i for i in validate_process(normalized)+validate_semantics(normalized) if i.severity=='error']
    xml=build_bpmn(normalized)
    assert not [i for i in validate_document(xml,complete_di=True) if i.severity=='error']
    # Independent XML assertions: do not use the application's importer as oracle.
    root=E.fromstring(xml.encode())
    for node in p.nodes:
        el=root.xpath('//*[@id=$id]',id=node.id)[0]
        assert el.get('name')==node.name
        assert set(el.xpath('bpmn:incoming/text()',namespaces=NS))=={f.id for f in p.flows if f.target==node.id}
        assert set(el.xpath('bpmn:outgoing/text()',namespaces=NS))=={f.id for f in p.flows if f.source==node.id}
    assert {(f.get('id'),f.get('sourceRef'),f.get('targetRef')) for f in root.findall('.//bpmn:sequenceFlow',NS)}=={(f.id,f.source,f.target) for f in p.flows}
    assert {(f.get('id'),f.get('sourceRef'),f.get('targetRef'),f.get('name')) for f in root.findall('.//bpmn:messageFlow',NS)}=={(f.id,f.source,f.target,f.name) for f in p.message_flows}
    assert p.model_dump()==snapshot
    async def supplied(_): return p
    trace=[]
    result=asyncio.run(generate_valid(supplied,max_retries=0,audit_trace=trace))
    assert result['process']==p and result['xml'] and result['attempts']==1
    assert [t['stage'] for t in trace]==['provider_output','normalization','validation','bpmn_xml']
    assert trace[0]['process']==snapshot and trace[1]['changes']==[] and trace[-1]['differences']==[]
    assert compare_process_xml(p,xml)==[]


def conditional_receive_choice():
    p=reference('two_pools')
    p.nodes=[n for n in p.nodes if n.id!='Check']
    p.nodes += [Node(id=i,type=t,name=name,participant_id='Operator') for i,t,name in
        [('Select','exclusive_gateway','Выбрать канал по настройкам'),
         ('Email','receive_task','Получить ответ по почте'),('Portal','receive_task','Получить ответ на портале')]]
    p.flows=[f for f in p.flows if f.source!='CompanyStart' and f.source!='Check']
    p.flows += [Flow(id=i,source=s,target=t,condition=c or None,name=c) for i,s,t,c in
        [('C1','CompanyStart','Select',''),('C2','Select','Email','Канал = email'),
         ('C3','Select','Portal','Канал = портал'),('C4','Email','CompanyEnd',''),('C5','Portal','CompanyEnd','')]]
    # A data choice followed by receives is legal; incoming messages alone are not proof of an event race.
    p.message_flows += [MessageFlow(id='EmailMessage',source='Send',target='Email'),MessageFlow(id='PortalMessage',source='Send',target='Portal')]
    # Preceding ordinary work rather than a message event, to expose the old heuristic.
    p.nodes.append(Node(id='Configure',type='user_task',name='Прочитать настройки',participant_id='Operator'))
    next(f for f in p.flows if f.id=='C1').target='Configure'
    p.flows.append(Flow(id='C0',source='Configure',target='Select'))
    return p


def test_data_choice_before_receives_does_not_lose_conditions():
    p=conditional_receive_choice()
    assert not [i for i in validate_process(p) if i.severity=='error']
    assert polish_process(p)==p
    assert not [i for i in validate_semantics(p) if i.severity=='error']
    assert compare_process_xml(p,build_bpmn(p))==[]


def test_single_pool_builder_rejects_alternative_branches_joined_with_and():
    p=reference('xor')
    p.nodes=[n for n in p.nodes if n.type!='end_event']
    p.nodes += [Node(id='Join',type='parallel_gateway',name='Неверное объединение',participant_id='Analyst'),
                Node(id='End',type='end_event',name='Завершено',participant_id='Analyst')]
    for f in p.flows:
        if f.source in ('Accept','Reject'): f.target='Join'
    p.flows.append(Flow(id='ToEnd',source='Join',target='End'))
    assert not [i for i in validate_process(p) if i.severity=='error']
    assert any(i.code=='PARALLEL_JOIN_MISMATCH' for i in validate_semantics(p))
    with pytest.raises(ValueError): build_bpmn(p)


def test_converging_xor_does_not_hide_wrong_final_outcome():
    p=reference('xor')
    p.nodes=[n for n in p.nodes if n.type!='end_event']
    p.nodes += [Node(id='Merge',type='exclusive_gateway',name='Объединить исходы',participant_id='Analyst'),
                Node(id='End',type='end_event',name='Заявка отклонена',participant_id='Analyst')]
    for f in p.flows:
        if f.source in ('Accept','Reject'): f.target='Merge'
    p.flows.append(Flow(id='ToEnd',source='Merge',target='End'))
    assert any(i.code=='INVALID_BRANCH_SEMANTICS' and 'End' in i.node_ids for i in validate_semantics(p))


@pytest.mark.parametrize('extra',['message','label'])
def test_receive_with_additional_business_information_is_not_removed(extra):
    p=reference('two_pools')
    start=next(n for n in p.nodes if n.id=='CompanyStart'); start.event_definition='none'
    p.nodes.append(Node(id='Receive',type='receive_task',name='Получить пакет инженерной документации',participant_id='Operator'))
    next(f for f in p.flows if f.source=='CompanyStart').target='Receive'
    p.flows.append(Flow(id='ReceiveToCheck',source='Receive',target='Check',name='Документы зарегистрированы' if extra=='label' else ''))
    p.message_flows[0].target='Receive'
    if extra=='message':
        p.message_flows.append(MessageFlow(id='Receipt',source='Receive',target='Send',name='Подтверждение получения'))
    assert not [i for i in validate_process(p) if i.severity=='error']
    assert polish_process(p)==p


def test_message_start_conversion_preserves_received_action_label():
    p=reference('two_pools')
    next(n for n in p.nodes if n.id=='CompanyStart').event_definition='none'
    p.nodes.append(Node(id='Receive',type='receive_task',name='Получить пакет инженерной документации',participant_id='Operator'))
    next(f for f in p.flows if f.source=='CompanyStart').target='Receive'
    p.flows.append(Flow(id='ReceiveToCheck',source='Receive',target='Check'))
    p.message_flows[0].target='Receive'
    normalized=polish_process(p)
    assert next(n for n in normalized.nodes if n.id=='CompanyStart').name=='Получить пакет инженерной документации'


@pytest.mark.parametrize('pooled',[False,True])
def test_default_flow_condition_is_preserved_for_roundtrip(pooled):
    p=reference('xor'); next(f for f in p.flows if f.source=='X' and f.target=='Reject').is_default=True
    if pooled:
        p.pools=[Pool(id='Company',name='Компания')]
        p.participants[0].pool_id='Company'
    xml=build_bpmn(p)
    assert compare_process_xml(p,xml)==[]
    from app.bpmn import import_process
    assert import_process(xml,p)==p
    # BPMN ignores this condition at runtime; retaining documentation does not turn it into a data branch.
    assert any(i.code=='DEFAULT_CONDITION_IGNORED' for i in validate_document(xml))


@pytest.mark.parametrize('mutation',['lost_task','wrong_branch','wrong_condition','wrong_gateway',
                                    'wrong_role','premature_end','extra_flow','lost_incoming','wrong_default'])
def test_xsd_valid_xml_drift_is_detected(mutation):
    p=reference('xor'); root=E.fromstring(build_bpmn(p).encode())
    node=lambda id: root.xpath('//*[@id=$id]',id=id)[0]
    if mutation=='lost_task': node('Accept').getparent().remove(node('Accept'))
    elif mutation=='wrong_branch': node('F2').set('targetRef','Reject')
    elif mutation=='wrong_condition': node('F2').find('bpmn:conditionExpression/bpmn:documentation',NS).text='Отклонено'
    elif mutation=='wrong_gateway': node('X').tag='{'+NS['bpmn']+'}parallelGateway'
    elif mutation=='wrong_role': node('Analyst').find('bpmn:flowNodeRef',NS).text='Reject'
    elif mutation=='premature_end': node('Accept').tag='{'+NS['bpmn']+'}endEvent'
    elif mutation=='extra_flow':
        extra=E.SubElement(node('Reference'),'{'+NS['bpmn']+'}sequenceFlow',id='Unexpected',sourceRef='A',targetRef='YesEnd')
    elif mutation=='lost_incoming': node('Accept').remove(node('Accept').find('bpmn:incoming',NS))
    elif mutation=='wrong_default': node('X').set('default','F2')
    xml=E.tostring(root,encoding='unicode')
    assert compare_process_xml(p,xml)
    with pytest.raises(PipelineFailure) as error: require_xml_fidelity(p,xml)
    assert all(i.code=='BPMN_SEMANTIC_DRIFT' for i in error.value.diagnostics)


@pytest.mark.parametrize('mutation',['return_target','message_target','pool_membership'])
def test_loops_and_message_membership_drift_are_detected(mutation):
    p=reference('documents_loop' if mutation=='return_target' else 'two_pools')
    root=E.fromstring(build_bpmn(p).encode())
    if mutation=='return_target': root.find('.//bpmn:sequenceFlow[@id="F4"]',NS).set('targetRef','End')
    elif mutation=='message_target': root.find('.//bpmn:messageFlow',NS).set('targetRef','CompanyEnd')
    else: root.find('.//bpmn:participant[@id="CompanyPool"]',NS).set('processRef','Process_ClientPool')
    assert compare_process_xml(p,E.tostring(root,encoding='unicode'))


def test_pipeline_drift_is_backend_failure_without_llm_retry(monkeypatch):
    p=reference('sequence'); root=E.fromstring(build_bpmn(p).encode())
    root.find('.//bpmn:sequenceFlow[@id="F1"]',NS).set('targetRef','End')
    import app.pipeline as pipeline
    monkeypatch.setattr(pipeline,'build_bpmn',lambda _:E.tostring(root,encoding='unicode'))
    calls=[]; trace=[]
    async def supplied(correction): calls.append(correction); return p
    with pytest.raises(PipelineFailure) as error:
        asyncio.run(generate_valid(supplied,max_retries=2,audit_trace=trace))
    assert len(calls)==1 and error.value.diagnostics[0].code=='BPMN_SEMANTIC_DRIFT'
    assert trace[-1]['differences']


def test_stage_diff_reports_full_normalization_changes_without_mutation():
    from test_event_semantics import none_start_receive
    before=none_start_receive(); snapshot=before.model_dump()
    after=polish_process(before); diff=process_diff(before,after)
    assert {'nodes.InitialReceive','nodes.CompanyStart.event_definition','message_flows.Message0.target'} <= {d['path'] for d in diff}
    assert before.model_dump()==snapshot


def test_parallel_branch_cannot_finish_before_synchronization():
    p=reference('and'); next(f for f in p.flows if f.source=='B').target='End'
    assert any(i.code=='PARALLEL_JOIN_MISMATCH' for i in validate_semantics(p))
    with pytest.raises(ValueError): build_bpmn(p)


def test_nested_parallel_synchronizations_preserve_each_join():
    p=reference('and')
    p.nodes += [Node(id=i,type=t,name=n,participant_id='Analyst') for i,t,n in
        [('InnerFork','parallel_gateway','Дополнительные проверки'),('InnerA','user_task','Проверить реквизиты'),
         ('InnerB','user_task','Проверить сроки'),('InnerJoin','parallel_gateway','Дополнительные проверки завершены')]]
    next(f for f in p.flows if f.source=='A').target='InnerFork'
    p.flows += [Flow(id=f'Nested{i}',source=s,target=t) for i,(s,t) in enumerate(
        [('InnerFork','InnerA'),('InnerFork','InnerB'),('InnerA','InnerJoin'),('InnerB','InnerJoin'),('InnerJoin','Join')])]
    assert not [i for i in validate_semantics(p) if i.severity=='error']
    assert polish_process(p)==p and compare_process_xml(p,build_bpmn(p))==[]


def test_offline_audit_saves_all_stages_without_a_provider(tmp_path):
    from tools.audit_logic import audit
    import json
    report=audit(reference('documents_loop'),tmp_path)
    assert report['passed'] and report['llm_calls']==0
    assert report['normalization_changes']==[] and report['xml_differences']==[]
    assert {p.name for p in tmp_path.iterdir()}=={'input.json','normalized.json','result.bpmn','trace.json','report.json'}
    trace=json.loads((tmp_path/'trace.json').read_text(encoding='utf8'))
    assert trace[-1]['stage']=='bpmn_xml'


def test_offline_audit_keeps_failure_diagnostics(tmp_path):
    from tools.audit_logic import audit
    import json
    p=reference('and'); p.flows.append(Flow(id='EarlyExit',source='B',target='End'))
    report=audit(p,tmp_path)
    assert not report['passed'] and report['llm_calls']==0
    assert any(i['code']=='PARALLEL_JOIN_MISMATCH' for i in report['diagnostics'])
    trace=json.loads((tmp_path/'trace.json').read_text(encoding='utf8'))
    assert any(i['code']=='PARALLEL_JOIN_MISMATCH' for i in trace[-1]['issues'])
    assert not (tmp_path/'result.bpmn').exists()


def test_conditional_branch_labels_stay_near_their_own_target():
    p=reference('xor'); p.pools=[Pool(id='Company',name='Компания')];p.participants[0].pool_id='Company'
    root=E.fromstring(build_bpmn(p).encode())
    for flow in p.flows:
        if flow.source!='X': continue
        edge=root.find('.//bpmndi:BPMNEdge[@bpmnElement="'+flow.id+'"]',
                       {'bpmndi':'http://www.omg.org/spec/BPMN/20100524/DI'})
        label=edge.find('bpmndi:BPMNLabel/dc:Bounds',
                        {'bpmndi':'http://www.omg.org/spec/BPMN/20100524/DI','dc':'http://www.omg.org/spec/DD/20100524/DC'})
        end=list(edge.findall('{http://www.omg.org/spec/DD/20100524/DI}waypoint'))[-1]
        cx=float(label.get('x'))+float(label.get('width'))/2
        cy=float(label.get('y'))+float(label.get('height'))/2
        assert ((cx-float(end.get('x')))**2+(cy-float(end.get('y')))**2)**0.5 < 160
