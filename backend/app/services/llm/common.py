import json
from pathlib import Path
from pydantic import ValidationError

PROMPTS = Path(__file__).resolve().parents[2] / 'prompts'


def prompt_text(operation):
    return (PROMPTS / f'{operation}.txt').read_text(encoding='utf-8')


def validated_json(text, model_class):
    """Extract a single unambiguous JSON object, then apply the strict model."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Модель не вернула JSON.')
    text = text.strip()
    try:
        return model_class.model_validate_json(text)
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
    return model_class.model_validate_json(objects[0][0])


def corrective_message(exc):
    if isinstance(exc, ValidationError):
        errors = [{'location': list(e['loc']), 'type': e['type']} for e in exc.errors(include_input=False)]
        return 'Исправь JSON по схеме: ' + json.dumps(errors, ensure_ascii=False)
    return 'Верни один корректный JSON-объект по схеме без Markdown и пояснений.'
