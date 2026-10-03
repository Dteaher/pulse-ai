"""Small, deterministic notation refinements over the provider-neutral graph.

Only topology/message evidence permits a change; business decisions, roles and
existing IDs remain intact. Structural faults still reach the strict validator.
"""
import re
from .models import ProcessDefinition, Node, Flow


def is_message_wait(p, gateway):
    if gateway.decision_basis == 'data':
        return False
    if gateway.decision_basis == 'event':
        return True
    nodes = {n.id:n for n in p.nodes}
    outs = [f for f in p.flows if f.source == gateway.id]
    ins = [f for f in p.flows if f.target == gateway.id]
    message_targets = {f.target for f in p.message_flows}
    # A preceding receive already obtained data; its status may be a data decision.
    return len(outs) > 1 and all(f.target in nodes and nodes[f.target].type in ('receive_task', 'intermediate_event') and f.target in message_targets for f in outs) and not any(nodes.get(f.source) and (nodes[f.source].type == 'receive_task' or nodes[f.source].event_definition == 'message') for f in ins)


def polish_process(process: ProcessDefinition) -> ProcessDefinition:
    p = process.model_copy(deep=True)
    nodes = {n.id:n for n in p.nodes}
    used = {p.id, *(x.id for x in p.nodes+p.flows+p.message_flows+p.participants+p.pools)}
    ids=[p.id]+[x.id for x in p.nodes+p.flows+p.message_flows+p.participants+p.pools]
    if len(ids)!=len(set(ids)) or any(f.source not in nodes or f.target not in nodes for f in p.flows+p.message_flows):
        return p  # Do not hide broken references/duplicate IDs during polishing.
    def allocate(base):
        base=base[:90]
        key, index = base, 1
        while key in used:
            key = f'{base}_{index}'; index += 1
        used.add(key); return key
    def merge_before(target, ins):
        merge = Node(id=allocate('Merge_'+target.id), type='exclusive_gateway', name='Объединить пути', participant_id=target.participant_id, decision_basis='data', inferred=True, confidence='confirmation_required')
        p.nodes.append(merge);nodes[merge.id]=merge
        for f in ins:f.target=merge.id
        p.flows.append(Flow(id=allocate('Flow_'+merge.id),source=merge.id,target=target.id))
    for n in list(p.nodes):
        if n.type == 'exclusive_gateway' and is_message_wait(p,n):
            outs=[f for f in p.flows if f.source==n.id]
            # Leave invalid target kinds for a precise validator/corrective error.
            if len(outs)>1 and all(f.target in nodes and (nodes[f.target].type=='receive_task' or (nodes[f.target].type=='intermediate_event' and nodes[f.target].event_definition=='message')) for f in outs):
                n.type='event_based_gateway';n.decision_basis='event'
                for f in outs:f.condition=None;f.is_default=False
                ins=[f for f in p.flows if f.target==n.id]
                if len(ins)>1:merge_before(n,ins)
    # An inferred generic waiting task is redundant before an event wait; keep
    # tasks carrying source evidence or real participant work.
    for gate in list(p.nodes):
        if gate.type!='event_based_gateway':continue
        ins=[f for f in p.flows if f.target==gate.id]
        outs=[f for f in p.flows if f.source==gate.id]
        if len(ins)>1 and len(outs)>1 and gate.decision_basis!='data' and all(not f.condition and not f.is_default and f.target in nodes and (nodes[f.target].type=='receive_task' or (nodes[f.target].type=='intermediate_event' and nodes[f.target].event_definition=='message')) for f in outs):
            merge_before(gate,ins)
            ins=[f for f in p.flows if f.target==gate.id]
        if len(ins)!=1 or ins[0].source not in nodes:continue
        wait=nodes[ins[0].source]
        wait_ins=[f for f in p.flows if f.target==wait.id]
        wait_out=[f for f in p.flows if f.source==wait.id]
        if wait.type=='task' and wait.inferred and not wait.source_text and re.match(r'^(ожидать|ждать|wait)\b',wait.name,re.I) and len(wait_out)==1 and wait_ins and wait.participant_id==gate.participant_id and not any(f.source==wait.id or f.target==wait.id for f in p.message_flows):
            for f in wait_ins:f.target=gate.id
            p.flows.remove(ins[0]);p.nodes.remove(wait);nodes.pop(wait.id)
            for a in p.ambiguities:a.related_node_ids=list(dict.fromkeys(gate.id if i==wait.id else i for i in a.related_node_ids))
            if len(wait_ins)>1:merge_before(gate,wait_ins)
    def first_send(key):
        seen=set()
        while key in nodes and key not in seen:
            seen.add(key);n=nodes[key]
            if n.type=='start_event':return True
            if n.type.endswith('gateway'):return False
            ins=[f for f in p.flows if f.target==key]
            if len(ins)!=1:return False
            key=ins[0].source
        return False
    for start in list(p.nodes):
        if start.type!='start_event' or start.event_definition!='none':continue
        messages=[f for f in p.message_flows if f.target==start.id]
        if messages:
            start.event_definition='message'
            continue
        outs=[f for f in p.flows if f.source==start.id]
        if len(outs)!=1 or outs[0].target not in nodes:continue
        receiver=nodes[outs[0].target]
        successors=[f for f in p.flows if f.source==receiver.id]
        messages=[f for f in p.message_flows if f.target==receiver.id]
        other_inputs=[f for f in p.flows if f.target==receiver.id and f.source!=start.id]
        initial=[f for f in messages if first_send(f.source)]
        if not other_inputs and messages:initial=messages[:1]
        if receiver.type!='receive_task' or receiver.participant_id!=start.participant_id or len(successors)!=1 or len(initial)!=1:continue
        start.event_definition='message'
        start.name='Получено сообщение'+(': '+initial[0].name if initial[0].name else '')
        start.source_text=receiver.source_text or start.source_text
        initial[0].target=start.id
        if not other_inputs:
            for message in messages:message.target=start.id
        outs[0].target=successors[0].target
        if not any(f.target==receiver.id for f in p.flows):
            p.nodes.remove(receiver);p.flows.remove(successors[0]);nodes.pop(receiver.id)
            for ambiguity in p.ambiguities:
                ambiguity.related_node_ids=list(dict.fromkeys(start.id if i==receiver.id else i for i in ambiguity.related_node_ids))
    # Explicitly merge alternative data branches before their common action.
    for n in list(p.nodes):
        ins=[f for f in p.flows if f.target==n.id]
        if n.type.endswith('task') and len(ins)>1 and all(nodes.get(f.source) and nodes[f.source].type=='exclusive_gateway' and f.condition for f in ins):
            merge_before(n,ins)
    return p
