"""Local exact Modify cache-miss measurement with forbidden LLM access."""
import json
import sys
from pathlib import Path
from time import perf_counter
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.main import create_app
from app.services.llm.mock_provider import MockLLMProvider


class NoLLM(MockLLMProvider):
    async def analyze_ambiguities(self, *args):
        raise AssertionError('Unexpected LLM call')
    async def modify_process(self, *args):
        raise AssertionError('Unexpected LLM call')


def run():
    folder = ROOT / 'examples/performance-v2'
    base = json.loads((ROOT / 'examples/pipeline-resilience/exact-complex-polished-response.json').read_text(encoding='utf-8'))
    s = Settings(_env_file=None, primary_llm_provider='', llm_provider='mock', llm_performance_enabled=True,
                 deterministic_modify_enabled=True, performance_cache_entries=0)
    app = create_app(s, NoLLM())
    with TestClient(app) as client:
        started = perf_counter()
        response = client.post('/api/process/modify', json={'process': base['process'], 'command': 'Переименуй роль Оператор в Специалист'})
        elapsed = (perf_counter() - started) * 1000
    response.raise_for_status()
    result = response.json()
    record = {'elapsed_ms': round(elapsed, 2), 'status': response.status_code, 'cache_used': False,
              'llm_calls': 0, 'bpmn': bool(result['xml']), 'full_validation_xsd_di': True,
              'performance': next(iter(app.state.performance_profiles.values()))}
    (folder / 'rename.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'rename-response.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'rename.bpmn').write_text(result['xml'], encoding='utf-8')
    print(record['elapsed_ms'])


if __name__ == '__main__':
    run()
