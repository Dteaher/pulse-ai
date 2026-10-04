"""Cache-miss real API comparison, same text and validators, isolated config."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.main import create_app
from app.models import ProcessDefinition
from app.services.llm.decision import ReadyDecision, ParseDecision
from app.notation import polish_process
from tools.quality_oracle import quality


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--providers', default='multiai,vertex_gemini')
    parser.add_argument('--modes', default='separate,combined')
    parser.add_argument('--cases', default='simple,complex,ambiguous')
    parser.add_argument('--prefix', default='')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    folder = ROOT / 'examples/performance-v2'
    folder.mkdir(exist_ok=True)
    for name in args.providers.split(','):
        for mode in args.modes.split(','):
            for case in args.cases.split(','):
                stem = f'{args.prefix}{name}-{mode}-{case}'
                s = Settings(llm_fallback_enabled=False, llm_performance_enabled=True, llm_patch_corrective_enabled=True,
                             llm_combined_parse_enabled=mode == 'combined', llm_combined_enabled=False, llm_patch_enabled=False,
                             app_env='production', llm_timeout=args.timeout, llm_request_budget=600, performance_cache_entries=0)
                if name == 'vertex_gemini':
                    s.primary_llm_provider = 'vertex_gemini'
                    s.primary_llm_model = s.fallback_llm_model
                    s.primary_llm_api_key = s.fallback_llm_api_key
                app = create_app(s)
                adapter = app.state.provider.primary
                original = adapter._request_once
                candidates = []
                async def trace(*a, **kw):
                    value = await original(*a, **kw)
                    p = value if isinstance(value, ProcessDefinition) else value.result.process if isinstance(value, ParseDecision) and isinstance(value.result, ReadyDecision) else None
                    if p is not None:
                        candidates.append(p.model_copy(deep=True))
                    return value
                adapter._request_once = trace
                text = (ROOT / f'examples/performance/{case}.txt').read_text(encoding='utf-8')
                start = perf_counter()
                with TestClient(app) as client:
                    response = client.post('/api/process/generate', json={'text': text}, headers={'X-Pulse-Session': 'performance-v2-' + stem})
                    data = response.json()
                    performance = app.state.performance_profiles.get(response.headers.get('X-Request-ID'))
                record = {'provider': name, 'mode': mode, 'case': case, 'input_sha256': hashlib.sha256(text.encode()).hexdigest(),
                          'status': response.status_code, 'elapsed_ms': round((perf_counter()-start)*1000, 2), 'performance': performance,
                          'metadata': data.get('metadata'), 'bpmn': bool(data.get('xml')), 'questions': len(data.get('ambiguities', [])),
                          'cache_used': False, 'fallback_enabled': False}
                if data.get('xml'):
                    record['quality'] = quality(ProcessDefinition.model_validate(data['process']), case)
                    record['xsd_pass'] = record['di_pass'] = True  # Mandatory pipeline succeeded.
                    (folder / f'{stem}.bpmn').write_text(data['xml'], encoding='utf-8')
                elif case == 'ambiguous':
                    questions = data.get('ambiguities', [])
                    record['quality'] = {'passed': response.status_code == 200 and len(questions) >= 2 and all(q['severity'] == 'critical' for q in questions) and data.get('process') is None,
                                         'invariants': {'missing_parallelism_and_recheck': len(questions) >= 2}, 'question_types': [q['type'] for q in questions]}
                else:
                    record['quality'] = {'passed': False, 'reason': 'no validated BPMN'}
                if candidates:
                    first = polish_process(candidates[0])
                    record['first_pass_quality'] = quality(first, case)
                    record['first_pass_validator_success'] = not record['first_pass_quality']['graph_errors']
                    (folder / f'{stem}-first-process.json').write_text(candidates[0].model_dump_json(indent=2), encoding='utf-8')
                (folder / f'{stem}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                (folder / f'{stem}-response.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                print(stem, record['elapsed_ms'], response.status_code, record['quality']['passed'], flush=True)


if __name__ == '__main__':
    run()
