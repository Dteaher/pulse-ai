"""Independent semantic checks for the three fixed public benchmark texts.
This intentionally never derives expected business logic from the LLM description.
"""
import re
from app.logic_audit import compare_process_xml
from app.models import ProcessDefinition


def evaluate(case, process, xml):
    p=ProcessDefinition.model_validate(process)
    assert not compare_process_xml(p, xml), 'ProcessDefinition/XML fidelity mismatch'
    nodes={n.id:n for n in p.nodes}
    outgoing={n.id:[f for f in p.flows if f.source==n.id] for n in p.nodes}
    def role(pattern):
        found=[r.id for r in p.participants if re.search(pattern,r.name,re.I)]
        assert len(found)==1, 'Missing/ambiguous role: '+pattern
        return found[0]
    def step(pattern, owner, kind=None):
        found=[n.id for n in p.nodes if n.participant_id==owner and re.search('^(?:'+pattern+')',n.name,re.I)
            and (n.type==kind if kind else n.type.endswith('task'))]
        assert len(found)==1, 'Missing/ambiguous action: '+pattern
        return found[0]
    def path(start, target, stop_tasks=False):
        visited=set();stack=[start]
        while stack:
            current=stack.pop()
            if current==target:return True
            if current in visited:continue
            visited.add(current)
            if current!=start and (len(outgoing[current])>1 or stop_tasks and nodes[current].type.endswith('task')):continue
            stack.extend(f.target for f in outgoing[current])
        return False
    def follow(start,target):
        assert path(start,target), 'Wrong transition: '+nodes[start].name+' → '+nodes[target].name
    def return_to(start,target):
        assert path(start,target,stop_tasks=True), 'Wrong first action after return: '+nodes[start].name+' → '+nodes[target].name
    def condition(gateway, target, negative):
        candidates=[f for f in outgoing[gateway] if path(f.target,target)]
        assert len(candidates)==1 and (candidates[0].condition or candidates[0].is_default), 'Missing/ambiguous branch condition'
        value=(candidates[0].condition or candidates[0].name).lower()
        no=bool(re.search(r'нет|неполн|не коррект|некорр|отклон|не соглас|отказ',value))
        yes=bool(re.search(r'да|полн|коррект|одобрен|согласован',value))
        assert no if negative else yes and not no, 'Branch outcome conflicts with source'
    operator=role('оператор')
    check=step('провер.*(комплект|документ)',operator)
    documents=step('документ.*полн|комплект',operator,'exclusive_gateway')
    follow(check,documents)
    if case=='simple':
        register=step('зарегистр|регистри',operator)
        refuse=step('отправ.*(отказ|уведом)',operator)
        condition(documents,register,False);condition(documents,refuse,True)
        assert len([n for n in p.nodes if n.type=='end_event'])==2
        assert not any(n.type=='parallel_gateway' for n in p.nodes)
        return {'passed':True,'checks':['actions','role','XOR outcomes','two ends','XML fidelity']}
    client=role('клиент')
    assert len(p.pools)==2 and len(p.message_flows)==(4 if case=='medium' else 5)
    receive=step('получ.*исправ',operator)
    fix=step('исправ.*документ',client)
    send_fix=step('отправ.*исправ',client)
    follow(fix,send_fix);return_to(receive,check)
    waiting=[n for n in p.nodes if n.participant_id==client and n.type=='event_based_gateway']
    assert len(waiting)==1
    assert len(outgoing[waiting[0].id])==(2 if case=='medium' else 3)
    assert all(nodes[f.target].type in ('receive_task','intermediate_event') and f.condition is None for f in outgoing[waiting[0].id])
    follow(send_fix,waiting[0].id)
    request=step('отправ.*(запрос|доработ)',operator)
    condition(documents,request,True)
    if case=='medium':
        register=step('зарегистр|регистри',operator)
        condition(documents,register,False)
        return {'passed':True,'checks':['roles','XOR outcomes','document loop','event wait','four MessageFlow','XML fidelity']}
    legal=role('юрид|юрист');security=role('безопас');head=role('руковод');manager=role('менеджер')
    legal_check=step('провер',legal);security_check=step('провер',security)
    splits=[n.id for n in p.nodes if n.type=='parallel_gateway' and len(outgoing[n.id])==2]
    joins=[n.id for n in p.nodes if n.type=='parallel_gateway' and sum(f.target==n.id for f in p.flows)==2]
    assert len(splits)==len(joins)==1
    condition(documents,splits[0],False)
    for check_id in (legal_check,security_check):follow(splits[0],check_id);follow(check_id,joins[0])
    review=step('рассмотр.*результ',head)
    follow(joins[0],review)
    approval=step('заявк.*одобрен|одобрен.*заявк',head,'exclusive_gateway')
    follow(review,approval)
    refusal=step('отправ.*(отказ|уведом)',manager)
    draft=step('состав.*(договор|проект)|формир.*договор|подготов.*(проект|договор)',manager)
    condition(approval,refusal,True);condition(approval,draft,False)
    contract_check=step('провер.*(проект|договор)',head)
    contract_decision=step('договор.*соглас|соглас.*договор',head,'exclusive_gateway')
    follow(contract_check,contract_decision)
    revise=step('доработ.*договор|измен.*договор|исправ.*договор',manager)
    retransmit=step('повтор.*перед|перед.*доработ|направ.*повтор',manager)
    send_contract=step('отправ.*(согласован|договор)',manager)
    condition(contract_decision,revise,True);condition(contract_decision,send_contract,False)
    follow(revise,retransmit);return_to(retransmit,contract_check)
    assert not path(refusal,draft)
    return {'passed':True,'checks':['six roles','actions','XOR outcomes','AND split/join','two loops','event wait','five MessageFlow','XML fidelity']}
