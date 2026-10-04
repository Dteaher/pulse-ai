"""Audit saved real responses without rewriting the original measurements."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.models import ProcessDefinition
from app.notation import polish_process
from app.telemetry import corrective_metrics
from tools.quality_oracle import quality


def run():
    folder = ROOT / 'examples/performance-v2'
    audit = {'oracle_sha256': hashlib.sha256((ROOT / 'backend/tools/quality_oracle.py').read_bytes()).hexdigest(),
             'cohort': 'same three texts; no cache; no fallback; equal 180s call timeout; one measurement per case, not a stability/performance percentile',
             'quality_policy': 'Explicit tasks need source_text; receive tasks may instead have a concrete incoming Message Flow, as in the frozen six-role reference.',
             'cases': {}, 'groups': {}}
    for provider in ('multiai', 'vertex_gemini'):
        for mode in ('separate', 'combined'):
            records, graph_cases = [], []
            for case in ('simple', 'complex', 'ambiguous'):
                stem = f'{provider}-{mode}-{case}'
                original = json.loads((folder / f'{stem}.json').read_text(encoding='utf-8'))
                response = json.loads((folder / f'{stem}-response.json').read_text(encoding='utf-8'))
                if response.get('xml'):
                    checked = quality(ProcessDefinition.model_validate(response['process']), case)
                elif case == 'ambiguous':
                    qs = response.get('ambiguities', [])
                    order = any(q['type'] in ('unclear_parallelism', 'unclear_sequence') and ('поряд' in q['question'].casefold() or 'паралл' in q['question'].casefold() or 'одноврем' in q['question'].casefold()) for q in qs)
                    recheck = any('повтор' in q['question'].casefold() and 'провер' in q['question'].casefold() for q in qs)
                    checked = {'passed': original['status'] == 200 and not response.get('process') and order and recheck,
                               'invariants': {'order_question': order, 'recheck_actor_question': recheck}}
                else:
                    checked = {'passed': False, 'reason': 'No validated complex BPMN: timeout or unnecessary critical questions'}
                record = {'elapsed_ms': original['elapsed_ms'], 'status': original['status'], 'quality': checked,
                          'first_pass_validator_success': original.get('first_pass_validator_success'),
                          'first_pass_quality_passed': original.get('first_pass_quality', {}).get('passed'),
                          'input_tokens': original['performance']['input_tokens'], 'output_tokens': original['performance']['output_tokens'],
                          'usage_complete': original['performance']['usage_complete']}
                audit['cases'][stem] = record
                records.append(original)
                if case != 'ambiguous':
                    graph_cases.append(bool(original.get('first_pass_validator_success')) and checked['passed'])
            group = corrective_metrics([r['performance'] for r in records])
            group.update(model_quality_pass_rate=sum(audit['cases'][f'{provider}-{mode}-{c}']['quality']['passed'] for c in ('simple', 'complex', 'ambiguous')) / 3,
                         quality_sample_count=3, first_pass_graph_request_success_rate=sum(graph_cases) / 2,
                         graph_request_sample_count=2)
            if mode == 'combined':
                group['combined_call_quality_pass_rate'] = group['model_quality_pass_rate']
            audit['groups'][provider + '-' + mode] = group
    controls = [json.loads((folder / f'revised-{p}-patch.json').read_text(encoding='utf-8')) for p in ('multiai', 'vertex_gemini')]
    audit['revised_patch_controls'] = {'successes': sum(c['bpmn'] and c['exact_canonical_preservation'] for c in controls),
        'attempts': 2, 'patch_success_rate': sum(c['bpmn'] and c['exact_canonical_preservation'] for c in controls) / 2,
        'patch_rejected_quality_rate': 0, 'meaning': 'Two injected duplicate-flow controls, not live extraction corrective coverage. Initial MultiAI schema failure remains saved separately.'}
    (folder / 'audited-results.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    run()
