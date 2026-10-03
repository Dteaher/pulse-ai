"""XML exchange checks for PULSE's non-executable Process/Collaboration subset.

XSD checks syntax/types, not QName integrity or diagram completeness. Partial DI
is legal BPMN; complete_di is an additional PULSE export quality requirement.
No token simulation or execution-language validation is claimed here.
"""
import logging
import math
from collections import Counter
from time import perf_counter
from lxml import etree as E
from .models import Issue
from .bpmn import NS, REVERSE, parse_xml, validate_xml

logger = logging.getLogger('pulse.validation')
FLOW_NODES = set(REVERSE)
ACTIVITIES = {x for x in FLOW_NODES if x.endswith('Task') or x == 'task'}
EVENTS = {x for x in FLOW_NODES if x.endswith('Event')}


def local(element):
    return E.QName(element).localname


def validate_xsd(xml: str) -> list[Issue]:
    started = perf_counter()
    try:
        validate_xml(xml)
        return []
    except (E.LxmlError, ValueError) as exc:
        logger.debug('bpmn_xsd_failed details=%s', str(exc)[:2000])
        entries = list(exc.error_log) if isinstance(exc, E.LxmlError) else []
        return [Issue(severity='error', source='XSD', code='INVALID_BPMN_XML',
                      spec_section='§15.3.2', message='BPMN XML не соответствует XML-схеме стандарта.',
                      line=entry.line, column=entry.column) for entry in entries[-20:]] or [
            Issue(severity='error', source='XSD', code='INVALID_BPMN_XML',
                  spec_section='§15.3.2', message='Не удалось прочитать или проверить BPMN XML.')]
    finally:
        logger.debug('bpmn_xsd duration_ms=%.1f', (perf_counter() - started) * 1000)


def validate_document(xml: str, *, complete_di=False, check_xsd=True) -> list[Issue]:
    issues = validate_xsd(xml) if check_xsd else []
    if issues:
        return issues
    try:
        root = parse_xml(xml)
    except (E.LxmlError, ValueError) as exc:
        return [Issue(severity='error', source='XSD', code='INVALID_BPMN_XML',
                      message='Не удалось прочитать или проверить BPMN XML.', spec_section='§15.3.2')]
    elements = [el for el in root.iter() if isinstance(el.tag, str)]
    index = {el.get('id'): el for el in elements if el.get('id')}
    # Resolve local references even when a document has constructs outside our
    # semantic subset. XSD QName validity alone does not establish referential integrity.
    reference_attributes = {'sourceRef', 'targetRef', 'processRef', 'bpmnElement',
                            'messageRef', 'default', 'participantRef'}
    reference_text = {'flowNodeRef', 'eventDefinitionRef', 'incoming', 'outgoing'}
    for el in elements:
        refs = [(E.QName(key).localname, value) for key, value in el.attrib.items() if E.QName(key).localname in reference_attributes]
        if local(el) in reference_text and el.text:
            refs.append((local(el), el.text.strip()))
        for ref_kind, value in refs:
            if ':' in value:
                prefix, identifier = value.split(':', 1)
                if el.nsmap.get(prefix) != root.get('targetNamespace'):
                    continue  # External namespaces are not local resources.
            else:
                identifier = value
            target = index.get(identifier)
            expected = {'messageRef': {'message'}, 'processRef': {'process'},
                        'participantRef': {'participant'}, 'default': {'sequenceFlow'},
                        'incoming': {'sequenceFlow'}, 'outgoing': {'sequenceFlow'}}.get(ref_kind)
            wrong_type = target is not None and (
                (expected is not None and local(target) not in expected) or
                (ref_kind == 'eventDefinitionRef' and not local(target).endswith('EventDefinition')))
            if target is None or wrong_type:
                key = el.get('id')
                issues.append(Issue(code='INVALID_DI_REFERENCE' if el.get('bpmnElement') == value else 'BROKEN_REFERENCE', severity='error', source='BPMN_SPEC',
                    line=el.sourceline, type='INTEROPERABILITY_ERROR' if el.get('bpmnElement') == value else 'SPEC_ERROR', spec_section='§15.3.2', node_ids=[key] if key else [],
                    message='Ссылка отсутствует в документе или указывает на элемент неподходящего типа.',
                    suggested_fix='Восстановите ссылку или удалите ссылающийся элемент.'))
    duplicates = [key for key, count in Counter(el.get('id') for el in elements if el.get('id')).items() if count > 1]
    for key in duplicates:
        issues.append(Issue(code='DUPLICATE_ID', severity='error', source='BPMN_SPEC',
            message='ID повторяется в XML.', spec_section='§8.3.1; §15.3.2',
            line=index[key].sourceline, node_ids=[key]))
    if issues:
        return issues
    unsupported = [el for process in root.findall('bpmn:process', NS) for el in process
                   if local(el) not in FLOW_NODES | {'documentation', 'laneSet', 'sequenceFlow'}]
    unsupported += [el for el in elements if E.QName(el).namespace == NS['bpmn'] and local(el).endswith('EventDefinition') and local(el) != 'messageEventDefinition']
    unsupported += [el for el in elements if E.QName(el).namespace == NS['bpmn'] and (
        local(el) in {'childLaneSet', 'standardLoopCharacteristics', 'multiInstanceLoopCharacteristics'} or
        (local(el) == 'messageEventDefinition' and el.get('messageRef')) or
        (local(el) == 'participant' and not el.get('processRef')) or
        (local(el) == 'eventBasedGateway' and (el.get('instantiate') in {'true','1'} or el.get('eventGatewayType') == 'Parallel')))]
    if unsupported:
        return [Issue(type='INTEROPERABILITY_ERROR', category='unsupported_subset', severity='info', source='MODEL_QUALITY', code='OUTSIDE_PULSE_SUBSET',
                      message='XSD проверена; расширенная семантика этого BPMN вне subset PULSE. Ручной экспорт сохраняет исходный XML.',
                      node_ids=[el.get('id') for el in unsupported if el.get('id')][:150])]

    def add(code, message, element, section, source='BPMN_SPEC'):
        key = element.get('id')
        issues.append(Issue(code=code, severity='error', source=source,
                            message=message, spec_section=section, line=element.sourceline,
                            node_id=key, node_ids=[key] if key else []))

    # §15.3.2: local QName references may use a prefix bound to targetNamespace.
    def resolve(value, context):
        if not value:
            return None
        value = value.strip()
        if ':' in value:
            prefix, value = value.split(':', 1)
            if context.nsmap.get(prefix) != root.get('targetNamespace'):
                return None  # External resources are outside this local exchange subset.
        return index.get(value)

    def ref(value, context, kinds, code='BROKEN_REFERENCE'):
        target = resolve(value, context)
        if target is None or E.QName(target).namespace != NS['bpmn'] or local(target) not in kinds:
            add(code, f'Ссылка {value or "(пусто)"} отсутствует или имеет недопустимый тип.', context, '§15.3.2')
            return None
        return target

    def process_of(el):
        return next((a for a in el.iterancestors() if a.tag == f"{{{NS['bpmn']}}}process"), None)

    process_pools = {}
    for pool in root.findall('.//bpmn:participant', NS):
        if pool.get('processRef'):
            process = ref(pool.get('processRef'), pool, {'process'})
            if process is not None:
                process_pools[process.get('id')] = pool.get('id')

    flows = root.findall('.//bpmn:sequenceFlow', NS)
    ins, outs = {}, {}
    for flow in flows:
        source = ref(flow.get('sourceRef'), flow, FLOW_NODES)
        target = ref(flow.get('targetRef'), flow, FLOW_NODES)
        if source is None or target is None:
            continue
        if process_of(source) is not process_of(target) or process_of(flow) is not process_of(source):
            add('CROSS_POOL_SEQUENCE_FLOW', 'Sequence Flow должен оставаться в одном Process.', flow, '§8.4.13; §9.3')
        ins.setdefault(target.get('id'), []).append(flow)
        outs.setdefault(source.get('id'), []).append(flow)
        if local(target) == 'startEvent':
            add('START_HAS_INCOMING', 'Start Event не имеет входящих Sequence Flow.', target, '§10.5.2')
        if local(source) == 'endEvent':
            add('END_HAS_OUTGOING', 'End Event не имеет исходящих Sequence Flow.', source, '§10.5.3')
        condition = flow.find('bpmn:conditionExpression', NS)
        if condition is not None:
            if local(source) not in ACTIVITIES | {'exclusiveGateway', 'inclusiveGateway'}:
                add('INVALID_CONDITION_SOURCE', 'Этот источник не допускает conditionExpression.', flow, '§8.4.13')
            if source.get('default') == flow.get('id'):
                # BPMN ignores a condition on default, so report quality, not invalid XML.
                issues.append(Issue(severity='warning', source='MODEL_QUALITY', code='DEFAULT_CONDITION_IGNORED',
                                    message='Условие default flow игнорируется.', node_ids=[flow.get('id')], spec_section='§8.4.13'))

    for element in elements:
        if E.QName(element).namespace != NS['bpmn'] or local(element) not in FLOW_NODES:
            continue
        key, kind = element.get('id'), local(element)
        incoming, outgoing = ins.get(key, []), outs.get(key, [])
        for direction, expected in [('incoming', incoming), ('outgoing', outgoing)]:
            for declared in element.findall('bpmn:' + direction, NS):
                flow = ref(declared.text, declared, {'sequenceFlow'})
                if flow is not None and flow not in expected:
                    add('INCORRECT_FLOW_NODE_REFERENCE', 'incoming/outgoing не соответствует концам Sequence Flow.', element, '§8.4.13')
        if element.get('default'):
            flow = ref(element.get('default'), element, {'sequenceFlow'})
            if kind not in ACTIVITIES | {'exclusiveGateway', 'inclusiveGateway'} or (flow is not None and flow not in outgoing):
                add('INVALID_DEFAULT_REFERENCE', 'default должен ссылаться на исходящий переход допустимого источника.', element, '§8.4.13')
        if kind in ACTIVITIES and len(outgoing) == 1 and outgoing[0].find('bpmn:conditionExpression', NS) is not None:
            add('CONDITIONAL_ACTIVITY_SINGLE_FLOW', 'Activity с Conditional Flow требует другой исходящий переход.', element, '§8.4.13')
        if kind.endswith('Gateway'):
            direction = element.get('gatewayDirection', 'Unspecified')
            if (direction == 'Diverging' and len(incoming) > 1) or (direction == 'Converging' and len(outgoing) > 1) or (direction == 'Mixed' and (len(incoming) < 2 or len(outgoing) < 2)):
                add('INVALID_GATEWAY_DIRECTION', 'gatewayDirection противоречит числу входов/выходов.', element, '§10.6.1')
        if kind == 'eventBasedGateway':
            targets = [resolve(f.get('targetRef'), f) for f in outgoing]
            valid = len(outgoing) >= 2 and all(t is not None and (local(t) == 'receiveTask' or (local(t) == 'intermediateCatchEvent' and t.find('bpmn:messageEventDefinition', NS) is not None)) for t in targets)
            if not valid:
                add('INVALID_EVENT_GATEWAY_SUCCESSOR', 'В subset Event-Based Gateway выбирает минимум два Receive Task либо Message Catch Event.', element, '§10.6.6')
            elif len({local(t) for t in targets}) > 1 or len({t.get('id') for t in targets}) != len(targets) or any(len(ins.get(t.get('id'), [])) != 1 for t in targets):
                add('INVALID_EVENT_GATEWAY_TARGETS', 'Нельзя смешивать Receive и Message Catch или давать ожиданиям другие входы.', element, '§10.6.6')
        if kind == 'intermediateCatchEvent' and not any(local(c).endswith('EventDefinition') or local(c) == 'eventDefinitionRef' for c in element):
            add('INVALID_NONE_CATCH', 'None Intermediate Event должен быть Throw Event.', element, '§10.5.4, Table 10.89')

    for lane_set in root.findall('.//bpmn:laneSet', NS):
        memberships = set()
        for lane in lane_set.findall('bpmn:lane', NS):
            for declared in lane.findall('bpmn:flowNodeRef', NS):
                node = ref(declared.text, declared, FLOW_NODES)
                if node is not None:
                    if process_of(node) is not process_of(lane) or node.get('id') in memberships:
                        add('INVALID_LANE_MEMBERSHIP', 'Узел должен принадлежать своему Process и одной дорожке данного LaneSet.', lane, '§10.7')
                    memberships.add(node.get('id'))

    for message in root.findall('.//bpmn:messageFlow', NS):
        source = ref(message.get('sourceRef'), message, ACTIVITIES | EVENTS | {'participant'})
        target = ref(message.get('targetRef'), message, ACTIVITIES | EVENTS | {'participant'})
        if source is None or target is None:
            continue
        def pool_of(el):
            process = process_of(el)
            return el.get('id') if local(el) == 'participant' else process_pools.get(process.get('id')) if process is not None else None
        if not pool_of(source) or not pool_of(target) or pool_of(source) == pool_of(target):
            add('SAME_POOL_MESSAGE_FLOW', 'Message Flow соединяет участников разных Pool одной Collaboration.', message, '§9.4')
        if local(source) == 'startEvent' or local(target) == 'endEvent' or (local(source) in EVENTS and source.find('bpmn:messageEventDefinition', NS) is None) or local(source) == 'intermediateCatchEvent' or (local(target) == 'intermediateThrowEvent'):
            add('INVALID_MESSAGE_ENDPOINT', 'Message Flow имеет недопустимое отправляющее/получающее событие.', message, '§9.4.1; §10.5')

    rendered = set()
    planes = root.findall('.//bpmndi:BPMNPlane', NS)
    for plane in planes:
        ref(plane.get('bpmnElement'), plane, {'process', 'collaboration'})
        plane_rendered = set()
        for graphic in plane:
            if local(graphic) not in {'BPMNShape', 'BPMNEdge'}:
                continue
            shape = local(graphic) == 'BPMNShape'
            kinds = FLOW_NODES | {'participant', 'lane'} if shape else {'sequenceFlow', 'messageFlow'}
            semantic = ref(graphic.get('bpmnElement'), graphic, kinds, 'INVALID_DI_REFERENCE')
            if semantic is not None:
                key = semantic.get('id')
                if key in plane_rendered:
                    add('DUPLICATE_DI_ELEMENT', 'Элемент изображён дважды на одной Plane.', graphic, '§12.2.3', 'LAYOUT')
                plane_rendered.add(key)
                rendered.add(key)
            bounds = graphic.find('dc:Bounds', NS) if shape else None
            points = graphic.findall('di:waypoint', NS) if not shape else []
            if (shape and bounds is None) or (not shape and len(points) < 2):
                add('MISSING_DI_GEOMETRY', 'Shape требует Bounds, Edge — минимум два waypoint.', graphic, '§12.2.3', 'LAYOUT')
            for geometry in ([bounds] if bounds is not None else []) + points + graphic.findall('bpmndi:BPMNLabel/dc:Bounds', NS):
                try:
                    values = {k: float(geometry.get(k)) for k in (('x', 'y', 'width', 'height') if local(geometry) == 'Bounds' else ('x', 'y'))}
                    if not all(math.isfinite(v) for v in values.values()) or any(v < 0 for v in values.values()) or (local(geometry) == 'Bounds' and (values['width'] <= 0 or values['height'] <= 0)):
                        raise ValueError()
                except (ValueError, TypeError):
                    add('INVALID_DI_GEOMETRY', 'Геометрия должна быть конечной, неотрицательной, с положительным размером.', graphic, '§12.2.3; §12.3', 'LAYOUT')
    if complete_di:
        expected = {el.get('id') for el in elements if E.QName(el).namespace == NS['bpmn'] and local(el) in FLOW_NODES | {'lane', 'participant', 'sequenceFlow', 'messageFlow'}}
        for key in expected - rendered:
            add('MISSING_DI_ELEMENT', 'Профиль полного экспорта PULSE требует Shape/Edge для каждого элемента.', index[key], '§12.1 (частичные diagrams допустимы)', 'LAYOUT')
    for issue in issues:
        logger.debug('bpmn_document code=%s element=%s severity=%s source=%s spec=%s', issue.code, issue.node_id, issue.severity, issue.source, issue.spec_section)
    return issues


def require_valid_document(issues):
    errors = [i for i in issues if i.severity == 'error']
    if errors:
        raise ValueError('; '.join(f'{i.code}: {i.message}' for i in errors))
