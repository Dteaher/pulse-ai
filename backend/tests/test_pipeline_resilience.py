import asyncio
import json
from pathlib import Path
import httpx
import pytest
from lxml import etree as E
from app.models import Ambiguity, AmbiguityAnalysis, Assumption, Node, Flow, MessageFlow, ProcessDefinition
from app.preflight import analyze
from app.pipeline import generate_valid, audit_rules
from app.diagnostics import PipelineFailure
from app.bpmn import build_bpmn, import_process, parse_xml, NS
from app.xml_validation import validate_document
from app.validator import validate_process
from app.semantics import validate_semantics
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.services.llm.router import LLMRouter
from app.services.llm.base import ProviderError
from app.models import Issue
from test_clarify_modify import Scripted, client
from test_bpmn_conformance import simple
from test_controlled_loops import contract_process
from test_collaboration import collaboration_example
from app.notation import polish_process


def exact_complex_reference():
    p = collaboration_example()
    folder = Path(__file__).resolve().parents[2] / 'examples/pipeline-resilience'
    p.description = (folder / 'exact-complex-input.txt').read_text(encoding='utf-8')
    p.name = 'Подключение объекта и согласование договора'
    p.flows = [f for f in p.flows if f.id not in {'Flow9', 'Flow27'}]
    next(n for n in p.nodes if n.id == 'Reject').participant_id = 'Operator'
    next(n for n in p.nodes if n.id == 'CompanyNo').participant_id = 'Operator'
    next(n for n in p.nodes if n.id == 'ClientYes').name = 'Подписанный договор отправлен'
    next(n for n in p.nodes if n.id == 'CompanyYes').name = 'Договор зарегистрирован'
    specs = [
        ('ReviewContract', 'user_task', 'Проверить договор', 'Client'),
        ('Agree', 'exclusive_gateway', 'Клиент согласен?', 'Client'),
        ('SendRevision', 'send_task', 'Вернуть договор на доработку', 'Client'),
        ('ReceiveUpdated', 'receive_task', 'Получить изменённый договор', 'Client'),
        ('Sign', 'user_task', 'Подписать договор', 'Client'),
        ('SendSigned', 'send_task', 'Отправить подписанный договор', 'Client'),
        ('WaitContractResponse', 'event_based_gateway', 'Ожидать ответ клиента', 'Manager'),
        ('ReceiveRevision', 'receive_task', 'Получить возвращённый договор', 'Manager'),
        ('ReviseContract', 'user_task', 'Изменить договор', 'Manager'),
        ('ResendContract', 'send_task', 'Повторно отправить договор', 'Manager'),
        ('ReceiveSigned', 'receive_task', 'Получить подписанный договор', 'Manager'),
        ('Register', 'user_task', 'Зарегистрировать договор', 'Manager'),
    ]
    p.nodes += [Node(id=i, type=t, name=name, participant_id=role) for i,t,name,role in specs]
    edges = [
        ('ReceiveContract','ReviewContract',''), ('ReviewContract','Agree',''),
        ('Agree','SendRevision','Клиент не согласен'), ('Agree','Sign','Клиент согласен'),
        ('SendRevision','ReceiveUpdated',''), ('ReceiveUpdated','ReviewContract',''),
        ('Sign','SendSigned',''), ('SendSigned','ClientYes',''),
        ('Send','WaitContractResponse',''), ('WaitContractResponse','ReceiveRevision',''),
        ('WaitContractResponse','ReceiveSigned',''), ('ReceiveRevision','ReviseContract',''),
        ('ReviseContract','ResendContract',''), ('ResendContract','WaitContractResponse',''),
        ('ReceiveSigned','Register',''), ('Register','CompanyYes',''),
    ]
    p.flows += [Flow(id=f'RevisionFlow{i}', source=a, target=b, name=c, condition=c or None)
                for i,(a,b,c) in enumerate(edges)]
    p.message_flows += [MessageFlow(id=f'RevisionMessage{i}',source=a,target=b)
        for i,(a,b) in enumerate([('SendRevision','ReceiveRevision'),
                                 ('ResendContract','ReceiveUpdated'),('SendSigned','ReceiveSigned')])]
    return polish_process(p)


def test_exact_requested_complex_regression_two_loops_parallel_xor_and_messages():
    p = exact_complex_reference()
    assert not any(i.severity == 'error' for i in validate_process(p) + validate_semantics(p))
    assert len(p.pools) == 2 and len(p.participants) == 6 and len(p.message_flows) == 8
    assert next(n for n in p.nodes if n.id == 'Approved').type == 'exclusive_gateway'
    assert next(n for n in p.nodes if n.id == 'Fork').type == 'parallel_gateway'
    assert next(n for n in p.nodes if n.id == 'Join').type == 'parallel_gateway'
    assert any(f.source == 'Correction' and f.target == 'Merge' for f in p.flows)
    assert any(f.source == 'ReceiveUpdated' and f.target == 'ReviewContract' for f in p.flows)
    xml = build_bpmn(p)
    assert not validate_document(xml, complete_di=True)
    assert import_process(xml, p) == p


class Analyzed(Scripted):
    def __init__(self, analyses, results=()):
        super().__init__(results)
        self.analyses = iter(analyses)
        self.analysis_calls = []

    async def analyze_ambiguities(self, text, context=None):
        self.analysis_calls.append((text, context))
        return next(self.analyses)


@pytest.mark.parametrize('kind,question', [
    ('unclear_participant', 'Кто повторно проверяет документы?'),
    ('unclear_parallelism', 'Проверки выполняются параллельно или последовательно?'),
    ('missing_branch', 'Что происходит при отказе руководителя?'),
    ('unclear_sequence', 'После какого действия продолжается обработка?'),
])
def test_critical_preflight_does_not_extract_or_build(kind, question):
    analysis = AmbiguityAnalysis(ambiguities=[Ambiguity(id='q', type=kind, question=question,
        source_excerpt='Исходная неопределённость', reason='Без ответа требуется угадать граф.')])
    provider = Analyzed([analysis, AmbiguityAnalysis()], [simple()])
    c = client(provider)
    first = c.post('/api/process/generate', json={'text': 'Исходный текст процесса'}).json()
    assert first['process'] is None and first['xml'] is None
    assert provider.calls == []
    assert first['diagnostics'][0]['type'] == 'CLARIFICATION_REQUIRED'
    final = c.post('/api/process/clarify', json={'preflight': first['preflight'],
        'answers': {'q': 'Оператор выполняет проверку последовательно; при отказе завершает обработку.'}})
    assert final.status_code == 200, final.text
    assert final.json()['xml'] and len(provider.calls) == 1
    assert 'Кто' in provider.analysis_calls[-1][1]['answers']['q'] or question in provider.analysis_calls[-1][1]['answers']['q']


def test_round_cap_never_accepts_critical_as_assumption():
    analysis = AmbiguityAnalysis(ambiguities=[Ambiguity(id='q', question='Кто исполняет задачу?')])
    provider = Analyzed([analysis, analysis])
    c = client(provider, max_clarification_rounds=1)
    first = c.post('/api/process/generate', json={'text': 'Описание без исполнителя'}).json()
    rejected = c.post('/api/process/clarify', json={'preflight': first['preflight'], 'answers': {}, 'accepted_ambiguity_ids': ['q']})
    assert rejected.status_code == 422
    second = c.post('/api/process/clarify', json={'preflight': first['preflight'], 'answers': {'q': 'Не знаю'}}).json()
    assert second['clarification_limit_reached'] and not second['xml']
    assert provider.calls == []


def test_modify_preflight_preserves_original_version():
    p = simple(); before = p.model_dump_json()
    provider = Analyzed([AmbiguityAnalysis(ambiguities=[Ambiguity(id='q', question='Кто выполняет повторную проверку?')])])
    result = client(provider).post('/api/process/modify', json={'process': p.model_dump(), 'command': 'Добавь повторную проверку'})
    assert result.status_code == 200 and result.json()['process'] is None
    assert provider.calls == [] and p.model_dump_json() == before


def test_optional_assumption_does_not_block_generation():
    q = Ambiguity(id='label', severity='warning', question='Как назвать завершение?', assumption='Заявка обработана')
    provider = Analyzed([AmbiguityAnalysis(ambiguities=[q])], [simple()])
    result = client(provider).post('/api/process/generate', json={'text': 'Описание линейного процесса'}).json()
    assert result['xml'] and result['ambiguities'] == []
    assert result['process']['assumptions'][0]['source'] == 'model_inference'
    assert import_process(result['xml']).assumptions[0].text == q.assumption


@pytest.mark.parametrize('collaboration', [False, True])
def test_assumptions_survive_xml_roundtrip(collaboration):
    p = contract_process() if collaboration else simple()
    p.assumptions = [Assumption(id='a', text='Ручная регистрация', confidence=0.8)]
    assert import_process(build_bpmn(p)).assumptions == p.assumptions


def test_explicit_modify_supersedes_only_conflicting_assumption():
    assumptions = [Assumption(id='old', text='Ручная проверка'), Assumption(id='keep', text='Ручной архив')]
    provider = Analyzed([AmbiguityAnalysis(superseded_assumption_ids=['old'])])
    state, _ = asyncio.run(analyze(provider, 'Автоматизируй проверку', context={'operation':'modify'}, assumptions=assumptions))
    assert [a.id for a in state.analysis.assumptions] == ['keep']


def test_builder_failure_never_retries_llm(monkeypatch):
    calls = []
    async def call(_): calls.append(1); return simple()
    def broken(_): raise ValueError('private parser traceback')
    monkeypatch.setattr('app.pipeline.build_bpmn', broken)
    with pytest.raises(PipelineFailure) as exc:
        asyncio.run(generate_valid(call))
    assert len(calls) == 1
    assert exc.value.diagnostics[0].type == 'INTEROPERABILITY_ERROR'
    assert 'traceback' not in str(exc.value)


def test_postbuild_rejects_bad_xml_without_model_retry(monkeypatch):
    calls = []
    async def call(_): calls.append(1); return simple()
    monkeypatch.setattr('app.pipeline.build_bpmn', lambda _: '<invalid/>')
    with pytest.raises(PipelineFailure): asyncio.run(generate_valid(call))
    assert len(calls) == 1


def test_bad_graph_with_critical_question_is_not_corrective_retry():
    p = simple(); p.flows[0].target = 'missing'
    p.ambiguities = [Ambiguity(id='q', question='Куда направить заявку?')]
    calls = []
    async def call(_): calls.append(1); return p
    result = asyncio.run(generate_valid(call))
    assert result['xml'] is None and len(calls) == 1


def test_legal_loop_is_business_warning_not_generation_error():
    p = contract_process()
    assert not any(i.severity == 'error' for i in validate_process(p) + validate_semantics(p))
    findings = [i for i in audit_rules(p) if i.code == 'UNBOUNDED_REWORK_LOOP']
    assert findings and all(i.type == 'BUSINESS_WARNING' and i.severity == 'warning' for i in findings)
    assert build_bpmn(p)


@pytest.mark.parametrize('mutation', ['orphan', 'deadend', 'wrong_role', 'missing_parallel_branch',
    'xor_as_parallel', 'parallel_as_xor', 'invalid_event_gateway', 'dangling_message', 'duplicate_flow', 'cross_pool_flow'])
def test_adversarial_graphs_are_rejected(mutation):
    p = contract_process()
    if mutation == 'orphan': p.nodes.append(Node(id='Orphan', type='task', participant_id=p.participants[0].id))
    elif mutation == 'deadend': p.flows = [f for f in p.flows if f.source != 'Price']
    elif mutation == 'wrong_role': p.nodes[0].participant_id = 'UnknownRole'
    elif mutation == 'missing_parallel_branch':
        split = next(n for n in p.nodes if n.type == 'parallel_gateway')
        outgoing = [f for f in p.flows if f.source == split.id]
        p.flows.remove(outgoing[0])
    elif mutation == 'xor_as_parallel': next(n for n in p.nodes if n.id == 'Amount').type = 'parallel_gateway'
    elif mutation == 'parallel_as_xor': next(n for n in p.nodes if n.type == 'parallel_gateway').type = 'exclusive_gateway'
    elif mutation == 'invalid_event_gateway': next(n for n in p.nodes if n.id == 'Amount').type = 'event_based_gateway'
    elif mutation == 'dangling_message': p.message_flows[0].target = 'Unknown'
    elif mutation == 'duplicate_flow': p.flows.append(p.flows[0].model_copy())
    elif mutation == 'cross_pool_flow':
        source, target = p.message_flows[0].source, p.message_flows[0].target
        p.flows.append(Flow(id='Cross', source=source, target=target))
    assert any(i.severity == 'error' for i in validate_process(p) + validate_semantics(p)), mutation


@pytest.mark.parametrize('attribute', ['messageRef', 'eventDefinitionRef', 'processRef', 'bpmnElement'])
def test_global_references_checked_even_outside_subset(attribute):
    root = parse_xml(build_bpmn(contract_process()))
    if attribute == 'messageRef':
        root.find('.//bpmn:messageEventDefinition', NS).set('messageRef', 'Missing')
    elif attribute == 'eventDefinitionRef':
        start = root.find('.//bpmn:startEvent', NS)
        E.SubElement(start, '{'+NS['bpmn']+'}eventDefinitionRef').text = 'Missing'
    elif attribute == 'processRef': root.find('.//bpmn:participant', NS).set('processRef', 'Missing')
    else: root.find('.//bpmndi:BPMNShape', NS).set('bpmnElement', 'Missing')
    issues = validate_document(E.tostring(root, encoding='unicode'))
    assert any(i.severity == 'error' for i in issues)


def test_real_compatible_analysis_transport_schema(monkeypatch):
    requests = []
    original = httpx.AsyncClient
    def handle(request):
        data = json.loads(request.content); requests.append(data)
        assert 'Не создавай узлы' in data['messages'][0]['content']
        return httpx.Response(200, json={'choices': [{'message': {'content': AmbiguityAnalysis().model_dump_json()}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    provider = OpenAICompatibleProvider('secret', 'https://test/v1', 'model', structured=True)
    assert asyncio.run(provider.analyze_ambiguities('Оператор принимает заявку')).ambiguities == []
    assert len(requests) == 1


def test_analysis_timeout_routes_to_fallback():
    class Failed(Analyzed):
        async def analyze_ambiguities(self, text, context=None):
            raise ProviderError('timeout', retryable=True, reason='timeout')
    router = LLMRouter(Failed([]), Analyzed([AmbiguityAnalysis()], [simple()]))
    result = client(router).post('/api/process/generate', json={'text': 'Оператор регистрирует заявку'})
    assert result.status_code == 200 and result.json()['metadata']['fallback_used']


def test_default_graph_retry_budget_is_one():
    calls = []
    async def bad(correction):
        calls.append(correction)
        p = simple(); p.flows[0].target = 'Missing'
        return p
    with pytest.raises(ProviderError) as exc:
        asyncio.run(generate_valid(bad))
    assert len(calls) == 2 and 'previous_result' in calls[1]
    assert exc.value.diagnostics and all(i.type for i in exc.value.diagnostics)


def test_real_modify_preserves_ids_and_has_explicit_xor_approval():
    folder = Path(__file__).resolve().parents[2] / 'examples/pipeline-resilience'
    before = ProcessDefinition.model_validate(json.loads((folder/'exact-complex-response.json').read_text(encoding='utf-8'))['process'])
    after = ProcessDefinition.model_validate(json.loads((folder/'exact-complex-polished-response.json').read_text(encoding='utf-8'))['process'])
    old = {n.id for n in before.nodes}; new = {n.id for n in after.nodes}
    assert len(old & new) / len(old) >= 0.9
    decision = next(n for n in after.nodes if n.id == 'o_decide')
    successors = {f.target for f in after.flows if f.source == decision.id}
    assert any(n.id in successors and n.type == 'exclusive_gateway' for n in after.nodes)
    assert not any(i.severity == 'error' for i in validate_process(after) + validate_semantics(after))


def test_di_failure_is_interoperability_not_business_warning():
    issue = Issue(code='INVALID_DI_REFERENCE', source='BPMN_SPEC', severity='error', message='Неверная ссылка DI.')
    assert issue.type == 'INTEROPERABILITY_ERROR'


def test_superseded_assumption_cannot_be_reintroduced_by_candidate():
    from app.preflight import attach_assumptions
    from app.models import PreflightState
    state = PreflightState(original_text='Автоматизировать проверку',
        analysis=AmbiguityAnalysis(superseded_assumption_ids=['old']))
    p = simple(); p.assumptions = [Assumption(id='old', text='Проверка вручную')]
    assert attach_assumptions(p, state).assumptions == []


def test_single_pool_generate_checks_explicit_business_contradiction():
    from test_business_safety import decision
    provider = Analyzed([AmbiguityAnalysis()], [decision('Отказ'), decision()])
    result = client(provider).post('/api/process/generate', json={'text': 'Полное описание решения по заявке'})
    assert result.status_code == 200 and result.json()['attempts'] == 2
    assert 'Противоречивый исход' in provider.calls[1][2]


def test_doctor_reports_orphan_in_collaboration_without_llm_audit():
    p = contract_process()
    root = parse_xml(build_bpmn(p))
    lane = root.find('.//bpmn:lane', NS)
    process = next(a for a in lane.iterancestors() if a.tag == '{'+NS['bpmn']+'}process')
    E.SubElement(process, '{'+NS['bpmn']+'}userTask', id='OrphanTask', name='Действие без связей')
    E.SubElement(lane, '{'+NS['bpmn']+'}flowNodeRef').text = 'OrphanTask'
    class NoAudit(Analyzed):
        async def audit_process(self, process, correction=''):
            raise AssertionError('Technical failure must not reach LLM business audit.')
    r = client(NoAudit([])).post('/api/process/audit', json={'xml': E.tostring(root, encoding='unicode')})
    assert r.status_code == 200, r.text
    assert r.json()['llm_audit'] is False
    assert any(i['code'] == 'ORPHAN_NODE' and i['type'] == 'MODEL_ERROR' for i in r.json()['issues'])


def test_production_adapter_optional_extraction_does_not_block_generation(monkeypatch):
    p = simple()
    p.ambiguities = [Ambiguity(id='wording', severity='warning', question='Как назвать завершение?', assumption='Заявка обработана')]
    requests = []
    original = httpx.AsyncClient
    def handle(request):
        body = json.loads(request.content); requests.append(body)
        name = body['response_format']['json_schema']['name']
        text = AmbiguityAnalysis().model_dump_json() if name == 'AmbiguityAnalysis' else p.model_dump_json()
        return httpx.Response(200, json={'choices': [{'message': {'content': text}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    provider = OpenAICompatibleProvider('secret', 'https://test/v1', 'model', structured=True)
    result = client(provider).post('/api/process/generate', json={'text': 'Оператор регистрирует заявку и завершает работу.'})
    assert result.status_code == 200, result.text
    assert result.json()['xml'] and result.json()['ambiguities'] == []
    assert result.json()['process']['assumptions'][0]['source'] == 'model_inference'
    assert len(requests) == 2
