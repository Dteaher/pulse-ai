from collections import Counter, defaultdict
import re
from .models import ProcessDefinition, Issue
from .spec_rules import evidence


def validate_process(p: ProcessDefinition) -> list[Issue]:
    issues: list[Issue] = []

    def error(message: str, ids: list[str] | None = None, code='INVALID_PROCESS_GRAPH', severity='error', **context):
        ids = ids or []
        if ids and 'process_id' not in context:
            context['process_id'] = pool_of(ids[0]) or p.id
        issues.append(Issue(severity=severity, code=code, message=message, node_ids=ids, node_id=ids[0] if ids else None, **evidence(code), **context))

    all_ids = [p.id] + [x.id for x in p.participants + p.nodes + p.flows + p.pools + p.message_flows]
    duplicates = [key for key, count in Counter(all_ids).items() if count > 1]
    if duplicates:
        error('Повторяющиеся ID: ' + ', '.join(duplicates), code='DUPLICATE_ID')
    if len({a.id for a in p.ambiguities}) != len(p.ambiguities):
        error('Повторяющиеся ID уточнений.')
    nodes = {n.id: n for n in p.nodes}
    participants = {x.id for x in p.participants}
    roles = {x.id: x for x in p.participants}
    pools = {x.id: x for x in p.pools}
    def pool_of(node_id):
        n = nodes.get(node_id)
        return roles[n.participant_id].pool_id if n and n.participant_id in roles else None
    if pools:
        for role in p.participants:
            if role.pool_id not in pools:
                error(f'Неизвестный Pool участника «{role.name}».', code='UNKNOWN_POOL')
            elif role.kind != pools[role.pool_id].kind:
                error(f'Внешний участник «{role.name}» не может быть дорожкой внутреннего Pool (и наоборот).', code='POOL_KIND_MISMATCH')
        for pool in p.pools:
            members = [n for n in p.nodes if pool_of(n.id) == pool.id]
            if not members:
                error(f'Раскрытый Pool «{pool.name}» не содержит процесса.', code='INCOMPLETE_LOCAL_PROCESS', process_id=pool.id)
            if pool.kind == 'external' and len([r for r in p.participants if r.pool_id == pool.id]) != 1:
                error('Внешний Pool представляет одного внешнего участника.')
    elif any(r.pool_id or r.kind == 'external' for r in p.participants) or p.message_flows:
        error('Принадлежность к Pool и Message Flow требуют явно заданных pools.')
    incoming, outgoing = defaultdict(list), defaultdict(list)
    for n in p.nodes:
        if n.participant_id not in participants:
            error(f'Неизвестный участник у «{n.name}».', [n.id], code='UNKNOWN_PARTICIPANT')
    for f in p.flows:
        if f.source not in nodes or f.target not in nodes:
            error(f'Связь {f.id} ссылается на отсутствующий узел.', code='BROKEN_REFERENCE', flow_id=f.id)
        else:
            if pools and pool_of(f.source) != pool_of(f.target):
                error('Sequence Flow не может пересекать границу Pool; используйте Message Flow.', [f.source, f.target], code='CROSS_POOL_SEQUENCE_FLOW', flow_id=f.id, source_pool=pool_of(f.source), target_pool=pool_of(f.target))
                continue  # A forbidden cross-pool edge cannot make local nodes reachable.
            outgoing[f.source].append(f)
            incoming[f.target].append(f)
            if f.source == f.target and nodes[f.source].type != 'exclusive_gateway':
                error('Связь узла с самим собой: используйте явную ветку возврата.', [f.source])
    for f in p.message_flows:
        if f.source not in nodes or f.target not in nodes:
            error(f'Message Flow {f.id} ссылается на отсутствующий узел.', code='BROKEN_REFERENCE', flow_id=f.id)
        elif pool_of(f.source) == pool_of(f.target):
            error('Message Flow должен соединять разные Pool.', [f.source, f.target], code='SAME_POOL_MESSAGE_FLOW', flow_id=f.id)
        elif nodes[f.source].type.endswith('gateway') or nodes[f.target].type.endswith('gateway') or nodes[f.source].type in ('start_event', 'end_event', 'intermediate_event') or nodes[f.target].type == 'end_event' or (nodes[f.target].type == 'intermediate_event' and nodes[f.target].event_definition != 'message'):
            error('Message Flow должен соединять допустимые отправляющие и получающие действия/события.', [f.source, f.target], code='INVALID_MESSAGE_ENDPOINT', flow_id=f.id)
        elif nodes[f.target].type == 'start_event' and nodes[f.target].event_definition != 'message':
            error('Получение сообщения лучше обозначить Message Start Event.', [f.target], code='MESSAGE_START_MARKER_RECOMMENDED', severity='warning', flow_id=f.id)
    starts = [n.id for n in p.nodes if n.type == 'start_event']
    ends = [n.id for n in p.nodes if n.type == 'end_event']
    for n in p.nodes:
        if n.type == 'start_event' and n.event_definition == 'none' and re.search(r'\b(пода[её]т|отправляет|созда[её]т|проверяет|принимает|выполняет|получает|подать|отправить|создать|проверить|получить|выполнить|submit[s]?|create[s]?|check[s]?|receive[s]?)\b', n.name, re.I):
            error('Рекомендуется обозначить действие задачей, а Start Event назвать триггером.', [n.id], code='ACTION_AS_START_EVENT', severity='warning')
        ins, outs = incoming[n.id], outgoing[n.id]
        if n.event_definition == 'message' and n.type not in ('start_event', 'intermediate_event'):
            error('Определение сообщения допустимо у стартового или промежуточного catch event.', [n.id], code='INVALID_EVENT_DEFINITION')
        if n.type == 'start_event' and n.event_definition == 'message' and not any(f.target == n.id and pool_of(f.source) != pool_of(n.id) for f in p.message_flows):
            error('Отправитель Message Start не показан: при необходимости добавьте Message Flow.', [n.id], code='MESSAGE_START_WITHOUT_MESSAGE', severity='warning')
        if n.type == 'event_based_gateway':
            if not ins or len(outs) < 2:
                error('Неинстанцирующий Event-Based Gateway требует вход и минимум две альтернативы.', [n.id], code='INVALID_EVENT_GATEWAY_TOPOLOGY', gateway_id=n.id)
            successors = [nodes[f.target] for f in outs]
            if any(t.type != 'receive_task' and not (t.type == 'intermediate_event' and t.event_definition == 'message') for t in successors):
                error('После Event-Based Gateway допустимы Receive Task или catch message event.', [n.id], code='INVALID_EVENT_GATEWAY_SUCCESSOR', gateway_id=n.id)
            if len({t.id for t in successors}) != len(successors) or any(len(incoming[t.id]) != 1 for t in successors):
                error('Ожидания после Event-Based Gateway должны быть различными и иметь только вход от шлюза.', [n.id], code='SHARED_EVENT_GATEWAY_SUCCESSOR', gateway_id=n.id)
            if len({t.type for t in successors}) > 1:
                error('В одном Event-Based Gateway не смешивайте Receive Task и catch message event.', [n.id], code='MIXED_EVENT_GATEWAY_SUCCESSORS', gateway_id=n.id)
            if any(f.condition or f.is_default for f in outs):
                error('Event-Based Gateway выбирает событие, а не conditionExpression/default.', [n.id], code='EVENT_GATEWAY_HAS_CONDITION', gateway_id=n.id)
            if n.decision_basis == 'data':
                error('Для решения по данным нужен Exclusive Gateway.', [n.id], code='EVENT_GATEWAY_DATA_DECISION', gateway_id=n.id)
        if n.type == 'start_event' and ins:
            error('Стартовое событие имеет входящие связи.', [n.id], code='START_HAS_INCOMING')
        if n.type == 'end_event' and outs:
            error('Конечное событие имеет исходящие связи.', [n.id], code='END_HAS_OUTGOING')
        local_start = any(x.type == 'start_event' and pool_of(x.id) == pool_of(n.id) for x in p.nodes)
        local_end = any(x.type == 'end_event' and pool_of(x.id) == pool_of(n.id) for x in p.nodes)
        if n.type != 'start_event' and not ins and (local_start or n.type == 'intermediate_event'):
            error(f'У «{n.name}» нет входящей связи.', [n.id], code='ORPHAN_NODE')
        if n.type != 'end_event' and not outs and (local_end or n.type in ('start_event', 'intermediate_event')):
            error(f'У «{n.name}» нет исходящей связи.', [n.id], code='DEAD_END')
        if n.type.endswith('gateway'):
            if len(ins) < 2 and len(outs) < 2:
                error(f'Шлюз «{n.name}» не разделяет и не объединяет ветки.', [n.id], code='INVALID_GATEWAY_TOPOLOGY', gateway_id=n.id)
            if len(ins) > 1 and len(outs) > 1 and n.type not in ('exclusive_gateway', 'event_based_gateway'):
                error('Разделите объединение и разделение на два шлюза.', [n.id], code='INVALID_GATEWAY_TOPOLOGY', gateway_id=n.id)
            if n.type in ('exclusive_gateway', 'inclusive_gateway') and len(outs) > 1:
                if sum(f.is_default for f in outs) > 1:
                    error('У шлюза несколько веток по умолчанию.', [n.id])
                for f in outs:
                    if not f.is_default and not f.condition:
                        error(f'Не задано условие ветки «{f.name or f.id}».', [n.id], code='MISSING_BRANCH_CONDITION', flow_id=f.id, gateway_id=n.id)
            if n.type == 'parallel_gateway' and any(f.condition or f.is_default for f in outs):
                error('У параллельного шлюза не должно быть условий.', [n.id], code='PARALLEL_HAS_CONDITION', gateway_id=n.id)
        elif len(outs) > 1:
            error('Несколько исходящих переходов создают независимые пути; явный шлюз может улучшить читаемость.', [n.id], code='MULTIPLE_OUTGOING_WITHOUT_GATEWAY', severity='warning')
        for f in outs:
            conditional_source = n.type.endswith('task') or n.type in ('exclusive_gateway', 'inclusive_gateway')
            if f.is_default and not conditional_source:
                error('Default допустим у Activity или условного шлюза.', [n.id], code='INVALID_DEFAULT_SOURCE')
            if f.condition and not conditional_source:
                error('Условие допустимо у Activity или условного шлюза.', [n.id], code='CONDITION_OUTSIDE_GATEWAY', flow_id=f.id)
            if f.condition and n.type.endswith('task') and len(outs) < 2:
                error('Conditional Flow из Activity требует другого исходящего перехода.', [n.id], code='CONDITIONAL_ACTIVITY_SINGLE_FLOW', flow_id=f.id)
        if sum(f.is_default for f in outs) > 1:
            error('У источника несколько default flow.', [n.id], code='MULTIPLE_DEFAULT_FLOWS')
    def walk(seed, links, direction):
        visited, pending = set(), list(seed)
        while pending:
            node_id = pending.pop()
            if node_id not in visited:
                visited.add(node_id)
                pending.extend(getattr(f, direction) for f in links[node_id])
        return visited
    for process_id in pools or {p.id: None}:
        members = [n for n in p.nodes if not pools or pool_of(n.id) == process_id]
        local_starts = [n.id for n in members if n.type == 'start_event']
        local_ends = [n.id for n in members if n.type == 'end_event']
        if bool(local_starts) != bool(local_ends):
            error('Явные Start и End должны присутствовать вместе на уровне Process.', code='START_END_PAIR_REQUIRED', process_id=process_id)
        if len(local_starts) > 1:
            error('Несколько Start Events допустимы; проверьте независимые механизмы запуска.', local_starts, code='MULTIPLE_START_EVENTS', severity='warning')
        reachable = walk(local_starts or [n.id for n in members if not incoming[n.id]], outgoing, 'target')
        terminating = walk(local_ends or [n.id for n in members if not outgoing[n.id]], incoming, 'source')
        for n in members:
            if n.id not in reachable:
                error(f'«{n.name}» недостижим от старта своего процесса.', [n.id], code='UNREACHABLE_NODE', process_id=process_id)
            if n.id not in terminating:
                error(f'Из «{n.name}» нет пути к завершению своего процесса.', [n.id], code='NO_PATH_TO_END', process_id=process_id)
    for a in p.ambiguities:
        if any(i not in nodes for i in a.related_node_ids):
            error('Уточнение ссылается на отсутствующий узел.')
    return issues
