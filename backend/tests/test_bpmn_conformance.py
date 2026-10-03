"""Normative regression checks, separated from PULSE modeling recommendations."""
import random
from pathlib import Path
import pytest
from lxml import etree as E
from app.models import ProcessDefinition, Participant, Node, Flow
from app.bpmn import NS, build_bpmn, import_process, parse_xml
from app.validator import validate_process
from app.xml_validation import validate_document, validate_xsd
from app.pipeline import audit_rules
from test_event_semantics import event_contract
from test_controlled_loops import contract_process
from test_collaboration import collaboration_example
from test_clarify_modify import client, Scripted


def simple(types=('start_event', 'task', 'end_event')):
    nodes = [Node(id=f'N{i}', type=kind, name=f'Этап {i}', participant_id='Role') for i, kind in enumerate(types)]
    return ProcessDefinition(id='P', name='Проверка subset', participants=[Participant(id='Role', name='Оператор')],
                             nodes=nodes, flows=[Flow(id=f'F{i}', source=nodes[i].id, target=nodes[i+1].id) for i in range(len(nodes)-1)])


@pytest.mark.parametrize('kind', ['task', 'user_task', 'service_task', 'script_task', 'send_task', 'receive_task'])
def test_task_types_roundtrip_official_xsd_and_complete_di(kind):
    p = simple(('start_event', kind, 'end_event'))
    xml = build_bpmn(p)
    assert validate_xsd(xml) == [] and validate_document(xml, complete_di=True) == []
    assert import_process(xml, p) == p


@pytest.mark.parametrize('types', [('task', 'user_task'), ('start_event', 'end_event'), ('start_event', 'intermediate_event', 'end_event')])
def test_implicit_start_end_empty_process_and_none_intermediate(types):
    p = simple(types)
    assert not any(i.severity == 'error' for i in validate_process(p))
    xml = build_bpmn(p)
    assert not validate_document(xml, complete_di=True)
    assert import_process(xml, p) == p
    if 'intermediate_event' in types:
        assert 'intermediateThrowEvent' in xml and 'intermediateCatchEvent' not in xml


def test_multiple_starts_are_warning_not_standard_violation():
    p = simple()
    p.nodes.append(Node(id='AnotherStart', type='start_event', name='Другой запуск', participant_id='Role'))
    p.flows.append(Flow(id='SecondInput', source='AnotherStart', target='N1'))
    issues = validate_process(p)
    assert not any(i.severity == 'error' for i in issues)
    assert any(i.code == 'MULTIPLE_START_EVENTS' and i.source == 'MODEL_QUALITY' for i in issues)
    assert not validate_document(build_bpmn(p), complete_di=True)


def test_message_start_without_visible_sender_is_legal():
    p = simple(); p.nodes[0].event_definition = 'message'
    assert not any(i.severity == 'error' for i in validate_process(p))
    assert not validate_document(build_bpmn(p), complete_di=True)


def test_start_end_pair_required_by_standard():
    p = simple(('start_event', 'task'))
    assert any(i.code == 'START_END_PAIR_REQUIRED' and i.source == 'BPMN_SPEC' for i in validate_process(p))
    with pytest.raises(ValueError): build_bpmn(p)


def test_activity_conditional_and_implicit_merge_are_legal_non_executable():
    p = simple(); p.nodes.append(Node(id='Alternative', type='task', name='Другая задача', participant_id='Role'))
    p.flows += [Flow(id='Branch', source='N1', target='Alternative', condition='Нужна дополнительная проверка'), Flow(id='Finish', source='Alternative', target='N2')]
    p.flows[1].is_default = True
    assert not any(i.severity == 'error' for i in validate_process(p))
    xml = build_bpmn(p)
    root = parse_xml(xml)
    expression = root.find('.//bpmn:conditionExpression', NS)
    assert expression.get(f"{{{NS['xsi']}}}type") == 'bpmn:tExpression'
    assert expression.findtext('bpmn:documentation', namespaces=NS) == 'Нужна дополнительная проверка'
    assert import_process(xml, p) == p
    assert not validate_document(xml, complete_di=True)


def test_single_conditional_activity_is_error():
    p = simple(); p.flows[1].condition = 'Условие'
    assert any(i.code == 'CONDITIONAL_ACTIVITY_SINGLE_FLOW' for i in validate_process(p))


def test_mixed_xor_gateway_direction_and_document_correction_loop():
    p = contract_process()
    gate = next(n for n in p.nodes if n.id == 'Reply'); gate.type = 'exclusive_gateway'; gate.decision_basis = 'data'
    for f in p.flows:
        if f.source == 'Reply': f.condition = f.name
        if f.source == 'Resubmit': f.target = 'Reply'
    xml = build_bpmn(p); root = parse_xml(xml)
    mixed = [n for n in p.nodes if sum(f.target == n.id for f in p.flows) > 1 and sum(f.source == n.id for f in p.flows) > 1]
    assert mixed
    for n in mixed:
        assert root.xpath('//*[@id=$key]', key=n.id)[0].get('gatewayDirection') == 'Mixed'
    assert not validate_document(xml, complete_di=True)


def test_event_gateway_multiple_inputs_supported_without_shared_successor():
    p = event_contract(); p.flows.append(Flow(id='ExtraIncoming', source='Resubmit', target='Reply'))
    assert not any(i.severity == 'error' for i in validate_process(p))
    assert not validate_document(build_bpmn(p), complete_di=True)


def xml_with_mutation(case):
    root = parse_xml(build_bpmn(collaboration_example()))
    node = root.find('.//bpmn:task', NS)
    flow = root.find('.//bpmn:sequenceFlow', NS)
    shape = root.find('.//bpmndi:BPMNShape', NS)
    edge = root.find('.//bpmndi:BPMNEdge', NS)
    if case == 'missing_source': flow.set('sourceRef', 'Missing')
    elif case == 'wrong_ref_type': flow.set('sourceRef', root.get('id'))
    elif case == 'wrong_incoming': node.find('bpmn:incoming', NS).text = flow.get('id')
    elif case == 'duplicate_id': edge.set('id', shape.get('id'))
    elif case == 'invalid_pool_ref': root.find('.//bpmn:participant', NS).set('processRef', 'MissingProcess')
    elif case == 'invalid_lane_ref': root.find('.//bpmn:flowNodeRef', NS).text = 'MissingNode'
    elif case == 'duplicate_lane_member':
        ref = root.find('.//bpmn:flowNodeRef', NS); ref.getparent().append(E.fromstring(E.tostring(ref)))
    elif case == 'invalid_default': node.set('default', 'MissingDefault')
    elif case == 'invalid_di_ref': shape.set('bpmnElement', 'MissingShape')
    elif case == 'wrong_di_type': shape.set('bpmnElement', flow.get('id'))
    elif case == 'missing_shape': shape.getparent().remove(shape)
    elif case == 'missing_edge': edge.getparent().remove(edge)
    elif case == 'duplicate_di':
        duplicate = E.fromstring(E.tostring(shape)); duplicate.set('id', 'OtherShape'); shape.getparent().append(duplicate)
    elif case == 'negative_bounds': shape.find('dc:Bounds', NS).set('x', '-1')
    elif case == 'zero_width': shape.find('dc:Bounds', NS).set('width', '0')
    elif case == 'infinite_waypoint': edge.find('di:waypoint', NS).set('x', 'INF')
    elif case == 'one_waypoint':
        for point in edge.findall('di:waypoint', NS)[1:]: edge.remove(point)
    elif case == 'invalid_plane': root.find('.//bpmndi:BPMNPlane', NS).set('bpmnElement', node.get('id'))
    elif case == 'invalid_xsd_type': shape.find('dc:Bounds', NS).set('width', 'wide')
    elif case == 'missing_definitions_namespace': root.set('targetNamespace', '')
    else: raise AssertionError(case)
    return E.tostring(root, encoding='unicode')


@pytest.mark.parametrize('case,code', [
    ('missing_source','BROKEN_REFERENCE'), ('wrong_ref_type','BROKEN_REFERENCE'),
    ('wrong_incoming','INCORRECT_FLOW_NODE_REFERENCE'), ('duplicate_id','INVALID_BPMN_XML'),
    ('invalid_pool_ref','BROKEN_REFERENCE'), ('invalid_lane_ref','BROKEN_REFERENCE'),
    ('duplicate_lane_member','INVALID_LANE_MEMBERSHIP'), ('invalid_default','BROKEN_REFERENCE'),
    ('invalid_di_ref','INVALID_DI_REFERENCE'), ('wrong_di_type','INVALID_DI_REFERENCE'),
    ('missing_shape','MISSING_DI_ELEMENT'), ('missing_edge','MISSING_DI_ELEMENT'),
    ('duplicate_di','DUPLICATE_DI_ELEMENT'), ('negative_bounds','INVALID_DI_GEOMETRY'),
    ('zero_width','INVALID_DI_GEOMETRY'), ('infinite_waypoint','INVALID_DI_GEOMETRY'),
    ('one_waypoint','INVALID_BPMN_XML'), ('invalid_plane','BROKEN_REFERENCE'),
    ('invalid_xsd_type','INVALID_BPMN_XML')])
def test_damaged_xml_rejected_with_evidence(case, code):
    issues = validate_document(xml_with_mutation(case), complete_di=True)
    assert any(i.code == code and i.severity == 'error' and i.spec_section and i.line for i in issues)


def test_partial_di_is_legal_bpmn_but_not_complete_pulse_export():
    xml = xml_with_mutation('missing_shape')
    assert validate_document(xml) == []
    assert any(i.source == 'LAYOUT' for i in validate_document(xml, complete_di=True))


def test_qname_target_namespace_resolves():
    xml = build_bpmn(simple())
    # DI references are QName; Sequence Flow sourceRef/targetRef are IDREF.
    xml = xml.replace('xmlns:xsi=', 'xmlns:local="https://pulse.local/bpmn" xmlns:xsi=').replace('bpmnElement="', 'bpmnElement="local:').replace('processRef="', 'processRef="local:')
    assert validate_document(xml, complete_di=True) == []


def test_none_catch_is_xsd_valid_but_semantically_invalid():
    xml = build_bpmn(simple(('start_event','intermediate_event','end_event'))).replace('intermediateThrowEvent', 'intermediateCatchEvent')
    assert validate_xsd(xml) == []
    assert any(i.code == 'INVALID_NONE_CATCH' and i.source == 'BPMN_SPEC' for i in validate_document(xml))
    with pytest.raises(ValueError): import_process(xml)


def test_xsd_has_line_column_and_doctor_does_not_call_llm_on_broken_xml():
    xml = xml_with_mutation('invalid_xsd_type')
    issues = validate_xsd(xml)
    assert issues and issues[0].line is not None and issues[0].column is not None
    provider = Scripted([])
    response = client(provider).post('/api/process/audit', json={'xml': xml})
    assert response.status_code == 200
    assert response.json()['technical']['BPMN XML / XSD'] is False
    assert response.json()['issues'][0]['source'] == 'XSD'
    assert provider.calls == []


def test_clarification_is_distinct_from_spec_error():
    assert any(i.severity == 'clarification' and i.source == 'BUSINESS_LOGIC' for i in audit_rules(simple()))


def test_random_id_renaming_preserves_all_references_and_di():
    randomizer = random.Random(202602)
    for _ in range(12):
        p = event_contract()
        ids = [p.id] + [x.id for group in ('pools','participants','nodes','flows','message_flows') for x in getattr(p, group)]
        mapping = {key: 'Id_' + str(randomizer.randrange(10**12)) for key in ids}
        data = p.model_dump()
        def rewrite(value):
            if isinstance(value, str): return mapping.get(value, value)
            if isinstance(value, list): return [rewrite(v) for v in value]
            if isinstance(value, dict): return {k: rewrite(v) for k,v in value.items()}
            return value
        renamed = ProcessDefinition.model_validate(rewrite(data))
        xml = build_bpmn(renamed)
        assert not validate_document(xml, complete_di=True)
        assert import_process(xml, renamed) == renamed


def test_five_export_models_second_audit(tmp_path):
    scenarios = [simple(), simple(('task','user_task')), simple(('start_event','intermediate_event','end_event')), contract_process(), event_contract()]
    for index,p in enumerate(scenarios):
        xml = build_bpmn(p)
        assert not validate_document(xml, complete_di=True)
        assert import_process(xml, p) == p
        (tmp_path / f'audit-{index}.bpmn').write_text(xml, encoding='utf-8')


def test_incoming_message_none_start_is_recommendation_not_invented_standard_error():
    p = collaboration_example()
    start = next(n for n in p.nodes if n.id == 'CompanyStart')
    start.event_definition = 'none'
    issues = validate_process(p)
    assert not any(i.severity == 'error' for i in issues)
    assert any(i.code == 'MESSAGE_START_MARKER_RECOMMENDED' and i.source == 'MODEL_QUALITY' for i in issues)
    assert not validate_document(build_bpmn(p), complete_di=True)


def test_foreign_executable_expression_is_not_silently_downgraded_for_ai():
    xml = build_bpmn(contract_process()).replace('exporter="PULSE"', 'exporter="Other"')
    root = parse_xml(xml)
    expression = root.find('.//bpmn:conditionExpression', NS)
    expression.set(f"{{{NS['xsi']}}}type", 'bpmn:tFormalExpression')
    for doc in list(expression): expression.remove(doc)
    expression.text = 'price > 500000'
    with pytest.raises(ValueError, match='FormalExpression'): import_process(E.tostring(root,encoding='unicode'))


def test_unknown_event_type_gets_scope_notice_not_false_bpmn_error():
    xml = build_bpmn(simple(('start_event','intermediate_event','end_event'))).replace('intermediateThrowEvent','intermediateCatchEvent')
    root = parse_xml(xml)
    event = root.find('.//bpmn:intermediateCatchEvent', NS)
    E.SubElement(event, f"{{{NS['bpmn']}}}timerEventDefinition")
    issues = validate_document(E.tostring(root,encoding='unicode'))
    assert issues and all(i.code=='OUTSIDE_PULSE_SUBSET' and i.severity=='info' for i in issues)


def test_import_rejects_wrong_namespace_and_duplicate_ids_before_mapping():
    for xml in [xml_with_mutation('duplicate_id'), build_bpmn(simple()).replace('http://www.omg.org/spec/BPMN/20100524/MODEL','https://invalid.example/model')]:
        with pytest.raises(ValueError): import_process(xml)


def test_real_regression_has_all_roles_and_both_rework_paths():
    folder = Path(__file__).resolve().parents[2] / 'examples/bpmn-conformance'
    p = ProcessDefinition.model_validate_json((folder/'real-process.json').read_text(encoding='utf-8'))
    assert {r.name for r in p.participants} == {'Клиент','Оператор','Юридический отдел','Служба безопасности','Руководитель','Менеджер'}
    assert any(n.participant_id == 'leader' and n.type=='user_task' for n in p.nodes)
    edges = {(f.source,f.target) for f in p.flows}
    assert ('c_return_contract','c_receive_modified_contract') in edges
    assert ('c_receive_modified_contract','c_check_contract') in edges
    assert ('o_wait_corrected','o_check_docs') in edges
    assert ('o_resend_contract','Merge_o_wait_contract_response') in edges
    assert len([n for n in p.nodes if n.type=='parallel_gateway']) == 2
    assert not validate_document((folder/'real-result.bpmn').read_text(encoding='utf-8'),complete_di=True)
