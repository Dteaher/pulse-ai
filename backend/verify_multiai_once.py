"""Manual real-provider check: one generation, no fallback or corrective calls."""
import asyncio
import json
import time
import httpx
from app.config import ROOT, Settings
from app.services.llm.factory import get_llm_provider
from app.services.llm.base import ProviderError
from app.pipeline import generate_valid
from app.bpmn import validate_xml


async def main():
    settings = Settings()
    settings.llm_fallback_enabled = False
    settings.llm_max_retries = 0
    settings.llm_structured_output = True
    provider = get_llm_provider(settings)
    folder = ROOT / 'examples' / 'multiai'
    folder.mkdir(exist_ok=True)
    text = (ROOT / 'examples/controlled-loops/input.txt').read_text(encoding='utf-8')
    (folder / 'input.txt').write_text(text, encoding='utf-8')
    statuses = []
    started = time.monotonic()
    original = httpx.AsyncClient

    async def record(response):
        statuses.append(response.status_code)

    httpx.AsyncClient = lambda **kwargs: original(
        event_hooks={'response': [record]}, **kwargs)
    report = {'provider_requested': provider.name, 'model_requested': provider.model,
              'http_statuses': statuses, 'fallback_enabled_for_check': False,
              'configured_timeout_seconds': settings.llm_timeout}
    report['reasoning_effort'] = settings.llm_reasoning_effort
    try:
        process = await provider.parse_process(text)
        report['pydantic_valid'] = True
        (folder / 'process.json').write_text(process.model_dump_json(indent=2), encoding='utf-8')

        async def cached(_):
            return process.model_copy(deep=True)

        result = await generate_valid(cached, provider=None)
        report['metadata'] = provider.metadata.model_dump()
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
        report['ambiguities'] = len(result['ambiguities'])
        report['bpmn_generated'] = bool(result['xml'])
        if result['xml']:
            validate_xml(result['xml'])
            (folder / 'result.bpmn').write_text(result['xml'], encoding='utf-8')
            report['xml_schema_valid'] = True
        report['counts'] = {k: len(getattr(process, k)) for k in
                            ('pools', 'participants', 'nodes', 'flows', 'message_flows')}
    except ProviderError as exc:
        report['error_reason'] = exc.reason
        report['cause_type'] = type(exc.__cause__).__name__ if exc.__cause__ else None
        report['bpmn_generated'] = False
    except Exception as exc:
        report['error_type'] = type(exc).__name__
        report['bpmn_generated'] = False
    finally:
        httpx.AsyncClient = original
        report['metadata'] = provider.metadata.model_dump()
        report['elapsed_seconds'] = round(time.monotonic() - started, 2)
    (folder / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
