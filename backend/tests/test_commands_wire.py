import json
from pathlib import Path
from copy import deepcopy

import pytest
from hypothesis import given, strategies as st, settings as hypothesis_settings
from fastapi.testclient import TestClient

from app.commands import rename_command
from app.config import Settings
from app.models import ProcessDefinition
from app.main import create_app
from app.repair import quality_fingerprint
from app.services.llm.mock_provider import MockLLMProvider
from tools.evidence_wire import encode, decode

ROOT = Path(__file__).resolve().parents[2]
DATA = json.loads((ROOT / 'examples/pipeline-resilience/exact-complex-polished-response.json').read_text(encoding='utf-8'))['process']


@pytest.mark.parametrize('command,collection,old,new', [
    ('Переименуй роль Оператор в Специалист', 'participants', 'Оператор', 'Специалист'),
    ('Переименуй дорожку «Менеджер» в «Координатор»', 'participants', 'Менеджер', 'Координатор'),
    ('Измени название задачи «Проверить документы» на «Проверить комплектность»', 'nodes', 'Проверить документы', 'Проверить комплектность'),
])
def test_exact_rename_one_field_all_ids_business_and_evidence_stable(command, collection, old, new):
    p = ProcessDefinition.model_validate(DATA)
    candidate = rename_command(p, command)
    assert candidate is not None
    expected = deepcopy(DATA)
    next(item for item in expected[collection] if item['name'] == old)['name'] = new
    assert candidate == ProcessDefinition.model_validate(expected)
    assert p == ProcessDefinition.model_validate(DATA)


@pytest.mark.parametrize('command', [
    'Оптимизируй согласование договора', 'Удалить задачу Проверить документы',
    'Переименуй роль Неизвестный в Специалист', 'Переименуй роль Оператор в Менеджер',
    'Переименуй роль Оператор в Специалист и удали согласование',
    'Переименуй роль Оператор в Специалист\nЗатем измени процесс',
    'Переименуй Менеджера в Специалиста',  # Morphological/fuzzy matching stays LLM.
    'Переименуй задачу Проверить заявку в Проверить комплект',  # Two separate departments.
])
def test_ambiguous_semantic_and_compound_commands_stay_llm(command):
    assert rename_command(ProcessDefinition.model_validate(DATA), command) is None


def test_exact_modify_endpoint_no_model_preview_changes_valid_xml():
    class ForbiddenLLM(MockLLMProvider):
        async def analyze_ambiguities(self, *args):
            raise AssertionError('Exact rename must not call LLM')
        async def modify_process(self, *args):
            raise AssertionError('Exact rename must not call LLM')
    with TestClient(create_app(Settings(llm_provider='mock', primary_llm_provider='', deterministic_modify_enabled=True), ForbiddenLLM())) as client:
        response = client.post('/api/process/modify', json={'process': DATA, 'command': 'Переименуй роль Оператор в Специалист'})
    assert response.status_code == 200
    result = response.json()
    assert result['xml'] and result['changes'] and result['metadata'] is None
    assert next(r for r in result['process']['participants'] if r['id'] == 'p_operator')['name'] == 'Специалист'


@hypothesis_settings(max_examples=100)
@given(st.lists(st.text(max_size=180), min_size=1, max_size=40))
def test_lossless_evidence_wire_exact_roundtrip_arbitrary_unicode_duplicates(texts):
    data = deepcopy(DATA)
    for index, node in enumerate(data['nodes']):
        node['source_text'] = texts[index % len(texts)]
    p = ProcessDefinition.model_validate(data)
    assert quality_fingerprint(decode(encode(p))) == quality_fingerprint(p)


@pytest.mark.parametrize('ref', [-1, True, 9999, '0'])
def test_wire_invalid_refs_never_reach_process(ref):
    wire = encode(ProcessDefinition.model_validate(DATA))
    wire['process']['nodes'][0]['source_text'] = {'ref': ref}
    with pytest.raises(ValueError):
        decode(wire)


def test_all_frozen_goldens_lossless_wire_roundtrip():
    for entry in json.loads((ROOT / 'examples/performance/golden.json').read_text(encoding='utf-8')):
        data = json.loads((ROOT / entry['path']).read_text(encoding='utf-8'))
        p = ProcessDefinition.model_validate(data.get('process', data))
        assert quality_fingerprint(decode(encode(p))) == quality_fingerprint(p)
