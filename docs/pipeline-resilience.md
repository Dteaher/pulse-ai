# Устойчивость pipeline PULSE

Проверка: 3 октября 2026. Архитектура, интерфейс редактора, MultiAI GPT-6.1 Sol,
Vertex Gemini, детерминированный builder, ChangeSet и exact Undo сохранены.
Это доработка существующего BPMN subset; полный BPMN 2.0.2 не заявляется.

## 1. Найденные причины

Clarify раньше зависел от `ambiguities` уже построенного ProcessDefinition.
Граф проверялся до показа вопросов, поэтому недостающая бизнес-информация
могла запускать corrective retry. Общий обработчик ValueError охватывал
ответ модели и сборку XML. Некоторые выводы Doctor не имели отдельной
классификации. Локальные QName проверялись не для всех reference-полей,
а ранний выход для unsupported XML мог пропускать проверку ссылок.

## 2. Почему Clarify не запускался

Вопрос попадал к пользователю только после прохождения графовой проверки.
LLM, угадавшая неизвестного исполнителя или продолжение возврата, могла
вернуть повреждённый граф, который исправлялся вместо уточнения текста.

## 3. Clarify gate

`LLMProvider.analyze_ambiguities` возвращает отдельный `AmbiguityAnalysis`.
Production adapters используют соответствующий API и общую Pydantic-модель.
При critical возвращаются вопросы, `process=null`, `xml=null` и `preflight`;
ни extraction, ни builder не вызываются. `/clarify` сохраняет исходный текст,
вопросы и ответы, повторно анализирует достаточность информации и вызывает
extraction/modify только после снятия критических вопросов. Максимум три
вопроса за раунд и `MAX_CLARIFICATION_ROUNDS`, по умолчанию 3. Критичные
вопросы не принимаются как assumptions, включая последний раунд.

Старый контракт `/clarify` с заполненным `process` также поддерживается.
`warning` сохранено как API-наименование optional. Fixture mock использует
старые встроенные вопросы; произвольный текст не подменяется fixture.

## 4. Critical ambiguity

Неизвестный исполнитель, принципиальный порядок/параллельность,
необходимый недостающий исход решения и неизвестная точка продолжения
возврата. Анализ семантический, через LLM, без списка русских фраз.
Линейная проверка сама по себе не требует придуманной отрицательной ветки.
Косметические вопросы не блокируют production-генерацию.

## 5. Assumptions

`ProcessDefinition.assumptions`: id, text, source (`model_inference` или
`user_accepted`), confidence 0..1. Optional детали сохраняются явно.
Существенную неопределённость нельзя автоматически заменить предположением.
Assumptions передаются в Clarify/Modify/Doctor, сохраняются в snapshot Undo,
а в BPMN — дополнительной Documentation `PULSE_ASSUMPTIONS:`. Основное
описание остаётся первой Documentation. Импорт без previous также
восстанавливает assumptions. Явная Modify-команда отменяет только
противоречащие ей assumptions; кандидат не может вернуть отменённое значение.
В Copilot показаны компактный счётчик и список с устранением дублей ID.

## 6. Taxonomy

| Тип | Назначение |
|---|---|
| SPEC_ERROR | Нарушение BPMN / поддерживаемого subset |
| MODEL_ERROR | Некорректный typed result или противоречие структуры |
| CLARIFICATION_REQUIRED | Недостающая бизнес-информация |
| INTEROPERABILITY_ERROR | Сборка XML, XSD, DI, импорт, unsupported construct, транспорт LLM |
| LAYOUT_WARNING | Качество размещения |
| BUSINESS_WARNING | Риск или рекомендация Doctor |

Issue содержит code, severity, type, category, message, node_ids и отдельные
идентификаторы узла/связи/шлюза/процесса, spec_section, suggested_fix.
API сохраняет `detail`, дополнительно возвращает `human_message`,
`diagnostics` и request_id для ошибок pipeline/provider. Parser/XSD/Pydantic
trace не передаётся пользователю; подробности остаются в developer logs.

## 7. Добавленные diagnostic codes

`CLARIFICATION_REQUIRED`, `BPMN_BUILD_FAILED`, `MODEL_SCHEMA_INVALID`,
`UNBOUNDED_REWORK_LOOP`, `INVALID_PROCESS_INPUT`, `REQUEST_SCHEMA_INVALID`,
`INTERNAL_PIPELINE_ERROR`. Транспорт использует `LLM_<REASON>`: например
`LLM_TIMEOUT`, `LLM_HTTP_429`, `LLM_INVALID_RESPONSE`, `LLM_NETWORK_ERROR`.
Существующие `INVALID_BPMN_XML`, `BROKEN_REFERENCE`, `INVALID_DI_REFERENCE`,
`DUPLICATE_ID`, `OUTSIDE_PULSE_SUBSET` расширены, а не переименованы.

## 8. Validator

Проверяет Pydantic, целостность графа, membership, локальную достижимость
каждого Process, разрешённые связи/события/шлюзы, явные противоречия исходов
и поддерживаемые parallel split/join. Семантическая проверка выполняется
также для одно-пуловой extraction. MessageFlow не участвует в token reachability.
Несколько Start Events и цикл с выходом не становятся ошибкой автоматически.

## 9. Doctor

Бизнес-риски и рекомендации: неподтверждённые сведения, неопределённость
отказа, ручные согласования, предел повторной обработки. Циклические SCC
дают BUSINESS_WARNING о том, что предел повторов не представлен в subset.
Это не доказательство бесконечного исполнения. Предположения передаются
в аудит. LLM-предложения Doctor маркируются бизнес-наблюдениями и не
получают нормативную spec_reference. Ошибки XML/reference проверяются
программно; для повреждённого или unsupported документа AI-аудит не запускается.

## 10. Corrective retry

Для графа: initial + один focused corrective retry, затем необязательный
fallback. `LLM_GRAPH_MAX_RETRIES=1` (допускается 0..2), ограничено также
`LLM_MAX_RETRIES`. JSON/Pydantic retries внутри provider отдельно подчиняются
`LLM_MAX_RETRIES`. Feedback содержит точные Issue и предыдущий typed result.
Critical ambiguity немедленно возвращается в Clarify и не исправляется
угадыванием. Ошибка builder/post-build не запускает LLM retry.
Старый тест восстановления после двух графовых retries сохранён с явной
настройкой `max_retries=2`; fallback-тест теперь проверяет новый бюджет 1.

## 11. Post-build

Безопасный XML parse, официальные XSD, локальные reference integrity и DI.
Проверяются sourceRef, targetRef, processRef, flowNodeRef, bpmnElement,
messageRef, eventDefinitionRef, incoming/outgoing, default и participantRef.
Проверка ссылок/дублированных ID выполняется до выхода для unsupported subset.
Для поддерживаемых references проверяется тип адресата. Полный экспорт
PULSE требует Shape/Edge каждого элемента; частичный DI в общем BPMN
может быть допустим. Ошибка DI reference отдельно классифицируется как
interoperability. bpmn-js импортируется в реальном браузере при проверках
и в frontend до применения результата. Manual XML сохраняется для
редактирования/экспорта, если AI importer его не поддерживает.

## 12. Regression tests

38 новых backend cases: graph-free gate, ответы и дополнительный раунд,
лимит, optional без блокировки, Modify сохраняет v1, assumptions, оба XML
roundtrip, отмена конфликтующего assumption, отсутствие LLM retry при
builder/XSD ошибке, critical на повреждённом draft, циклы с выходом, точный
сложный пример, HTTP DTO analysis, failover, стабильность ID, DI taxonomy,
бюджет retry, одно-пуловая семантика. 12 новых browser/frontend cases:
graph-free UI, импорт реальных XML и эталона, exact Undo, TO-BE Clarify,
assumptions через bpmn-js export/import. Старые cases не удалены.

## 13. Adversarial tests

Orphan, dead end, неизвестный participant, отсутствующая parallel branch,
XOR вместо parallel, parallel вместо XOR, event gateway с недопустимыми
successors, dangling MessageFlow, duplicate Flow, cross-pool SequenceFlow.
Также проверяются отсутствующие processRef/messageRef/eventDefinitionRef/DI
references. Предыдущие mutation/invariant tests сохранены.

## 14. Exact complex scenario

Исходный текст сохранён дословно в `examples/pipeline-resilience/exact-complex-input.txt`.
Эталон `exact-reference` включает документы на доработке, legal/security
parallel split/join, решение руководителя XOR, клиентский цикл согласования
договора, регистрацию, 2 pools, 6 ролей и 8 MessageFlow.
Реальная extraction MultiAI сначала создала cross-pool SequenceFlow:
validator отклонил их, один графовый corrective вернул валидный XML.
`exact-complex.bpmn` — этот результат, без ручного переписывания XML.
Conditional flows от действий допустимы BPMN, но для требуемого явного XOR
выполнена отдельная явная Modify-команда о представлении решений и удалении
дублированного получения первоначальной заявки. Результат:
`exact-complex-polished.bpmn`, 40 nodes, 2 pools, 6 ролей, 8 сообщений,
явные XOR и 97,44% прежних node IDs. ChangeSet содержит 11 изменений.
Оба XML проходят XSD/reference/DI и bpmn-js.

## 15. Реальный GPT-6.1 Sol

Provider `multiai`, model `gpt-6.1-sol`, без fallback. Simple и medium:
HTTP 200 анализа + HTTP 200 extraction, BPMN получен. Complex: то же,
178,10 с. Ambiguous: HTTP 200 анализа, два critical вопроса и ноль extraction.
После ответов был найден третий неизвестный исполнитель доработки; после
второго раунда реальный pipeline создал BPMN. Exact complex занял 273,93 с
с одним графовым corrective. Явный Modify — HTTP 200 и BPMN за 147,28 с.
JSON evidence и входные тексты находятся в `examples/pipeline-resilience`.
Это наблюдения отдельных реальных запросов, не гарантия безошибочности LLM.

## 16. Реальный Vertex fallback

В тестовом триггере primary программно вернул timeout; сам вызов
`vertex_gemini / gemini-2.5-flash` был реальным. Анализ и extraction выполнены
резервом, API 200, `fallback_used=true`, BPMN прошёл проверки и bpmn-js.
Это контролируемая проверка маршрутизации, не заявление о реальном падении MultiAI.

## 17–19. Проверки

Полный итог: 313 backend + 55 browser = 368 passed; TypeScript и production build прошли. Результаты тестов и build: `examples/pipeline-resilience/checks.json`.
В production build возможное предупреждение размера основного JS chunk
сохраняется; оно не является ошибкой сборки. Backend имеет предупреждение
о переходе FastAPI TestClient с httpx на httpx2.

## 20. Ограничения

LLM может пропустить неоднозначность или неверно интерпретировать текст:
детерминированные проверки не доказывают полную бизнес-эквивалентность.
Supported subset не исполняемый; нет универсальной token simulation,
General OR synchronization и полного BPMN runtime. External QName resources
не загружаются автоматически. Оценка ограничения повторов — бизнес-риск,
так как отдельного retry-bound в subset нет. Латентность зависит от LLM;
сложный запрос с corrective может приблизиться к frontend timeout 300 с.
Оптимальная укладка всех сложных графов не гарантируется. Добавление
`process=null/preflight` расширяет response-contract: старые клиенты должны
учитывать состояние до extraction. Текущий frontend это состояние поддерживает.

## Измерения и безопасность

Timings: ambiguity_analysis_ms, llm_parse_ms, normalization_ms,
semantic_validation_ms, bpmn_build_ms, xsd_validation_ms,
post_validation_ms, total_ms. Итоговые stage durations учитывают графовые
retries; total включает pipeline и начальный анализ. Dev logs имеют
request ID, operation, provider/model, attempt, fallback, Issue и timings.
Секретный `.env` не менялся, ключи в API/артефакты не включались.
