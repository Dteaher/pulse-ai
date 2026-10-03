import json
from pathlib import Path
from pydantic import ValidationError
from ...models import ProcessDefinition, Assumption

PROMPTS = Path(__file__).resolve().parents[2] / 'prompts'


def prompt_text(operation):
    text = (PROMPTS / f'{operation}.txt').read_text(encoding='utf-8')
    if operation in ('extraction', 'clarification', 'modification'):
        text += '\n' + (PROMPTS / 'bpmn_rules.txt').read_text(encoding='utf-8')
    return text


def _typed_result(text, model_class):
    result = model_class.model_validate_json(text)
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
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Модель не вернула JSON.')
    text = text.strip()
    try:
        return _typed_result(text, model_class)
    except ValidationError as exc:
        if not any(error['type'] == 'json_invalid' for error in exc.errors()):
            raise
    decoder = json.JSONDecoder()
    objects = []
    position = 0
    while position < len(text):
        start = text.find('{', position)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            position = start + 1
            continue
        objects.append((text[start:start + consumed], value))
        position = start + consumed
    if len(objects) != 1:
        raise ValueError('Ответ должен содержать один JSON-объект.')
    return _typed_result(objects[0][0], model_class)


def corrective_message(exc):
    if isinstance(exc, ValidationError):
        errors = [{'location': list(e['loc']), 'type': e['type']} for e in exc.errors(include_input=False)]
        return 'Исправь JSON по схеме: ' + json.dumps(errors, ensure_ascii=False)
    return 'Верни один корректный JSON-объект по схеме без Markdown и пояснений.'
