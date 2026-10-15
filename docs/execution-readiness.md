# Этап B2/B3: подготовка к исполнению

POST /api/process/execution-readiness принимает ProcessDefinition и явно заданную отдельную конфигурацию. Проверка без LLM, сетевых вызовов и запуска движка. Исходная модель и XML не меняются, isExecutable=false сохраняется.

Поля запроса:

- process: текущая ProcessDefinition.
- variables: словарь с обязательным type (string/integer/number/boolean/date/object/array). Значения и default не придумываются.
- conditions: по ID SequenceFlow, expression, language, referenced_variables. Только текст пользователя; список переменных не выводится из выражения.
- tasks: по ID задачи, implementation, assignee/candidate_groups, integration. Предоставленные ссылки/назначения, без секретов.
- messages: по ID MessageFlow, message_name, correlation_mode (new_instance/existing_instance/unresolved), correlation_variables.
- executable_pool_ids: внутренние Pool, явно выбранные пользователем. Внешние участники не становятся исполняемыми.
- error_policy_element_ids / timeout_policy_element_ids: декларации о предусмотренных политиках; они не создают таймеры, обработчики или сроки.

Ответ перечисляет element_ids и недостающие настройки. Пока всегда engine=null, engine_selected=false, execution_verified=false, deployable=false, supplied_configuration_verified=false, status=requires_configuration. Настройка, предоставленная пользователем, не является проверкой в движке. Выражения не исполняются и не возвращаются в отчёте. Проверяются только явно перечисленные ссылки на словарь; синтаксис и boolean-тип результата ждут выбранного профиля.

Архитектура: execution/models.py — отдельная конфигурация вне LLM-контракта; readiness.py — read-only проверка; profiles.py — Protocol EngineProfile для проверок выражений и задач. Конкретных профилей, автоматического выбора, deployment client и генерации исполняемого XML нет. Реализация профиля требует решения пользователя.

Роль в lane не считается назначением пользователя. Service task может иметь локальную реализацию, поэтому внешний API для каждой задачи не требуется. Default, переменные и таймауты не создаются автоматически. Ограничения структурного AND-анализа остаются предупреждениями, не доказательствами невозможности исполнения. Настройки интеграций и политик не проверены инфраструктурой.

Endpoint защищён существующим контролем доступа и размера тела. Изменены execution/, main.py и тесты. Концептуальные Builder, ProcessDefinition, провайдеры, frontend, .env и Amvera в этапе B не менялись.

Испытания движков — deployment, запуск, решения, циклы, корреляция, завершение и ошибки/таймауты — **not verified**. Этап C не выполнялся.

## Проверка этапа B

Полный backend: **629 passed**, включая **17 новых** проверок readiness. Одно существующее предупреждение о deprecated TestClient. Проверены неизменность ProcessDefinition/XML, отсутствие исполнения выражений, явные типы переменных, неизвестные ID, ссылки на необъявленные переменные, область внутренних/внешних Pool, отличие роли от назначения пользователя, сообщения, защита endpoint и отсутствие LLM-вызовов. Git diff проверен. Frontend не менялся, браузерные тесты в этом этапе не повторялись. Платных запросов нет.

Риски: предоставленный список referenced_variables может быть неполным; без парсера языка нельзя доказать полноту ссылок. Декларации ошибок/таймаутов и task bindings не подтверждают существование инфраструктуры. Профиль и движок должны проверить типы, синтаксис, реализации и runtime-поведение. До выбора и испытаний deployable остаётся false.
