"""Bounded structural corrections, never arbitrary edits of business objects."""
import hashlib
import json
from typing import Literal
from pydantic import Field, model_validator
from .models import StrictModel, ProcessDefinition, Node, Flow, MessageFlow


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class RepairOperation(StrictModel):
    op: Literal['redirect_source', 'redirect_target', 'add_flow', 'add_gateway',
                'change_gateway_type', 'remove_duplicate_flow', 'sequence_to_message']
    element_id: str = Field(min_length=1, max_length=100)
    source: str | None = None
    target: str | None = None
    participant_id: str | None = None
    gateway_type: Literal['exclusive_gateway', 'parallel_gateway'] | None = None

    @model_validator(mode='after')
    def fields_for_operation(self):
        required = {'redirect_source': {'source'}, 'redirect_target': {'target'},
                    'add_flow': {'source', 'target'}, 'add_gateway': {'participant_id', 'gateway_type'},
                    'change_gateway_type': {'gateway_type'}, 'remove_duplicate_flow': set(),
                    'sequence_to_message': set()}[self.op]
        present = {k for k in ('source', 'target', 'participant_id', 'gateway_type') if getattr(self, k) is not None}
        if present != required:
            raise ValueError('Операция требует только свои обязательные поля.')
        return self


class RepairPatch(StrictModel):
    operations: list[RepairOperation] = Field(min_length=1, max_length=12)


SUPPORTED = {'INVALID_EVENT_GATEWAY_TOPOLOGY', 'INVALID_GATEWAY_TOPOLOGY',
             'PARALLEL_JOIN_MISMATCH', 'BROKEN_REFERENCE', 'CROSS_POOL_SEQUENCE_FLOW',
             'ORPHAN_NODE', 'DEAD_END', 'UNREACHABLE_NODE', 'NO_PATH_TO_END'}


def repair_context(process, errors):
    """A one-hop neighbourhood, bounded independently of graph size."""
    if not errors or any(e.code not in SUPPORTED for e in errors):
        return None
    nodes = {n.id: n for n in process.nodes}
    affected = {key for e in errors for key in [e.node_id, e.gateway_id, *e.node_ids] if key in nodes}
    flow_ids = {e.flow_id for e in errors if e.flow_id}
    for f in process.flows:
        if f.id in flow_ids:
            affected.update(k for k in (f.source, f.target) if k in nodes)
    if not affected:
        return None
    neighbours = set(affected)
    for f in process.flows:
        if f.source in affected or f.target in affected:
            neighbours.update(k for k in (f.source, f.target) if k in nodes)
    if len(neighbours) > 24:
        return None  # A broad reachability failure is not a proven local repair.
    flows = [f for f in process.flows if f.source in neighbours or f.target in neighbours or f.id in flow_ids]
    if len(flows) > 40:
        return None
    roles = {nodes[k].participant_id for k in neighbours}
    return {'errors': [e.model_dump(include={'code', 'message', 'node_id', 'node_ids', 'flow_id', 'gateway_id'}, exclude_none=True) for e in errors],
            'affected_ids': sorted(affected), 'allowed_node_ids': sorted(neighbours),
            'nodes': [nodes[k].model_dump(exclude_defaults=True) for k in sorted(neighbours)],
            'flows': [f.model_dump(exclude_defaults=True) for f in flows],
            'participants': [r.model_dump(exclude_defaults=True) for r in process.participants if r.id in roles],
            'message_flows': [f.model_dump(exclude_defaults=True) for f in process.message_flows if f.source in neighbours or f.target in neighbours]}


def quality_fingerprint(p):
    return {key: digest(value) for key, value in p.model_dump(mode='json').items()}


def business_loops(p):
    """Business-task SCCs: technical gateways may change, existing cycles may not disappear."""
    roles = {r.id: r.pool_id for r in p.participants}
    nodes = {n.id: n for n in p.nodes}
    graph = {n.id: [] for n in p.nodes}
    for f in p.flows:
        if f.source in nodes and f.target in nodes and roles[nodes[f.source].participant_id] == roles[nodes[f.target].participant_id]:
            graph[f.source].append(f.target)
    reachable = {}
    for start in graph:
        visited, todo = set(), list(graph[start])
        while todo:
            key = todo.pop()
            if key in visited:
                continue
            visited.add(key); todo.extend(graph[key])
        reachable[start] = visited
    tasks = {n.id for n in p.nodes if n.type.endswith('task')}
    return {frozenset(other for other in tasks if other in reachable[key] and key in reachable[other]) for key in tasks if key in reachable[key]}


def assert_preservation(before, after, patch):
    """Audit complete canonical objects, not merely counts or lossy summaries."""
    for key in ('id', 'name', 'description', 'participants', 'pools', 'assumptions', 'ambiguities'):
        if getattr(before, key) != getattr(after, key):
            raise ValueError('Patch изменил защищённую бизнес-информацию: ' + key)
    original_nodes, final_nodes = {n.id: n for n in before.nodes}, {n.id: n for n in after.nodes}
    changed_gateways = {o.element_id for o in patch.operations if o.op == 'change_gateway_type'}
    for key, node in original_nodes.items():
        candidate = final_nodes.get(key)
        if candidate is None:
            raise ValueError('Patch удалил действие/событие/шлюз.')
        allowed = {'type'} if key in changed_gateways else set()
        if node.model_dump(exclude=allowed) != candidate.model_dump(exclude=allowed):
            raise ValueError('Patch изменил имя, роль, evidence или unrelated node.')
    added_nodes = {o.element_id for o in patch.operations if o.op == 'add_gateway'}
    if set(final_nodes) - set(original_nodes) != added_nodes:
        raise ValueError('Непредусмотренные новые узлы после normalization.')
    changed_flows = {o.element_id: set() for o in patch.operations}
    removed = set()
    for o in patch.operations:
        changed_flows[o.element_id].update({'source'} if o.op == 'redirect_source' else {'target'} if o.op == 'redirect_target' else set())
        if o.op in ('remove_duplicate_flow', 'sequence_to_message'):
            removed.add(o.element_id)
    final_flows = {f.id: f for f in after.flows}
    for f in before.flows:
        if f.id in removed:
            continue
        if f.id not in final_flows or f.model_dump(exclude=changed_flows.get(f.id, set())) != final_flows[f.id].model_dump(exclude=changed_flows.get(f.id, set())):
            raise ValueError('Patch изменил unrelated flow, условие или label.')
    additions = {o.element_id for o in patch.operations if o.op == 'add_flow'}
    if set(final_flows) - {f.id for f in before.flows} != additions:
        raise ValueError('Непредусмотренные новые переходы.')
    original_messages = {f.id: f for f in before.message_flows}
    final_messages = {f.id: f for f in after.message_flows}
    if any(final_messages.get(key) != value for key, value in original_messages.items()):
        raise ValueError('Patch потерял/изменил сообщение.')
    if set(final_messages) - set(original_messages) != {o.element_id for o in patch.operations if o.op == 'sequence_to_message'}:
        raise ValueError('Непредусмотренные сообщения.')
    if not business_loops(before) <= business_loops(after):
        raise ValueError('Patch потерял существующий business loop.')


def apply_repair(before: ProcessDefinition, errors, patch: RepairPatch):
    context = repair_context(before, errors)
    if context is None:
        raise ValueError('Ошибка не допускает ограниченный patch.')
    p = before.model_copy(deep=True)
    allowed_nodes = set(context['allowed_node_ids'])
    allowed_flows = {f['id'] for f in context['flows']}
    affected = set(context['affected_ids'])
    codes = {e.code for e in errors}
    used = {before.id, *(x.id for x in before.nodes + before.flows + before.message_flows + before.participants + before.pools)}
    fresh = {o.element_id for o in patch.operations if o.op in ('add_gateway', 'add_flow')}
    if fresh & used or len(fresh) != sum(o.op in ('add_gateway', 'add_flow') for o in patch.operations):
        raise ValueError('Новые ID не уникальны.')
    # Additions may be referenced later, but definitions must precede their use.
    for o in patch.operations:
        nodes, flows = {n.id: n for n in p.nodes}, {f.id: f for f in p.flows}
        if o.op == 'add_gateway':
            if not codes & {'INVALID_EVENT_GATEWAY_TOPOLOGY', 'INVALID_GATEWAY_TOPOLOGY', 'PARALLEL_JOIN_MISMATCH'}:
                raise ValueError('Ошибка не разрешает новый шлюз.')
            if o.participant_id not in {r['id'] for r in context['participants']}:
                raise ValueError('Неизвестная/unrelated роль шлюза.')
            p.nodes.append(Node(id=o.element_id, type=o.gateway_type, participant_id=o.participant_id,
                                name='Объединить пути', inferred=True, confidence='confirmation_required'))
            allowed_nodes.add(o.element_id)
        elif o.op == 'add_flow':
            if o.source not in allowed_nodes or o.target not in allowed_nodes or o.source not in nodes or o.target not in nodes:
                raise ValueError('Новый переход ссылается на unrelated/unknown node.')
            if not {o.source, o.target} & (affected | fresh):
                raise ValueError('Новый переход не связан с конкретной ошибкой.')
            p.flows.append(Flow(id=o.element_id, source=o.source, target=o.target))
        elif o.op == 'change_gateway_type':
            if o.element_id not in affected or o.element_id not in nodes or not nodes[o.element_id].type.endswith('gateway'):
                raise ValueError('Нельзя менять business task/event/unknown gateway.')
            if not any(e.code == 'PARALLEL_JOIN_MISMATCH' and o.element_id in [e.node_id, e.gateway_id, *e.node_ids] for e in errors):
                raise ValueError('Тип синхронизации не доказан ошибкой.')
            gateway = nodes[o.element_id]
            incoming = [f for f in p.flows if f.target == gateway.id]
            outgoing = [f for f in p.flows if f.source == gateway.id]
            if len(incoming) < 2 or len(outgoing) != 1 or gateway.decision_basis == 'data':
                raise ValueError('Разрешено только исправление технического converging join.')
            nodes[o.element_id].type = o.gateway_type
        else:
            if o.element_id not in allowed_flows or o.element_id not in flows:
                raise ValueError('Unknown/unrelated flow ID.')
            f = flows[o.element_id]
            if o.op in ('redirect_source', 'redirect_target'):
                target = o.source if o.op == 'redirect_source' else o.target
                if target not in allowed_nodes or target not in nodes:
                    raise ValueError('Unknown/unrelated endpoint.')
                if f.source not in affected and f.target not in affected and target not in fresh:
                    raise ValueError('Flow не связан с конкретной ошибкой.')
                setattr(f, 'source' if o.op == 'redirect_source' else 'target', target)
            elif o.op == 'remove_duplicate_flow':
                if not any(other.model_dump(exclude={'id'}) == f.model_dump(exclude={'id'}) for other in p.flows[:p.flows.index(f)]):
                    raise ValueError('Удалять можно только точный дубликат перехода.')
                p.flows.remove(f)
            elif o.op == 'sequence_to_message':
                if not any(e.code == 'CROSS_POOL_SEQUENCE_FLOW' and e.flow_id == f.id for e in errors) or f.condition or f.is_default:
                    raise ValueError('Только доказанный cross-pool переход без conditions.')
                p.flows.remove(f)
                p.message_flows.append(MessageFlow(id=f.id, source=f.source, target=f.target, name=f.name))
    # Strict types/references are revalidated before the full pipeline sees it.
    p = ProcessDefinition.model_validate(p.model_dump())
    for key in {o.element_id for o in patch.operations if o.op == 'add_gateway'}:
        if sum(f.target == key for f in p.flows) < 2 or sum(f.source == key for f in p.flows) != 1:
            raise ValueError('Новый technical gateway должен быть converging merge/join.')
    assert_preservation(before, p, patch)
    return p
