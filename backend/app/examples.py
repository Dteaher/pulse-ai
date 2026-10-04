from .models import ProcessDefinition, Node, Flow, Participant, Ambiguity

TEXTS = [
    'Клиент отправляет заявку. Менеджер проверяет заявку. После проверки менеджер принимает решение и уведомляет клиента.',
    'Клиент отправляет заявку на подключение. Оператор проверяет документы. Если документов не хватает, оператор возвращает заявку клиенту на доработку. После получения корректных документов заявку одновременно проверяют юридический отдел и служба безопасности. После завершения обеих проверок руководитель принимает решение. Если заявка одобрена, менеджер готовит договор и отправляет его клиенту. Если заявка отклонена, клиент получает уведомление об отказе.',
    'Клиент отправляет заявку. Менеджер проверяет документы. Потом её смотрят юрист и безопасность. После этого руководитель решает, что делать.'
]


def demo_process(index=1):
    if index == 0:
        parts = [('Client', 'Клиент'), ('Manager', 'Менеджер')]
        specs = [('Start', 'start_event', 'Заявка поступила', 'Client'),
                 ('Submit', 'user_task', 'Отправить заявку', 'Client'),
                 ('Check', 'user_task', 'Проверить заявку', 'Manager'),
                 ('Decide', 'user_task', 'Принять решение', 'Manager'),
                 ('Notify', 'user_task', 'Уведомить клиента', 'Manager'),
                 ('End', 'end_event', 'Клиент уведомлён', 'Manager')]
        edges = [(a[0], b[0], '') for a, b in zip(specs, specs[1:])]
    else:
        parts = [('Client', 'Клиент'), ('Operator', 'Оператор'), ('Legal', 'Юридический отдел'),
                 ('Security', 'Служба безопасности'), ('Head', 'Руководитель'), ('Manager', 'Менеджер')]
        specs = [('Start', 'start_event', 'Потребность в подключении', 'Client'),
                 ('Submit', 'user_task', 'Отправить заявку', 'Client'),
                 ('Check', 'user_task', 'Проверить документы', 'Operator'),
                 ('Complete', 'exclusive_gateway', 'Документы полные?', 'Operator'),
                 ('Return', 'user_task', 'Вернуть на доработку', 'Operator'),
                 ('Revise', 'user_task', 'Дополнить документы', 'Client'),
                 ('Fork', 'parallel_gateway', 'Начать проверки', 'Operator'),
                 ('LegalCheck', 'user_task', 'Юридическая проверка', 'Legal'),
                 ('SecurityCheck', 'user_task', 'Проверка безопасности', 'Security'),
                 ('Join', 'parallel_gateway', 'Проверки завершены', 'Head'),
                 ('Decide', 'user_task', 'Принять решение', 'Head'),
                 ('Approved', 'exclusive_gateway', 'Заявка одобрена?', 'Head'),
                 ('Contract', 'user_task', 'Подготовить договор', 'Manager'),
                 ('Send', 'user_task', 'Отправить договор', 'Manager'),
                 ('Reject', 'user_task', 'Уведомить об отказе', 'Client'),
                 ('EndYes', 'end_event', 'Договор отправлен', 'Manager'),
                 ('EndNo', 'end_event', 'Отказ получен', 'Client')]
        edges = [('Start', 'Submit', ''), ('Submit', 'Check', ''), ('Check', 'Complete', ''),
                 ('Complete', 'Return', 'Нет'), ('Return', 'Revise', ''), ('Revise', 'Check', ''),
                 ('Complete', 'Fork', 'Да'), ('Fork', 'LegalCheck', ''), ('Fork', 'SecurityCheck', ''),
                 ('LegalCheck', 'Join', ''), ('SecurityCheck', 'Join', ''), ('Join', 'Decide', ''),
                 ('Decide', 'Approved', ''), ('Approved', 'Contract', 'Да'), ('Approved', 'Reject', 'Нет'),
                 ('Contract', 'Send', ''), ('Send', 'EndYes', ''), ('Reject', 'EndNo', '')]
    sentences = TEXTS[index].split('. ')
    evidence = {'Submit': sentences[0], 'Check': sentences[1], 'Return': sentences[2] if index == 1 else '',
                'LegalCheck': sentences[3] if index == 1 else '', 'SecurityCheck': sentences[3] if index == 1 else '',
                'Decide': sentences[4] if index == 1 else sentences[-1],
                'Contract': sentences[5] if index == 1 else '', 'Send': sentences[5] if index == 1 else '',
                'Reject': sentences[-1] if index == 1 else '', 'Notify': sentences[-1]}
    return ProcessDefinition(id='Process_Connection' if index else 'Process_Application',
        name='Подключение к электросети' if index else 'Обработка заявки', description=TEXTS[index],
        participants=[Participant(id=i, name=n) for i, n in parts],
        nodes=[Node(id=i, type=t, name=n, participant_id=p, source_text=evidence.get(i, ''),
                    confidence='high' if evidence.get(i) else 'confirmation_required', inferred=not bool(evidence.get(i))) for i, t, n, p in specs],
        flows=[Flow(id=f'Flow_{i}', source=s, target=t, name=c, condition=c or None) for i, (s, t, c) in enumerate(edges)])


def ambiguous_process():
    p = demo_process()
    p.description = TEXTS[2]
    p.ambiguities = [Ambiguity(id='parallel', question='Юрист и служба безопасности работают параллельно или последовательно?',
                             related_node_ids=['Fork'], suggested_answers=['Параллельно', 'Последовательно']),
                     Ambiguity(id='refusal', question='Что происходит при отказе руководителя?', related_node_ids=['Approved'],
                               suggested_answers=['Уведомить клиента об отказе', 'Вернуть документы на доработку'])]
    for n in p.nodes:
        n.source_text = ''
        n.inferred = True
        n.confidence = 'confirmation_required'
    return p
