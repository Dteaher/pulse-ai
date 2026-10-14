"""Conservative shared API-attempt budget; no credentials or request contents."""
import json
from app.services.llm.base import ProviderError


def reserve_api_attempt(path, limit, *, run, provider):
    lock = path.with_suffix('.lock')
    try:
        handle = lock.open('x', encoding='utf8')
    except FileExistsError:
        raise ProviderError('Benchmark budget занят другим процессом.', reason='benchmark_budget') from None
    try:
        with handle:
            entries = json.loads(path.read_text(encoding='utf8')) if path.exists() else []
            if limit is not None and len(entries) >= limit:
                raise ProviderError('Лимит платных API-вызовов исчерпан.', reason='benchmark_budget')
            entries.append({'run':run,'provider':provider,'attempt':len(entries)+1})
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf8')
            temporary.replace(path)
            return len(entries)
    finally:
        lock.unlink(missing_ok=True)
