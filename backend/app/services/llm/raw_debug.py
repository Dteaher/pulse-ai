"""Local opt-in diagnostics. Never retains request headers or credentials."""
import json
import re
from pathlib import Path
from uuid import uuid4
from pydantic import ValidationError
from .structured_output import json_object_text, response_content, StructuredResponseError, schema_errors

DEBUG_DIR = Path(__file__).resolve().parents[4] / '.llm-debug'
SECRET_FIELD = re.compile(r'authorization|api.?key|access.?token|secret|password', re.I)


def schema_stats(schema):
    stats = {'size_chars': len(json.dumps(schema,separators=(',',':'))), 'required_fields_count': 0,
             'nesting_depth': 0, 'unions': 0, 'enums': [], 'additional_properties': []}
    defs = schema.get('$defs', {})
    def visit(node, depth=0, refs=frozenset(), count=True):
        if isinstance(node, dict):
            stats['nesting_depth'] = max(stats['nesting_depth'],depth)
            if count:
                stats['required_fields_count'] += len(node.get('required', []))
                stats['unions'] += sum(k in node for k in ('anyOf','oneOf'))
                if 'enum' in node: stats['enums'].append(node['enum'])
                if 'additionalProperties' in node: stats['additional_properties'].append(node['additionalProperties'])
            ref = node.get('$ref')
            if ref and ref not in refs:
                visit(defs.get(ref.rsplit('/',1)[-1],{}),depth,refs|{ref},False)
            for k,v in node.items():
                if k in ('properties','$defs'):
                    for child in v.values(): visit(child,depth+int(k=='properties'),refs,count)
                elif k in ('items','anyOf','oneOf','allOf'):
                    visit(v,depth+int(k=='items'),refs,count)
        elif isinstance(node,list):
            for child in node: visit(child,depth,refs,count)
    visit(schema)
    return stats


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if SECRET_FIELD.search(k) else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]')
        return re.sub(r'(?i)Bearer\s+\S+', 'Bearer [REDACTED]', value)
    return value


def shape(value):
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return {'type': 'list', 'length': len(value), 'items': [shape(v) for v in value[:3]]}
    return type(value).__name__


def inspect_content(content, model, finish_reason=None):
    result = {'valid_json': False, 'pydantic_pass': False, 'markdown_wrapper': False,
              'text_prefix_suffix': False, 'truncated': finish_reason in ('length', 'max_tokens'),
              'classification': None, 'validation_errors': []}
    if content is None:
        result.update(classification='EMPTY_CONTENT', reason='missing content')
        return result
    if not isinstance(content, str):
        result.update(classification='PROVIDER_FORMAT_MISMATCH', reason='content is not a string')
        return result
    if not content.strip():
        result.update(classification='EMPTY_CONTENT', reason='empty content')
        return result
    text = content.strip()
    result['markdown_wrapper'] = text.startswith('```')
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        result['reason'] = f'{exc.msg}; line {exc.lineno}, column {exc.colno}, position {exc.pos}'
        result['classification'] = 'TRUNCATED' if result['truncated'] else 'JSON_PARSE_FAIL'
        result['raw_classification'] = result['classification']
        result['text_prefix_suffix'] = not result['markdown_wrapper'] and (not text.startswith('{') or exc.msg == 'Extra data')
        result['json_error_kind'] = ('markdown fence' if result['markdown_wrapper'] else
                                   'text before JSON' if not text.startswith('{') else
                                   'multiple JSON objects' if exc.msg == 'Extra data' and text[exc.pos:].lstrip().startswith('{') else
                                   'text after JSON' if exc.msg == 'Extra data' else
                                   'truncated response' if result['truncated'] else
                                   'unclosed bracket' if exc.pos >= len(text)-1 and not text.endswith(('}',']')) else
                                   'invalid comma' if 'delimiter' in exc.msg else 'other JSON error')
        try:
            text = json_object_text(text)
            result['unwrap_parseable'] = True
        except StructuredResponseError as unwrap:
            result['unwrap_parseable'] = False
            result['unwrap_reason'] = unwrap.reason
            return result
    else:
        result['valid_json'] = True
        result['unwrap_parseable'] = False
    try:
        model.model_validate_json(text)
        result['pydantic_pass'] = True
        result['classification'] = None
    except ValidationError as exc:
        result['classification'] = 'TRUNCATED' if result['truncated'] else 'SCHEMA_FAIL'
        result['validation_errors'] = schema_errors(exc)
    if result['truncated']:
        result['classification'] = 'TRUNCATED'
    return result


def save_raw(*, provider, model, status, duration_ms, body, response, model_class, secrets=()):
    # Whitelist request metadata; messages/headers/base URLs are never stored.
    choices = response.get('choices') if isinstance(response,dict) else None
    choice = choices[0] if isinstance(choices,list) and choices else {}
    choice = choice if isinstance(choice, dict) else {}
    message = choice.get('message') or {}
    message = message if isinstance(message, dict) else {}
    try:
        content = response_content(response)
        analysis = inspect_content(content, model_class, choice.get('finish_reason'))
    except StructuredResponseError as exc:
        analysis = {'classification': exc.classification, 'reason': exc.reason}
    if choice.get('finish_reason') in ('length','max_tokens'):
        analysis['classification']='OUTPUT_TRUNCATED'
    if analysis.get('classification'):
        analysis['transport_classification'] = 'TRANSPORT_OK_PARSE_FAIL' if status == 200 else 'HTTP_ERROR'
    record = {'provider': provider, 'model': model, 'http_status': status, 'duration_ms': duration_ms,
              'finish_reason': choice.get('finish_reason'), 'usage': response.get('usage') if isinstance(response, dict) else None,
              'response_format': body.get('response_format'), 'max_tokens': body.get('max_tokens', body.get('max_completion_tokens')),
              'reasoning': {k: v for k, v in body.items() if 'reasoning' in k},
              'response_body_structure': shape(response), 'raw_response': response,
              'analysis': analysis}
    raw_content = message.get('content')
    record['response_length']=len(raw_content) if isinstance(raw_content,str) else 0
    record['response_tail_500']=raw_content[-500:] if isinstance(raw_content,str) else ''
    output_tokens = record['usage'].get('completion_tokens') if isinstance(record['usage'],dict) else None
    record['output_tokens_at_limit'] = output_tokens >= record['max_tokens'] if isinstance(output_tokens,(int,float)) and isinstance(record['max_tokens'],(int,float)) else None
    fmt = body.get('response_format') or {}
    if isinstance(fmt,dict) and fmt.get('type') == 'json_schema':
        spec = fmt.get('json_schema') or {}
        record['schema_stats'] = {**schema_stats(spec.get('schema',{})), 'strict': spec.get('strict')}
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f'{uuid4().hex}.json'
    path.write_text(json.dumps(redact(record, secrets), ensure_ascii=False, indent=2), encoding='utf-8')
    return path
