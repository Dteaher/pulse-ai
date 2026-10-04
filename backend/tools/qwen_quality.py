"""Golden-scenario oracle only. No business dictionaries enter production."""
from tools.quality_oracle import quality
from app.repair import business_loops

ROLES=('клиент','оператор','юридический отдел','служба безопасности','руководитель','менеджер')
ACTIONS={
 'submit':('клиент',(('пода','подать','отправ','направ'),('заяв',))),
 'fix_documents':('клиент',(('исправ','доработ'),('документ',))),
 'resend_documents':('клиент',(('повтор',),('отправ','направ'),('документ','заяв'))),
 'check_documents':('оператор',(('провер',),('документ',))),
 'return_documents':('оператор',(('вер','направ','отправ'),('доработ',))),
 'legal_check':('юридический отдел',(('провер',),)),
 'security_check':('служба безопасности',(('провер',),)),
 'decision':('руководитель',(('решен','решён','одобр','соглас'),)),
 'rejection':('оператор',(('отказ','отклон'),)),
 'form_contract':('менеджер',(('формир','подготов','состав'),('договор',))),
 'send_contract':('менеджер',(('отправ','направ'),('договор',))),
 'revise_contract':('менеджер',(('измен','доработ','исправ'),('договор',))),
 'resend_contract':('менеджер',(('повтор',),('отправ','направ'),('договор',))),
 'register_contract':('менеджер',(('регистр',),('договор',))),
 'review_contract':('клиент',(('провер','рассмотр'),('договор',))),
 'return_contract':('клиент',(('вер','отправ','направ'),('доработ',),('договор',))),
 'sign_contract':('клиент',(('подпис',),('договор',))),
 'send_signed':('клиент',(('отправ','направ'),('подпис',))),
}


def categories(p):
    roles={r.id:r.name.casefold() for r in p.participants}
    return {n.id:sorted(key for key,(actor,groups) in ACTIONS.items()
              if roles.get(n.participant_id)==actor and all(any(stem in n.name.casefold() for stem in group) for group in groups)) for n in p.nodes}


def semantic_fingerprint(p):
    actors={r.id:r.name.casefold() for r in p.participants};cats=categories(p)
    def node(key):
        n=next(n for n in p.nodes if n.id==key)
        return (actors[n.participant_id],tuple(cats[key]) or (n.type,))
    return {'roles':sorted((r.name.casefold(),r.kind) for r in p.participants),
            'actions':sorted({c for v in cats.values() for c in v}),
            'sequence_relationships':sorted((node(f.source),node(f.target),bool(f.condition),f.is_default) for f in p.flows),
            'message_relationships':sorted((node(f.source),node(f.target)) for f in p.message_flows),
            'loop_structure':sorted(set(tuple(sorted(node(key) for key in loop)) for loop in business_loops(p))),
            'gateways':sorted((actors[n.participant_id],n.type) for n in p.nodes if 'gateway' in n.type)}


def complex_quality(p):
    report=quality(p,'complex');cats=categories(p)
    present={c for v in cats.values() for c in v}
    report['missing_actions']=sorted(set(ACTIONS)-present)
    roles={r.id:r.name.casefold() for r in p.participants};nodes={n.id:n for n in p.nodes}
    required=[('клиент','оператор','заяв'),('оператор','клиент','доработ'),
              ('клиент','оператор','документ'),('оператор','клиент','отказ'),
              ('менеджер','клиент','договор'),('клиент','менеджер','доработ'),
              ('менеджер','клиент','измен'),('клиент','менеджер','подпис')]
    message_checks=[]
    for source,target,subject in required:
        subject_terms=('измен','исправ','доработ') if subject=='измен' else (subject,)
        target_terms=('доработ','возвращ','вернут') if subject=='доработ' else subject_terms
        message_checks.append(any(roles[nodes[m.source].participant_id]==source and roles[nodes[m.target].participant_id]==target
            and any(term in (m.name+' '+nodes[m.source].name).casefold() for term in subject_terms)
            and any(term in nodes[m.target].name.casefold() for term in target_terms) for m in p.message_flows))
    graph={key:[] for key in nodes}
    for f in p.flows:graph[f.source].append(f.target)
    def reachable(start,target):
        todo=[start];seen=set()
        while todo:
            key=todo.pop()
            if key==target:return True
            if key in seen:continue
            seen.add(key);todo.extend(graph[key])
        return False
    parallel_join=any(n.type=='parallel_gateway' and sum(f.target==n.id for f in p.flows)>=2
        and all(any(role in cats[v] and reachable(v,n.id) for v in nodes) for role in ('legal_check','security_check'))
        and any('decision' in cats[v] and reachable(n.id,v) for v in nodes) for n in p.nodes)
    report['invariants'].update(all_explicit_actions=not report['missing_actions'],
        message_business_relationships=all(message_checks),parallel_join_before_decision=parallel_join)
    report['passed']=not report['graph_errors'] and all(report['invariants'].values())
    report['fingerprint']=semantic_fingerprint(p)
    return report


def preservation(before,after):
    report={}
    for field in ('participants','pools','nodes','flows','message_flows','assumptions'):
        old={x.id:x for x in getattr(before,field)};new={x.id:x for x in getattr(after,field)}
        shared=set(old)&set(new)
        report[field]={'id_preservation':len(shared)/len(old) if old else 1,
                       'removed_ids':sorted(set(old)-set(new)),
                       'changed_ids':sorted(k for k in shared if old[k]!=new[k])}
    return report


def medium_quality(p):
    from app.validator import validate_process
    from app.semantics import validate_semantics
    actors={r.id:r.name.casefold() for r in p.participants};cats=categories(p)
    present={c for group in cats.values() for c in group}
    required={'submit','fix_documents','resend_documents','check_documents','return_documents',
              'decision','rejection','form_contract','send_contract'}
    nodes={n.id:n for n in p.nodes};loops=business_loops(p)
    doc_roles={actors[nodes[k].participant_id] for loop in loops for k in loop if any(c in cats[k] for c in ('fix_documents','resend_documents','check_documents'))}
    errors=[e.code for e in validate_process(p)+validate_semantics(p) if e.severity=='error']
    checks={'two_pools':len(p.pools)==2,'four_distinct_roles':set(actors.values())=={'клиент','оператор','руководитель','менеджер'},
            'explicit_actions':required<=present,'document_loop':{'клиент','оператор'}<=doc_roles,
            'xor_conditions':any(n.type=='exclusive_gateway' and sum(f.source==n.id and bool(f.condition) for f in p.flows)>=2 for n in p.nodes),
            'messages':all(any(actors[nodes[m.source].participant_id]==a and actors[nodes[m.target].participant_id]==b for m in p.message_flows) for a,b in [('клиент','оператор'),('оператор','клиент'),('менеджер','клиент')])}
    return {'passed':not errors and all(checks.values()),'graph_errors':errors,'invariants':checks}
