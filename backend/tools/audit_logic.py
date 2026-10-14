"""Offline audit: python -m tools.audit_logic process.json --output audit-dir."""
import argparse
import asyncio
import json
from pathlib import Path
from app.models import ProcessDefinition
from app.pipeline import generate_valid
from app.diagnostics import PipelineFailure
from app.services.llm.base import ProviderError


def audit(process, output):
    """No provider construction, environment changes, API requests, or repairs."""
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    trace=[]
    async def supplied(_): return process.model_copy(deep=True)
    def write(name,value):
        (output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    write('input.json',process.model_dump())
    try:
        result=asyncio.run(generate_valid(supplied,max_retries=0,audit_trace=trace))
        if result['xml'] is not None:
            (output/'result.bpmn').write_text(result['xml'],encoding='utf8')
        report={'passed':result['xml'] is not None,'llm_calls':0,
                'normalization_changes':next((t['changes'] for t in trace if t['stage']=='normalization'),[]),
                'xml_differences':next((t['differences'] for t in trace if t['stage']=='bpmn_xml'),None),
                'diagnostics':[i.model_dump() for i in result.get('diagnostics',[])],
                'timings':result['timings']}
    except (PipelineFailure,ProviderError,ValueError) as exc:
        report={'passed':False,'llm_calls':0,'error_type':type(exc).__name__,
                'diagnostics':[i.model_dump() for i in getattr(exc,'diagnostics',[])]}
    write('trace.json',trace)
    write('report.json',report)
    normalized=next((t['process'] for t in trace if t['stage']=='normalization'),None)
    if normalized is not None: write('normalized.json',normalized)
    return report


def main():
    parser=argparse.ArgumentParser(description='Проверка сохранения бизнес-графа без LLM')
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raw=json.loads(args.input.read_text(encoding='utf8'))
    process=ProcessDefinition.model_validate(raw.get('process',raw))
    report=audit(process,args.output)
    print(json.dumps({'passed':report['passed'],'llm_calls':0,'output':str(args.output)},ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
