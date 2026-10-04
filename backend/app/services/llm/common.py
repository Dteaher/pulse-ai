import json
from pathlib import Path
import re
from pydantic import ValidationError
from ...models import ProcessDefinition, Assumption, ProcessPreparation
from .structured_output import json_object_text

PROMPTS = Path(__file__).resolve().parents[2] / 'prompts'


def prompt_text(operation):
    text = (PROMPTS / f'{operation}.txt').read_text(encoding='utf-8')
    if operation == 'preparation_v2':
        extraction = prompt_text('extraction').replace('Возвращай только ProcessDefinition по заданной JSON Schema.', 'Возвращай ParseDecision по заданной JSON Schema.')
        return text + '\n' + prompt_text('ambiguity') + '\n' + extraction + '\nФинальная форма ответа: только ParseDecision {result: {status: ready, process: ...}} ИЛИ {result: {status: clarification_required, analysis: ...}}. Инструкции про отдельный AmbiguityAnalysis/ProcessDefinition выше описывают содержимое ветки, не верхний уровень ответа.'
    if operation in ('extraction', 'clarification', 'modification'):
        text += '\n' + (PROMPTS / 'bpmn_rules.txt').read_text(encoding='utf-8')
    return text


def performance_prompt(operation):
    # The short extraction experiment lost business actions. Keep the proven
    # semantic instructions; optimize round trips/deltas, not completeness.
    if operation in ('extraction', 'modification', 'clarification'):
        return prompt_text(operation)
    if operation == 'corrective':
        # The short full-graph corrective trial merged separate business roles.
        # Keep all proven rules even though this costs more input tokens.
        return prompt_text('corrective') + '\n' + prompt_text('extraction')
    if operation == 'preparation_v2':
        return prompt_text(operation)
    if operation == 'preparation':
        extraction = prompt_text('extraction').replace('Возвращай только ProcessDefinition по заданной JSON Schema.', 'Готовую ProcessDefinition размести в поле process ответа ProcessPreparation.')
        return prompt_text('preparation') + '\n' + extraction + '\nПри critical: status=clarification_required, process=null, только analysis с вопросами; не создавай provisional graph. При ready: полная ProcessDefinition в process.'
    return prompt_text(operation)


def explicit_unknown(text, context):
    # Selects a cheaper schema; never replaces or answers semantic analysis.
    return not (context or {}).get('answers') and bool(re.search(
        r'\b(?:не\s+указан\w*|неизвест\w*|не\s+определ[её]н\w*|не\s*ясн\w*|не\s+реш[её]н\w*)', text, re.I))


def _typed_result(text, model_class):
    result = model_class.model_validate_json(text)
    if isinstance(result, ProcessPreparation) and result.process is not None:
        result.process = _typed_result(result.process.model_dump_json(), ProcessDefinition)
    if isinstance(result, ProcessDefinition):
        # Every production adapter exposes the same optional-detail policy.
        # Fixture providers do not use JSON transport and retain legacy questions.
        assumptions = {a.id: a for a in result.assumptions}
        for question in result.ambiguities:
            if question.severity == 'warning':
                assumptions.setdefault(question.id, Assumption(
                    id=question.id, text=question.assumption or question.question))
        result.assumptions = list(assumptions.values())
        result.ambiguities = [q for q in result.ambiguities if q.severity == 'critical']
        return ProcessDefinition.model_validate(result.model_dump())
    return result


def validated_json(text, model_class):
    """Extract a single unambiguous JSON object, then apply the strict model."""
    return _typed_result(json_object_text(text), model_class)


async def checked_coverage(call, process, source_text, answers, max_retries):
    """Correct only unverifiable reviewer feedback, never weaken evidence checks."""
    from ...business_coverage import coverage_issues
    from .base import ProviderError
    correction = ''
    for attempt in range(min(max_retries, 1) + 1):
        report = await call(correction)
        try:
            coverage_issues(report, process, source_text, answers)
            return report
        except ProviderError:
            if attempt == min(max_retries, 1):
                raise
            correction = ('Исправь только CoverageReport: source_excerpt должен быть дословной подстрокой source_text '
                          'или answers. node_ids должны существовать в process.nodes; для пропущенного узла верни []. '
                          'Не выдумывай пробелы, не исправляй сам process. Предыдущий ответ: ' + report.model_dump_json())


def corrective_message(exc):
    if isinstance(exc, ValidationError):
        errors = [{'location': list(e['loc']), 'type': e['type']} for e in exc.errors(include_input=False)]
        return 'Исправь JSON по схеме: ' + json.dumps(errors, ensure_ascii=False)
    return 'Верни один корректный JSON-объект по схеме без Markdown и пояснений.'
