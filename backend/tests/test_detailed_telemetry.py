import json
import httpx
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.examples import demo_process
from app.telemetry import PerformanceProfile, profile, record_call


def test_metric_allowlist_and_key_redaction(caplog):
    measured=PerformanceProfile();token=profile.set(measured)
    try:
        with caplog.at_level('INFO',logger='pulse.llm'):
            safe=record_call({'provider':'polza','model':'private-key','payload':'CLIENT PRIVATE TEXT',
                'headers':{'Authorization':'private-key'},'api_key':'private-key','duration_ms':2},secrets=('private-key',))
        assert safe['model']=='[REDACTED]'
        assert 'CLIENT PRIVATE TEXT' not in caplog.text
        assert 'private-key' not in caplog.text
        assert 'headers' not in measured.calls[0]
    finally:profile.reset(token)


def test_detailed_metadata_mock_has_timing_format_tokens_and_local_stages(monkeypatch):
    original=httpx.AsyncClient
    def handler(request):
        name=json.loads(request.content)['response_format']['json_schema']['name']
        result=AmbiguityAnalysis() if name=='AmbiguityAnalysis' else demo_process(0)
        return httpx.Response(200,json={'model':'actual-luna','usage':{'prompt_tokens':10,'completion_tokens':20,'completion_tokens_details':{'reasoning_tokens':3}},
            'choices':[{'finish_reason':'stop','message':{'content':result.model_dump_json()}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    s=Settings(_env_file=None,primary_llm_provider='polza',primary_llm_model='luna',primary_llm_api_key='private-key',
        llm_fallback_enabled=False,llm_detailed_metrics_enabled=True)
    with TestClient(create_app(s)) as client:
        response=client.post('/api/process/generate',json={'text':'CLIENT PRIVATE TEXT'})
    assert response.status_code==200
    perf=response.json()['metadata']['performance']
    call=perf['calls'][0]
    assert call['started_at']<=call['ended_at']
    assert call['duration_ms']>=0 and call['attempt']==1
    assert call['ttft_ms'] is None and call['streaming'] is False
    assert call['response_format']=='json_schema'
    assert call['reasoning_tokens']==3 and call['finish_reason']=='stop'
    assert call['input_tokens']==10 and call['output_tokens']==20
    assert 'private-key' not in json.dumps(perf) and 'CLIENT PRIVATE TEXT' not in json.dumps(perf)
    stages={event['stage'] for event in perf['stage_events']}
    assert {'normalization_ms','process_validation_ms','semantic_validation_ms','layout_ms','xsd_validation_ms','di_validation_ms','reference_validation_ms'}<=stages
    assert all(e['end_offset_ms']>=e['start_offset_ms'] for e in perf['stage_events'])
    assert all(e['provider']=='backend' and e['model'] is None and e['attempt']==1 for e in perf['stage_events'])


def test_default_detailed_response_metrics_are_off():
    assert Settings(_env_file=None).llm_detailed_metrics_enabled is False



import pytest
from pathlib import Path
from app.services.llm.decision import ParseDecision
from app.models import AmbiguityAnalysis
from app.logic_audit import compare_process_xml


@pytest.mark.parametrize('case',['simple','medium','complex'])
@pytest.mark.parametrize('combined',[False,True])
def test_combined_parse_preserves_golden_semantics_and_reduces_calls(monkeypatch,case,combined):
    root=Path(__file__).resolve().parents[2]
    process=demo_process(0).model_validate_json((root/f'examples/llm-benchmark/{case}.json').read_text(encoding='utf8'))
    bodies=[]
    original=httpx.AsyncClient
    def handler(request):
        body=json.loads(request.content);bodies.append(body)
        name=body['response_format']['json_schema']['name']
        if name=='ParseDecision': result=ParseDecision.model_validate({'result':{'status':'ready','process':process.model_dump()}})
        else:result=AmbiguityAnalysis() if name=='AmbiguityAnalysis' else process
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':result.model_dump_json()}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    s=Settings(_env_file=None,primary_llm_provider='multiai',primary_llm_model='luna',primary_llm_api_key='test-secret',
        llm_fallback_enabled=False,llm_performance_enabled=True,llm_combined_parse_enabled=combined)
    with TestClient(create_app(s)) as client:
        r=client.post('/api/process/generate',json={'text':(root/f'examples/llm-benchmark/{case}.txt').read_text(encoding='utf8')})
    assert r.status_code==200 and r.json()['xml']
    assert len(bodies)==(1 if combined else 2)
    assert r.json()['process']==process.model_dump()
    assert not compare_process_xml(process,r.json()['xml'])



def test_validation_error_logs_do_not_include_customer_or_exception_payload(caplog):
    import asyncio
    from app.pipeline import generate_valid
    from app.services.llm.base import ProviderError
    async def failed(_):
        raise ValueError('CLIENT PRIVATE TEXT private-key')
    with caplog.at_level('INFO',logger='pulse.validation'):
        with pytest.raises(ProviderError):
            asyncio.run(generate_valid(failed,development=True,max_retries=0))
    assert 'CLIENT PRIVATE TEXT' not in caplog.text
    assert 'private-key' not in caplog.text
    assert 'model_schema_validation' in caplog.text
