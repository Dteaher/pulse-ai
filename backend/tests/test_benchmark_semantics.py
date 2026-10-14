import json
from pathlib import Path
import pytest
from app.models import ProcessDefinition
from app.bpmn import build_bpmn
from tools.benchmark_semantics import evaluate
ROOT=Path(__file__).resolve().parents[2]


def fixture(case):
    return ProcessDefinition.model_validate_json((ROOT/f'examples/llm-benchmark/{case}.json').read_text(encoding='utf8'))


@pytest.mark.parametrize('case',['simple','medium','complex'])
def test_offline_benchmark_reference_semantics(case):
    p=fixture(case)
    assert evaluate(case,p.model_dump(),build_bpmn(p))['passed']


@pytest.mark.parametrize('mutation',['condition','executor','return','parallel','message','action'])
def test_benchmark_detects_confirmed_business_mutations(mutation):
    p=fixture('complex')
    if mutation=='condition':
        next(f for f in p.flows if f.target=='send_refusal').condition='Заявка одобрена'
    elif mutation=='executor':
        next(n for n in p.nodes if n.id=='legal_check').participant_id='manager'
    elif mutation=='return':
        next(f for f in p.flows if f.source=='resubmit_contract').target='draft_contract'
    elif mutation=='parallel':
        next(n for n in p.nodes if n.id=='checks_split').type='exclusive_gateway'
    elif mutation=='message':
        p.message_flows.pop()
    else:
        node=next(n for n in p.nodes if n.id=='revise_contract')
        node.name='Пропущенное действие'
    # These mutations retain the same XML/model relationship: failure must be
    # a semantic assertion or the builder's existing safety gate, not old XML.
    with pytest.raises((AssertionError,ValueError)):
        evaluate('complex',p.model_dump(),build_bpmn(p))
