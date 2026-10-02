import asyncio
import pytest
from fastapi.testclient import TestClient
from lxml import etree
from pydantic import ValidationError
from app.examples import demo_process, TEXTS
from app.models import ProcessDefinition, Node, Flow
from app.validator import validate_process
from app.bpmn import build_bpmn, import_process, validate_xml, NS, layout
from app.config import Settings
from app.main import create_app
from app.services.llm.mock_provider import MockLLMProvider
from app.services.llm.openai_compatible_provider import strict_schema
from app.services.llm.base import ProviderError
from app.pipeline import generate_valid


@pytest.fixture
def client():
    return TestClient(create_app(Settings(_env_file=None, llm_provider='mock'), MockLLMProvider()))


def test_strict_model():
    data = demo_process().model_dump()
    data['unexpected'] = True
    with pytest.raises(ValidationError):
        ProcessDefinition.model_validate(data)
    data.pop('unexpected')
    data['nodes'][0]['id'] = 'bad id'
    with pytest.raises(ValidationError):
        ProcessDefinition.model_validate(data)


@pytest.mark.parametrize('index', [0, 1])
def test_valid_examples(index):
    assert validate_process(demo_process(index)) == []


@pytest.mark.parametrize('problem', ['flow', 'duplicate', 'start', 'end', 'participant', 'unreachable', 'dangling', 'condition', 'parallel_condition'])
def test_graph_errors(problem):
    p = demo_process()
    if problem == 'flow': p.flows[0].target = 'Missing'
    if problem == 'duplicate': p.nodes[1].id = p.nodes[0].id
    if problem == 'start': p.nodes = [n for n in p.nodes if n.type != 'start_event']
    if problem == 'end': p.nodes = [n for n in p.nodes if n.type != 'end_event']
    if problem == 'participant': p.nodes[0].participant_id = 'Missing'
    if problem == 'unreachable': p.nodes.append(Node(id='Unreachable', type='task', name='Сирота', participant_id='Client'))
    if problem == 'dangling': p.flows = [f for f in p.flows if f.source != 'Send']
    if problem == 'condition': next(f for f in p.flows if f.source == 'Complete').condition = None
    if problem == 'parallel_condition': next(f for f in p.flows if f.source == 'Fork').condition = 'Да'
    assert validate_process(p)


def test_bpmn_xsd_and_roundtrip():
    p = demo_process()
    xml = build_bpmn(p)
    validate_xml(xml)
    root = etree.fromstring(xml.encode())
    assert len(root.findall('.//bpmn:lane', NS)) == 6
    assert len(root.findall('.//bpmn:exclusiveGateway', NS)) == 2
    assert len(root.findall('.//bpmn:parallelGateway', NS)) == 2
    assert len(root.findall('.//bpmndi:BPMNShape', NS)) == len(p.nodes) + 7
    assert len(root.findall('.//bpmndi:BPMNEdge', NS)) == len(p.flows)
    imported = import_process(xml, p)
    assert imported.nodes == p.nodes
    assert imported.flows == p.flows
    assert not validate_process(imported)


def test_layout_no_node_overlap():
    p = demo_process()
    bounds, *_ = layout(p)
    boxes = list(bounds.values())
    for i, (x, y, w, h) in enumerate(boxes):
        for tx, ty, tw, th in boxes[i+1:]:
            assert x+w <= tx or tx+tw <= x or y+h <= ty or ty+th <= y
    assert bounds['Join'][0] > bounds['LegalCheck'][0]
    assert bounds['Join'][0] > bounds['SecurityCheck'][0]


def test_default_branch_xsd():
    p = demo_process()
    f = next(f for f in p.flows if f.source == 'Complete')
    f.is_default, f.condition = True, None
    validate_xml(build_bpmn(p))


def test_inclusive_and_intermediate_xsd():
    p = demo_process()
    for n in p.nodes:
        if n.type == 'exclusive_gateway': n.type = 'inclusive_gateway'
    p.nodes.append(Node(id='Intermediate', type='intermediate_event', name='Договор получен', participant_id='Manager'))
    next(f for f in p.flows if f.source == 'Send').source = 'Intermediate'
    p.flows.append(Flow(id='WaitForReceipt', source='Send', target='Intermediate'))
    validate_xml(build_bpmn(p))
    assert import_process(build_bpmn(p)).nodes[-1].type == 'intermediate_event'


@pytest.mark.parametrize('modification', ['executable', 'multiple_pools', 'extension'])
def test_import_does_not_drop_semantics(modification):
    xml = build_bpmn(demo_process())
    if modification == 'executable': xml = xml.replace('isExecutable="false"', 'isExecutable="true"')
    if modification == 'multiple_pools': xml = xml.replace('</bpmn:collaboration>', '<bpmn:participant id="External" name="Другой пул"/></bpmn:collaboration>')
    if modification == 'extension': xml = xml.replace('<bpmn:userTask id="Submit"', '<bpmn:userTask isForCompensation="true" id="Submit"')
    with pytest.raises(ValueError): import_process(xml)


def test_xml_escaping():
    p = demo_process()
    p.nodes[1].name = 'Заявка <500 & >100'
    assert import_process(build_bpmn(p)).nodes[1].name == p.nodes[1].name


def test_dtd_rejected():
    with pytest.raises(ValueError):
        import_process('<!DOCTYPE definitions [<!ENTITY a SYSTEM "file:///test">]><definitions/>')


def test_unsupported_elements_do_not_silently_disappear():
    xml = build_bpmn(demo_process()).replace('<bpmn:userTask id="Submit"', '<bpmn:subProcess id="Submit"').replace('</bpmn:userTask>', '</bpmn:subProcess>', 1)
    with pytest.raises(ValueError): import_process(xml)


def test_generate_modify_audit(client):
    response = client.post('/api/process/generate', json={'text': TEXTS[1]})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['xml'] and data['attempts'] == 1
    modified = client.post('/api/process/modify', json={'process': data['process'], 'command': 'После проверки документов добавь согласование руководителем'})
    assert modified.status_code == 200, modified.text
    after = modified.json()
    assert len(after['process']['nodes']) == len(data['process']['nodes']) + 1
    report = client.post('/api/process/audit', json={'xml': after['xml'], 'previous': after['process']})
    assert report.status_code == 200, report.text
    assert all(report.json()['technical'].values())
    assert report.json()['llm_audit'] is False


@pytest.mark.parametrize('parallel', ['Параллельно', 'Последовательно'])
@pytest.mark.parametrize('refusal', ['Уведомить клиента об отказе', 'Вернуть документы на доработку'])
def test_clarification(client, parallel, refusal):
    data = client.post('/api/process/generate', json={'text': TEXTS[2]}).json()
    assert data['xml'] is None
    assert len(data['ambiguities']) == 2
    result = client.post('/api/process/clarify', json={'process': data['process'], 'answers': {'parallel': parallel, 'refusal': refusal}})
    assert result.status_code == 200, result.text
    assert result.json()['xml']
    assert not result.json()['ambiguities']


def test_mock_rejects_arbitrary_input(client):
    response = client.post('/api/process/generate', json={'text': 'Произвольный процесс, который нельзя подменять примером.'})
    assert response.status_code == 502


def test_missing_key_is_clear():
    client = TestClient(create_app(Settings(_env_file=None, llm_provider='openai', llm_api_key='', llm_model='')))
    response = client.post('/api/process/generate', json={'text': TEXTS[1]})
    assert response.status_code == 502
    assert '.env' in response.json()['detail']
    assert 'Traceback' not in response.text


def test_corrective_retry():
    calls = []
    async def call(correction):
        calls.append(correction)
        p = demo_process()
        if len(calls) < 3: p.flows[0].target = 'Missing'
        return p
    result = asyncio.run(generate_valid(call))
    assert result['attempts'] == 3
    assert 'previous_result' in calls[1]


def test_corrective_retry_bounded():
    async def bad(correction):
        raise ValueError('invalid json')
    with pytest.raises(ProviderError): asyncio.run(generate_valid(bad))


def test_strict_schema_required():
    schema = strict_schema(ProcessDefinition)
    for definition in [schema, *schema['$defs'].values()]:
        if definition.get('type') == 'object':
            assert definition['additionalProperties'] is False
            assert set(definition['required']) == set(definition['properties'])
