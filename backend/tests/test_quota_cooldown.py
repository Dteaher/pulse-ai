import asyncio
import httpx
import pytest
from app.services.llm.cooldown import ProviderCooldowns, retry_delay
from app.services.llm.factory import get_llm_provider
from app.services.llm.base import ProviderError
from test_llm_router import settings, install, success


def test_new_requests_skip_primary_until_retry_after_then_recover(monkeypatch):
    now = [0]
    store = ProviderCooldowns(lambda: now[0])
    monkeypatch.setattr('app.services.llm.factory.cooldowns', store)
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if len(calls) == 1:
            return httpx.Response(429, headers={'Retry-After': '120'}, text='private secret')
        return success()
    install(monkeypatch, handler)
    for _ in range(2):
        router = get_llm_provider(settings()).new_request()
        asyncio.run(router.parse_process('Описание'))
        assert router.metadata.fallback_used
    assert calls == ['primary.test', 'backup.test', 'backup.test']
    now[0] = 121
    router = get_llm_provider(settings())
    asyncio.run(router.parse_process('Описание'))
    assert not router.metadata.fallback_used
    assert calls[-1] == 'primary.test'
    assert 'primary-secret' not in repr(store.deadlines)


def test_no_fallback_returns_wait_without_repeating_network_call(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={'Retry-After': '300'})
    install(monkeypatch, handler)
    cfg = settings().model_copy(update={'llm_fallback_enabled': False})
    for _ in range(2):
        with pytest.raises(ProviderError) as error:
            asyncio.run(get_llm_provider(cfg).parse_process('Описание'))
        assert error.value.reason == 'http_429'
        assert error.value.retry_after > 290
    assert len(calls) == 1


def test_changed_key_has_independent_cooldown():
    store = ProviderCooldowns()
    a = get_llm_provider(settings()).primary
    store.block(a, 600)
    b = get_llm_provider(settings().model_copy(update={'llm_api_key': 'other-secret'})).primary
    assert store.remaining(a) > 590
    assert store.remaining(b) == 0


def test_both_limited_skip_both_on_next_request(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(429, headers={'Retry-After': '300'}, text='secret')
    install(monkeypatch, handler)
    for _ in range(2):
        with pytest.raises(ProviderError) as error:
            asyncio.run(get_llm_provider(settings()).parse_process('Описание'))
        assert 'secret' not in str(error.value)
    assert calls == ['primary.test', 'backup.test']


def test_http_date_retry_after():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    assert 118 < retry_delay({'retry-after': value}) <= 120


@pytest.mark.parametrize('value,expected', [('120',120), ('bad',60), ('NaN',60), ('-1',1)])
def test_retry_after_is_bounded(value, expected):
    assert retry_delay({'retry-after': value}) == expected
