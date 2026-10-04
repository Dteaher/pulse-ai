import asyncio
import json
import httpx
import pytest
from pydantic import ValidationError
from app.config import Settings
from app.examples import demo_process
from app.models import ProcessDefinition
from app.services.llm.common import validated_json
from app.services.llm.factory import get_llm_provider
from app.services.llm.structured_output import response_content, StructuredResponseError
from app.services.llm.raw_debug import inspect_content, save_raw
from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider


@pytest.mark.parametrize('wrapper', ['{}', '```json\n{}\n```', 'Result: {}', '{}\nDone.'])
def test_safe_unwrap_preserves_every_business_field(wrapper):
    p = demo_process(0)
    assert validated_json(wrapper.replace('{}', p.model_dump_json()), ProcessDefinition) == p


@pytest.mark.parametrize('text', ['{} {}', '{"outer": {"id": "nested"}', '{"x":1,}', '',
                                 '[{}]', '```json\n{}', 'prefix [ {} ]', '{"broken": {} garbage'])
def test_unsafe_unwrap_never_repairs_or_extracts_nested_objects(text):
    with pytest.raises(ValueError):
        validated_json(text, ProcessDefinition)


def test_valid_json_schema_fail_is_not_json_parse_fail():
    result = inspect_content('{"nodes":[]}', ProcessDefinition)
    assert result['valid_json']
    assert result['classification'] == 'SCHEMA_FAIL'
    assert any(e['location'] == 'id' and e['type'] == 'missing' for e in result['validation_errors'])
    with pytest.raises(ValidationError):
        validated_json('{"nodes":[]}', ProcessDefinition)


def test_complete_process_inside_truncated_envelope_is_never_accepted():
    text = '{"broken_envelope":' + demo_process(0).model_dump_json()
    with pytest.raises(ValueError):
        validated_json(text, ProcessDefinition)


def test_wrapper_does_not_hide_schema_failure():
    result = inspect_content('```json\n{}\n```', ProcessDefinition)
    assert result['markdown_wrapper'] and result['unwrap_parseable']
    assert not result['pydantic_pass'] and result['classification'] == 'SCHEMA_FAIL'


def test_truncation_has_priority_over_schema_failure():
    assert inspect_content('{}', ProcessDefinition, 'length')['classification'] == 'TRUNCATED'
    assert inspect_content('{"id":', ProcessDefinition, 'length')['classification'] == 'TRUNCATED'


@pytest.mark.parametrize('content,expected', [(None,'EMPTY_CONTENT'), ('','EMPTY_CONTENT'),
                                           (42,'PROVIDER_FORMAT_MISMATCH')])
def test_precise_content_diagnostics(content, expected):
    assert inspect_content(content,ProcessDefinition)['classification'] == expected


@pytest.mark.parametrize('body', [[], {}, {'choices':[]}, {'choices':[{'message':[]} ]},
                                {'choices':[{'message':{'content':[{'type':'image','text':'{}'}]}}]},
                                {'choices':[{'message':{'tool_calls':[{}], 'content':'{}'}}]}])
def test_wrong_provider_shape_does_not_guess(body):
    with pytest.raises(StructuredResponseError,match='PROVIDER_FORMAT_MISMATCH'):
        response_content(body)


def test_content_blocks_and_parsed_use_same_canonical_schema():
    p = demo_process(0)
    text = p.model_dump_json()
    for msg in [{'content':[{'type':'text','text':text[:30]},{'type':'text','text':text[30:]}]},
                {'content':None,'parsed':p.model_dump()},
                {'content':text,'reasoning_content':'ignore this'}]:
        content = response_content({'choices':[{'message':msg}]})
        assert validated_json(content,ProcessDefinition) == p


def test_twenty_unique_validation_errors_without_input_values():
    p = demo_process(0).model_dump()
    for i in range(25):
        p[f'unsupported_{i}'] = 'sensitive business text'
    result = inspect_content(json.dumps(p),ProcessDefinition)
    assert len(result['validation_errors']) == 20
    assert 'sensitive business text' not in json.dumps(result)


def test_debug_logger_redacts_secrets_and_omits_headers(tmp_path,monkeypatch):
    monkeypatch.setattr('app.services.llm.raw_debug.DEBUG_DIR',tmp_path)
    path = save_raw(provider='multiai',model='test',status=200,duration_ms=1,
        body={'headers':{'Authorization':'Bearer private-key'},'messages':['private-key'],
              'response_format':{'type':'json_object'},'max_tokens':1000},
        response={'Authorization':'Bearer leaked-secret','api_key':'private-key',
                  'choices':[{'message':{'content':'private-key'}}]},
        model_class=ProcessDefinition,secrets=('private-key',))
    raw = path.read_text(encoding='utf-8')
    assert 'private-key' not in raw and 'leaked-secret' not in raw
    assert 'headers' not in raw and 'messages' not in raw
    assert '[REDACTED]' in raw


@pytest.mark.parametrize('env,flag,expected',[('production',True,False),('development',False,False),('development',True,True)])
def test_debug_is_opt_in_and_never_production(env,flag,expected):
    s=Settings(_env_file=None,primary_llm_provider='multiai',primary_llm_api_key='key',primary_llm_model='test',
               llm_fallback_enabled=False,app_env=env,llm_debug_raw_response=flag)
    assert get_llm_provider(s).primary.debug_raw_response is expected


def test_capture_happens_before_validation_and_corrective(tmp_path,monkeypatch):
    monkeypatch.setattr('app.services.llm.raw_debug.DEBUG_DIR',tmp_path)
    original=httpx.AsyncClient
    count=0
    def handler(request):
        nonlocal count
        count+=1
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}' if count==1 else demo_process(0).model_dump_json()}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))
    p=OpenAICompatibleProvider('private-key','https://test/v1','test',max_retries=1)
    p.debug_raw_response=True
    assert asyncio.run(p.parse_process('Input')) == demo_process(0)
    records=[json.loads(f.read_text(encoding='utf-8')) for f in tmp_path.glob('*.json')]
    assert len(records)==2
    assert any(r['analysis']['classification']=='SCHEMA_FAIL' for r in records)
    assert any(r['analysis']['pydantic_pass'] for r in records)


def test_failed_http_response_is_captured_without_secrets(tmp_path,monkeypatch):
    from app.services.llm.base import ProviderError
    monkeypatch.setattr('app.services.llm.raw_debug.DEBUG_DIR',tmp_path)
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(
        lambda r:httpx.Response(500,text='provider private-key failure')),**kw))
    p=OpenAICompatibleProvider('private-key','https://test/v1','test',max_retries=0)
    p.debug_raw_response=True
    with pytest.raises(ProviderError):
        asyncio.run(p.parse_process('Input'))
    files=list(tmp_path.glob('*.json'))
    assert len(files)==1
    assert json.loads(files[0].read_text(encoding='utf-8'))['http_status']==500
    assert 'private-key' not in files[0].read_text(encoding='utf-8')
