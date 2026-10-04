"""Explicit real API experiment; no production configuration is modified."""
import asyncio
import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.models import ProcessDefinition
from app.notation import polish_process
from app.pipeline import generate_valid
from app.repair import repair_context, quality_fingerprint, business_loops
from app.validator import validate_process
from app.semantics import validate_semantics
from app.services.llm.factory import get_llm_provider
from app.telemetry import profile, PerformanceProfile


def fixture():
    data = json.loads((ROOT / 'examples/pipeline-resilience/exact-complex-polished-response.json').read_text(encoding='utf-8'))
    reference = polish_process(ProcessDefinition.model_validate(data['process']))
    broken = reference.model_copy(deep=True)
    flow = next(f for f in broken.flows if f.target == 'o_join_checks')
    broken.flows.append(flow.model_copy(update={'id': 'injected_duplicate'}))
    return reference, broken


async def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--providers', default='multiai,vertex_gemini')
    parser.add_argument('--modes', default='patch,full')
    parser.add_argument('--prefix', default='')
    args = parser.parse_args()
    folder = ROOT / 'examples/performance-v2'
    folder.mkdir(exist_ok=True)
    reference, broken = fixture()
    errors = [e for e in validate_process(broken) + validate_semantics(broken) if e.severity == 'error']
    (folder / 'repair-input.json').write_text(broken.model_dump_json(indent=2), encoding='utf-8')
    for provider_name in args.providers.split(','):
        for mode in args.modes.split(','):
            output_name = f'{args.prefix}{provider_name}-{mode}'
            s = Settings(llm_fallback_enabled=False, llm_patch_corrective_enabled=mode == 'patch',
                         llm_performance_enabled=True, llm_request_budget=360)
            if provider_name == 'vertex_gemini':
                s.primary_llm_provider = 'vertex_gemini'
                s.primary_llm_model = s.fallback_llm_model
                s.primary_llm_api_key = s.fallback_llm_api_key
            provider = get_llm_provider(s)
            measured = PerformanceProfile()
            token = profile.set(measured)
            start = perf_counter()
            record = {'mode': mode, 'provider': provider_name, 'fixture': 'same injected duplicate, six-role complex canonical graph',
                      'synthetic_fault': True, 'cache_used': False}
            try:
                if mode == 'patch':
                    patch = await provider.repair_process(repair_context(broken, errors))
                    record['patch'] = patch.model_dump()
                    from app.repair import apply_repair, assert_preservation
                    candidate = apply_repair(broken, errors, patch)
                    async def resolved(_):
                        return candidate
                    result = await generate_valid(resolved, max_retries=0)
                    assert_preservation(broken, result['process'], patch)
                    record['patch'] = patch.model_dump()
                else:
                    correction = json.dumps({'previous_result': broken.model_dump(exclude_defaults=True), 'errors': [e.model_dump(exclude_none=True) for e in errors]}, ensure_ascii=False)
                    result = await generate_valid(lambda _: provider.parse_process(reference.description, correction), max_retries=0)
                candidate = result['process']
                record.update(bpmn=True, exact_canonical_preservation=quality_fingerprint(candidate) == quality_fingerprint(reference),
                              roles=len(candidate.participants), pools=len(candidate.pools), messages=len(candidate.message_flows),
                              loops_preserved=business_loops(reference) == business_loops(candidate),
                              role_definitions_preserved=reference.participants == candidate.participants,
                              tasks_preserved={n.id: n for n in reference.nodes if n.type.endswith('task')} == {n.id: n for n in candidate.nodes if n.type.endswith('task')},
                              messages_preserved=reference.message_flows == candidate.message_flows,
                              xsd_pass=True, di_pass=True)
                (folder / f'{output_name}.bpmn').write_text(result['xml'], encoding='utf-8')
                (folder / f'{output_name}-process.json').write_text(candidate.model_dump_json(indent=2), encoding='utf-8')
            except Exception as exc:
                record.update(bpmn=False, error_type=type(exc).__name__, reason=getattr(exc, 'reason', 'quality_or_schema'))
                if isinstance(exc, ValueError):
                    record['quality_rejection'] = str(exc)[:300]
            finally:
                record['elapsed_ms'] = round((perf_counter() - start) * 1000, 2)
                record['performance'] = measured.report()
                await provider.close()
                profile.reset(token)
                (folder / f'{output_name}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
                print(provider_name, mode, record['elapsed_ms'], record['bpmn'], flush=True)


if __name__ == '__main__':
    asyncio.run(run())
