"""Opt-in live benchmarks of Modify/Doctor/Clarify using fixed saved inputs."""
import json
import sys
import argparse
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.main import create_app


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--continue-clarify', action='store_true')
    parser.add_argument('--final-clarify', action='store_true')
    args = parser.parse_args()
    folder = ROOT / 'examples/performance'
    source = json.loads((folder / 'before-simple-response.json').read_text(encoding='utf-8'))
    unclear = json.loads((folder / 'before-ambiguous-response.json').read_text(encoding='utf-8'))
    answers = {q['id']: 'Оператор выполняет повторную проверку документов. Юрист и инженер проверяют параллельно; после обеих проверок оператор принимает заявку.' for q in unclear['ambiguities']}
    cases = {
        'doctor': ('/api/process/audit', {'xml': source['xml'], 'previous': source['process']}),
        'modify': ('/api/process/modify', {'process': source['process'], 'command': 'Переименуй действие регистрации заявки в «Зарегистрировать заявку в системе». Сохрани всех участников, условия, ветки, связи и остальные действия.'}),
        'clarify': ('/api/process/clarify', {'process': None, 'preflight': unclear['preflight'], 'answers': answers, 'original_text': unclear['preflight']['original_text']}),
    }
    if args.continue_clarify:
        pending = json.loads((folder / 'ops-before-clarify-response.json').read_text(encoding='utf-8'))
        cases = {'clarify-complete': ('/api/process/clarify', {
            'process': None, 'preflight': pending['preflight'], 'clarification_round': 1,
            'answers': {q['id']: 'Оператор дорабатывает заявку после замечаний юриста или инженера и сам выполняет повторную проверку. Юрист и инженер выполняют проверки параллельно.' for q in pending['ambiguities']},
            'original_text': pending['preflight']['original_text']})}
    phases = [('ops-before', False), ('ops-after', True)]
    if args.final_clarify:
        pending = json.loads((folder / 'ops-after-clarify-complete-response.json').read_text(encoding='utf-8'))
        cases = {'clarify-final': ('/api/process/clarify', {
            'process': None, 'preflight': pending['preflight'], 'clarification_round': 2,
            'answers': {q['id']: 'Если замечания остаются, оператор снова дорабатывает заявку и повторно проверяет документы. Если замечаний нет, заявка возвращается на параллельные проверки юриста и инженера. После обеих успешных проверок менеджер оформляет договор и передаёт его клиенту. Клиент получает договор; этим весь процесс заканчивается. Если у юриста или инженера опять есть замечания, цикл доработки повторяется.' for q in pending['ambiguities']},
            'original_text': pending['preflight']['original_text']})}
        phases = [('ops-after', True)]
    for phase, enabled in phases:
        settings = Settings(app_env='production', llm_performance_enabled=enabled, llm_combined_enabled=False,
                            llm_patch_enabled=False, llm_request_budget=600, llm_reasoning_effort='low')
        app = create_app(settings)
        with TestClient(app) as client:
            for name, (endpoint, body) in cases.items():
                started = perf_counter()
                response = client.post(endpoint, json=body, headers={'X-Pulse-Session': 'operations-benchmark-' + phase})
                data = response.json()
                report = {'status': response.status_code, 'request_total_ms': round((perf_counter()-started)*1000, 2),
                          'metadata': data.get('metadata'), 'bpmn': bool(data.get('xml')),
                          'performance': app.state.performance_profiles[response.headers['X-Request-ID']]}
                if name == 'modify' and data.get('process'):
                    before_ids = {n['id'] for n in source['process']['nodes']}
                    after_ids = {n['id'] for n in data['process']['nodes']}
                    report['preserved_node_ids'] = len(before_ids & after_ids)
                    report['base_node_ids'] = len(before_ids)
                    report['changes'] = data.get('changes')
                (folder / f'{phase}-{name}.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                (folder / f'{phase}-{name}-response.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps(report, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    run()
