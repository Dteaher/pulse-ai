"""Bounded collaboration serialization/import and horizontal multi-pool DI.

Processes remain independent control-flow graphs. Messages affect presentation
alignment only, never graph reachability or incoming/outgoing sequence refs.
"""
import json
from collections import defaultdict, deque
from lxml import etree as E
from .models import ProcessDefinition, Participant, Pool, Node, Flow, MessageFlow
from .bpmn import NS, TAGS, REVERSE, tag, child, layout, validate_xml, event_definition, write_node_documentation, read_node_documentation, write_assumptions, read_assumptions
from .bpmn import node_tag, gateway_direction, write_condition, read_condition


def build_collaboration(p: ProcessDefinition) -> str:
    used = {p.id, *(x.id for x in p.pools + p.participants + p.nodes + p.flows + p.message_flows)}
    def allocate(prefix):
        key, count = prefix, 1
        while key in used:
            key = f'{prefix}_{count}'; count += 1
        used.add(key)
        return key
    roles = {r.id: r for r in p.participants}
    membership = {n.id: roles[n.participant_id].pool_id for n in p.nodes}
    process_ids = {pool.id: allocate('Process_' + pool.id) for pool in p.pools}
    root = E.Element(tag('bpmn:definitions'), nsmap=NS, id=allocate('Definitions'), targetNamespace='https://pulse.local/bpmn', exporter='PULSE', exporterVersion='0.2')
    ordered = sorted(p.pools, key=lambda pool: pool.kind != 'external')
    for pool in ordered:
        proc = child(root, 'bpmn:process', id=process_ids[pool.id], name=pool.name, isExecutable='false')
        if pool.kind == 'internal':
            lane_set = child(proc, 'bpmn:laneSet', id=allocate('LaneSet'))
            for role in p.participants:
                if role.pool_id != pool.id: continue
                lane = child(lane_set, 'bpmn:lane', id=role.id, name=role.name)
                for n in p.nodes:
                    if n.participant_id == role.id: child(lane, 'bpmn:flowNodeRef').text = n.id
        for n in p.nodes:
            if membership[n.id] != pool.id: continue
            outs = [f for f in p.flows if f.source == n.id]
            ins = [f for f in p.flows if f.target == n.id]
            attrs = dict(id=n.id, name=n.name)
            if n.type.endswith('gateway'): attrs['gatewayDirection'] = gateway_direction(ins, outs)
            default = next((f for f in outs if f.is_default), None)
            if default: attrs['default'] = default.id
            element = child(proc, 'bpmn:' + node_tag(n), **attrs)
            write_node_documentation(element, n)
            for f in ins: child(element, 'bpmn:incoming').text = f.id
            for f in outs: child(element, 'bpmn:outgoing').text = f.id
            if n.event_definition == 'message': child(element, 'bpmn:messageEventDefinition', id=allocate('MessageEvent_'+n.id))
        for f in p.flows:
            if membership[f.source] != pool.id: continue
            element = child(proc, 'bpmn:sequenceFlow', id=f.id, sourceRef=f.source, targetRef=f.target, name=f.name)
            if f.condition and not f.is_default:
                write_condition(element, f.condition)
    collab = child(root, 'bpmn:collaboration', id=p.id, name=p.name)
    child(collab, 'bpmn:documentation').text = p.description
    write_assumptions(collab, p)
    for pool in ordered:
        element = child(collab, 'bpmn:participant', id=pool.id, name=pool.name, processRef=process_ids[pool.id])
        child(element, 'bpmn:documentation').text = 'PULSE:' + json.dumps({'kind': pool.kind, 'roles': [r.model_dump() for r in p.participants if r.pool_id == pool.id]}, ensure_ascii=False)
    for f in p.message_flows:
        child(collab, 'bpmn:messageFlow', id=f.id, sourceRef=f.source, targetRef=f.target, name=f.name)
    diagram = child(root, 'bpmndi:BPMNDiagram', id=allocate('Diagram'))
    plane = child(diagram, 'bpmndi:BPMNPlane', id=allocate('Plane'), bpmnElement=p.id)
    bounds, lane_y, heights, pool_boxes = collaboration_layout(p, ordered)
    def shape(key, box, horizontal=False):
        attrs = dict(id=allocate('Shape_' + key), bpmnElement=key)
        if horizontal: attrs['isHorizontal'] = 'true'
        element = child(plane, 'bpmndi:BPMNShape', **attrs)
        child(element, 'dc:Bounds', **dict(zip(('x', 'y', 'width', 'height'), box)))
    for pool in ordered:
        shape(pool.id, pool_boxes[pool.id], True)
        if pool.kind == 'internal':
            for r in p.participants:
                if r.pool_id == pool.id:
                    shape(r.id, (90, lane_y[r.id], pool_boxes[pool.id][2]-30, heights[r.id]), True)
    for n in p.nodes: shape(n.id, bounds[n.id])
    returns = defaultdict(int)
    labels = []
    def overlaps(a,b):
        x,y,w,h=a;tx,ty,tw,th=b
        return x<tx+tw and tx<x+w and y<ty+th and ty<y+h
    def place_label(x,y,role,message=False):
        # Lane/gap corridors keep label rectangles outside activities. Pick the
        # closest free position; do not stack all branch labels at the source.
        ys = [y] if message else [y,lane_y[role]+5,lane_y[role]+40]
        candidates = [(x+dx,cy,130,30) for cy in ys for dx in (0,145,-145,290,-290,435,-435)]
        for box in candidates:
            if box[0]<185:continue
            if not any(overlaps(box,b) for b in list(bounds.values())+labels):
                labels.append(box);return box
        # Crowded labels may use a further free corridor position, without
        # moving tasks or changing business topology.
        cx=185
        cy=ys[-1]
        while any(overlaps((cx,cy,130,30),b) for b in list(bounds.values())+labels):cx+=145
        box=(cx,cy,130,30);labels.append(box);return box
    node_roles = {n.id:n.participant_id for n in p.nodes}
    for f in p.flows + p.message_flows:
        sx, sy, sw, sh = bounds[f.source]; tx, ty, tw, th = bounds[f.target]
        message = isinstance(f, MessageFlow)
        if message:
            # Route outside node columns, through the pool gap. Short exchanges
            # remain nearly vertical; feedback messages use a separate corridor.
            down = ty > sy
            source_y, target_y = (sy+sh, ty) if down else (sy, ty+th)
            sc, tc = sx+sw/2, tx+tw/2
            source_box = pool_boxes[membership[f.source]]
            gap_y = source_box[1] + source_box[3] + 25 if down else source_box[1] - 25
            def column_gap(x):return 190+int((x-190)//190)*190+140+28
            source_x,target_x=column_gap(sx),column_gap(tx)
            role=node_roles[f.target]
            target_corridor=lane_y[role]+14 if down else lane_y[role]+heights[role]-14
            points=[(sx+sw,sy+sh/2),(source_x,sy+sh/2),(source_x,gap_y),(target_x,gap_y),(target_x,target_corridor),(tc,target_corridor),(tc,target_y)]
        elif tx > sx and node_roles[f.source] == node_roles[f.target] and tx-sx > 260:
            role = node_roles[f.source]
            returns[role] += 1
            route_y = lane_y[role] + 14 + returns[role]*12
            points = [(sx+sw,sy+sh/2),(sx+sw+18,sy+sh/2),(sx+sw+18,route_y),(tx-18,route_y),(tx-18,ty+th/2),(tx,ty+th/2)]
        elif tx > sx:
            sc, tc = sy+sh/2, ty+th/2
            mid = sx+sw+35
            points = [(sx+sw, sc), (tx, tc)] if abs(sc-tc)<1 else [(sx+sw, sc), (mid, sc), (mid, tc), (tx, tc)]
        else:
            role = next(n.participant_id for n in p.nodes if n.id == f.source)
            returns[role] += 1
            route_y = lane_y[role] + 14 + returns[role]*12
            points = [(sx+sw/2,sy), (sx+sw/2,route_y), (tx+tw/2,route_y), (tx+tw/2,ty)]
        edge = child(plane, 'bpmndi:BPMNEdge', id=allocate('Edge_'+f.id), bpmnElement=f.id)
        for x,y in points: child(edge, 'di:waypoint', x=x,y=y)
        if f.name:
            label = child(edge, 'bpmndi:BPMNLabel')
            segments=[(a,b) for a,b in zip(points,points[1:]) if abs(a[1]-b[1])<1 and abs(a[0]-b[0])>=145]
            if message:
                x,y=points[0][0]+8,gap_y-18
            elif segments:
                a,b=max(segments,key=lambda s:abs(s[0][0]-s[1][0]))
                x,y=(a[0]+b[0])/2-65,a[1]-30
            else:
                x,y=points[0][0]+8,points[0][1]-40
            if not message:
                x,y=x,max(y,lane_y[node_roles[f.source]]+5)
            box=place_label(x,y,node_roles[f.source],message)
            child(label, 'dc:Bounds', **dict(zip(('x','y','width','height'),box)))
    xml = E.tostring(root, encoding='unicode', pretty_print=True)
    validate_xml(xml)
    from .xml_validation import validate_document, require_valid_document
    require_valid_document(validate_document(xml, complete_di=True, check_xsd=False))
    return xml


def collaboration_layout(p, ordered):
    ordered_roles = [r for pool in ordered for r in p.participants if r.pool_id == pool.id]
    # Identify each process's feedback first. Otherwise a customer's revision
    # loop can push the company's initial check many columns to the right.
    presentation = p.model_copy(update={'participants': ordered_roles})
    bounds, lane_y, heights, width, _ = layout(presentation)
    ranks = {key: round((box[0]-190-(140-box[2])/2)/190) for key,box in bounds.items()}
    links = defaultdict(list)
    node_types = {n.id:n.type for n in p.nodes}
    for f in p.flows:
        if ranks[f.target] > ranks[f.source]:
            # Reserve label room on conditional branches rather than placing
            # condition text on top of the next task.
            step = 1  # Labels use reserved corridors instead of extra columns.
            links[f.source].append((f.target,step))
    for f in p.message_flows: links[f.source].append((f.target,0))
    colors, feedback = {}, set()
    def visit(key):
        colors[key]=1
        for target,_ in links[key]:
            if colors.get(target)==1: feedback.add((key,target))
            elif not colors.get(target): visit(target)
        colors[key]=2
    for n in p.nodes:
        if not colors.get(n.id): visit(n.id)
    indegree={n.id:0 for n in p.nodes}
    for key,edges in links.items():
        for target,_ in edges:
            if (key,target) not in feedback: indegree[target]+=1
    queue=deque(key for key,value in indegree.items() if not value)
    aligned={n.id:0 for n in p.nodes}
    while queue:
        key=queue.popleft()
        for target,step in links[key]:
            if (key,target) in feedback: continue
            aligned[target]=max(aligned[target],aligned[key]+step)
            indegree[target]-=1
            if not indegree[target]: queue.append(target)
    # Re-group nodes sharing a column in one lane (e.g. contract/refusal ends).
    groups=defaultdict(list)
    for n in p.nodes: groups[n.participant_id,aligned[n.id]].append(n.id)
    node_roles={n.id:n.participant_id for n in p.nodes}
    margins={r.id:max(65,26+12*sum(node_roles[f.source]==r.id and (aligned[f.target]<=aligned[f.source] or aligned[f.target]-aligned[f.source]>1) for f in p.flows)) for r in ordered_roles}
    for r in ordered_roles:
        heights[r.id]=max(155,max((len(peers) for (role,_),peers in groups.items() if role==r.id),default=1)*120+35)+margins[r.id]
    for n in p.nodes:
        _,_,w,h=bounds[n.id]; peers=groups[n.participant_id,aligned[n.id]]
        margin=margins[n.participant_id]
        center=margin+(heights[n.participant_id]-margin)*(peers.index(n.id)+1)/(len(peers)+1)
        bounds[n.id]=(190+aligned[n.id]*190+(140-w)/2,lane_y[n.participant_id]+center-h/2,w,h)
    width=330+max(aligned.values())*190
    pool_boxes, cursor = {}, 70
    for pool in ordered:
        roles = [r for r in ordered_roles if r.pool_id == pool.id]
        top = cursor
        for r in roles:
            delta = cursor - lane_y[r.id]
            for n in p.nodes:
                if n.participant_id == r.id:
                    x,y,w,h = bounds[n.id]; bounds[n.id] = (x,y+delta,w,h)
            lane_y[r.id] = cursor
            cursor += heights[r.id]
        pool_boxes[pool.id] = (60,top,width,cursor-top)
        cursor += 100
    return bounds,lane_y,heights,pool_boxes


def import_collaboration(root, previous=None):
    collabs = root.findall('bpmn:collaboration',NS)
    if len(collabs)!=1: raise ValueError('Поддерживается одна collaboration с раскрытыми Pool.')
    collab = collabs[0]
    if any(E.QName(c).localname not in {'documentation','participant','messageFlow'} for c in collab):
        raise ValueError('Расширенные взаимодействия доступны только для ручного редактирования и экспорта.')
    processes = {proc.get('id'):proc for proc in root.findall('bpmn:process',NS)}
    pools, roles, nodes, flows, messages = [],[],[],[],[]
    existing_ids = {el.get('id') for el in root.iter()}
    def fresh(prefix):
        while prefix in existing_ids: prefix += '_'
        existing_ids.add(prefix); return prefix
    old_nodes = {n.id:n for n in previous.nodes} if previous else {}
    seen_processes = set()
    for pool_el in collab.findall('bpmn:participant',NS):
        if any(E.QName(c).localname != 'documentation' for c in pool_el) or any(E.QName(k).localname not in {'id','name','processRef'} for k in pool_el.attrib):
            raise ValueError('Расширенные Pool доступны только для ручного редактирования и экспорта.')
        proc = processes.get(pool_el.get('processRef'))
        if proc is None or proc.get('id') in seen_processes:
            raise ValueError('Пустые или общие процессы Pool не поддерживаются для AI; ручной экспорт доступен.')
        seen_processes.add(proc.get('id'))
        if proc.get('isExecutable') == 'true': raise ValueError('Исполняемые процессы доступны только для ручного редактирования.')
        documentation = pool_el.findtext('bpmn:documentation',default='',namespaces=NS)
        metadata = json.loads(documentation[6:]) if documentation.startswith('PULSE:') else {}
        pool = Pool(id=pool_el.get('id'),name=pool_el.get('name') or 'Участник',kind=metadata.get('kind','internal'))
        pools.append(pool)
        lane_els = proc.findall('bpmn:laneSet/bpmn:lane',NS)
        old_roles = {r.id:r for r in previous.participants} if previous else {}
        meta_roles = {r['id']:r for r in metadata.get('roles',[])}
        memberships = {}
        for lane in lane_els:
            if lane.find('bpmn:childLaneSet',NS) is not None: raise ValueError('Вложенные дорожки не поддерживаются для AI.')
            key = lane.get('id')
            info = meta_roles.get(key) or (old_roles[key].model_dump() if key in old_roles else {})
            roles.append(Participant(id=key,name=lane.get('name') or 'Участник',type=info.get('type','role'),kind=pool.kind,pool_id=pool.id))
            for ref in lane.findall('bpmn:flowNodeRef',NS):
                if ref.text in memberships: raise ValueError('Узел принадлежит нескольким дорожкам.')
                memberships[ref.text]=key
        if not lane_els:
            info = next(iter(meta_roles.values()),{})
            role = Participant(id=info.get('id') or fresh('Role_'+pool.id),name=pool.name,type=info.get('type','external' if pool.kind=='external' else 'role'),kind=pool.kind,pool_id=pool.id)
            roles.append(role)
            memberships = {el.get('id'):role.id for el in proc if E.QName(el).localname in REVERSE}
        defaults = {el.get('default') for el in proc if el.get('default')}
        for el in proc:
            kind = E.QName(el).localname
            if kind in REVERSE:
                if any(E.QName(c).localname not in {'documentation','incoming','outgoing','messageEventDefinition'} for c in el) or any(E.QName(k).localname not in {'id','name','gatewayDirection','default'} for k in el.attrib):
                    raise ValueError('Расширенные задачи и события доступны только для ручного редактирования.')
                key = el.get('id'); name = el.get('name') or REVERSE[kind]
                definition = event_definition(el)
                source_text, basis = read_node_documentation(el)
                if key not in memberships: raise ValueError('Узел не принадлежит дорожке своего Pool.')
                old = old_nodes.get(key)
                same = old and old.name==name and old.type==REVERSE[kind] and old.event_definition==definition and old.participant_id==memberships[key]
                nodes.append(Node(id=key,name=name,type=REVERSE[kind],event_definition=definition,decision_basis=old.decision_basis if same else basis,participant_id=memberships[key],source_text=old.source_text if same else source_text,confidence=old.confidence if same else 'confirmation_required',inferred=old.inferred if same else True))
            elif kind=='sequenceFlow':
                flows.append(Flow(id=el.get('id'),source=el.get('sourceRef'),target=el.get('targetRef'),name=el.get('name') or '',condition=read_condition(el),is_default=el.get('id') in defaults))
            elif kind not in {'laneSet','documentation'}: raise ValueError('Неподдерживаемые элементы процесса; ручной экспорт доступен.')
    if set(processes)!=seen_processes: raise ValueError('Процесс не связан с Pool.')
    for el in collab.findall('bpmn:messageFlow',NS):
        if len(el) or any(E.QName(k).localname not in {'id','name','sourceRef','targetRef'} for k in el.attrib):
            raise ValueError('Расширенные сообщения доступны только для ручного редактирования и экспорта.')
        messages.append(MessageFlow(id=el.get('id'),source=el.get('sourceRef'),target=el.get('targetRef'),name=el.get('name') or ''))
    p = ProcessDefinition(id=collab.get('id'),name=collab.get('name') or (previous.name if previous else 'Взаимодействие участников'),description=previous.description if previous else collab.findtext('bpmn:documentation',default='',namespaces=NS),participants=roles,pools=pools,nodes=nodes,flows=flows,message_flows=messages,ambiguities=previous.ambiguities if previous else [], assumptions=previous.assumptions if previous else read_assumptions(collab))
    if previous:
        for collection in ('pools','participants','nodes','flows','message_flows'):
            order={item.id:index for index,item in enumerate(getattr(previous,collection))}
            getattr(p,collection).sort(key=lambda item:order.get(item.id,len(order)))
    from .validator import validate_process
    problems = [i for i in validate_process(p) if i.severity == 'error']
    if problems:
        from .diagnostics import ProcessValidationError
        raise ProcessValidationError(problems)
    return p
