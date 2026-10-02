"""Local evaluation against a configured real provider; no fabricated scores."""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from app.config import Settings, ROOT
from app.services.llm.factory import get_llm_provider
from app.services.llm.base import ProviderError
from app.pipeline import generate_valid

CASES = ROOT / 'examples' / 'evaluation-cases.json'


def has_cycle(process):
    links = {n.id: [] for n in process.nodes}
    for f in process.flows:
        links[f.source].append(f.target)
    color = {}
    def visit(node):
        color[node] = 1
        for target in links[node]:
            if color.get(target) == 1 or not color.get(target) and visit(target):
                return True
        color[node] = 2
        return False
    return any(not color.get(n) and visit(n) for n in links)


def check_expectations(process, expected):
    # These are observable structural properties, not a full semantic score.
    types = [n.type for n in process.nodes]
    checks = {
        'participants': len(process.participants) >= expected.get('min_participants', 1),
        'exclusive': types.count('exclusive_gateway') >= expected.get('min_exclusive', 0),
        'parallel': types.count('parallel_gateway') >= expected.get('min_parallel', 0),
        'ends': types.count('end_event') >= expected.get('min_ends', 1),
        'clarification': bool(process.ambiguities) if expected.get('clarification') else not any(a.severity == 'critical' for a in process.ambiguities),
        'return_cycle': has_cycle(process) if expected.get('return_cycle') else True,
    }
    return checks


async def evaluate_case(provider, case):
    started = perf_counter()
    try:
        result = await generate_valid(lambda c: provider.parse_process(case['text'], c))
        process = result['process']
        return {'id': case['id'], 'name': case['name'], 'seconds': round(perf_counter()-started, 2),
                'status': 'clarification' if result['xml'] is None else 'generated',
                'attempts': result['attempts'], 'checks': check_expectations(process, case['expected']),
                'semantic_review': 'required', 'review_points': case['review_points'],
                'process': process.model_dump(), 'xml': result['xml']}
    except ProviderError as exc:
        return {'id': case['id'], 'name': case['name'], 'status': 'error',
                'seconds': round(perf_counter()-started, 2), 'error': str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Проверка реальной LLM на энергетических процессах')
    parser.add_argument('--check-config', action='store_true', help='Только проверить настройки, без API-запросов')
    parser.add_argument('--case', action='append', help='ID одного или нескольких примеров; по умолчанию все 15')
    parser.add_argument('--limit', type=int, help='Ограничить число запросов')
    args = parser.parse_args(argv)
    # This script reads the same root .env as the API, without env overrides.
    settings = Settings()
    provider = get_llm_provider(settings)
    provider_name = settings.primary_llm_provider or settings.llm_provider
    if provider_name == 'mock':
        print('Для оценки качества выберите реальную модель в .env, а не mock.')
        return 2
    missing = provider.missing_settings()
    if missing:
        print('Не заполнены настройки: ' + ', '.join(missing) + '. Значения ключей не выводятся.')
        return 2
    print('Настройки заполнены. Провайдер: ' + provider_name + '. Доступ к аккаунту ещё не проверен.')
    if args.check_config:
        return 0
    cases = json.loads(CASES.read_text(encoding='utf-8'))
    if args.case:
        known = {c['id'] for c in cases}
        if not set(args.case).issubset(known):
            parser.error('Неизвестный ID примера.')
        cases = [c for c in cases if c['id'] in args.case]
    if args.limit is not None:
        if args.limit < 1:
            parser.error('--limit должен быть положительным.')
        cases = cases[:args.limit]
    output = ROOT / '.runtime' / 'evaluations' / datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True, exist_ok=False)
    report = {'provider': provider_name, 'model': provider.model,
              'created_at': datetime.now(timezone.utc).isoformat(), 'results': []}
    async def run():
        for case in cases:
            print('Проверяем ' + case['id'] + ': ' + case['name'], flush=True)
            result = await evaluate_case(provider, case)
            xml = result.pop('xml', None)
            if xml:
                (output / (case['id'] + '.bpmn')).write_text(xml, encoding='utf-8')
            if process := result.pop('process', None):
                (output / (case['id'] + '.json')).write_text(json.dumps(process, ensure_ascii=False, indent=2), encoding='utf-8')
            report['results'].append(result)
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(result['status'] + '; ' + str(result['seconds']) + ' сек.', flush=True)
            # Authentication/rate-limit/config errors are not helped by 14 more
            # paid requests; report the failure and let the user correct it.
            if result['status'] == 'error':
                break
    asyncio.run(run())
    print('Отчёт: ' + str(output / 'report.json'))
    print('Структурные проверки не заменяют ручную оценку бизнес-смысла.')
    return 1 if any(r['status'] == 'error' for r in report['results']) else 0


if __name__ == '__main__':
    sys.exit(main())
