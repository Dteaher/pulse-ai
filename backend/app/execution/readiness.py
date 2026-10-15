"""Read-only engine-neutral readiness. User declarations are not engine verification.
Expressions are never parsed as code or evaluated; referenced variables must be
supplied explicitly. No names, types, assignments or integrations are inferred.
"""
from .models import ExecutionReadinessRequest, ExecutionReadinessResult
from ..models import Issue
from ..validator import validate_process
from ..semantics import validate_semantics


def assess_readiness(request: ExecutionReadinessRequest) -> ExecutionReadinessResult:
    p = request.process
    issues = []
    def finding(code, ids, description, reason, suggestion, severity='warning'):
        issues.append(Issue(code=code, category='execution', severity=severity,
            source='BUSINESS_LOGIC', node_ids=ids, message=description,
            reason=reason, suggested_fix=suggestion, suggestion=suggestion))
    finding('EXECUTION_ENGINE_NOT_SELECTED', [p.id], 'Движок и его профиль не выбраны.',
        'Настройки разных движков несовместимы; исполнение и деплой не проверялись.',
        'Выберите движок перед реализацией конкретного профиля.')
    graph = validate_process(p)
    if not any(i.severity=='error' for i in graph):
        semantic = validate_semantics(p)
        for issue in semantic:
            if issue.code == 'PARALLEL_JOIN_MISMATCH':
                issue.severity = 'warning'
                issue.confidence = 0.6
                issue.reason = 'Структурное ограничение PULSE не доказывает невозможность исполнения в выбранном движке.'
        graph += semantic
    issues.extend(graph)
    nodes = {n.id:n for n in p.nodes}
    flows = {f.id:f for f in p.flows}
    messages = {f.id:f for f in p.message_flows}
    pools = {pool.id:pool for pool in p.pools}
    roles = {r.id:r for r in p.participants}
    for collection, supplied, allowed in [('conditions',request.conditions,flows),('tasks',request.tasks,nodes),('messages',request.messages,messages)]:
        for key in supplied.keys()-allowed.keys():
            finding('EXECUTION_UNKNOWN_BINDING', [key], 'Настройка ссылается на отсутствующий элемент.',
                'Нельзя связать конфигурацию с текущей версией модели.', 'Укажите существующий ID '+collection+'.',severity='error')
    for key in request.tasks.keys() & nodes.keys():
        if not nodes[key].type.endswith('task'):
            finding('EXECUTION_BINDING_TARGET_TYPE',[key], 'Настройка задачи привязана к событию или шлюзу.',
                'Конфигурации действий и событий имеют разную семантику.', 'Привяжите настройку к задаче.',severity='error')
    for key in request.conditions.keys() & flows.keys():
        if flows[key].is_default:
            finding('EXECUTION_DEFAULT_CONDITION_IGNORED',[key], 'Для default-ветви предоставлено условие.',
                'BPMN игнорирует условие default flow.', 'Удалите условие или явно измените ветвление.')
    for key in request.executable_pool_ids:
        if key not in pools or pools[key].kind=='external':
            finding('EXECUTION_INVALID_POOL_SCOPE',[key], 'Pool не существует или является внешним.',
                'Внешний участник не назначается исполняемым без изменения бизнес-модели.', 'Выберите внутренний Pool.',severity='error')
    if pools and not request.executable_pool_ids:
        finding('EXECUTION_POOL_SCOPE_REQUIRED',list(pools), 'Область исполнения не задана.',
            'Collaboration содержит независимые процессы и внешних участников.', 'Явно выберите внутренние Pool для исполнения.')
    for name, supplied in [('error',request.error_policy_element_ids),('timeout',request.timeout_policy_element_ids)]:
        for key in set(supplied)-nodes.keys():
            finding('EXECUTION_UNKNOWN_POLICY_ELEMENT',[key], 'Политика ссылается на отсутствующий элемент.',
                'Политика не может быть привязана к текущему графу.', 'Укажите существующий ID для '+name+'.',severity='error')
    def selected(node):
        role=roles.get(node.participant_id)
        if not role:return False
        if not pools:return role.kind!='external'
        pool=pools.get(role.pool_id)
        return bool(pool and pool.kind=='internal' and (not request.executable_pool_ids or pool.id in request.executable_pool_ids))
    def variables(ids, names):
        missing=sorted(set(names)-request.variables.keys())
        if missing:
            finding('EXECUTION_VARIABLE_UNDECLARED',ids,'Используемые переменные не объявлены: '+', '.join(missing),
                'Ссылки проверены только по явно заданному словарю пользователя.', 'Добавьте явные типы переменных.',severity='error')
    for flow in p.flows:
        source=nodes.get(flow.source)
        if not source or not selected(source):continue
        conditional=source.type in {'exclusive_gateway','inclusive_gateway'} or bool(flow.condition)
        if conditional and not flow.is_default and (flow.condition or sum(f.source==source.id for f in p.flows)>1):
            binding=request.conditions.get(flow.id)
            if binding is None:
                finding('EXECUTION_FORMAL_CONDITION_REQUIRED',[flow.id,source.id], 'Формальное условие перехода не задано.',
                    'Описание ветви на естественном языке не является исполняемым выражением.', 'Задайте выражение, язык и список используемых переменных.')
            else:
                variables([flow.id],binding.referenced_variables)
                finding('EXECUTION_CONDITION_PROFILE_UNVERIFIED',[flow.id], 'Условие предоставлено, но не проверено профилем движка.',
                    'Список переменных предоставлен пользователем; синтаксис и тип результата не проверены.', 'Проверьте условие после выбора профиля.',severity='info')
    for node in p.nodes:
        if not selected(node):continue
        binding=request.tasks.get(node.id)
        if node.type in {'task','service_task','script_task','send_task'} and not (binding and binding.implementation):
            finding('EXECUTION_TASK_IMPLEMENTATION_REQUIRED',[node.id], 'Реализация действия не задана.',
                'BPMN-элемент описывает работу, но не задаёт её исполняемый механизм.', 'Явно задайте ручную реализацию, worker или скрипт после выбора движка.')
        if node.type=='user_task' and not (binding and (binding.assignee or binding.candidate_groups)):
            finding('EXECUTION_USER_ASSIGNMENT_REQUIRED',[node.id], 'Исполнитель пользовательской задачи не настроен.',
                'Роль или дорожка не является назначением пользователя в движке.', 'Задайте исполнителя или группы кандидатов.')
        if node.type in {'send_task','receive_task'} and not (binding and binding.integration):
            finding('EXECUTION_INTEGRATION_REQUIRED',[node.id], 'Интеграция отправки/получения не настроена.',
                'MessageFlow не реализует передачу данных между системами.', 'Предоставьте явную конфигурацию интеграции без секретов.')
        if node.type in {'service_task','script_task','send_task','receive_task'}:
            if node.id not in request.error_policy_element_ids:
                finding('EXECUTION_ERROR_POLICY_REQUIRED',[node.id], 'Обработка ошибок не настроена.',
                    'В исходной модели не определена политика отказа интеграции или задачи.', 'Определите требуемую обработку ошибок.')
        if node.type=='receive_task' or node.event_definition=='message':
            if node.id not in request.timeout_policy_element_ids:
                finding('EXECUTION_TIMEOUT_POLICY_UNSPECIFIED',[node.id], 'Политика срока ожидания не задана.',
                    'Бессрочное ожидание допустимо, но должно быть осознанным решением.', 'Явно определите политику ожидания.')
    for message in p.message_flows:
        endpoints=[nodes.get(message.source),nodes.get(message.target)]
        if not any(n and selected(n) for n in endpoints):continue
        binding=request.messages.get(message.id)
        if binding is None:
            finding('EXECUTION_MESSAGE_CONFIGURATION_REQUIRED',[message.id,message.source,message.target], 'Имя и корреляция сообщения не настроены.',
                'Формальное messageRef не определяет привязку сообщения к экземпляру процесса.', 'Задайте имя и явную стратегию корреляции после выбора движка.')
        else:
            variables([message.id],binding.correlation_variables)
            if binding.correlation_mode=='unresolved' or (binding.correlation_mode=='existing_instance' and not binding.correlation_variables):
                finding('EXECUTION_CORRELATION_STRATEGY_REQUIRED',[message.id], 'Стратегия корреляции не определена.',
                    'Запуск нового процесса отличается от доставки в существующий экземпляр.', 'Явно задайте режим и необходимые ключи корреляции.')
            finding('EXECUTION_MESSAGE_PROFILE_UNVERIFIED',[message.id], 'Конфигурация сообщения предоставлена, но не проверена.',
                'Не каждый старт сообщения требует корреляции с существующим экземпляром; это определяет профиль.', 'Проверьте запуск и корреляцию в выбранном движке.',severity='info')
    return ExecutionReadinessResult(issues=issues)
