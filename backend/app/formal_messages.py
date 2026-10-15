"""Opt-in formal message declarations derived only from existing MessageFlow.
No payload schema, integration or correlation is inferred. PULSE-owned bindings
can be regenerated deterministically from the canonical collaboration.
"""
from collections import defaultdict
from lxml import etree as E
from .bpmn import NS, parse_xml, child

MARKER = 'PULSE:derived-message-flow:'


def add_formal_messages(xml):
    root = parse_xml(xml)
    used = {e.get('id') for e in root.iter() if e.get('id')}
    endpoints = defaultdict(list)
    for flow in sorted(root.findall('.//bpmn:messageFlow', NS), key=lambda f: f.get('id')):
        base = 'Message_' + flow.get('id')
        key, i = base, 1
        while key in used:
            key = f'{base}_{i}'; i += 1
        used.add(key)
        message = E.Element('{'+NS['bpmn']+'}message', id=key, name=flow.get('name', ''))
        child(message, 'bpmn:documentation').text = MARKER + flow.get('id')
        diagram = root.find('bpmndi:BPMNDiagram', NS)
        root.insert(root.index(diagram) if diagram is not None else len(root), message)
        flow.set('messageRef', key)
        endpoints[flow.get('sourceRef')].append(key)
        endpoints[flow.get('targetRef')].append(key)
    for element in root.iter():
        refs = endpoints.get(element.get('id'), [])
        if len(set(refs)) != 1:
            continue  # A single activity cannot invent one message for distinct flows.
        event = element.find('bpmn:messageEventDefinition', NS)
        if event is not None:
            event.set('messageRef', refs[0])
        elif E.QName(element).localname in {'sendTask', 'receiveTask'}:
            element.set('messageRef', refs[0])
    from .xml_validation import validate_document, require_valid_document
    result = E.tostring(root, encoding='unicode', pretty_print=True)
    require_valid_document(validate_document(result, complete_di=True))
    return result


def strip_derived_messages(root):
    """Recognize only our losslessly regenerable declarations; reject extensions."""
    messages = root.findall('bpmn:message', NS)
    if not messages:
        return
    if root.get('exporter') != 'PULSE':
        raise ValueError('Внешние формальные сообщения остаются в ручном XML.')
    flows = {f.get('id'): f for f in root.findall('.//bpmn:messageFlow', NS)}
    refs = {}
    used = {e.get('id') for e in root.iter() if e.get('id')} - {m.get('id') for m in messages}
    expected_ids = {}
    for flow in sorted(flows.values(), key=lambda f: f.get('id')):
        base = 'Message_' + flow.get('id'); key, i = base, 1
        while key in used:
            key = f'{base}_{i}'; i += 1
        used.add(key); expected_ids[flow.get('id')] = key
    for message in messages:
        marker = message.findtext('bpmn:documentation', namespaces=NS) or ''
        if not marker.startswith(MARKER) or set(message.attrib)-{'id','name'} or any(E.QName(c).localname != 'documentation' for c in message):
            raise ValueError('Расширенные сообщения не преобразуются в AI subset.')
        flow = flows.get(marker[len(MARKER):])
        if flow is None or expected_ids.get(flow.get('id')) != message.get('id') or flow.get('messageRef') != message.get('id') or message.get('name','') != flow.get('name',''):
            raise ValueError('Определение сообщения не соответствует MessageFlow.')
        refs[message.get('id')] = {flow.get('sourceRef'), flow.get('targetRef')}
    for element in root.iter():
        ref = element.get('messageRef')
        if ref:
            owner = element.getparent().get('id') if E.QName(element).localname=='messageEventDefinition' else element.get('id')
            if ref not in refs or (E.QName(element).localname != 'messageFlow' and owner not in refs[ref]):
                raise ValueError('Связь сообщения не может быть восстановлена из MessageFlow.')
            element.attrib.pop('messageRef')
    for message in messages:
        root.remove(message)
