"""Bounded live smoke benchmark; no production settings or secret outputs.
Run from repository root: .venv/Scripts/python.exe backend/tools/llm_benchmark.py --model gpt-6-sol --case simple
"""
import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from time import perf_counter

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
# The module-level ASGI app must not read production credentials on import.
os.environ['PULSE_ENV_FILE'] = str(ROOT / ('.env.benchmark' if (ROOT/'.env.benchmark').exists() else '.env.example'))
from app.config import Settings
from app.main import create_app
from app.pipeline import generate_valid
from app.models import ProcessDefinition
from benchmark_semantics import evaluate
from benchmark_budget import reserve_api_attempt
from app.services.llm.factory import get_llm_provider
import app.process_routes as routes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reasoning-effort', choices=['low','medium','high','xhigh','max'], default='low', help='Use only a level supported by the authenticated model catalog')
    parser.add_argument('--fallback-schema-in-prompt', action='store_true', help='Explicit schema only on forced fallback')
    parser.add_argument('--diagnostic-all-cases', action='store_true', help='Explicitly authorised independent scenario diagnostics; retain budget but skip prerequisite success gate')
    parser.add_argument('--omit-token-limit', action='store_true', help='Omit provider output token cap in an explicitly authorised benchmark')
    parser.add_argument('--max-total-wire-calls', type=int, default=None, help='Shared POST budget across this output directory, including retries')
    parser.add_argument('--combined-parse', action='store_true', help='Enable existing opt-in ParseDecision optimization only for this run')
    parser.add_argument('--continue-after-failed-complex', action='store_true', help='Explicit all-scenario diagnostics: medium may follow failed complex; simple must still pass')
    parser.add_argument('--max-generations', type=int, choices=range(1,11), default=5, help='Increase only after explicit approval')
    parser.add_argument('--model', default='gpt-6-sol')
    parser.add_argument('--provider', choices=['multiai', 'polza'], default='multiai')
    parser.add_argument('--case', choices=['simple', 'medium', 'complex'], required=True)
    parser.add_argument('--force-primary', choices=['timeout', 'network', '429', '503'])
    parser.add_argument('--coverage', type=Path)
    parser.add_argument('--credentials', type=Path, default=ROOT / '.env.benchmark')
    parser.add_argument('--output', type=Path, default=ROOT / '.runtime/polza-final')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    creds = dotenv_values(args.credentials)
    secrets = [v for k, v in creds.items() if v and (k.endswith('API_KEY') or k == 'PULSE_ACCESS_TOKEN')]
    logging.disable(logging.CRITICAL)
    if args.provider == 'multiai' or args.force_primary:
        with httpx.Client(trust_env=False, timeout=30) as catalog_client:
            catalog = catalog_client.get('https://multiai.store/v1/models', headers={'Authorization':'Bearer '+creds['FALLBACK_LLM_API_KEY']})
        if not catalog.is_success:
            raise SystemExit('STOP: authenticated catalog unavailable')
        entry = next((m for m in catalog.json().get('data', []) if m.get('id') == args.model), None)
        if entry is None or entry.get('availability') not in (None, 'available'):
            raise SystemExit('STOP: model not available in authenticated catalog')
        (args.output/'multiai-model-catalog.json').write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding='utf8')
    selected_model = 'openai/gpt-6-luna' if args.provider == 'polza' else args.model
    safe_model = re.sub(r'[^a-zA-Z0-9._-]', '_', selected_model)
    wire_ledger_file = args.output / 'paid-wire-ledger.json'
    ledger_file = args.output / 'generation-ledger.json'
    ledger = json.loads(ledger_file.read_text()) if ledger_file.exists() else []
    if not args.coverage and not args.force_primary and args.case != 'simple' and not args.diagnostic_all_cases:
        prerequisite = 'simple' if args.case == 'complex' or args.continue_after_failed_complex else 'complex'
        successes = [item for item in ledger if prerequisite+'-'+args.provider+'-'+safe_model in item['run'] and item.get('xml_received')]
        if not successes or not any((args.output/item['run']/'semantic-checks.json').exists() and json.loads((args.output/item['run']/'semantic-checks.json').read_text()).get('passed') for item in successes):
            raise SystemExit('STOP: prerequisite semantic benchmark has not passed')
    if not args.coverage and len(ledger) >= args.max_generations:
        raise SystemExit('STOP: five full generations already used; use a new approved budget/output directory.')
    run_id = f'{len(ledger)+1}-{args.case}-{args.provider}-{safe_model}' if not args.coverage else f'coverage-{args.provider}-{args.case}-{args.coverage.stem}'
    if args.combined_parse:
        run_id += '-combined'
    if args.force_primary:
        run_id += '-fallback-' + args.force_primary
    out = args.output / run_id
    if out.exists():
        raise SystemExit('STOP: output exists; no accidental duplicate paid request.')
    out.mkdir()
    def save(path, data):
        content = json.dumps(data, ensure_ascii=False, indent=2, default=str)
        for secret in secrets:
            content = content.replace(secret, '[REDACTED]')
        path.write_text(content, encoding='utf8')
    if not args.coverage:
        ledger.append({'run': run_id, 'started': True})
        save(ledger_file, ledger)
    production = ROOT / '.env'
    checksum = hashlib.sha256(production.read_bytes()).hexdigest()
    trace = []
    async def traced(*a, **kw):
        return await generate_valid(*a, **kw, audit_trace=trace)
    routes.generate_valid = traced
    original_client = httpx.AsyncClient
    wire_count = 0
    polza_reserved = 0.0
    polza_rates = None
    if args.provider == 'polza' and not args.force_primary:
        catalog = httpx.get('https://polza.ai/api/v1/models', timeout=30)
        catalog.raise_for_status()
        model = next(m for m in catalog.json()['data'] if m['id'] == 'openai/gpt-6-luna')
        price = model.get('pricing') or model['top_provider']['pricing']
        polza_rates = (float(price['prompt_per_million']), float(price['completion_per_million']))
        save(args.output / 'polza-price-snapshot.json', model)
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.inner = httpx.AsyncHTTPTransport(retries=0)
        async def handle_async_request(self, request):
            nonlocal wire_count, polza_reserved
            if args.force_primary and request.url.host == 'polza.ai':
                if args.force_primary == 'timeout':
                    raise httpx.ReadTimeout('Injected primary timeout', request=request)
                if args.force_primary == 'network':
                    raise httpx.ConnectError('Injected primary network error', request=request)
                return httpx.Response(int(args.force_primary), request=request, json={'error':'Injected primary failure'})
            reserve_api_attempt(wire_ledger_file, args.max_total_wire_calls, run=run_id, provider=request.url.host)
            wire_count += 1
            if wire_count > 6:
                raise RuntimeError('Wire call limit exceeded')
            if request.url.host == 'polza.ai':
                # Current catalog rates. A conservative whole-byte
                # input bound and output budget reserve prevent unbounded spending.
                body = json.loads(request.content)
                limit = body.get('max_tokens')
                if not limit:
                    raise RuntimeError('Unbounded Polza output prohibited')
                reserve = (len(request.content)*polza_rates[0] + limit*polza_rates[1])/1_000_000
                if polza_reserved + reserve > 10:
                    raise RuntimeError('Polza per-run 10 RUB reserve exceeded')
                polza_reserved += reserve
            return await self.inner.handle_async_request(request)
        async def aclose(self):
            await self.inner.aclose()
    def client(**kw):
        if 'transport' not in kw:
            kw['transport'] = Transport()
        return original_client(**kw)
    httpx.AsyncClient = client
    async def run():
        is_polza = args.provider == 'polza' or bool(args.force_primary)
        s = Settings(_env_file=args.credentials, app_env='benchmark', api_limits_enabled=False,
            primary_llm_provider='polza' if is_polza else 'multiai',
            primary_llm_base_url='https://polza.ai/api/v1' if is_polza else 'https://multiai.store/v1',
            primary_llm_model='openai/gpt-6-luna' if is_polza else args.model,
            primary_llm_api_key=creds['PRIMARY_LLM_API_KEY' if is_polza else 'FALLBACK_LLM_API_KEY'],
            fallback_llm_schema_in_prompt=args.fallback_schema_in_prompt,
            fallback_llm_provider='multiai' if args.force_primary else '',
            fallback_llm_model=args.model if args.force_primary else '',
            fallback_llm_base_url='https://multiai.store/v1', fallback_llm_api_key=creds.get('FALLBACK_LLM_API_KEY',''),
            llm_fallback_enabled=bool(args.force_primary), llm_structured_output=None, primary_llm_omit_token_limit=args.omit_token_limit,
            llm_performance_enabled=True, llm_detailed_metrics_enabled=True, llm_combined_parse_enabled=args.combined_parse, llm_reasoning_effort=args.reasoning_effort, llm_timeout=120,
            llm_request_budget=360, llm_fallback_reserve=120, llm_connect_timeout=30, llm_trust_env=False,
            llm_max_tokens=8000, llm_parse_max_tokens=8000, llm_max_retries=1, llm_graph_max_retries=1,
            llm_business_coverage_enabled=False, performance_cache_entries=0, llm_debug_raw_response=False)
        text = (ROOT / f'examples/llm-benchmark/{args.case}.txt').read_text(encoding='utf8')
        started = perf_counter()
        if args.coverage:
            data = json.loads(args.coverage.read_text(encoding='utf8'))
            process = ProcessDefinition.model_validate(data.get('process', data))
            router = get_llm_provider(s)
            try:
                result = await router.check_business_coverage(process, text)
                save(out/'coverage.json', result.model_dump())
                save(out/'metadata.json', router.metadata.model_dump())
                metrics = router.primary.call_metrics
            except Exception as exc:
                save(out/'calls.json', router.primary.call_metrics)
                save(out/'metadata.json', router.metadata.model_dump())
                save(out/'provider-failure.json', {'exception_type':type(exc).__name__, 'reason':getattr(exc,'reason',None)})
                raise
            finally:
                await router.close()
            save(out/'calls.json', metrics)
            summary = {'run':run_id, 'seconds':round(perf_counter()-started,2), 'gaps':len(result.gaps), 'wire_calls':wire_count}
        else:
            benchmark_provider = get_llm_provider(s)
            if args.force_primary and args.omit_token_limit:
                benchmark_provider.fallback.omit_token_limit = True
            app = create_app(s, provider=benchmark_provider)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://pulse.local',timeout=None) as session:
                    response = await session.post('/api/process/generate', json={'text':text})
                data = response.json()
                performance = app.state.performance_profiles.get(response.headers.get('X-Request-ID'), {})
            save(out/'response.json',data);save(out/'trace.json',trace);save(out/'performance.json',performance)
            if data.get('xml'):
                (out/'result.bpmn').write_text(data['xml'], encoding='utf8')
                try:
                    semantics = evaluate(args.case, data['process'], data['xml'])
                except AssertionError as exc:
                    semantics = {'passed':False, 'reason':str(exc)}
                save(out/'semantic-checks.json',semantics)
            summary = {'run':run_id, 'seconds':round(perf_counter()-started,2), 'http_status':response.status_code,
                'xml_received':bool(data.get('xml')), 'metadata':data.get('metadata'), 'wire_calls':wire_count}
            ledger[-1].update(summary);save(ledger_file,ledger)
        assert checksum == hashlib.sha256(production.read_bytes()).hexdigest()
        save(out/'summary.json',summary)
        console_summary = {**summary}
        if console_summary.get('metadata'):
            console_summary['metadata'] = {k:v for k,v in console_summary['metadata'].items() if k != 'performance'}
        print(json.dumps(console_summary,ensure_ascii=True),flush=True)
    try:
        asyncio.run(run())
    except Exception as exc:
        # Never print provider exceptions, request headers or Settings inputs.
        save(out/'failure.json',{'exception_type':type(exc).__name__})
        raise SystemExit('Benchmark stopped: '+type(exc).__name__)

if __name__ == '__main__':
    main()
