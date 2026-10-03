"""Deterministic identity preservation and user-facing diff; no LLM dependencies."""
from collections import Counter
from .models import Change, ChangeSet, ProcessDefinition


def preserve_ids(before: ProcessDefinition, candidate: ProcessDefinition) -> ProcessDefinition:
    p = candidate.model_copy(deep=True)
    p.id = before.id
    used = {p.id, *(x.id for x in p.participants + p.nodes + p.flows + p.pools + p.message_flows)}

    def reconcile(old_items, new_items, signature):
        old_counts = Counter(signature(x) for x in old_items)
        new_counts = Counter(signature(x) for x in new_items)
        old = {signature(x): x for x in old_items}
        mapping = {}
        for item in new_items:
            key = signature(item)
            previous = old.get(key)
            if previous and old_counts[key] == new_counts[key] == 1 and previous.id != item.id and previous.id not in used:
                mapping[item.id] = previous.id
                used.remove(item.id)
                used.add(previous.id)
                item.id = previous.id
        return mapping

    pools = reconcile(before.pools, p.pools, lambda x: (x.name.strip().casefold(), x.kind))
    for r in p.participants:
        r.pool_id = pools.get(r.pool_id, r.pool_id)
    roles = reconcile(before.participants, p.participants, lambda x: (x.name.strip().casefold(), x.type))
    for n in p.nodes:
        n.participant_id = roles.get(n.participant_id, n.participant_id)
    nodes = reconcile(before.nodes, p.nodes, lambda x: (x.name.strip().casefold(), x.type, x.event_definition, x.participant_id))
    for f in p.flows + p.message_flows:
        f.source = nodes.get(f.source, f.source)
        f.target = nodes.get(f.target, f.target)
    for a in p.ambiguities:
        a.related_node_ids = [nodes.get(i, i) for i in a.related_node_ids]
    reconcile(before.flows, p.flows, lambda x: (x.source, x.target))
    reconcile(before.message_flows, p.message_flows, lambda x: (x.source, x.target))
    return p


def changes_between(before: ProcessDefinition, after: ProcessDefinition) -> ChangeSet:
    changes = []
    names = {x.id: x.name for x in before.nodes + after.nodes}
    def add(kind, ids, message):
        category = 'participant' if kind == 'participant_changed' else 'condition' if kind == 'condition_changed' else 'branching' if kind == 'gateway_added' or any('gateway' in n.type for n in before.nodes + after.nodes if n.id in ids) else 'structure'
        action = 'added' if kind.endswith('_added') or message.startswith('Добавлен') else 'removed' if kind.endswith('_removed') or message.startswith('Удалён') else 'changed'
        changes.append(Change(type=kind, element_ids=ids, description=message, category=category, action=action))
    for collection in ('pools', 'participants', 'nodes', 'flows', 'message_flows'):
        old = {x.id: x for x in getattr(before, collection)}
        new = {x.id: x for x in getattr(after, collection)}
        for key in old.keys() | new.keys():
            a, b = old.get(key), new.get(key)
            if a == b:
                continue
            item = b or a
            if collection in ('participants', 'pools'):
                verb = 'Добавлен' if a is None else 'Удалён' if b is None else 'Изменён'
                label = 'Pool' if collection == 'pools' else 'участник'
                add('participant_changed', [key], f'{verb} {label} «{item.name}».')
            elif collection == 'nodes':
                if a is None:
                    gateway = 'gateway' in b.type
                    kind = 'gateway_added' if gateway else 'node_added'
                    label = 'параллельный шлюз' if b.type == 'parallel_gateway' else 'шлюз' if gateway else 'элемент'
                    add(kind, [key], f'Добавлен {label} «{b.name}».')
                elif b is None:
                    add('node_removed', [key], f'Удалён элемент «{a.name}».')
                elif any(getattr(a, field) != getattr(b, field) for field in ('name', 'type', 'event_definition', 'decision_basis', 'participant_id')):
                    add('node_changed', [key], f'Изменён элемент «{b.name}».')
            else:
                path = f'«{names.get(item.source, "Начало")}» → «{names.get(item.target, "Завершение")}»'
                if a is None or b is None:
                    add('flow_added' if a is None else 'flow_removed', [key, item.source, item.target], f'{"Добавлен" if a is None else "Удалён"} переход {path}.')
                else:
                    if collection == 'message_flows' and a.name != b.name:
                        add('flow_changed', [key], f'Изменено сообщение {path}: {b.name}.')
                    if collection == 'flows' and (a.condition, a.is_default, a.name) != (b.condition, b.is_default, b.name):
                        add('condition_changed', [key], f'Изменено условие перехода {path}: {b.name or b.condition or "без условия"}.')
                    if (a.source, a.target) != (b.source, b.target):
                        add('sequence_changed', [key, b.source, b.target], f'Изменён порядок: {path}.')
    if (before.name, before.description) != (after.name, after.description):
        add('other', [after.id], 'Обновлено описание процесса.')
    priority = ['participant_changed', 'gateway_added', 'node_added', 'node_removed', 'node_changed', 'condition_changed', 'sequence_changed', 'flow_changed', 'flow_added', 'flow_removed', 'other']
    return ChangeSet(changes=sorted(changes, key=lambda c: (priority.index(c.type), c.element_ids)))
