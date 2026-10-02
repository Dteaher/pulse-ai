from collections import Counter, defaultdict
from .models import ProcessDefinition, Issue


def validate_process(p: ProcessDefinition) -> list[Issue]:
    issues: list[Issue] = []

    def error(message: str, ids: list[str] | None = None):
        issues.append(Issue(severity='error', message=message, node_ids=ids or []))

    all_ids = [p.id] + [x.id for x in p.participants + p.nodes + p.flows]
    duplicates = [key for key, count in Counter(all_ids).items() if count > 1]
    if duplicates:
        error('Повторяющиеся ID: ' + ', '.join(duplicates))
    if len({a.id for a in p.ambiguities}) != len(p.ambiguities):
        error('Повторяющиеся ID уточнений.')
    nodes = {n.id: n for n in p.nodes}
    participants = {x.id for x in p.participants}
    incoming, outgoing = defaultdict(list), defaultdict(list)
    for n in p.nodes:
        if n.participant_id not in participants:
            error(f'Неизвестный участник у «{n.name}».', [n.id])
    for f in p.flows:
        if f.source not in nodes or f.target not in nodes:
            error(f'Связь {f.id} ссылается на отсутствующий узел.')
        else:
            outgoing[f.source].append(f)
            incoming[f.target].append(f)
            if f.source == f.target:
                error('Связь узла с самим собой: используйте явную ветку возврата.', [f.source])
    starts = [n.id for n in p.nodes if n.type == 'start_event']
    ends = [n.id for n in p.nodes if n.type == 'end_event']
    if not starts:
        error('Нет стартового события.')
    if not ends:
        error('Нет конечного события.')
    for n in p.nodes:
        ins, outs = incoming[n.id], outgoing[n.id]
        if n.type == 'start_event' and ins:
            error('Стартовое событие имеет входящие связи.', [n.id])
        if n.type == 'end_event' and outs:
            error('Конечное событие имеет исходящие связи.', [n.id])
        if n.type != 'start_event' and not ins:
            error(f'У «{n.name}» нет входящей связи.', [n.id])
        if n.type != 'end_event' and not outs:
            error(f'У «{n.name}» нет исходящей связи.', [n.id])
        if n.type.endswith('gateway'):
            if len(ins) < 2 and len(outs) < 2:
                error(f'Шлюз «{n.name}» не разделяет и не объединяет ветки.', [n.id])
            if len(ins) > 1 and len(outs) > 1:
                error('Разделите объединение и разделение на два шлюза.', [n.id])
            if n.type in ('exclusive_gateway', 'inclusive_gateway') and len(outs) > 1:
                if sum(f.is_default for f in outs) > 1:
                    error('У шлюза несколько веток по умолчанию.', [n.id])
                for f in outs:
                    if not f.is_default and not f.condition:
                        error(f'Не задано условие ветки «{f.name or f.id}».', [n.id])
            if n.type == 'parallel_gateway' and any(f.condition or f.is_default for f in outs):
                error('У параллельного шлюза не должно быть условий.', [n.id])
        elif len(outs) > 1:
            error('Для нескольких исходящих веток нужен явный шлюз.', [n.id])
        for f in outs:
            if f.is_default and n.type not in ('exclusive_gateway', 'inclusive_gateway'):
                error('Ветка по умолчанию допустима только у условного шлюза.', [n.id])
            if f.condition and n.type not in ('exclusive_gateway', 'inclusive_gateway'):
                error('Условие перехода должно исходить из условного шлюза.', [n.id])
    def walk(seed, links, direction):
        visited, pending = set(), list(seed)
        while pending:
            node_id = pending.pop()
            if node_id not in visited:
                visited.add(node_id)
                pending.extend(getattr(f, direction) for f in links[node_id])
        return visited
    reachable = walk(starts, outgoing, 'target')
    terminating = walk(ends, incoming, 'source')
    for n in p.nodes:
        if n.id not in reachable:
            error(f'«{n.name}» недостижим от старта.', [n.id])
        if n.id not in terminating:
            error(f'Из «{n.name}» нет пути к завершению.', [n.id])
    for a in p.ambiguities:
        if any(i not in nodes for i in a.related_node_ids):
            error('Уточнение ссылается на отсутствующий узел.')
    return issues
