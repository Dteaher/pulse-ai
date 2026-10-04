"""Deterministic BPMN serializer and deliberately bounded single-pool importer."""
from collections import defaultdict, deque
from pathlib import Path
import json
from time import perf_counter
from .telemetry import add_stage
from lxml import etree as E
from .models import ProcessDefinition, Participant, Node, Flow, Assumption
from .validator import validate_process

NS = {'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
      'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
      'dc': 'http://www.omg.org/spec/DD/20100524/DC',
      'di': 'http://www.omg.org/spec/DD/20100524/DI',
      'xsi': 'http://www.w3.org/2001/XMLSchema-instance'}
TAGS = dict(start_event='startEvent', end_event='endEvent', task='task',
            user_task='userTask', service_task='serviceTask', script_task='scriptTask',
            send_task='sendTask', receive_task='receiveTask',
            exclusive_gateway='exclusiveGateway', parallel_gateway='parallelGateway',
            inclusive_gateway='inclusiveGateway', event_based_gateway='eventBasedGateway', intermediate_event='intermediateCatchEvent')
REVERSE = {v: k for k, v in TAGS.items()}
REVERSE['intermediateThrowEvent'] = 'intermediate_event'


def node_tag(node):
    if node.type == 'intermediate_event' and node.event_definition == 'none':
        return 'intermediateThrowEvent'  # §10.5.4, Table 10.89
    return TAGS[node.type]


def gateway_direction(ins, outs):
    if len(ins) > 1 and len(outs) > 1:
        return 'Mixed'  # §10.6.1
    return 'Diverging' if len(outs) > 1 else 'Converging'


def write_condition(element, text):
    # §8.4.6: natural-language conditions are non-executable Expressions.
    expression = child(element, 'bpmn:conditionExpression')
    expression.set(tag('xsi:type'), 'bpmn:tExpression')
    child(expression, 'bpmn:documentation').text = text


def read_condition(element):
    expression = element.find('bpmn:conditionExpression', NS)
    if expression is None:
        return None
    if expression.get('language') or expression.get('evaluatesToTypeRef'):
        raise ValueError('Исполняемые условия доступны только для ручного редактирования и экспорта.')
    expression_type = expression.get(tag('xsi:type'), '').split(':')[-1]
    if expression_type == 'tFormalExpression' and element.getroottree().getroot().get('exporter') != 'PULSE':
        raise ValueError('FormalExpression стороннего BPMN не преобразуется в естественный язык для AI. Ручной экспорт сохраняет XML.')
    if any(E.QName(c).localname != 'documentation' for c in expression):
        raise ValueError('Расширенные условия доступны только для ручного редактирования и экспорта.')
    return expression.findtext('bpmn:documentation', namespaces=NS) or expression.text or None


def tag(name):
    prefix, local = name.split(':')
    return f'{{{NS[prefix]}}}{local}'


def child(parent, qualified_tag, **attrs):
    return E.SubElement(parent, tag(qualified_tag), {k: str(v) for k, v in attrs.items()})


def event_definition(element):
    definitions = [c for c in element if E.QName(c).localname.endswith('EventDefinition')]
    if not definitions:
        if E.QName(element).localname == 'intermediateCatchEvent':
            raise ValueError('None Intermediate Event должен быть Throw Event (§10.5.4).')
        return 'none'
    if E.QName(element).localname == 'intermediateThrowEvent':
        raise ValueError('Throw Message Events пока вне AI subset; ручной экспорт доступен.')
    if len(definitions) != 1 or E.QName(definitions[0]).localname != 'messageEventDefinition' or len(definitions[0]):
        raise ValueError('Это определение события доступно только для ручного редактирования и экспорта.')
    if any(E.QName(k).localname not in {'id', 'messageRef'} for k in definitions[0].attrib):
        raise ValueError('Расширенное определение сообщения не поддерживается для AI.')
    # Message references/correlation are deliberately not silently discarded.
    if definitions[0].get('messageRef'):
        raise ValueError('Ссылки на определения сообщений пока доступны только для ручного редактирования.')
    return 'message'


def write_assumptions(element, process):
    if process.assumptions:
        child(element, 'bpmn:documentation').text = 'PULSE_ASSUMPTIONS:' + json.dumps(
            [a.model_dump() for a in process.assumptions], ensure_ascii=False)


def read_assumptions(element):
    for doc in element.findall('bpmn:documentation', NS):
        if (doc.text or '').startswith('PULSE_ASSUMPTIONS:'):
            data = json.loads(doc.text[len('PULSE_ASSUMPTIONS:'):])
            if not isinstance(data, list) or len(data) > 30:
                raise ValueError('Некорректные допущения BPMN.')
            return [Assumption.model_validate(value) for value in data]
    return []


def write_node_documentation(element, node):
    text = node.source_text
    if node.decision_basis != 'unspecified':
        text = 'PULSE_NODE:' + json.dumps({'source_text': text, 'decision_basis': node.decision_basis}, ensure_ascii=False)
    child(element, 'bpmn:documentation').text = text


def read_node_documentation(element):
    text = element.findtext('bpmn:documentation', default='', namespaces=NS)
    if text.startswith('PULSE_NODE:'):
        metadata = json.loads(text[len('PULSE_NODE:'):])
        return metadata['source_text'], metadata['decision_basis']
    return text, 'unspecified'


def layout(p: ProcessDefinition):
    # DFS identifies feedback edges. Longest-path layering on the remaining DAG
    # keeps joins after every predecessor, while explicit returns remain visible.
    adjacency = defaultdict(list)
    for f in p.flows:
        adjacency[f.source].append(f)
    colors, feedback = {}, set()
    def visit(node):
        colors[node] = 1
        for f in adjacency[node]:
            if colors.get(f.target) == 1:
                feedback.add(f.id)
            elif not colors.get(f.target):
                visit(f.target)
        colors[node] = 2
    for n in p.nodes:
        if n.type == 'start_event':
            visit(n.id)
    for n in p.nodes:
        if not colors.get(n.id):
            visit(n.id)
    forward = [f for f in p.flows if f.id not in feedback]
    indegree = {n.id: 0 for n in p.nodes}
    rank = {n.id: 0 for n in p.nodes}
    links = defaultdict(list)
    for f in forward:
        indegree[f.target] += 1
        links[f.source].append(f.target)
    queue = deque(k for k, v in indegree.items() if v == 0)
    while queue:
        node = queue.popleft()
        for target in links[node]:
            rank[target] = max(rank[target], rank[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    groups = defaultdict(list)
    for n in p.nodes:
        groups[n.participant_id, rank[n.id]].append(n.id)
    lane_heights = {x.id: max(145, max((len(v) for (lane, _), v in groups.items() if lane == x.id), default=1) * 120 + 35) for x in p.participants}
    lane_y, cursor = {}, 70
    for part in p.participants:
        lane_y[part.id] = cursor
        cursor += lane_heights[part.id]
    bounds = {}
    for n in p.nodes:
        width, height = (38, 38) if n.type.endswith('event') else (52, 52) if n.type.endswith('gateway') else (140, 80)
        peers = groups[n.participant_id, rank[n.id]]
        center_y = lane_y[n.participant_id] + lane_heights[n.participant_id] * (peers.index(n.id) + 1) / (len(peers) + 1)
        bounds[n.id] = (190 + rank[n.id] * 190 + (140-width)/2, center_y-height/2, width, height)
    return bounds, lane_y, lane_heights, 330 + max(rank.values()) * 190, cursor - 70


def build_bpmn(p: ProcessDefinition) -> str:
    problems = [i for i in validate_process(p) if i.severity == 'error']
    if p.pools and not problems:
        from .quality import validate_labels
        from .semantics import validate_semantics
        problems += [i for i in validate_semantics(p) if i.severity == 'error']
    if problems:
        raise ValueError('; '.join(i.message for i in problems))
    if p.pools:
        from .collaboration import build_collaboration
        return build_collaboration(p)
    used = {p.id} | {x.id for x in p.nodes + p.flows + p.participants}
    def allocate(prefix):
        candidate, count = prefix, 1
        while candidate in used:
            candidate = f'{prefix}_{count}'
            count += 1
        used.add(candidate)
        return candidate
    root = E.Element(tag('bpmn:definitions'), nsmap=NS, id=allocate('Definitions'), targetNamespace='https://pulse.local/bpmn', exporter='PULSE', exporterVersion='0.1')
    process = child(root, 'bpmn:process', id=p.id, name=p.name, isExecutable='false')
    child(process, 'bpmn:documentation').text = p.description
    write_assumptions(process, p)
    lanes = child(process, 'bpmn:laneSet', id=allocate('LaneSet'))
    for participant in p.participants:
        lane = child(lanes, 'bpmn:lane', id=participant.id, name=participant.name)
        for n in p.nodes:
            if n.participant_id == participant.id:
                child(lane, 'bpmn:flowNodeRef').text = n.id
    for n in p.nodes:
        attrs = {'id': n.id, 'name': n.name}
        outs = [f for f in p.flows if f.source == n.id]
        ins = [f for f in p.flows if f.target == n.id]
        if n.type.endswith('gateway'):
            attrs['gatewayDirection'] = gateway_direction(ins, outs)
        if default := next((f for f in outs if f.is_default), None):
            attrs['default'] = default.id
        element = child(process, 'bpmn:' + node_tag(n), **attrs)
        write_node_documentation(element, n)
        for f in ins:
            child(element, 'bpmn:incoming').text = f.id
        for f in outs:
            child(element, 'bpmn:outgoing').text = f.id
        if n.event_definition == 'message':
            child(element, 'bpmn:messageEventDefinition', id=allocate('MessageEvent_' + n.id))
    for f in p.flows:
        element = child(process, 'bpmn:sequenceFlow', id=f.id, sourceRef=f.source, targetRef=f.target, name=f.name)
        if f.condition and not f.is_default:
            write_condition(element, f.condition)
    collaboration_id, pool_id = allocate('Collaboration'), allocate('Pool')
    collaboration = child(root, 'bpmn:collaboration', id=collaboration_id)
    child(collaboration, 'bpmn:participant', id=pool_id, name=p.name, processRef=p.id)
    diagram = child(root, 'bpmndi:BPMNDiagram', id=allocate('Diagram'))
    plane = child(diagram, 'bpmndi:BPMNPlane', id=allocate('Plane'), bpmnElement=collaboration_id)
    bounds, lane_y, heights, width, height = layout(p)
    def shape(element_id, box, horizontal=False):
        attrs = {'id': allocate('Shape_' + element_id), 'bpmnElement': element_id}
        if horizontal:
            attrs['isHorizontal'] = 'true'
        element = child(plane, 'bpmndi:BPMNShape', **attrs)
        child(element, 'dc:Bounds', **dict(zip(('x', 'y', 'width', 'height'), box)))
    shape(pool_id, (60, 70, width, height), True)
    for participant in p.participants:
        shape(participant.id, (90, lane_y[participant.id], width-30, heights[participant.id]), True)
    for n in p.nodes:
        shape(n.id, bounds[n.id])
    back_count = 0
    for f in p.flows:
        sx, sy, sw, sh = bounds[f.source]
        tx, ty, tw, th = bounds[f.target]
        sc, tc = sy + sh/2, ty + th/2
        if tx > sx:
            mid = sx + sw + 35
            points = [(sx+sw, sc), (tx, tc)] if abs(sc-tc) < 1 else [(sx+sw, sc), (mid, sc), (mid, tc), (tx, tc)]
        else:
            back_count += 1
            # Feedback travels through the top margin of its source lane.
            source_lane = next(n.participant_id for n in p.nodes if n.id == f.source)
            route_y = lane_y[source_lane] + 14 + (back_count % 3) * 10
            points = [(sx+sw/2, sy), (sx+sw/2, route_y), (tx+tw/2, route_y), (tx+tw/2, ty)]
        edge = child(plane, 'bpmndi:BPMNEdge', id=allocate('Edge_' + f.id), bpmnElement=f.id)
        for x, y in points:
            child(edge, 'di:waypoint', x=x, y=y)
        if f.name:
            label = child(edge, 'bpmndi:BPMNLabel')
            x, y = points[0]
            child(label, 'dc:Bounds', x=x+8, y=y-26, width=110, height=22)
    xml = E.tostring(root, encoding='unicode', pretty_print=True)
    validate_xml(xml)
    from .xml_validation import validate_document, require_valid_document
    require_valid_document(validate_document(xml, complete_di=True, check_xsd=False))
    return xml


def parse_xml(xml: str):
    parser = E.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, remove_comments=True, remove_pis=True)
    root = E.fromstring(xml.encode('utf-8'), parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError('DTD в BPMN не поддерживается.')
    return root


_SCHEMA = None
def validate_xml(xml: str):
    global _SCHEMA
    started = perf_counter()
    try:
        if _SCHEMA is None:
            _SCHEMA = E.XMLSchema(E.parse(str(Path(__file__).parent / 'schemas' / 'BPMN20.xsd'), E.XMLParser(resolve_entities=False, no_network=True)))
        _SCHEMA.assertValid(parse_xml(xml))
    finally:
        add_stage('xsd_validation_ms', started)


def import_process(xml: str, previous: ProcessDefinition | None = None):
    # Verify exchange syntax and referential integrity before building dictionaries.
    # Diagram/modeling diagnostics remain available in Doctor and do not discard
    # the original XML snapshot used by exact Undo.
    from .xml_validation import validate_document, require_valid_document
    diagnostics = validate_document(xml)
    integrity_codes = {'INVALID_BPMN_XML', 'DUPLICATE_ID', 'BROKEN_REFERENCE',
                       'INCORRECT_FLOW_NODE_REFERENCE', 'INVALID_LANE_MEMBERSHIP',
                       'CROSS_POOL_SEQUENCE_FLOW', 'INVALID_DEFAULT_REFERENCE'}
    require_valid_document([i for i in diagnostics if i.code in integrity_codes])
    root = parse_xml(xml)
    processes = root.findall('bpmn:process', NS)
    if len(processes) > 1 or (previous and previous.pools) or root.findall('.//bpmn:messageFlow', NS) or any(pool.findtext('bpmn:documentation', default='', namespaces=NS).startswith('PULSE:') for pool in root.findall('.//bpmn:participant', NS)):
        from .collaboration import import_collaboration
        return import_collaboration(root, previous)
    if len(processes) != 1 or root.findall('.//bpmn:messageFlow', NS) or len(root.findall('.//bpmn:participant', NS)) > 1:
        raise ValueError('AI-операции поддерживают один процесс с дорожками. Этот файл можно редактировать и экспортировать на полотне.')
    proc = processes[0]
    if proc.get('isExecutable') == 'true':
        raise ValueError('Исполняемые процессы пока доступны только для ручного редактирования и экспорта.')
    allowed = set(REVERSE) | {'documentation', 'laneSet', 'sequenceFlow'}
    if any(E.QName(c).localname not in allowed for c in proc):
        raise ValueError('В файле есть элементы за пределами текущей Process JSON модели. Ручное редактирование и экспорт доступны.')
    for n in proc:
        if E.QName(n).localname in REVERSE and any(E.QName(c).localname not in {'documentation', 'incoming', 'outgoing', 'messageEventDefinition'} for c in n):
            raise ValueError('События с определениями или расширенные задачи пока доступны только для ручного редактирования.')
        if E.QName(n).localname in REVERSE and any(E.QName(key).localname not in {'id', 'name', 'gatewayDirection', 'default'} for key in n.attrib):
            raise ValueError('Расширенные атрибуты элементов не поддерживаются в AI-модели. Ручное редактирование и экспорт доступны.')
    lane_elements = proc.findall('bpmn:laneSet/bpmn:lane', NS)
    if any(lane.find('bpmn:childLaneSet', NS) is not None for lane in lane_elements):
        raise ValueError('Вложенные дорожки пока не поддерживаются для AI-операций.')
    old_participants = {p.id: p for p in previous.participants} if previous else {}
    participants = [Participant(id=l.get('id'), name=l.get('name') or 'Участник',
                               type=old_participants[l.get('id')].type if l.get('id') in old_participants else 'role') for l in lane_elements]
    existing_ids = {e.get('id') for e in root.iter()}
    unassigned = 'ImportedLane'
    while unassigned in existing_ids:
        unassigned += '_'
    memberships = {}
    for lane in lane_elements:
        for ref in lane.findall('bpmn:flowNodeRef', NS):
            if ref.text in memberships:
                raise ValueError('Узел принадлежит нескольким дорожкам одного LaneSet (§10.7).')
            memberships[ref.text] = lane.get('id')
    old = {n.id: n for n in previous.nodes} if previous else {}
    nodes, flows = [], []
    defaults = {n.get('default') for n in proc if n.get('default')}
    for element in proc:
        kind = E.QName(element).localname
        if kind in REVERSE:
            node_id, name = element.get('id'), element.get('name') or REVERSE[kind]
            definition = event_definition(element)
            source_text, basis = read_node_documentation(element)
            evidence = old.get(node_id)
            unchanged = evidence and evidence.name == name and evidence.type == REVERSE[kind] and evidence.event_definition == definition and evidence.participant_id == memberships.get(node_id, unassigned)
            nodes.append(Node(id=node_id, name=name, type=REVERSE[kind], event_definition=definition, decision_basis=evidence.decision_basis if unchanged else basis, participant_id=memberships.get(node_id, unassigned),
                              source_text=evidence.source_text if unchanged else source_text,
                              confidence=evidence.confidence if unchanged else 'confirmation_required', inferred=evidence.inferred if unchanged else True))
        elif kind == 'sequenceFlow':
            flows.append(Flow(id=element.get('id'), source=element.get('sourceRef'), target=element.get('targetRef'),
                              name=element.get('name') or '', condition=read_condition(element), is_default=element.get('id') in defaults))
    if any(n.participant_id == unassigned for n in nodes):
        participants.append(Participant(id=unassigned, name='Без дорожки'))
    return ProcessDefinition(id=proc.get('id'), name=proc.get('name') or 'Импортированный процесс',
                             description=previous.description if previous else proc.findtext('bpmn:documentation', default='', namespaces=NS),
                             participants=participants, nodes=nodes, flows=flows, ambiguities=previous.ambiguities if previous else [], assumptions=previous.assumptions if previous else read_assumptions(proc))
