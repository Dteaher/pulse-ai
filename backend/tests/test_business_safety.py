import json
import logging
import httpx
import pytest
from fastapi.testclient import TestClient
from app.models import ProcessDefinition, Participant, Node, Flow, Ambiguity
from app.semantics import validate_semantics
from app.validator import validate_process
from app.changes import preserve_ids, changes_between
from app.main import create_app
from app.config import Settings
from app.services.llm.factory import get_llm_provider
from app.services.llm.base import ProviderError
from test_clarify_modify import Scripted, client
from app.examples import demo_process


def process(nodes, flows):
    return ProcessDefinition(id='Process', name='Процесс', participants=[Participant(id='Role', name='Менеджер')],
        nodes=[Node(id=i, type=t, name=n, participant_id='Role') for i, t, n in nodes],
        flows=[Flow(id=f'F{j}', source=a, target=b, name=label, condition=condition) for j, (a, b, label, condition) in enumerate(flows)])


def decision(approved_end='Одобрено', positive_label='Одобрено'):
    return process([('S','start_event','Начало'),('G','exclusive_gateway','Заявка одобрена?'),('A','end_event',approved_end),('B','end_event','Отказ')],
        [('S','G','',None),('G','A',positive_label,positive_label),('G','B','Отказ','Не одобрено')])


def parallel():
    return process([('S','start_event','Начало'),('G','parallel_gateway','Параллельные проверки'),('A','task','Юридическая проверка'),('B','task','Проверка безопасности'),('J','parallel_gateway','Объединить проверки'),('E','end_event','Проверки завершены')],
        [('S','G','',None),('G','A','',None),('G','B','',None),('A','J','',None),('B','J','',None),('J','E','',None)])


def test_explicit_approved_branch_cannot_end_in_refusal():
    p = decision('Отказ')
    assert not validate_process(p)  # A graph-valid error that the old validator misses.
    assert any('Противоречивый исход' in i.message for i in validate_semantics(p))
    assert validate_semantics(decision()) == []


def test_yes_no_inherits_gateway_question_polarity():
    assert validate_semantics(decision('Одобрено', 'Да')) == []
    assert any(i.severity == 'error' for i in validate_semantics(decision('Отказ', 'Да')))


def test_a_new_decision_may_legitimately_change_outcome():
    p = decision()
    p.nodes.extend([Node(id='T',type='task',name='Проверить основания отказа',participant_id='Role'),Node(id='H',type='exclusive_gateway',name='Нарушение выявлено?',participant_id='Role')])
    p.flows[1].target = 'T'
    p.flows.extend([Flow(id='Extra1',source='T',target='H'),Flow(id='Extra2',source='H',target='B',condition='Да',name='Да'),Flow(id='Extra3',source='H',target='A',condition='Нет',name='Нет')])
    assert not validate_process(p)
    assert not any(i.severity == 'error' for i in validate_semantics(p))


@pytest.mark.parametrize('condition', ['  ', 'Условие 1', '???', '...'])
def test_conditions_must_be_meaningful(condition):
    p = decision(); p.flows[1].condition = condition
    assert any('содержательное условие' in i.message for i in validate_semantics(p))


def test_duplicate_conditions_rejected():
    p = decision(); p.flows[2].condition = p.flows[1].condition
    assert any('повторяются условия' in i.message for i in validate_semantics(p))


def test_branch_label_cannot_contradict_condition():
    p = decision(); p.flows[1].name = 'Отказ'
    assert any('противоречит условию' in i.message for i in validate_semantics(p))


def test_approval_task_is_not_mistaken_for_approved_outcome():
    p = decision('Согласование завершено')
    assert not any(i.severity == 'error' for i in validate_semantics(p))


def test_parallel_join_and_missing_join():
    p = parallel()
    assert not validate_process(p) and validate_semantics(p) == []
    p.nodes = [n for n in p.nodes if n.id != 'J']
    p.flows = [f for f in p.flows if f.source != 'J']
    for f in p.flows:
        if f.target == 'J': f.target = 'E'
    assert not validate_process(p)
    assert any('нет общего параллельного объединения' in i.message for i in validate_semantics(p))


def test_join_must_postdominate_all_branch_paths():
    p = parallel()
    p.nodes.append(Node(id='H', type='exclusive_gateway', name='Проверка успешна?', participant_id='Role'))
    p.nodes.append(Node(id='EndEarly', type='end_event', name='Проверка прервана', participant_id='Role'))
    p.flows[3].target = 'H'
    p.flows.extend([Flow(id='ToJoin',source='H',target='J',name='Да',condition='Да'),Flow(id='Bypass',source='H',target='EndEarly',name='Нет',condition='Нет')])
    assert not validate_process(p)
    assert any(i.severity == 'error' and 'параллель' in i.message for i in validate_semantics(p))


def test_empty_parallel_branch_can_feed_join_directly():
    p = parallel(); p.nodes = [n for n in p.nodes if n.id != 'A']
    p.flows = [f for f in p.flows if f.source != 'A']
    p.flows[1].target = 'J'
    assert not validate_process(p) and validate_semantics(p) == []


def test_nested_parallel_branches_use_their_own_joins():
    p = parallel()
    p.nodes.extend([Node(id=i, type=t, name=n, participant_id='Role') for i, t, n in
        [('InnerSplit', 'parallel_gateway', 'Дополнительные проверки'),
         ('C', 'task', 'Проверить реквизиты'), ('D', 'task', 'Проверить полномочия'),
         ('InnerJoin', 'parallel_gateway', 'Объединить дополнительные проверки')]])
    p.flows[3].target = 'InnerSplit'
    p.flows.extend([Flow(id=f'Inner{i}', source=a, target=b) for i, (a, b) in enumerate(
        [('InnerSplit', 'C'), ('InnerSplit', 'D'), ('C', 'InnerJoin'),
         ('D', 'InnerJoin'), ('InnerJoin', 'J')])])
    assert not validate_process(p) and validate_semantics(p) == []


def test_parallel_branches_cannot_share_a_join_input():
    p = parallel()
    p.nodes.append(Node(id='Merge', type='exclusive_gateway', name='Объединение', participant_id='Role'))
    p.nodes.append(Node(id='Choice', type='exclusive_gateway', name='Выбрать вход', participant_id='Role'))
    p.flows[3].target = 'Merge'
    p.flows[4].target = 'Merge'
    p.flows.extend([Flow(id='Merged', source='Merge', target='Choice'),
                    Flow(id='Shared1', source='Choice', target='J', condition='Первое', name='Первое'),
                    Flow(id='Shared2', source='Choice', target='J', condition='Второе', name='Второе')])
    assert not validate_process(p)
    assert any('отдельный вход' in issue.message for issue in validate_semantics(p))


def test_semantic_corrective_retry_and_safe_reason():
    p = decision('Отказ')
    scripted = Scripted([p, p, p])
    r = client(scripted).post('/api/process/modify', json={'process': decision().model_dump(), 'instruction':'Сделай изменение'})
    assert r.status_code == 502
    assert 'Причина: некорректное ветвление' in r.json()['detail']
    assert 'Противоречивый исход' in scripted.calls[1][2]
    good = Scripted([p, decision()])
    assert client(good).post('/api/process/modify',json={'process':decision().model_dump(),'instruction':'Сделай изменение'}).status_code == 200


def test_optional_assumptions_do_not_call_model_and_critical_cannot_be_skipped():
    p = demo_process(0)
    p.ambiguities = [Ambiguity(id=f'w{i}', question=f'Способ уведомления {i}?', severity='warning', assumption='Уведомление через личный кабинет') for i in range(2)]
    scripted = Scripted([])
    body = {'process':p.model_dump(),'answers':{},'accepted_ambiguity_ids':['w0','w1']}
    r = client(scripted).post('/api/process/clarify',json=body)
    assert r.status_code == 200 and r.json()['xml']
    assert len(r.json()['accepted_assumptions']) == 2 and not scripted.calls
    p.ambiguities[0].severity = 'critical'; body['process'] = p.model_dump()
    assert client(scripted).post('/api/process/clarify',json=body).status_code == 422
    body.update(continue_with_draft=True,clarification_round=3)
    assert client(scripted).post('/api/process/clarify',json=body).status_code == 422


def test_partial_optional_acceptance_preserves_critical_questions():
    p = demo_process(0)
    p.ambiguities = [Ambiguity(id='warn',question='Канал уведомления?',severity='warning'),Ambiguity(id='crit',question='Что происходит при отказе?')]
    provider = Scripted([demo_process(0)])
    r = client(provider).post('/api/process/clarify',json={'process':p.model_dump(),'answers':{'crit':'Уведомить клиента'},'accepted_ambiguity_ids':['warn']})
    assert r.status_code == 200 and len(r.json()['accepted_assumptions']) == 1
    assert provider.calls[0][1] == {'crit':'Уведомить клиента'}
    assert 'Канал уведомления' in provider.calls[0][2]


def test_id_stability_for_ninety_percent_unchanged_process():
    nodes = [('S','start_event','Начало')] + [(f'T{i}','task',f'Действие {i}') for i in range(20)] + [('E','end_event','Завершено')]
    flows = [(nodes[i][0],nodes[i+1][0],'',None) for i in range(len(nodes)-1)]
    before = process(nodes, flows); candidate = before.model_copy(deep=True)
    mapping = {n.id: 'New_' + n.id for n in candidate.nodes}
    for n in candidate.nodes: n.id = mapping[n.id]
    for f in candidate.flows: f.source, f.target = mapping[f.source], mapping[f.target]
    candidate.nodes[1].name = 'Новое действие А'; candidate.nodes[2].name = 'Новое действие Б'
    stable = preserve_ids(before,candidate)
    assert not validate_process(stable)
    unchanged = before.nodes[3:]
    preserved = sum(any(n.id == old.id and n.name == old.name for n in stable.nodes) for old in unchanged)
    assert preserved / len(unchanged) == 1
    assert len({n.id for n in before.nodes} & {n.id for n in stable.nodes}) / len(before.nodes) >= .9


def test_changes_have_categories_and_actions():
    before = decision(); after = before.model_copy(deep=True)
    after.participants[0].name = 'Старший менеджер'; after.flows[1].condition = 'Одобрено руководителем'
    changes = changes_between(before,after).changes
    assert {c.category for c in changes} == {'participant','condition'}
    assert all(c.action == 'changed' for c in changes)


@pytest.mark.parametrize('failure,reason', [(429,'http_429'),(503,'http_503'),('timeout','timeout'),('network','network_error'),(404,'model_unavailable')])
def test_failover_logs_safe_reason_not_error_body(monkeypatch,caplog,failure,reason):
    original = httpx.AsyncClient
    def handler(request):
        if request.url.host == 'primary.test':
            if failure == 'timeout': raise httpx.ReadTimeout('private_secret',request=request)
            if failure == 'network': raise httpx.ConnectError('private_secret',request=request)
            return httpx.Response(failure,text='private_secret traceback')
        return httpx.Response(200,json={'choices':[{'message':{'content':demo_process(0).model_dump_json()}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    s=Settings(_env_file=None,llm_provider='openai_compatible',llm_api_key='private_secret',llm_model='test',llm_base_url='https://primary.test',fallback_llm_provider='openai_compatible',fallback_llm_api_key='private_secret',fallback_llm_model='test',fallback_llm_base_url='https://backup.test')
    with caplog.at_level(logging.WARNING):
        r=TestClient(create_app(s)).post('/api/process/generate',json={'text':'Описание процесса'})
    assert r.status_code == 200
    assert 'reason='+reason in caplog.text and 'operation=parse_process' in caplog.text
    assert 'private_secret' not in caplog.text and 'traceback' not in caplog.text


def test_untrusted_reason_is_sanitized():
    assert ProviderError('Safe',reason='private_secret').reason == 'api_error'
    assert ProviderError('Safe',reason=None).reason == 'api_error'
