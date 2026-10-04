"""Graph-free clarification gate shared by generation and modification."""
import json
import logging
from time import perf_counter
from .models import PreflightState, Assumption, Issue
from .services.llm.performance import compact_process


async def analyze(provider, text, *, context=None, answers=None, assumptions=None):
    started = perf_counter()
    context = {**(context or {}), 'answers': answers or {}}
    analysis = await provider.analyze_ambiguities(text, context)
    if len({a.id for a in analysis.ambiguities}) != len(analysis.ambiguities):
        raise ValueError('Модель вернула повторяющиеся вопросы уточнения.')
    analysis.ambiguities.sort(key=lambda a: a.severity != 'critical')
    previous = {a.id: a for a in (assumptions or [])}
    if context.get('operation') == 'modify':
        for key in analysis.superseded_assumption_ids:
            previous.pop(key, None)
    previous.update({a.id: a for a in analysis.assumptions})
    for question in analysis.ambiguities:
        if question.severity == 'warning':
            previous[question.id] = Assumption(id=question.id, text=question.assumption or question.question, source='model_inference', confidence=0.5)
    analysis.ambiguities = [q for q in analysis.ambiguities if q.severity == 'critical']
    analysis.assumptions = list(previous.values())
    state = PreflightState(original_text=text, analysis=analysis, answers=answers or {})
    elapsed = round((perf_counter() - started) * 1000, 2)
    logging.getLogger('pulse.validation').debug('ambiguity_analysis provider=%s model=%s fallback=%s critical=%s duration_ms=%s',
        provider.metadata.provider_used, provider.metadata.model_used, provider.metadata.fallback_used,
        len(analysis.ambiguities), elapsed)
    return state, {'ambiguity_analysis_ms': elapsed}


async def prepare(provider, text, *, context=None, answers=None, assumptions=None):
    started = perf_counter()
    context = {**(context or {}), 'answers': answers or {}}
    if context.get('base_process'):
        from .models import ProcessDefinition
        context['base_process'] = compact_process(ProcessDefinition.model_validate(context['base_process']))
    decision = await provider.prepare_process(text, context)
    if decision is None:
        state, timings = await analyze(provider, text, context=context, answers=answers, assumptions=assumptions)
        return state, timings, None
    analysis = decision.analysis
    previous = {a.id: a for a in (assumptions or [])}
    if context.get('operation') == 'modify':
        for key in analysis.superseded_assumption_ids:
            previous.pop(key, None)
    previous.update({a.id: a for a in analysis.assumptions})
    for question in analysis.ambiguities:
        if question.severity == 'warning':
            previous[question.id] = Assumption(id=question.id, text=question.assumption or question.question, confidence=0.5)
    analysis.ambiguities = [q for q in analysis.ambiguities if q.severity == 'critical']
    analysis.assumptions = list(previous.values())
    state = PreflightState(original_text=text, analysis=analysis, answers=answers or {})
    elapsed = round((perf_counter()-started)*1000, 2)
    # Combined call cannot honestly be split into reasoning versus extraction.
    timings = {'combined_preparation_ms': elapsed, 'ambiguity_analysis_ms': 0}
    return state, timings, decision.process


def questions_result(state, timings, provider, *, round_number=0, limit=3):
    return {'process': None, 'preflight': state, 'ambiguities': state.analysis.ambiguities,
            'xml': None, 'attempts': 0, 'status': 'clarification_required',
            'metadata': provider.metadata, 'timings': {**{key: 0 for key in ('llm_parse_ms','normalization_ms','semantic_validation_ms','bpmn_build_ms','xsd_validation_ms','post_validation_ms')}, **timings, 'total_ms': timings.get('combined_preparation_ms', timings['ambiguity_analysis_ms'])},
            'clarification_round': round_number,
            'clarification_limit_reached': round_number >= limit,
            'accepted_assumptions': [], 'changes': [],
            'human_message': 'Перед построением схемы нужно уточнить бизнес-информацию.',
            'diagnostics': [Issue(code='CLARIFICATION_REQUIRED', severity='clarification',
                type='CLARIFICATION_REQUIRED', category='business_information',
                source='BUSINESS_LOGIC', message=a.question,
                suggested_fix='Ответьте на вопрос перед построением схемы.')
                for a in state.analysis.ambiguities]}


def extraction_context(state):
    return 'Уточнённый бизнес-контекст (не угадывай недостающие данные):\n' + json.dumps(
        {'analysis': state.analysis.model_dump(exclude_defaults=True, exclude_none=True), 'answers': state.answers}, ensure_ascii=False, separators=(',', ':'))


def focused_correction(correction, state):
    try:
        data = json.loads(correction)
    except (ValueError, TypeError):
        return correction
    if 'errors' in data:
        data['business_context'] = {**data.get('business_context', {}), 'answers': state.answers,
            'assumptions': [a.model_dump(exclude_defaults=True) for a in state.analysis.assumptions]}
        return json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return correction


def attach_assumptions(process, state):
    merged = {a.id: a for a in state.analysis.assumptions}
    merged.update({a.id: a for a in process.assumptions if a.id not in state.analysis.superseded_assumption_ids})
    process.assumptions = list(merged.values())
    return process


def accepted_assumption(question):
    return Assumption(id=question.id, text=question.assumption or question.question,
                      source='user_accepted', confidence=1)
