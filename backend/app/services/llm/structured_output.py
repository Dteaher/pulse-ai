"""Transport extraction only: no schema repair or business-field mutation."""
import json


class StructuredResponseError(ValueError):
    def __init__(self, classification, reason):
        self.classification = classification
        self.reason = reason
        super().__init__(f'{classification}: {reason}')


def json_object_text(content):
    if not isinstance(content, str):
        raise StructuredResponseError('PROVIDER_FORMAT_MISMATCH', 'content is not text')
    text = content.strip()
    if not text:
        raise StructuredResponseError('EMPTY_CONTENT', 'empty content')
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        # Never scan past a broken outer object to find valid nested objects.
        start = text.find('{')
        if start < 0:
            raise StructuredResponseError('JSON_PARSE_FAIL', f'{exc.msg}; line {exc.lineno}, column {exc.colno}') from exc
        prefix = text[:start]
        if len(prefix) > 256 or any(c in prefix for c in '{}[]'):
            raise StructuredResponseError('JSON_PARSE_FAIL', 'unsafe JSON prefix') from exc
        try:
            value, end = json.JSONDecoder().raw_decode(text, start)
        except json.JSONDecodeError as inner:
            raise StructuredResponseError('JSON_PARSE_FAIL', f'{inner.msg}; line {inner.lineno}, column {inner.colno}') from inner
        suffix = text[end:]
        if len(suffix) > 256 or any(c in suffix for c in '{}[]'):
            raise StructuredResponseError('JSON_PARSE_FAIL', 'multiple JSON objects or unsafe suffix')
        if '```' in prefix or '```' in suffix:
            if prefix.strip().lower() not in ('```', '```json') or suffix.strip() != '```':
                raise StructuredResponseError('JSON_PARSE_FAIL', 'unmatched or mixed markdown fence')
        text = text[start:end]
    if not isinstance(value, dict):
        raise StructuredResponseError('JSON_PARSE_FAIL', 'expected one top-level JSON object')
    return text


def response_content(data):
    if not isinstance(data, dict):
        raise StructuredResponseError('PROVIDER_FORMAT_MISMATCH', 'response body is not an object')
    choices = data.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise StructuredResponseError('PROVIDER_FORMAT_MISMATCH', 'expected exactly one choices entry')
    message = choices[0].get('message')
    if not isinstance(message, dict):
        raise StructuredResponseError('PROVIDER_FORMAT_MISMATCH', 'missing message object')
    if message.get('tool_calls'):
        raise StructuredResponseError('PROVIDER_FORMAT_MISMATCH', 'tool calls are not structured process output')
    content = message.get('content')
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get('type') not in ('text', 'output_text') or not isinstance(part.get('text'), str):
                raise StructuredResponseError('PROVIDER_FORMAT_MISMATCH', 'unsupported content block')
            parts.append(part['text'])
        content = ''.join(parts)
    if content in (None, '') and isinstance(message.get('parsed'), dict):
        content = json.dumps(message['parsed'], ensure_ascii=False)
    if content is None:
        raise StructuredResponseError('EMPTY_CONTENT', 'missing content and parsed result')
    return content


def schema_errors(exc):
    unique = {}
    for e in exc.errors(include_input=False, include_context=False, include_url=False):
        loc = '.'.join(str(p) for p in e['loc'])
        unique.setdefault((loc, e['type']), {'location': loc, 'type': e['type'], 'message': e['msg']})
    return list(unique.values())[:20]
