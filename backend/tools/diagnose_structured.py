"""Isolated initial-response benchmark. Production Settings and routing untouched."""
import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.models import ProcessDefinition
from app.services.llm.factory import get_llm_provider
from app.services.llm.raw_debug import save_raw, inspect_content, DEBUG_DIR


async def run(args):
    records = []
    for model in args.models.split(','):
        for mode in args.modes.split(','):
            s = Settings(primary_llm_provider='multiai', primary_llm_model=model, llm_fallback_enabled=False,
                         llm_max_retries=1 if args.corrective else 0, llm_timeout=args.timeout, performance_cache_entries=0)
            p = get_llm_provider(s).primary
            p.structured = True  # No auto downgrade, identical messages in all variants.
            saved = []
            prompt_hashes = []
            class Capture:
                async def post(self, url, *, headers, json):
                    if mode == 'json_object':
                        json['response_format'] = {'type': 'json_object'}
                    elif mode == 'schema_relaxed':
                        json['response_format']['json_schema']['strict'] = False
                    start = perf_counter()
                    prompt_hashes.append(hashlib.sha256(__import__('json').dumps(json['messages'],ensure_ascii=False,sort_keys=True).encode()).hexdigest())
                    async with httpx.AsyncClient(timeout=args.timeout) as client:
                        r = await client.post(url, headers=headers, json=json)
                    try:
                        data = r.json()
                    except ValueError:
                        data = {'non_json_response': r.text}
                    path = save_raw(provider=p.name, model=model, status=r.status_code,
                                    duration_ms=round((perf_counter()-start)*1000,2), body=json, response=data,
                                    model_class=ProcessDefinition, secrets=(p.key,))
                    saved.append(path)
                    return r
            p.client = lambda: Capture()
            start = perf_counter()
            error = None
            success = False
            try:
                text = (ROOT / args.input).read_text(encoding='utf-8')
                request = p.parse_process(text) if args.corrective else p._request_once('extraction', {'description': text}, ProcessDefinition)
                process = await asyncio.wait_for(request, args.timeout)
                success = True
                if args.pipeline:
                    from app.pipeline import generate_valid
                    async def existing(_): return process
                    result = await generate_valid(existing, max_retries=0)
                    if result.get('xml'):
                        (DEBUG_DIR / f'{model}-{mode}.bpmn').write_text(result['xml'], encoding='utf-8')
            except Exception as exc:
                # Class names only: raw validation inputs stay in ignored local files.
                error = type(exc).__name__
            record = {'model': model, 'mode': mode, 'elapsed_ms': round((perf_counter()-start)*1000,2),
                      'adapter_pass': success, 'error_type': error, 'raw_file': str(saved[0]) if saved else None,
                      'raw_files': [str(f) for f in saved], 'prompt_sha256': prompt_hashes,
                      'corrective_enabled': args.corrective, 'cache_enabled': False, 'fallback_enabled': False}
            if saved:
                raw = json.loads(saved[0].read_text(encoding='utf-8'))
                record.update(http_status=raw['http_status'], analysis=raw['analysis'], usage=raw['usage'],
                              response_body_structure=raw['response_body_structure'])
            records.append(record)
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / f'{args.label}-summary.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in record.items() if k!='response_body_structure'},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', default='gpt-6.1-sol,claude-sonnet-5-5,gemini-3.1-pro')
    parser.add_argument('--modes', default='json_schema')
    parser.add_argument('--timeout', type=int, default=60)
    parser.add_argument('--input', default='examples/performance/simple.txt')
    parser.add_argument('--label', default='initial')
    parser.add_argument('--pipeline', action='store_true')
    parser.add_argument('--corrective', action='store_true')
    asyncio.run(run(parser.parse_args()))
