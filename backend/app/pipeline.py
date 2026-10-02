import json
from pydantic import ValidationError
from .services.llm.base import ProviderError
from .models import Issue
from .validator import validate_process
from .bpmn import build_bpmn


async def generate_valid(call):
    correction = ''
    for attempt in range(3):
        try:
            process = await call(correction)
            errors = validate_process(process)
            if errors:
                correction = json.dumps({'errors': [i.model_dump() for i in errors], 'previous_result': process.model_dump()}, ensure_ascii=False)
                continue
            xml = None if any(a.severity == 'critical' for a in process.ambiguities) else build_bpmn(process)
            return {'process': process, 'ambiguities': process.ambiguities, 'xml': xml, 'attempts': attempt + 1}
        except (ValidationError, ValueError) as exc:
            correction = str(exc)[:6000]
    raise ProviderError('Не удалось получить корректный процесс после двух попыток исправления. Уточните описание и повторите запрос.')


def audit_rules(p):
    issues = []
    for n in p.nodes:
        if n.type == 'end_event' and n.name.lower() in ('конец', 'end', 'завершение'):
            issues.append(Issue(severity='warning', message='Назовите результат конечной ветки, чтобы аналитик видел её исход.', node_ids=[n.id]))
    if any(n.type == 'parallel_gateway' for n in p.nodes):
        issues.append(Issue(severity='info', message='Проверьте, что каждая параллельная ветка гарантированно достигает объединения. Графовая проверка не доказывает отсутствие токеновых тупиков.', node_ids=[n.id for n in p.nodes if n.type == 'parallel_gateway']))
    if not any('отказ' in n.name.lower() or 'отклон' in n.name.lower() for n in p.nodes):
        issues.append(Issue(severity='warning', message='Обработка отказа не обозначена явно. Уточните, требуется ли она для этого процесса.'))
    issues.extend(Issue(severity='warning', message=a.question, node_ids=a.related_node_ids) for a in p.ambiguities)
    issues.append(Issue(severity='info', message='Сроки и обработка исключений не проверяются без соответствующих исходных данных.'))
    return issues
