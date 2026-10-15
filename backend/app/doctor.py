"""Deterministic, read-only diagnostics. No token simulation or LLM calls.
Graph warnings are conservative: cycles may terminate and AND paths may be
controlled by data not represented in BPMN. Confidence is not a probability.
"""
from collections import defaultdict
from .bpmn import parse_xml, NS
from .models import Issue


def doctor_rules(xml, *, execution_preparation=False):
    root = parse_xml(xml)
    findings = []
    def report(code, category, ids, description, reason, suggestion, severity='warning', confidence=None):
        findings.append(Issue(code=code, category=category, severity=severity,
            source='BUSINESS_LOGIC' if category in {'business','execution'} else 'MODEL_QUALITY',
            node_ids=ids, message=description, reason=reason, suggestion=suggestion,
            suggested_fix=suggestion, confidence=confidence))
    for proc in root.findall('bpmn:process', NS):
        nodes = {e.get('id'):e for e in proc if e.get('id') and (E_local(e).endswith(('Task','Gateway','Event')) or E_local(e) in {'task','subProcess','callActivity'})}
        if len(nodes) > 150:
            report('DOCTOR_ANALYSIS_LIMIT','readability',[proc.get('id')], 'Расширенный анализ ограничен 150 элементами на Process.',
                'Большой внешний XML выходит за размер AI subset PULSE; XSD и ссылки проверяются отдельно.',
                'Проверяйте отдельные процессы или используйте внешний анализатор.',severity='info')
            continue
        edges = [e for e in proc.findall('bpmn:sequenceFlow',NS) if e.get('sourceRef') in nodes and e.get('targetRef') in nodes]
        outgoing = defaultdict(list); incoming = defaultdict(list)
        for flow in edges:
            outgoing[flow.get('sourceRef')].append(flow.get('targetRef'))
            incoming[flow.get('targetRef')].append(flow.get('sourceRef'))
        def walk(seeds):
            seen=set();todo=list(seeds)
            while todo:
                n=todo.pop()
                if n in seen:continue
                seen.add(n);todo.extend(outgoing[n])
            return seen
        starts=[k for k,e in nodes.items() if E_local(e) in {'startEvent','boundaryEvent'}]
        reachable=walk(starts or [k for k in nodes if not incoming[k]])
        for key in nodes.keys()-reachable:
            report('DOCTOR_UNREACHABLE_ELEMENT','business',[key], 'Элемент недостижим от показанных точек запуска.',
                'Проверяются только SequenceFlow текущего Process; не внешние способы запуска.',
                'Проверьте входящие переходы и механизм запуска.',confidence=0.9)
        # Tarjan SCC; an exit changes the description, not an invented timer rule.
        indices={};low={};stack=[];active=set()
        def visit(key):
            indices[key]=low[key]=len(indices);stack.append(key);active.add(key)
            for target in outgoing[key]:
                if target not in indices:visit(target);low[key]=min(low[key],low[target])
                elif target in active:low[key]=min(low[key],indices[target])
            if low[key]==indices[key]:
                group=[]
                while True:
                    n=stack.pop();active.remove(n);group.append(n)
                    if n==key:break
                if len(group)>1 or key in outgoing[key]:
                    exit_exists=any(t not in group for n in group for t in outgoing[n])
                    report('DOCTOR_LOOP_WITH_EXIT' if exit_exists else 'DOCTOR_CLOSED_CYCLE','business',sorted(group),
                        'Цикл имеет выход; гарантированное завершение не доказано.' if exit_exists else 'В показанном цикле нет исходящего пути.',
                        'SCC обнаруживает топологию, но не доказывает повторяемость условий или сроки.',
                        'Если это важно для процесса, задайте критерий выхода или политику повторов.',confidence=0.7)
        for key in nodes:
            if key not in indices:visit(key)
        for key,element in nodes.items():
            kind=E_local(element)
            if kind=='parallelGateway' and len(incoming[key])>1:
                upstream_splits=[n for n,e in nodes.items() if E_local(e)=='parallelGateway' and len(outgoing[n])>1 and key in walk([n])]
                if not upstream_splits:
                    report('DOCTOR_AND_JOIN_TOKEN_RISK','business',[key], 'Возможное ожидание недостающих токенов на AND join.',
                        'В показанном графе не найден предшествующий AND split. Это эвристика, а не симуляция.',
                        'Проверьте, что каждый вход join получает токен на выбранном сценарии.',confidence=0.6)
            if kind=='parallelGateway' and len(outgoing[key])>1:
                joins=[n for n in walk([key]) if E_local(nodes[n])=='parallelGateway' and len(incoming[n])>1]
                if joins:
                    report('DOCTOR_PARALLEL_REVIEW','business',[key]+joins, 'Проверьте, что каждая параллельная ветка достигает своего объединения.',
                        'Графовая проверка не доказывает отсутствие токеновых тупиков.',
                        'Проверьте сценарии с альтернативами и возвратами.',severity='info',confidence=0.5)
                paths=[walk([t]) for t in outgoing[key]]
                terminates=[n for path in paths for n in path if nodes[n].find('bpmn:terminateEventDefinition',NS) is not None]
                if terminates:
                    report('DOCTOR_TERMINATE_PARALLEL_RISK','business',[key]+sorted(set(terminates)), 'Terminate End может завершить другие активные параллельные ветви.',
                        'Terminate завершает область процесса; анализ достижимости не доказывает порядок прихода токенов.',
                        'Подтвердите, что прерывание остальных ветвей является требуемым исходом.',confidence=0.7)
                ends=[k for k,e in nodes.items() if E_local(e)=='endEvent']
                if all(any(e in path for e in ends) for path in paths) and not any(E_local(nodes[n])=='parallelGateway' and len(incoming[n])>1 for n in set.intersection(*paths)):
                    report('DOCTOR_PARALLEL_SEPARATE_ENDS','business',[key], 'Параллельные ветви завершаются без общего AND join.',
                        'Обычное End завершает свой путь; это допустимо. Общая синхронизация может быть бизнес-требованием.',
                        'Уточните, нужен ли общий результат после завершения всех ветвей.',severity='info',confidence=0.6)
            if kind=='receiveTask' or element.find('bpmn:messageEventDefinition',NS) is not None:
                timer=any(E_local(e)=='boundaryEvent' and e.get('attachedToRef')==key and e.find('bpmn:timerEventDefinition',NS) is not None for e in proc)
                preceding=[nodes[n] for n in incoming[key] if n in nodes and E_local(nodes[n])=='eventBasedGateway']
                timer=timer or any(nodes[t].find('bpmn:timerEventDefinition',NS) is not None for gate in preceding for t in outgoing[gate.get('id')])
                if not timer:
                    report('DOCTOR_MESSAGE_WAIT_NO_TIMEOUT','business',[key], 'Обработка истечения срока ожидания не показана.',
                        'Ожидание без таймера допустимо; возможный срок определяется требованиями пользователя.',
                        'При необходимости явно задайте срок и альтернативный исход.',severity='info',confidence=0.8)
            if execution_preparation and kind=='exclusiveGateway' and len(outgoing[key])>1:
                if not element.get('default'):
                    report('EXECUTION_XOR_NO_DEFAULT','execution',[key], 'Default-ветвь не задана.',
                        'Без доказательства полноты условий может не найтись подходящей ветви; default не обязателен в BPMN.',
                        'Докажите полноту условий либо явно задайте default.',confidence=0.6)
                for flow in edges:
                    if flow.get('sourceRef')!=key or flow.get('id')==element.get('default'):continue
                    expr=flow.find('bpmn:conditionExpression',NS)
                    if expr is None or not (expr.text or '').strip() or not expr.get('{'+NS['xsi']+'}type','').endswith('tFormalExpression'):
                        report('EXECUTION_CONDITION_NOT_FORMAL','execution',[flow.get('id'),key], 'Исполняемое условие не задано.',
                            'Условие на естественном языке не является выражением выбранного движка.',
                            'После выбора движка задайте выражение и явный словарь переменных.')
    return findings


def E_local(element):
    from lxml import etree
    return etree.QName(element).localname


def diagnostic_payload(issue):
    """Doctor API keeps legacy fields and adds the four-category contract."""
    data=issue.model_dump()
    data['category']=issue.category if issue.category in {'notation','execution','business','readability'} else ('notation' if issue.source in {'BPMN_SPEC','XSD'} else 'readability' if issue.source=='LAYOUT' else 'business')
    if issue.severity=='clarification':data['severity']='warning'
    return data
