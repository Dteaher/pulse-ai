import asyncio
import pytest
from lxml import etree as E
from app.models import ProcessDefinition, Participant, Pool, Node, Flow, MessageFlow, Ambiguity
from app.validator import validate_process
from app.semantics import validate_semantics
from app.quality import validate_labels, check_membership
from app.bpmn import build_bpmn, import_process, validate_xml, NS
from app.changes import preserve_ids, changes_between
from app.pipeline import generate_valid
from test_clarify_modify import Scripted, client

TEXT = 'Клиент подаёт заявку на подключение объекта. Оператор проверяет документы. Если документов не хватает, заявку возвращают клиенту на доработку. После получения корректных документов заявку проверяют юридический отдел и служба безопасности. Затем руководитель принимает решение. Если заявка одобрена, менеджер формирует договор и отправляет его клиенту. Если заявка отклонена, клиент получает уведомление об отказе.'


def collaboration_example():
    pools = [Pool(id='ClientPool',name='Клиент',kind='external'),Pool(id='CompanyPool',name='Энергоснабжающая компания')]
    roles = [Participant(id='Client',name='Клиент',kind='external',type='external',pool_id='ClientPool')]
    roles += [Participant(id=i,name=n,pool_id='CompanyPool') for i,n in [('Operator','Оператор'),('Legal','Юридический отдел'),('Security','Служба безопасности'),('Head','Руководитель'),('Manager','Менеджер')]]
    specs = [('ClientStart','start_event','Начало процесса','Client'),('Submit','send_task','Подать заявку','Client'),
      ('Wait','task','Ожидать ответ компании','Client'),('Reply','event_based_gateway','Какой ответ получен?','Client'),
      ('ReceiveReturn','receive_task','Получить запрос на доработку','Client'),('Revise','user_task','Доработать документы','Client'),('Resubmit','send_task','Отправить исправленные документы','Client'),
      ('ReceiveContract','receive_task','Получить договор','Client'),('ReceiveNotice','receive_task','Получить уведомление об отказе','Client'),('ClientYes','end_event','Договор получен','Client'),('ClientNo','end_event','Отказ получен','Client'),
      ('CompanyStart','start_event','Получена заявка','Operator'),('Merge','exclusive_gateway','Проверка документов','Operator'),('Check','user_task','Проверить документы','Operator'),('Complete','exclusive_gateway','Документы полные?','Operator'),
      ('Return','send_task','Вернуть заявку на доработку','Operator'),('Correction','receive_task','Получить исправленные документы','Operator'),
      ('Fork','parallel_gateway','Параллельные проверки','Operator'),('LegalCheck','user_task','Проверить заявку','Legal'),('SecurityCheck','user_task','Проверить заявку','Security'),('Join','parallel_gateway','Проверки завершены','Head'),
      ('Decide','user_task','Принять решение','Head'),('Approved','exclusive_gateway','Заявка одобрена?','Head'),('Contract','user_task','Сформировать договор','Manager'),('Send','send_task','Отправить договор клиенту','Manager'),('Reject','send_task','Отправить уведомление об отказе','Manager'),('CompanyYes','end_event','Договор отправлен','Manager'),('CompanyNo','end_event','Отказ отправлен','Manager')]
    edges = [('ClientStart','Submit',''),('Submit','Wait',''),('Wait','Reply',''),('Reply','ReceiveReturn','Нужна доработка'),('ReceiveReturn','Revise',''),('Revise','Resubmit',''),('Resubmit','Wait',''),('Reply','ReceiveContract','Договор'),('Reply','ReceiveNotice','Отказ'),('ReceiveContract','ClientYes',''),('ReceiveNotice','ClientNo',''),
      ('CompanyStart','Merge',''),('Correction','Merge',''),('Merge','Check',''),('Check','Complete',''),('Complete','Return','Документы неполные'),('Return','Correction',''),('Complete','Fork','Документы полные'),('Fork','LegalCheck',''),('Fork','SecurityCheck',''),('LegalCheck','Join',''),('SecurityCheck','Join',''),('Join','Decide',''),('Decide','Approved',''),('Approved','Contract','Одобрено'),('Approved','Reject','Отклонено'),('Contract','Send',''),('Send','CompanyYes',''),('Reject','CompanyNo','')]
    evidence = {'Submit':TEXT.split('. ')[0], 'Check':TEXT.split('. ')[1], 'LegalCheck':TEXT.split('. ')[3], 'SecurityCheck':TEXT.split('. ')[3], 'Decide':TEXT.split('. ')[4], 'Contract':TEXT.split('. ')[5], 'Send':TEXT.split('. ')[5], 'Reject':TEXT.split('. ')[6]}
    return ProcessDefinition(id='Connection',name='Подключение объекта',description=TEXT,pools=pools,participants=roles,
      nodes=[Node(id=i,type=t,name=n,participant_id=r,event_definition='message' if i=='CompanyStart' else 'none',source_text=evidence.get(i,''),inferred=i not in evidence) for i,t,n,r in specs],
      flows=[Flow(id=f'Flow{i}',source=a,target=b,name=c,condition=(c or None) if a!='Reply' else None) for i,(a,b,c) in enumerate(edges)],
      message_flows=[MessageFlow(id=f'Message{i}',source=a,target=b) for i,(a,b) in enumerate([('Submit','CompanyStart'),('Return','ReceiveReturn'),('Resubmit','Correction'),('Send','ReceiveContract'),('Reject','ReceiveNotice')])])


def test_collaboration_structure_xml_di_and_roundtrip():
    p=collaboration_example()
    assert not validate_process(p) and not validate_semantics(p) and not validate_labels(p)
    xml=build_bpmn(p); validate_xml(xml); root=E.fromstring(xml.encode())
    assert len(root.findall('bpmn:process',NS))==2
    assert len(root.findall('.//bpmn:participant',NS))==2
    assert len(root.findall('.//bpmn:lane',NS))==5
    assert len(root.findall('.//bpmn:messageFlow',NS))==5
    assert len(root.findall('.//bpmndi:BPMNEdge',NS))==len(p.flows)+5
    imported=import_process(xml,p)
    assert imported.model_dump()==p.model_dump()
    assert import_process(build_bpmn(imported),imported)==imported
    fresh=import_process(xml)
    assert fresh.pools==p.pools and fresh.participants==p.participants and fresh.message_flows==p.message_flows


@pytest.mark.parametrize('problem',['sequence_crosses','message_inside','reference','external_lane','start_action','orphan','unreachable_end','gateway_message'])
def test_collaboration_rejects_invalid_structure(problem):
    p=collaboration_example()
    if problem=='sequence_crosses': p.flows[0].target='Check'
    if problem=='message_inside': p.message_flows[0].target='Wait'
    if problem=='reference': p.message_flows[0].target='Missing'
    if problem=='external_lane': p.participants[0].pool_id='CompanyPool'
    if problem=='start_action': p.nodes[0].name='Клиент подаёт заявку'
    if problem=='orphan': p.flows=[f for f in p.flows if f.target!='Check']
    if problem=='unreachable_end': p.flows=[f for f in p.flows if f.target!='ClientYes']
    if problem=='gateway_message': p.message_flows[0].target='Complete'
    assert validate_process(p)
    if problem=='start_action':
        # §10.5.2 does not constrain an event's natural-language name.
        assert any(i.code=='ACTION_AS_START_EVENT' and i.severity=='warning' for i in validate_process(p))
        validate_xml(build_bpmn(p))
        return
    with pytest.raises(ValueError): build_bpmn(p)


def test_start_action_and_english_label_corrective_retry():
    good=collaboration_example(); bad=good.model_copy(deep=True); bad.nodes[0].name='Клиент подаёт заявку'
    responses=iter([bad,good])
    async def call(correction): return next(responses)
    result=asyncio.run(generate_valid(call))
    assert result['attempts']==1
    assert any(i.code=='ACTION_AS_START_EVENT' and i.severity=='warning' for i in result['semantic_warnings'])
    bad=good.model_copy(deep=True); bad.flows[0].name='submit'
    assert validate_labels(bad)
    responses=iter([bad,good])
    assert asyncio.run(generate_valid(call))['attempts']==1
    assert all(not f.name for f in good.flows if not f.condition and f.source!='Reply')


@pytest.mark.parametrize('label',['Менеджер отправляет договор','Сформировать договор и отправить клиенту'])
def test_short_labels_do_not_duplicate_role_or_merge_actions(label):
    p=collaboration_example();next(n for n in p.nodes if n.id=='Send').name=label
    assert validate_labels(p)
    assert build_bpmn(p)


def test_membership_comes_from_context_not_role_name():
    p=collaboration_example()
    p.pools[0].name='Аудитор';p.participants[0].name='Аудитор'
    p.participants[1].name='Клиент внутреннего сервиса'
    root=E.fromstring(build_bpmn(p).encode())
    assert len(root.findall('.//bpmn:lane',NS))==5
    assert root.find('.//bpmn:lane[@id="Operator"]',NS).get('name')=='Клиент внутреннего сервиса'
    assert import_process(build_bpmn(p)).participants[0].kind=='external'


def test_modify_preserves_membership_and_message_ids():
    p=collaboration_example(); bad=p.model_copy(deep=True); bad.participants[0].kind='internal'; bad.participants[0].pool_id='CompanyPool'
    with pytest.raises(ValueError): check_membership(p,bad,'После проверки добавь согласование клиента')
    candidate=p.model_copy(deep=True); candidate.nodes[5].name='Доработать и уточнить документы'
    scripted=Scripted([bad,candidate]); r=client(scripted).post('/api/process/modify',json={'process':p.model_dump(),'instruction':'Уточни название доработки документов'})
    assert r.status_code==200,r.text
    after=ProcessDefinition.model_validate(r.json()['process'])
    assert after.pools==p.pools and after.participants==p.participants and after.message_flows==p.message_flows
    renamed=p.model_copy(deep=True)
    mapping={n.id:'New_'+n.id for n in renamed.nodes}
    for n in renamed.nodes: n.id=mapping[n.id]
    for f in renamed.flows+renamed.message_flows: f.source,f.target=mapping[f.source],mapping[f.target]; f.id='New_'+f.id
    fixed=preserve_ids(p,renamed)
    assert fixed.nodes==p.nodes and fixed.message_flows==p.message_flows


def test_clarify_resolves_parallelism_without_losing_collaboration():
    p=collaboration_example(); draft=p.model_copy(deep=True)
    draft.ambiguities=[Ambiguity(id='order',type='unclear_parallelism',question='Каков порядок двух проверок?',related_node_ids=['Fork'])]
    scripted=Scripted([p]);r=client(scripted).post('/api/process/clarify',json={'process':draft.model_dump(),'original_text':TEXT,'answers':{'order':'Юридический отдел и служба безопасности работают параллельно. Исправленные документы повторно проверяет Оператор.'}})
    assert r.status_code==200 and r.json()['xml']
    after=ProcessDefinition.model_validate(r.json()['process'])
    assert after.pools==p.pools and after.participants==p.participants and after.message_flows==p.message_flows


def test_changes_include_pool_and_message_edits():
    p=collaboration_example(); candidate=p.model_copy(deep=True)
    candidate.pools[1].name='Энергетическая компания'; candidate.message_flows[0].name='Заявка'
    changes=changes_between(p,candidate).changes
    assert {c.category for c in changes}=={'participant','structure'}


def test_layout_separates_pools_nodes_and_places_join_after_branches():
    from app.collaboration import collaboration_layout
    p=collaboration_example()
    bounds,_,_,pools=collaboration_layout(p,p.pools)
    assert pools['ClientPool'][1]+pools['ClientPool'][3]<pools['CompanyPool'][1]
    boxes=list(bounds.values())
    for i,(x,y,w,h) in enumerate(boxes):
        for tx,ty,tw,th in boxes[i+1:]:
            assert x+w<=tx or tx+tw<=x or y+h<=ty or ty+th<=y
    assert bounds['Join'][0]>max(bounds['LegalCheck'][0],bounds['SecurityCheck'][0])
    root=E.fromstring(build_bpmn(p).encode())
    for label in root.findall('.//bpmndi:BPMNLabel/dc:Bounds',NS):
        x,y,w,h=[float(label.get(key)) for key in ('x','y','width','height')]
        assert all(x+w<=tx or tx+tw<=x or y+h<=ty or ty+th<=y for tx,ty,tw,th in boxes)


@pytest.mark.parametrize('failure', ['recoverable', 'invalid'])
def test_compatible_json_is_validated_or_corrected_on_same_provider(monkeypatch,failure):
    import httpx
    from app.services.llm.openai_compatible_provider import OpenAICompatibleProvider
    calls=[]; original=httpx.AsyncClient
    p=collaboration_example()
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(200,json={'choices':[{'message':{'content':p.model_dump_json() if failure=='recoverable' else '{"unexpected":true}'}}]})
        return httpx.Response(200,json={'choices':[{'message':{'content':p.model_dump_json()}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    provider=OpenAICompatibleProvider('test','https://example.test','test')
    assert asyncio.run(provider.parse_process(TEXT))==p
    assert len(calls)==(1 if failure=='recoverable' else 2)
