"""Read-only stage diffs and an independent canonical-model/XML fidelity check.

No LLM, repair, import_process, or business guesses. Explicit traces may contain
business text; callers keep them locally, never log or expose them by default.
Confidence/inferred are authoring metadata, not BPMN execution semantics.
"""
import json
from lxml import etree as E
from .models import Issue

NS={'bpmn':'http://www.omg.org/spec/BPMN/20100524/MODEL'}
NODE_TAGS={'start_event':'startEvent','end_event':'endEvent','task':'task',
    'user_task':'userTask','service_task':'serviceTask','script_task':'scriptTask',
    'send_task':'sendTask','receive_task':'receiveTask','exclusive_gateway':'exclusiveGateway',
    'parallel_gateway':'parallelGateway','inclusive_gateway':'inclusiveGateway',
    'event_based_gateway':'eventBasedGateway','intermediate_event':'intermediateCatchEvent'}
COLLECTIONS=('participants','pools','nodes','flows','message_flows','ambiguities','assumptions')


def process_diff(before, after):
    """Exact field diffs, including conditions, evidence, IDs, and memberships."""
    changes=[]
    for field in ('id','name','description'):
        if getattr(before,field)!=getattr(after,field):
            changes.append({'path':field,'before':getattr(before,field),'after':getattr(after,field)})
    for collection in COLLECTIONS:
        old={x.id:x.model_dump() for x in getattr(before,collection)}
        new={x.id:x.model_dump() for x in getattr(after,collection)}
        for key in sorted(old.keys()|new.keys()):
            if key not in old or key not in new:
                changes.append({'path':f'{collection}.{key}','before':old.get(key),'after':new.get(key)})
            else:
                for field in old[key].keys()|new[key].keys():
                    if old[key].get(field)!=new[key].get(field):
                        changes.append({'path':f'{collection}.{key}.{field}',
                                        'before':old[key].get(field),'after':new[key].get(field)})
    return sorted(changes,key=lambda x:x['path'])


def compare_process_xml(process, xml):
    """Compare emitted BPMN semantics directly; do not roundtrip via the importer."""
    root=E.fromstring(xml.encode(),E.XMLParser(resolve_entities=False,no_network=True,load_dtd=False))
    differences=[]
    def compare(path, expected, actual):
        if expected!=actual:
            differences.append({'path':path,'expected':expected,'actual':actual})
    all_nodes={el.get('id'):el for el in root.findall('.//bpmn:process/*',NS)
               if E.QName(el).localname in set(NODE_TAGS.values())|{'intermediateThrowEvent'}}
    compare('nodes.ids',sorted(n.id for n in process.nodes),sorted(all_nodes))
    pools={el.get('id'):el for el in root.findall('.//bpmn:collaboration/bpmn:participant',NS)}
    roles={r.id:r for r in process.participants}
    memberships={ref.text:lane.get('id') for lane in root.findall('.//bpmn:lane',NS)
                 for ref in lane.findall('bpmn:flowNodeRef',NS)}
    lane_ids={el.get('id') for el in root.findall('.//bpmn:lane',NS)}
    expected_lane_ids={r.id for r in process.participants if not process.pools or
                       next(p for p in process.pools if p.id==r.pool_id).kind!='external'}
    compare('participants.lanes',sorted(expected_lane_ids),sorted(lane_ids))
    envelope=root.find('bpmn:collaboration',NS) if process.pools else root.find('bpmn:process',NS)
    if envelope is not None:
        compare('process.id',process.id,envelope.get('id'))
        compare('process.name',process.name,envelope.get('name',''))
        docs=[el.text or '' for el in envelope.findall('bpmn:documentation',NS)]
        compare('process.description',process.description,next((text for text in docs if not text.startswith('PULSE_ASSUMPTIONS:')),''))
        assumptions=next((json.loads(text[len('PULSE_ASSUMPTIONS:'):]) for text in docs if text.startswith('PULSE_ASSUMPTIONS:')),[])
        compare('process.assumptions',[a.model_dump() for a in process.assumptions],assumptions)
    for node in process.nodes:
        el=all_nodes.get(node.id)
        if el is None: continue
        prefix='nodes.'+node.id
        expected_tag='intermediateThrowEvent' if node.type=='intermediate_event' and node.event_definition=='none' else NODE_TAGS[node.type]
        compare(prefix+'.type',expected_tag,E.QName(el).localname)
        compare(prefix+'.name',node.name,el.get('name',''))
        definitions=[E.QName(x).localname for x in el if E.QName(x).localname.endswith('EventDefinition')]
        compare(prefix+'.event_definition',['messageEventDefinition'] if node.event_definition=='message' else [],definitions)
        compare(prefix+'.incoming',sorted(f.id for f in process.flows if f.target==node.id),sorted(el.xpath('bpmn:incoming/text()',namespaces=NS)))
        compare(prefix+'.outgoing',sorted(f.id for f in process.flows if f.source==node.id),sorted(el.xpath('bpmn:outgoing/text()',namespaces=NS)))
        outs=[f for f in process.flows if f.source==node.id]
        compare(prefix+'.default',next((f.id for f in outs if f.is_default),None),el.get('default'))
        if node.type.endswith('gateway'):
            ins=[f for f in process.flows if f.target==node.id]
            direction='Mixed' if len(ins)>1 and len(outs)>1 else 'Diverging' if len(outs)>1 else 'Converging'
            compare(prefix+'.gatewayDirection',direction,el.get('gatewayDirection'))
        documentation=el.findtext('bpmn:documentation',default='',namespaces=NS)
        data=json.loads(documentation[11:]) if documentation.startswith('PULSE_NODE:') else {'source_text':documentation,'decision_basis':'unspecified'}
        compare(prefix+'.source_text',node.source_text,data.get('source_text'))
        compare(prefix+'.decision_basis',node.decision_basis,data.get('decision_basis'))
        role=roles[node.participant_id]
        if process.pools:
            pool=pools.get(role.pool_id)
            compare(prefix+'.pool',pool.get('processRef') if pool is not None else None,el.getparent().get('id'))
            if pool is not None:
                meta=json.loads(pool.findtext('bpmn:documentation',default='PULSE:{}',namespaces=NS)[6:])
                compare('pools.'+role.pool_id+'.roles',
                        [r.model_dump() for r in process.participants if r.pool_id==role.pool_id],meta.get('roles'))
            expected_pool=next(p for p in process.pools if p.id==role.pool_id)
            if expected_pool.kind!='external': compare(prefix+'.participant',node.participant_id,memberships.get(node.id))
        else:
            compare(prefix+'.process',process.id,el.getparent().get('id'))
            compare(prefix+'.participant',node.participant_id,memberships.get(node.id))
    if process.pools:
        compare('pools.ids',sorted(p.id for p in process.pools),sorted(pools))
        for pool in process.pools:
            if pool.id in pools:
                compare('pools.'+pool.id+'.name',pool.name,pools[pool.id].get('name',''))
                meta=json.loads(pools[pool.id].findtext('bpmn:documentation',default='PULSE:{}',namespaces=NS)[6:])
                compare('pools.'+pool.id+'.kind',pool.kind,meta.get('kind'))
    for role in process.participants:
        if not process.pools or next(p for p in process.pools if p.id==role.pool_id).kind!='external':
            lane=root.find('.//bpmn:lane[@id="'+role.id+'"]',NS)
            compare('participants.'+role.id+'.name',role.name,lane.get('name','') if lane is not None else None)
    for collection,tag in (('flows','sequenceFlow'),('message_flows','messageFlow')):
        emitted={el.get('id'):el for el in root.findall('.//bpmn:'+tag,NS)}
        compare(collection+'.ids',sorted(x.id for x in getattr(process,collection)),sorted(emitted))
        for flow in getattr(process,collection):
            el=emitted.get(flow.id)
            if el is None: continue
            for field,attr in (('source','sourceRef'),('target','targetRef'),('name','name')):
                compare(collection+'.'+flow.id+'.'+field,getattr(flow,field),el.get(attr,''))
            if collection=='flows':
                expr=el.find('bpmn:conditionExpression',NS)
                condition=(expr.findtext('bpmn:documentation',namespaces=NS) or expr.text or None) if expr is not None else None
                compare(collection+'.'+flow.id+'.condition',flow.condition or None,condition)
    return differences


def require_xml_fidelity(process, xml):
    differences=compare_process_xml(process,xml)
    if differences:
        from .diagnostics import PipelineFailure
        raise PipelineFailure('Сборка BPMN изменила исходную модель. Результат не выдан.',
            [Issue(code='BPMN_SEMANTIC_DRIFT',severity='error',source='BUSINESS_LOGIC',
                   message='Расхождение ProcessDefinition и XML: '+d['path']) for d in differences[:20]])
