import json
import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.examples import demo_process, ambiguous_process
from app.models import Ambiguity, ClarificationResult, ModificationResult, Flow
from app.changes import preserve_ids, changes_between
from app.services.llm.mock_provider import MockLLMProvider
from app.validator import validate_process
from app.bpmn import validate_xml


class Scripted(MockLLMProvider):
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    async def parse_process(self, text, correction=''):
        self.calls.append(('generate', text, correction))
        return next(self.results).model_copy(deep=True)

    async def clarify_process(self, process, answers, correction=''):
        self.calls.append(('clarify', answers, correction))
        return ClarificationResult(process=next(self.results).model_copy(deep=True))

    async def modify_process(self, process, command, correction=''):
        self.calls.append(('modify', command, correction))
        return ModificationResult(process=next(self.results).model_copy(deep=True))


def client(provider, **settings):
    return TestClient(create_app(Settings(_env_file=None, llm_provider='mock', **settings), provider))


def answers(p):
    return {a.id: 'Параллельно' for a in p.ambiguities}


def test_generate_questions_then_clarify_builds_valid_xml():
    draft = ambiguous_process()
    final = draft.model_copy(deep=True)
    final.ambiguities = []
    provider = Scripted([draft, final])
    c = client(provider)
    first = c.post('/api/process/generate', json={'text': 'Описание с несколькими проверками'}).json()
    assert first['xml'] is None and first['ambiguities']
    response = c.post('/api/process/clarify', json={'process': first['process'], 'original_text': 'Исходный текст', 'answers': [{'ambiguity_id': k, 'answer': v} for k, v in answers(draft).items()]})
    assert response.status_code == 200
    result = response.json()
    assert result['ambiguities'] == [] and result['xml']
    validate_xml(result['xml'])
    assert not validate_process(final)
    assert 'Исходный текст' in provider.calls[-1][2]


def test_additional_question_then_limit_requires_explicit_acceptance():
    draft = ambiguous_process()
    draft.ambiguities = [Ambiguity(id='followup', question='После какой проверки?', type='unclear_sequence')]
    provider = Scripted([draft])
    c = client(provider, max_clarification_rounds=1)
    body = {'process': draft.model_dump(), 'answers': {'followup': 'Наверное, после проверки'}}
    r = c.post('/api/process/clarify', json=body).json()
    assert r['xml'] is None and r['clarification_round'] == 1 and r['clarification_limit_reached']
    body['clarification_round'] = 1
    assert c.post('/api/process/clarify', json=body).status_code == 422
    body['continue_with_draft'] = True
    accepted = c.post('/api/process/clarify', json=body)
    assert accepted.status_code == 422
    assert 'Критичные' in accepted.json()['detail']
    assert len(provider.calls) == 1


@pytest.mark.parametrize('submitted', [{}, {'wrong': 'Ответ'}, {'parallel': ''}, {'parallel': 'Ответ'}])
def test_clarify_rejects_missing_unknown_or_empty_answers(submitted):
    c = client(Scripted([]))
    assert c.post('/api/process/clarify', json={'process': ambiguous_process().model_dump(), 'answers': submitted}).status_code == 422


def test_modify_v2_changes_and_id_preservation():
    before = demo_process(1)
    after = before.model_copy(deep=True)
    after.nodes[1].name = 'Проверить комплект документов'
    provider = Scripted([after])
    response = client(provider).post('/api/process/modify', json={'process': before.model_dump(), 'instruction': 'Переименуй проверку документов'})
    assert response.status_code == 200
    result = response.json()
    assert result['changes'][0]['type'] == 'node_changed'
    assert {n['id'] for n in result['process']['nodes']} == {n.id for n in before.nodes}
    validate_xml(result['xml'])
    assert before.nodes[1].name != after.nodes[1].name


def test_full_id_regeneration_is_reconciled_without_losing_references():
    before = demo_process(1)
    after = before.model_copy(deep=True)
    mapping = {x.id: 'New_' + x.id for x in after.participants + after.nodes + after.flows}
    for x in after.participants + after.nodes + after.flows:
        x.id = mapping[x.id]
    for n in after.nodes:
        n.participant_id = mapping[n.participant_id]
    for f in after.flows:
        f.source, f.target = mapping[f.source], mapping[f.target]
    stable = preserve_ids(before, after)
    assert stable == before
    assert not validate_process(stable)
    assert changes_between(before, stable).changes == []


@pytest.mark.parametrize('corrected', [True, False])
def test_invalid_v2_corrective_retry_or_safe_failure(corrected):
    before = demo_process(1)
    bad = before.model_copy(deep=True)
    bad.flows[0].target = 'missing'
    provider = Scripted([bad, before] if corrected else [bad, bad, bad])
    r = client(provider).post('/api/process/modify', json={'process': before.model_dump(), 'command': 'Измени процесс'})
    assert r.status_code == (200 if corrected else 502)
    assert 'missing' in provider.calls[1][2]
    assert before.flows[0].target != 'missing'
    if not corrected:
        assert 'Текущая версия процесса сохранена' in r.json()['detail']


def test_ambiguous_modify_reuses_clarify_with_original_instruction():
    before = demo_process(1)
    draft = before.model_copy(deep=True)
    draft.ambiguities = [Ambiguity(id='which', question='После какого согласования?', type='unclear_sequence')]
    provider = Scripted([draft, before])
    c = client(provider)
    r = c.post('/api/process/modify', json={'process': before.model_dump(), 'instruction': 'Добавь проверку после согласования'}).json()
    assert r['xml'] is None and r['changes'] == []
    r2 = c.post('/api/process/clarify', json={'process': r['process'], 'answers': {'which': 'После руководителя'}, 'base_process': before.model_dump(), 'operation': 'modify', 'instruction': 'Добавь проверку после согласования'})
    assert r2.status_code == 200 and r2.json()['xml']
    assert 'Добавь проверку после согласования' in provider.calls[-1][2]


def test_changes_cover_participant_condition_sequence_and_gateway():
    before = demo_process(1)
    after = before.model_copy(deep=True)
    after.participants[0].name += ' отдела'
    after.flows[0].target = before.nodes[-1].id
    after.flows[1].condition = 'Стоимость > 500000'
    after.nodes.append(after.nodes[0].model_copy(update={'id': 'NewGateway', 'type': 'parallel_gateway'}))
    kinds = {c.type for c in changes_between(before, after).changes}
    assert {'participant_changed', 'condition_changed', 'sequence_changed', 'gateway_added'} <= kinds


@pytest.mark.parametrize('operation', ['clarify', 'modify'])
@pytest.mark.parametrize('fallback', [False, True])
def test_operations_use_router_and_fallback(monkeypatch, operation, fallback):
    real_client = httpx.AsyncClient
    hosts = []
    def handler(request):
        hosts.append(request.url.host)
        if fallback and request.url.host == 'primary.test':
            return httpx.Response(503)
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process(1).model_dump_json()}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    settings = Settings(_env_file=None, primary_llm_provider='openai_compatible', primary_llm_model='gpt-6.1-sol', primary_llm_api_key='fake', primary_llm_base_url='https://primary.test', fallback_llm_provider='yandex', fallback_llm_model='gpt://folder/mock', fallback_llm_api_key='fake', fallback_llm_base_url='https://backup.test')
    body = {'process': demo_process(1).model_dump(), 'instruction': 'Измени процесс'} if operation == 'modify' else {'process': ambiguous_process().model_dump(), 'answers': answers(ambiguous_process())}
    r = TestClient(create_app(settings)).post('/api/process/' + operation, json=body)
    assert r.status_code == 200
    assert r.json()['metadata']['fallback_used'] == fallback
    assert hosts == (['primary.test', 'backup.test'] if fallback else ['primary.test'])
