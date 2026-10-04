"""Benchmark-specific business oracle. No scenario rules enter the application."""
from collections import Counter
from app.repair import business_loops
from app.validator import validate_process
from app.semantics import validate_semantics


def quality(process, case):
    errors = [e.code for e in validate_process(process) + validate_semantics(process) if e.severity == 'error']
    result = {'graph_errors': errors, 'pools': len(process.pools), 'roles': len(process.participants),
              'tasks': sum(n.type.endswith('task') for n in process.nodes), 'messages': len(process.message_flows),
              'conditions': sum(bool(f.condition) for f in process.flows), 'business_loop_groups': len(business_loops(process))}
    if case == 'simple':
        result['invariants'] = {'one_role': len(process.participants) == 1,
            'register_check_accept_reject': all(any(word in n.name.casefold() for n in process.nodes) for word in ('регистр', 'провер', 'при', 'откл')),
            'xor': any(n.type == 'exclusive_gateway' for n in process.nodes),
            'two_conditions': sum(bool(f.condition) for f in process.flows) >= 2}
    else:
        roles = {r.name.casefold(): r.id for r in process.participants}
        expected = ['клиент', 'оператор', 'юридический отдел', 'служба безопасности', 'руководитель', 'менеджер']
        nodes = {n.id: n for n in process.nodes}
        role_by_id = {r.id: r.name.casefold() for r in process.participants}
        def action(role, words):
            return any(n.participant_id == roles.get(role) and all(any(stem in n.name.casefold() for stem in alternatives) for alternatives in words) for n in process.nodes)
        pairs = Counter((role_by_id.get(nodes[f.source].participant_id), role_by_id.get(nodes[f.target].participant_id)) for f in process.message_flows if f.source in nodes and f.target in nodes)
        loops = business_loops(process)
        def cyclic(role, word):
            return any(any(nodes[key].participant_id == roles.get(role) and word in nodes[key].name.casefold() for key in group) for group in loops)
        parallel = False
        for n in process.nodes:
            if n.type != 'parallel_gateway':
                continue
            targets = {nodes[f.target].participant_id for f in process.flows if f.source == n.id and f.target in nodes}
            if roles.get('юридический отдел') in targets and roles.get('служба безопасности') in targets:
                parallel = True
        result['invariants'] = {
            'two_pools': len(process.pools) == 2,
            'six_distinct_roles': len(roles) == 6 and all(role in roles for role in expected),
            'legal_security_parallel': parallel,
            'approval_xor': any(n.type == 'exclusive_gateway' and any('одобр' in (f.condition or f.name).casefold() for f in process.flows if f.source == n.id) for n in process.nodes),
            'document_correction_loop': cyclic('клиент', 'документ') and cyclic('оператор', 'документ'),
            'contract_correction_loop': cyclic('клиент', 'договор') and cyclic('менеджер', 'договор'),
            'eight_expected_message_directions': pairs == Counter({('клиент', 'оператор'): 2, ('оператор', 'клиент'): 2, ('клиент', 'менеджер'): 2, ('менеджер', 'клиент'): 2}),
            'contract_form_send_revise_resend_register': all(action('менеджер', words) for words in [[['формир', 'подготов', 'состав'], ['договор']], [['отправ', 'направ'], ['договор']], [['измен', 'доработ', 'исправ'], ['договор']], [['повтор'], ['договор']], [['регистр'], ['договор']]]),
            'client_review_sign_send_rework_documents': all(action('клиент', words) for words in [[['провер', 'рассмотр'], ['договор']], [['подпис'], ['договор']], [['отправ', 'направ'], ['подпис']], [['исправ', 'доработ'], ['документ']]]),
            'operator_docs_rejection_return': all(action('оператор', words) for words in [[['провер'], ['документ']], [['отказ']], [['вер', 'направ'], ['доработ']]]),
            'head_decision_separate': action('руководитель', [['решен', 'решён', 'соглас', 'одобр']]),
            'task_count_not_reduced': result['tasks'] >= 25,
            'evidence_explicit_actions_or_message_basis': all(bool(n.source_text) or n.type == 'receive_task' and any(f.target == n.id for f in process.message_flows) for n in process.nodes if n.type.endswith('task')),
        }
    result['passed'] = not errors and all(result['invariants'].values())
    return result
