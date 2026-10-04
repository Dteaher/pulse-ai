"""Cache/fallback-free real benchmarks; never writes deployment configuration."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from time import perf_counter
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings
from app.models import ProcessDefinition
from app.services.llm.factory import get_llm_provider
from app.services.llm.raw_debug import DEBUG_DIR
from app.telemetry import PerformanceProfile, profile
from app.pipeline import generate_valid
from app.notation import polish_process
from app.validator import validate_process
from app.semantics import validate_semantics
from tools.quality_oracle import quality
from tools.qwen_quality import complex_quality,medium_quality,preservation


async def run(args):
    DEBUG_DIR.mkdir(exist_ok=True)
    folder=DEBUG_DIR/args.label;folder.mkdir(exist_ok=True)
    for index in range(args.runs):
        s=Settings(primary_llm_provider='multiai',primary_llm_model=args.model,
                   llm_fallback_enabled=False,llm_max_retries=0,llm_parse_max_tokens=args.tokens,
                   llm_timeout=args.deadline,performance_cache_entries=0,llm_performance_enabled=True)
        p=get_llm_provider(s).primary;p.debug_raw_response=True
        if args.id_hint:
            p.prompt_hints=('ID участника (дорожки) и ID pool — разные сущности: role_* и pool_* никогда не совпадают.',)
        stat=PerformanceProfile();token=profile.set(stat);initial=[]
        original=p._request_once
        async def trace(*a,**kw):
            result=await original(*a,**kw)
            if isinstance(result,ProcessDefinition): initial.append(result.model_copy(deep=True))
            return result
        p._request_once=trace
        text=(ROOT/args.input).read_text(encoding='utf-8')
        record={'model':args.model,'parse_max_tokens':args.tokens,'cache':False,'fallback':False,
                'corrective_enabled':args.corrective,'deadline_seconds':args.deadline}
        started=perf_counter()
        try:
            async def request():
                if args.analysis:
                    analysis=await p.analyze_ambiguities(text)
                    record['analysis']=analysis.model_dump()
                    record['clarification_required']=any(q.severity=='critical' for q in analysis.ambiguities)
                    return
                async def call(correction):return await p.parse_process(text,correction)
                result=await generate_valid(call,max_retries=int(args.corrective),provider=p)
                record['bpmn']=bool(result.get('xml'));record['pipeline_attempts']=result['attempts']
                if result.get('xml'):
                    (folder/f'{index+1}.bpmn').write_text(result['xml'],encoding='utf-8')
                    record['quality']=complex_quality(result['process']) if args.case=='complex' else quality(result['process'],'simple') if args.case=='simple' else medium_quality(result['process'])
                    record['xsd']=record['di']=record['references']=True
                (folder/f'{index+1}-process.json').write_text(result['process'].model_dump_json(indent=2),encoding='utf-8')
                if initial:record['preservation']=preservation(polish_process(initial[0]),result['process'])
            await asyncio.wait_for(request(),args.deadline)
        except Exception as exc:
            record.update(bpmn=False,error_type=type(exc).__name__,reason=getattr(exc,'reason','timeout' if isinstance(exc,TimeoutError) else 'unknown'))
            if getattr(exc,'diagnostics',None): record['diagnostics']=[i.model_dump() for i in exc.diagnostics]
        finally:
            await p.close();record['performance']=stat.report();profile.reset(token)
        record['elapsed_ms']=round((perf_counter()-started)*1000,2)
        record['initial_pydantic']=bool(initial)
        if initial:
            normalized=polish_process(initial[0]);record['initial_errors']=[i.model_dump() for i in validate_process(normalized)+validate_semantics(normalized) if i.severity=='error']
            (folder/f'{index+1}-initial.json').write_text(initial[0].model_dump_json(indent=2),encoding='utf-8')
        (folder/f'{index+1}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(record,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',default='qwen3.8-max')
    parser.add_argument('--input',default='examples/qwen/complex.txt')
    parser.add_argument('--case',default='complex')
    parser.add_argument('--tokens',type=int,default=8000)
    parser.add_argument('--deadline',type=int,default=120)
    parser.add_argument('--label',default='qwen-budget-8000')
    parser.add_argument('--runs',type=int,default=1)
    parser.add_argument('--corrective',action='store_true')
    parser.add_argument('--analysis',action='store_true')
    parser.add_argument('--id-hint',action='store_true')
    asyncio.run(run(parser.parse_args()))
