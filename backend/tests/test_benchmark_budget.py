import json
import pytest
from app.services.llm.base import ProviderError
from tools.benchmark_budget import reserve_api_attempt


def test_shared_wire_budget_includes_every_attempt_and_stops_before_sixth(tmp_path):
    ledger=tmp_path/'calls.json'
    for index in range(5):
        assert reserve_api_attempt(ledger,5,run='a' if index<2 else 'b',provider='test')==index+1
    with pytest.raises(ProviderError):reserve_api_attempt(ledger,5,run='retry',provider='test')
    assert len(json.loads(ledger.read_text()))==5
    assert not ledger.with_suffix('.lock').exists()


def test_concurrent_budget_owner_cannot_be_bypassed(tmp_path):
    ledger=tmp_path/'calls.json';lock=ledger.with_suffix('.lock');lock.write_text('')
    with pytest.raises(ProviderError):reserve_api_attempt(ledger,5,run='other',provider='test')
    assert not ledger.exists() and lock.exists()
