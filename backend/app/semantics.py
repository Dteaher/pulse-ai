"""Conservative checks for explicit outcomes and structured parallel branches.

These rules inspect finite graph paths, not arbitrary text meaning or execution.
"""
import re
from collections import defaultdict, deque
from .models import Issue, ProcessDefinition
from .notation import is_message_wait


def outcome(text: str):
    text = text.casefold().replace('ё', 'е')
    if re.search(r'\b(нет|без|не)\s+(отказ|отклон)\w*', text):
        return None  # Negated refusal is not treated as an explicit approval.
    negative = r'\b(отказ\w*|отклон\w*|неодобрен\w*|не\s+(одобрен\w*|согласован\w*|утвержден\w*|принят\w*)|rejected|declined|refused)\b'
    positive = r'\b(одобрен\w*|одобрени\w*|согласован[аоы]?|утвержден\w*|принят[аоы]?|approved|accepted)\b'
    if re.search(negative, text):
        return -1
    if re.search(positive, text):
        return 1
    return None


def validate_semantics(p: ProcessDefinition) -> list[Issue]:
    # Token-flow analysis is process-local; messages never join token graphs.
    event_issues = [Issue(severity='error',code='MESSAGE_WAIT_USES_XOR',message='Ожидание альтернативных внешних сообщений требует Event-Based Gateway без условий.',node_ids=[n.id],gateway_id=n.id) for n in p.nodes if n.type=='exclusive_gateway' and is_message_wait(p,n)]
    if not p.pools:
        return event_issues + _validate_local_semantics(p)
    roles = {r.id: r for r in p.participants}
    issues = event_issues
    for pool in p.pools:
        members = [n for n in p.nodes if n.participant_id in roles and roles[n.participant_id].pool_id == pool.id]
        ids = {n.id for n in members}
        local = p.model_copy(update={'nodes': members, 'flows': [f for f in p.flows if f.source in ids and f.target in ids]})
        found = _validate_local_semantics(local)
        for issue in found:
            issue.process_id = pool.id
        issues.extend(found)
    return issues


def _validate_local_semantics(p: ProcessDefinition) -> list[Issue]:
    nodes = {n.id: n for n in p.nodes}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for f in p.flows:
        if f.source in nodes and f.target in nodes:
            outgoing[f.source].append(f)
            incoming[f.target].append(f)
    issues = []
    def report(message, ids, severity='error', code='INVALID_BRANCH_SEMANTICS'):
        issues.append(Issue(severity=severity, source='BUSINESS_LOGIC', code=code, message=message, node_ids=ids, gateway_id=ids[0] if ids else None, process_id=p.id))

    for gateway in p.nodes:
        branches = outgoing[gateway.id]
        if gateway.type not in ('exclusive_gateway', 'inclusive_gateway') or len(branches) < 2:
            continue
        conditions = []
        for f in branches:
            condition = (f.condition or '').strip()
            if not f.is_default:
                if not condition or re.fullmatch(r'(условие|ветка|condition|branch)\s*\d*|\?+|\.\.\.', condition, re.I):
                    report(f'Некорректное ветвление: у «{gateway.name}» нужно содержательное условие каждой ветки.', [gateway.id])
                conditions.append(re.sub(r'\s+', ' ', condition.casefold()))
            label_sign, condition_sign = outcome(f.name), outcome(condition)
            if label_sign is not None and condition_sign is not None and label_sign != condition_sign:
                report(f'Некорректное ветвление: подпись «{f.name}» противоречит условию перехода.', [gateway.id])
            sign = label_sign or condition_sign
            simple = (condition or f.name).strip().casefold()
            if sign is None and simple in ('да', 'нет', 'yes', 'no'):
                base = None if re.search(r'\b(требуется|нужно|нужен|необходимо)\b', gateway.name, re.I) else outcome(gateway.name)
                if base:
                    sign = base if simple in ('да', 'yes') else -base
                elif not gateway.name.strip().endswith('?'):
                    report(f'Уточните смысл «Да/Нет» у развилки «{gateway.name}».', [gateway.id], 'warning')
            if sign is None:
                continue
            # Stop at another decision: a later decision can legitimately change the outcome.
            seen, pending = set(), [f.target]
            while pending:
                key = pending.pop()
                if key in seen:
                    continue
                seen.add(key)
                node = nodes[key]
                if node.type.endswith('gateway'):
                    continue
                target_sign = None if node.type != 'end_event' and re.match(r'\s*(провер|оцен|рассмотр)', node.name, re.I) else outcome(node.name)
                if target_sign is not None and target_sign != sign:
                    report(f'Противоречивый исход: ветка «{f.name or condition}» ведёт к «{node.name}» без нового решения.', [gateway.id, key])
                    break
                pending.extend(x.target for x in outgoing[key])
        if len(conditions) != len(set(conditions)):
            report(f'Некорректное ветвление: у «{gateway.name}» повторяются условия разных веток.', [gateway.id])

    # Postdominators: a valid join must occur on every terminating branch path.
    sink = object()
    all_keys = set(nodes) | {sink}
    post = {key: set(all_keys) for key in nodes}
    post[sink] = {sink}
    changed = True
    while changed:
        changed = False
        for key in nodes:
            successors = [f.target for f in outgoing[key]] or [sink]
            value = {key} | set.intersection(*(post[x] for x in successors))
            if value != post[key]:
                post[key] = value
                changed = True

    joins = [n.id for n in p.nodes if n.type == 'parallel_gateway' and len(incoming[n.id]) > 1]
    paired = set()
    def distances(start, stop=None):
        result = {start: 0}
        queue = deque([start])
        while queue:
            key = queue.popleft()
            if key == stop:
                continue
            for f in outgoing[key]:
                if f.target not in result:
                    result[f.target] = result[key] + 1
                    queue.append(f.target)
        return result

    for split in p.nodes:
        branches = outgoing[split.id]
        if split.type != 'parallel_gateway' or len(branches) < 2:
            continue
        reachable = [distances(f.target) for f in branches]
        candidates = [key for key in joins if key != split.id and all(key in post[f.target] for f in branches)]
        if not candidates:
            report(f'Некорректные параллельные ветви: после «{split.name}» нет общего параллельного объединения на всех путях.', [split.id], code='PARALLEL_JOIN_MISMATCH')
            continue
        join = min(candidates, key=lambda key: (sum(r.get(key, len(nodes)) for r in reachable), key))
        paired.add(join)
        reaches = [distances(f.target, join) for f in branches]
        # Each split branch must feed exactly its own join input. Shared inputs,
        # foreign inputs, and extra/missing tokens are rejected in this subset.
        feeds = [{i for i, edge in enumerate(incoming[join]) if edge.id == branch.id or edge.source in reach} for branch, reach in zip(branches, reaches)]
        if len(incoming[join]) != len(branches) or any(len(feed) != 1 for feed in feeds) or len(set.union(*feeds)) != len(branches):
            report('Некорректное параллельное объединение: каждая ветвь должна приходить на отдельный вход своего join.', [split.id, join], code='PARALLEL_JOIN_MISMATCH')
        else:
            # Cycles can prevent eventual arrival; finite-path checks cannot prove termination.
            def has_cycle(reach):
                active, done = set(), set()
                def visit(key):
                    if key == join or key not in reach or key in done:
                        return False
                    if key in active:
                        return True
                    active.add(key)
                    cyclic = any(visit(f.target) for f in outgoing[key])
                    active.remove(key)
                    done.add(key)
                    return cyclic
                return any(visit(key) for key in reach)
            if any(has_cycle(reach) for reach in reaches):
                report('В параллельных ветвях есть цикл. Проверьте, что все ветви действительно завершаются до объединения.', [split.id, join], 'warning')
    for join in joins:
        if join not in paired:
            report(f'Параллельное объединение «{nodes[join].name}» не соответствует разделению ветвей.', [join], code='PARALLEL_JOIN_MISMATCH')
    return issues
