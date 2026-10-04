import asyncio
import json
import httpx
from app.config import Settings
from app.examples import demo_process
from app.services.llm.yandex_provider import YandexProvider
from app.main import create_app
from app.services.llm.factory import get_llm_provider
from fastapi.testclient import TestClient
from evaluate import has_cycle, check_expectations, main


def test_short_qwen_name_becomes_uri(monkeypatch):
    original = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        assert body['model'] == 'gpt://test-folder/qwen3.6-35b-a3b'
        assert request.headers['OpenAI-Project'] == 'test-folder'
        assert body['response_format']['type'] == 'json_schema'
        return httpx.Response(200, json={'choices': [{'message': {'content': demo_process().model_dump_json()}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    settings = Settings(_env_file=None, llm_provider='yandex', llm_api_key='test-key', llm_model='qwen3.6-35b-a3b', yandex_folder_id='test-folder')
    provider = get_llm_provider(settings)
    assert provider.configured
    assert asyncio.run(provider.parse_process('Описание')).nodes


def test_missing_credentials_reported_without_network():
    provider = YandexProvider('', 'https://ai.api.cloud.yandex.net/v1', 'qwen3.6-35b-a3b')
    assert not provider.configured
    assert provider.missing_settings() == ['LLM_API_KEY', 'YANDEX_FOLDER_ID или каталог в LLM_MODEL']


def test_folder_mismatch_is_not_silently_sent():
    provider = YandexProvider('test-key', 'https://ai.api.cloud.yandex.net/v1', 'gpt://other-folder/qwen3.6-35b-a3b', folder_id='test-folder')
    assert not provider.configured
    assert 'должен совпадать' in provider.missing_settings()[0]


def test_health_does_not_expose_key():
    app = create_app(Settings(_env_file=None, llm_provider='yandex', llm_api_key='do-not-expose', llm_model='qwen3.6-35b-a3b', yandex_folder_id='test-folder'))
    response = TestClient(app).get('/api/health')
    assert response.json()['configured'] is True
    assert 'do-not-expose' not in response.text


def test_evaluation_structural_checks():
    p = demo_process()
    assert has_cycle(p)
    assert all(check_expectations(p, {'min_participants': 6, 'min_parallel': 2, 'min_exclusive': 2, 'min_ends': 2, 'return_cycle': True}).values())
    assert not check_expectations(p, {'clarification': True})['clarification']


def test_evaluation_config_check_never_calls_api(monkeypatch, capsys):
    class OfflineProvider:
        def missing_settings(self):
            return ['LLM_API_KEY']
        async def parse_process(self, *args):
            raise AssertionError('must not call API')
    monkeypatch.setattr('evaluate.get_llm_provider', lambda settings: OfflineProvider())
    monkeypatch.setattr('evaluate.Settings', lambda: Settings(_env_file=None, llm_provider='yandex'))
    assert main(['--check-config']) == 2
    assert 'LLM_API_KEY' in capsys.readouterr().out


def test_fifteen_evaluation_cases_have_unique_ids():
    from evaluate import CASES
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    assert len(cases) == 15
    assert len({c['id'] for c in cases}) == 15
    assert all(c['text'] and c['expected'] and c['review_points'] for c in cases)
