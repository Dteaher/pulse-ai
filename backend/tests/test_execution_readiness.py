import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.bpmn import build_bpmn
from app.models import ProcessDefinition
from app.execution.models import ExecutionReadinessRequest
from app.execution.readiness import assess_readiness
from app.config import Settings
from app.main import create_app

ROOT=Path(__file__).resolve().parents[2]
def golden(case):
    return ProcessDefinition.model_validate_json((ROOT/f'examples/llm-benchmark/{case}.json').read_text(encoding='utf8'))

@pytest.mark.parametrize('case',['simple','medium','complex'])
def test_readiness_is_read_only_and_never_claims_execution(case):
    p=golden(case);before=p.model_dump();xml=build_bpmn(p)
    report=assess_readiness(ExecutionReadinessRequest(process=p))
    assert p.model_dump()==before and build_bpmn(p)==xml
    assert not report.deployable and not report.execution_verified and not report.engine_selected
    assert report.engine is None
    assert any(i.code=='EXECUTION_FORMAL_CONDITION_REQUIRED' for i in report.issues)
    assert all(i.element_ids and i.reason and i.suggestion for i in report.issues)

def test_variable_types_are_explicit_and_expressions_not_executed(tmp_path):
    p=golden('simple');f=next(f for f in p.flows if f.condition)
    marker=tmp_path/'not-executed'
    expression=f"__import__('pathlib').Path({str(marker)!r}).touch()"
    request=ExecutionReadinessRequest.model_validate({'process':p.model_dump(),'variables':{'documents_complete':{'type':'boolean'}},'conditions':{f.id:{'expression':expression,'language':'user-supplied','referenced_variables':['documents_complete']}}})
    result=assess_readiness(request)
    assert not marker.exists()
    assert any(i.code=='EXECUTION_CONDITION_PROFILE_UNVERIFIED' and f.id in i.element_ids for i in result.issues)
    assert not any(i.code=='EXECUTION_VARIABLE_UNDECLARED' for i in result.issues)
    assert expression not in result.model_dump_json()
    assert not result.supplied_configuration_verified

def test_undeclared_variable_is_reported_without_inventing_it():
    p=golden('simple');f=next(f for f in p.flows if f.condition)
    request=ExecutionReadinessRequest.model_validate({'process':p.model_dump(),'conditions':{f.id:{'expression':'user_condition','language':'specified','referenced_variables':['unknown']}}})
    result=assess_readiness(request)
    assert request.variables=={}
    assert any(i.code=='EXECUTION_VARIABLE_UNDECLARED' for i in result.issues)

@pytest.mark.parametrize('value',[{}, {'type':'guessed'}, {'type':'boolean','default':True}])
def test_no_guessed_variable_types_or_defaults(value):
    with pytest.raises(ValidationError):
        ExecutionReadinessRequest.model_validate({'process':golden('simple').model_dump(),'variables':{'v':value}})

def test_external_pool_cannot_be_selected_for_execution():
    p=golden('medium');external=next(pool for pool in p.pools if pool.kind=='external')
    result=assess_readiness(ExecutionReadinessRequest(process=p,executable_pool_ids=[external.id]))
    assert any(i.code=='EXECUTION_INVALID_POOL_SCOPE' for i in result.issues)
    assert not any(i.code=='EXECUTION_USER_ASSIGNMENT_REQUIRED' and any(n.participant_id==r.id for n in p.nodes if n.id in i.node_ids for r in p.participants if r.kind=='external') for i in result.issues)

def test_roles_are_not_engine_user_assignments():
    p=golden('complex');report=assess_readiness(ExecutionReadinessRequest(process=p))
    users={n.id for n in p.nodes if n.type=='user_task' and next(r for r in p.participants if r.id==n.participant_id).kind=='internal'}
    reported={i.node_ids[0] for i in report.issues if i.code=='EXECUTION_USER_ASSIGNMENT_REQUIRED'}
    assert reported==users

def test_explicit_assignment_does_not_become_verified():
    p=golden('complex');user=next(n for n in p.nodes if n.type=='user_task')
    request=ExecutionReadinessRequest.model_validate({'process':p.model_dump(),'tasks':{user.id:{'assignee':'explicit-user'}}})
    result=assess_readiness(request)
    assert not any(i.code=='EXECUTION_USER_ASSIGNMENT_REQUIRED' and user.id in i.node_ids for i in result.issues)
    assert not result.deployable

@pytest.mark.parametrize('collection,value',[('conditions',{'expression':'x','language':'explicit'}),('tasks',{'implementation':'provided'}),('messages',{'message_name':'provided'})])
def test_unknown_binding_ids(collection,value):
    request=ExecutionReadinessRequest.model_validate({'process':golden('simple').model_dump(),collection:{'Missing':value}})
    assert any(i.code=='EXECUTION_UNKNOWN_BINDING' for i in assess_readiness(request).issues)

def test_formal_message_declaration_is_not_correlation_configuration():
    p=golden('medium');r=assess_readiness(ExecutionReadinessRequest(process=p))
    assert len([i for i in r.issues if i.code=='EXECUTION_MESSAGE_CONFIGURATION_REQUIRED'])==len(p.message_flows)

def test_api_requires_access_and_does_not_use_llm(monkeypatch):
    from app.services.llm.router import LLMRouter
    async def forbidden(*a,**kw):pytest.fail('Readiness called LLM')
    monkeypatch.setattr(LLMRouter,'parse_process',forbidden)
    s=Settings(_env_file=None,llm_provider='mock',primary_llm_provider='mock',llm_fallback_enabled=False,api_limits_enabled=False,pulse_access_token='test-only-access')
    with TestClient(create_app(s)) as c:
        body={'process':golden('simple').model_dump()}
        assert c.post('/api/process/execution-readiness',json=body).status_code==401
        response=c.post('/api/process/execution-readiness',json=body,headers={'Authorization':'Bearer test-only-access'})
        assert response.status_code==200 and response.json()['execution_verified'] is False
        assert 'test-only-access' not in response.text

def test_bad_graph_is_reported_and_never_repaired():
    p=golden('simple');p.flows[0].target='Missing';before=p.model_dump()
    report=assess_readiness(ExecutionReadinessRequest(process=p))
    assert any(i.code=='BROKEN_REFERENCE' for i in report.issues)
    assert p.model_dump()==before
