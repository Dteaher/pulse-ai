"""Presentation and ownership checks independent of the LLM provider."""
import re
from .models import Issue, ProcessDefinition, Participant, Pool


def validate_labels(p: ProcessDefinition) -> list[Issue]:
    issues = []
    for node in p.nodes:
        if node.type.endswith('gateway') and not node.name.strip():
            issues.append(Issue(severity='warning', code='EMPTY_GATEWAY_LABEL', message='Назовите шлюз, чтобы смысл ветвей был понятен.', node_ids=[node.id]))
        if node.type.endswith('task') and len(node.name) > 100:
            issues.append(Issue(severity='warning', code='LONG_TASK_LABEL', message='Сократите подпись задачи для удобства чтения.', node_ids=[node.id]))
    if not re.search('[А-Яа-яЁё]', p.description or p.name):
        return issues
    roles = {r.id:r for r in p.participants}
    for item in p.pools + p.participants + p.nodes + p.flows + p.message_flows:
        # Acronyms/proper system names (CRM, SAP) are permitted. Pure English
        # phrases in a Russian process are corrected by the model, never translated
        # using an incomplete dictionary or silently removed from the evidence.
        proper_name = isinstance(item, (Participant, Pool)) and item.name in p.description
        if item.name and re.search('[a-z]', item.name) and not re.search('[А-Яа-яЁё]', item.name) and not proper_name:
            issues.append(Issue(severity='warning', code='LABEL_STYLE', message=f'Видимая подпись «{item.name}» должна быть на языке исходного процесса; технические имена оставьте только в ID.', node_ids=[item.id]))
    actions = r'\b(?:провер|отправ|сформир|формир|подготов|доработ|исправ|уведом|получ|соглас|утверд|созд|прин|обработ|выполн)\w*(?:ть|ет|ют|ит|ят|ёт|ут|ат)\b'
    for node in p.nodes:
        if node.type.endswith('task'):
            role = roles.get(node.participant_id)
            if role and node.name.casefold().startswith(role.name.casefold()+' '):
                issues.append(Issue(severity='warning', code='LABEL_STYLE',message=f'Сократи задачу «{node.name}» до действия в инфинитиве без роли: исполнитель уже указан дорожкой. Полную фразу сохрани в source_text.',node_ids=[node.id]))
            if re.search(r'\bи\b',node.name,re.I) and len(re.findall(actions,node.name,re.I))>1:
                issues.append(Issue(severity='warning', code='LABEL_STYLE',message=f'Задача «{node.name}» объединяет несколько действий. Раздели действия на отдельные задачи, сохрани исполнителей, исходную фразу evidence и все сообщения.',node_ids=[node.id]))
    return issues


def check_membership(before: ProcessDefinition, after: ProcessDefinition, instruction: str):
    """Require explicit ownership intent before accepting a topology move.

    This detects authorization wording, not whether a role is external. That
    classification must come from the model's structured context.
    """
    explicit = re.search(r'(перенес|перемест|перевед|сдела|измен|смен|move|change|make)', instruction, re.I) and re.search(r'(пул|pool|внешн|внутрен|external|internal)', instruction, re.I)
    if explicit or not before.pools:
        return
    if not after.pools:
        raise ValueError('Сохрани структуру Pool и Message Flow исходного процесса.')
    old = {r.id:r for r in before.participants}
    for role in after.participants:
        if role.id in old and (role.kind,role.pool_id)!=(old[role.id].kind,old[role.id].pool_id):
            raise ValueError(f'Без явной команды нельзя менять kind/pool_id участника «{role.name}». Сохрани исходную принадлежность.')
    old_pools = {pool.id:pool for pool in before.pools}
    for pool in after.pools:
        if pool.id in old_pools and pool.kind!=old_pools[pool.id].kind:
            raise ValueError('Без явной команды нельзя менять тип существующего Pool.')
    assignment = re.search(r'(перенес|перемест|поруч|переда|назнач|move|reassign)', instruction, re.I) and re.search(r'(задач|действ|проверк|согласован|исполн|task|action|check)', instruction, re.I)
    old_nodes = {n.id:n for n in before.nodes}
    for node in after.nodes:
        previous = old_nodes.get(node.id)
        if previous and previous.participant_id != node.participant_id and not assignment:
            raise ValueError(f'Сохрани исполнителя и дорожку существующего действия «{previous.name}», если команда явно не меняет ответственность.')
