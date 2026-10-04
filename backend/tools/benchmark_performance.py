"""Opt-in real-provider benchmark. Never records credentials or request headers.

Run from backend: ../.venv/Scripts/python.exe tools/benchmark_performance.py --phase before
"""
import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.config import Settings
from app.main import create_app


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True)
    parser.add_argument('--cases', default='complex,simple,medium,ambiguous')
    parser.add_argument('--effort', choices=['low', 'medium', 'high'])
    parser.add_argument('--combined', choices=['true', 'false'])
    parser.add_argument('--optimized', action='store_true')
    parser.add_argument('--budget', type=int)
    parser.add_argument('--patch', action='store_true')
    parser.add_argument('--cache-hit', action='store_true')
    parser.add_argument('--compact', action='store_true')
    args = parser.parse_args()
    folder = ROOT / 'examples' / 'performance'
    folder.mkdir(exist_ok=True)
    old = ROOT / 'examples' / 'pipeline-resilience'
    sources = {'complex': 'exact-complex-input.txt', 'medium': 'medium-input.txt',
               'ambiguous': 'ambiguous-input.txt'}
    for name, source in sources.items():
        (folder / (name + '.txt')).write_text((old / source).read_text(encoding='utf-8'), encoding='utf-8')
    (folder / 'simple.txt').write_text('Оператор регистрирует заявку и проверяет комплектность документов. Если комплект полный, оператор принимает заявку и завершает процесс. Если комплект неполный, оператор отклоняет заявку и завершает процесс.', encoding='utf-8')
    options = {'app_env': 'production'}
    if args.optimized:
        options['llm_performance_enabled'] = True
    if args.budget:
        options['llm_request_budget'] = args.budget
    if args.patch:
        options['llm_patch_enabled'] = True
    if args.compact:
        parser.error('Compact wire experiment was rejected for quality; see saved compact-* evidence. It is not a runtime option.')
    if args.effort:
        options['llm_reasoning_effort'] = args.effort
    if args.combined:
        options['llm_combined_enabled'] = args.combined == 'true'
    settings = Settings(**options)
    app = create_app(settings)
    client = TestClient(app)
    client.__enter__()
    records = []
    original = httpx.AsyncClient.post

    async def measured(self, url, *a, **kw):
        if '/chat/completions' not in str(url):
            return await original(self, url, *a, **kw)
        body = kw.get('json', {})
        messages = body.get('messages', [])
        content = json.dumps(body, ensure_ascii=False, separators=(',', ':'))
        entry = {'model': body.get('model'), 'reasoning_effort': body.get('reasoning_effort'),
                 'prompt_sha256': hashlib.sha256(content.encode()).hexdigest(),
                 'system_prompt_chars': sum(len(m['content']) for m in messages if m['role'] == 'system'),
                 'user_payload_chars': sum(len(m['content']) for m in messages if m['role'] == 'user'),
                 'request_chars': len(content), 'max_tokens': body.get('max_tokens'),
                 'schema': body.get('response_format', {}).get('json_schema', {}).get('name')}
        started = time.perf_counter()
        try:
            response = await original(self, url, *a, **kw)
            entry['http_status'] = response.status_code
            try:
                entry['usage'] = response.json().get('usage')
                entry['finish_reason'] = response.json().get('choices', [{}])[0].get('finish_reason')
                if response.status_code >= 400:
                    message = str(response.json().get('error', {}))[:1500]
                    for key in (settings.primary_llm_api_key, settings.llm_api_key, settings.fallback_llm_api_key):
                        if key:
                            message = message.replace(key, '[redacted]')
                    entry['safe_error'] = message
            except (ValueError, IndexError):
                pass
            return response
        except Exception as exc:
            entry['error_type'] = type(exc).__name__
            raise
        finally:
            entry['duration_ms'] = round((time.perf_counter() - started) * 1000, 2)
            records.append(entry)

    httpx.AsyncClient.post = measured
    try:
        for case in args.cases.split(','):
            records.clear()
            text = (folder / (case + '.txt')).read_text(encoding='utf-8')
            started = time.perf_counter()
            response = client.post('/api/process/generate', json={'text': text}, headers={'X-Pulse-Session': 'performance-benchmark-' + args.phase})
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            data = response.json()
            evidence = {'phase': args.phase, 'case': case, 'input_sha256': hashlib.sha256(text.encode()).hexdigest(),
                        'request_total_ms': elapsed, 'status': response.status_code,
                        'metadata': data.get('metadata'), 'timings': data.get('timings'),
                        'performance': app.state.performance_profiles.get(response.headers.get('X-Request-ID')), 'calls': list(records),
                        'questions': len(data.get('ambiguities', [])), 'bpmn': bool(data.get('xml')),
                        'graph_attempts': data.get('attempts'), 'process_definition_size': len(json.dumps(data.get('process'), ensure_ascii=False))}
            (folder / f'{args.phase}-{case}.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
            (folder / f'{args.phase}-{case}-response.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            if data.get('xml'):
                (folder / f'{args.phase}-{case}.bpmn').write_text(data['xml'], encoding='utf-8')
            print(json.dumps(evidence, ensure_ascii=True), flush=True)
            if args.cache_hit and response.status_code == 200:
                records.clear()
                cached_start = time.perf_counter()
                cached = client.post('/api/process/generate', json={'text': text}, headers={'X-Pulse-Session': 'performance-benchmark-' + args.phase})
                hit = {'status': cached.status_code, 'request_total_ms': round((time.perf_counter()-cached_start)*1000, 2),
                       'performance': app.state.performance_profiles.get(cached.headers.get('X-Request-ID')),
                       'calls': list(records), 'same_process': cached.json().get('process') == data.get('process'),
                       'same_xml': cached.json().get('xml') == data.get('xml')}
                (folder / f'{args.phase}-{case}-cache.json').write_text(json.dumps(hit, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        httpx.AsyncClient.post = original
        client.__exit__(None, None, None)


if __name__ == '__main__':
    run()
