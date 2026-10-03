# PULSE: аудит BPMN 2.0.2 и границы поддерживаемого subset

Дата проверки: 03.10.2026. Источник правил — OMG BPMN 2.0.2, приложенный PDF
`BPMN-2-0-2-specification-ENG (1).pdf`. Номера страниц ниже — печатные страницы
спецификации, а не индекс PDF (основной текст начинается на физической странице 31).
Проверка проведена до изменения serializer/validator, затем повторена на результатах.

## A. Specification audit и target conformance

Изучены разделы 2.1–2.4 (conformance и subclasses), 7.6 (правила соединений),
8.2–8.3 (Definitions, BaseElement, ID, documentation), 8.4.6 (Expression),
8.4.13 (Sequence Flow/FlowNode), 9.3–9.4 (Participant, Pool, Lane, Message Flow),
10.3 (Activity/Tasks), 10.5.2–10.5.4 (Start/End/Intermediate),
10.6.1–10.6.6 (Gateway), 10.7 (Lane), 12.1–12.3 (BPMN DI),
15.3.2 (XML exchange/XSD). Прочитаны таблицы событий и ограничений, а также
изображение Event-Based Gateway с альтернативными Receive Tasks/Message Catch.

**Target: PULSE non-executable Process/Collaboration profile v1**, ограниченный
описательными бизнес-процессами, перечисленными в матрице ниже. Это выбранные
элементы Descriptive/Analytic, а не заявление соответствия всей subclass.
По §2.1–2.2 класс требует всех конструкций и атрибутов соответствующей таблицы;
PULSE не поддерживает, например, SubProcess, CallActivity и весь набор Events.
Execution, Choreography и BPEL conformance не заявляются. XML имеет
`isExecutable=false`; исполнение скриптов, условий, отправка реальных сообщений
и корреляция не реализуются.

SUPPORTED означает корректную генерацию и проверку **в границах указанного
профиля**, не полную реализацию metamodel BPMN для этого класса. PARTIAL обозначает
сохраняющееся ограничение реализации. MISSING — обязательное правило профиля,
которое не реализовано; после этого прохода известных MISSING правил профиля нет.
NOT IN SCOPE нельзя автоматически преобразовывать в ProcessDefinition с потерей
семантики: доступен ручной редактор/экспорт исходного XML.

## B. Before: результат внутреннего аудита

| Найденное расхождение | Источник | Исправление |
|---|---|---|
| None Intermediate записывался `intermediateCatchEvent` | §10.5.4 Table 10.89, с.250 | None → Throw; Message → Catch |
| Шлюз с несколькими входами/выходами получал Diverging | §10.6.1, с.288–289 | Mixed; проверка атрибута в XML |
| Русский текст условий записывался FormalExpression, с неявным XPath | §8.4.6, с.82–84 | Expression + documentation, без исполняемого body |
| Отсутствие явного start/end всегда считалось ошибкой | §10.5.2–10.5.3, с.238–246 | Допустимы implicit roots/sinks; явные события должны присутствовать парой |
| Несколько исходящих Activity требовали gateway | §8.4.13; §10.3 | Допустимы implicit split и conditional/default Activity flows |
| Имя Start как действие блокировало XML | Нет такого MUST в BPMN | MODEL_QUALITY warning |
| Message Start без показанного Message Flow блокировал XML | §10.5.2, с.245: zero or more | Warning, отправитель может быть не показан |
| Event-Based Gateway требовал ровно один вход | §10.6.6 не требует одного входа | Допускаются дополнительные входы, successors остаются отдельными ожиданиями |
| Dict импорта одного Process мог перезаписать повторное lane membership | §10.7 | Проверка повторов до построения модели |
| XSD не дополнялась проверкой типов/областей XML-ссылок и DI | §15.3.2; §12.2.3 | Отдельный document validator |
| Doctor смешивал warnings, стандарт и бизнес-риски | Продуктовая диагностика | severity/source/spec_section и XSD line/column |

Имена действий/дорожек, наличие видимого отправителя Message Start, явные XOR
merge для читаемости и параллельный join-паттерн не объявляются требованиями
стандарта. Для None Start с incoming Message Flow предпочтение Message Start
выдаётся как рекомендация профиля: §10.5.2 допускает targeting Start Events.

## C–D. Conformance matrix: текущие генератор, importer и validator

| Конструкция / проверяемая часть | Статус | Спецификация | Реализация / граница |
|---|---|---|---|
| Definitions, MODEL namespace, targetNamespace, exporter | SUPPORTED | §8.2.1; §15.3.2 | Детерминированный root; official XSD |
| BaseElement ID / documentation | SUPPORTED | §8.3.1 | Уникальность всех model/DI ID, escaping XML, source_text в documentation |
| Process | SUPPORTED | §10.2 | Один или несколько независимых non-executable Process |
| Collaboration, expanded Participant/Pool, processRef | SUPPORTED | §9.3–9.4 | По одному local Process на Pool; refs проверяются |
| LaneSet/Lane/flowNodeRef | SUPPORTED | §10.7, с.304–308 | Одно плоское разбиение на роли в каждом внутреннем Pool |
| Task, UserTask, ServiceTask, ScriptTask | SUPPORTED | §10.3.3 | Описательные элементы и стандартные маркеры bpmn-js; без execution configuration |
| SendTask/ReceiveTask | SUPPORTED | §10.3.3 | Обмен между участниками, Receive не instantiate, нет operationRef/messageRef |
| None Start / None End | SUPPORTED | §10.5.2–10.5.3 | Нельзя incoming Sequence на Start / outgoing на End; несколько начал/концов допустимы |
| Implicit starts/ends | SUPPORTED | §10.5.2–10.5.3 | Когда оба вида явных событий отсутствуют; sources/sinks Activity/Gateway |
| Message Start / messageEventDefinition | SUPPORTED | §10.5.2 | Inline definition без messageRef; внешний отправитель необязателен |
| Message Intermediate Catch | SUPPORTED | §10.5.4 | Только catch message; sequence вход/выход; нет boundary attachment |
| None Intermediate Throw | SUPPORTED | §10.5.4 Table 10.89 | None Catch отклоняется; no message sending |
| Exclusive split / merge / Mixed | SUPPORTED | §10.6.1–10.6.2 | Data-based условия, default, направление, независимые альтернативные пути |
| Parallel split / join | SUPPORTED | §10.6.4 | Без condition/default; независимые токены, structured pairing policy дополнительно |
| Inclusive split / join | PARTIAL | §10.6.3 | Сериализация и refs корректны; нет общего OR-join token activation solver |
| Event-Based Gateway | SUPPORTED | §10.6.6, с.296–299 | Exclusive, instantiate=false (defaults); ≥2 Receive либо Message Catch, не смешиваются |
| Event-Based successors | SUPPORTED | §10.6.6 | Разные цели; нет дополнительных incoming; нет conditions/default |
| SequenceFlow/incoming/outgoing/default | SUPPORTED | §8.4.13; §10.3 | Внутри Process; ends/default/source types; объявленные refs соответствуют endpoints |
| Conditional Activity / implicit merge | SUPPORTED | §8.4.13 | Условная Activity требует другой outgoing; several incoming не требуют обязательного merge |
| Natural-language conditionExpression | SUPPORTED | §8.4.6 | tExpression/documentation; bpmn-js может убрать redundant xsi:type при экспорте |
| MessageFlow | SUPPORTED | §9.4 | Collaboration-level, разные Pool, допустимые endpoints; не добавляется в token reachability |
| Cycles as explicit Sequence Flow | SUPPORTED | §8.4.13; §10.6 | Возвраты, включая XOR; проверка exit path — политика PULSE, не proof of termination |
| QName/IDREF integrity | SUPPORTED | §15.3.2 | Local refs; QName prefix к targetNamespace; Sequence source/target именно IDREF |
| BPMNDiagram/Plane/Shape/Edge | SUPPORTED | §12.2.3 | Semantic refs, один semantic element на Plane, Bounds, ≥2 waypoints |
| Bounds/waypoints/Label Bounds | SUPPORTED | §12.2.3; §12.3 | Finite неотрицательные координаты, положительные размеры, semantic kind refs |
| Полный DI экспорт | SUPPORTED | PULSE profile поверх §12.1 | Каждый generated element изображён; partial diagram допустим для входного BPMN |
| Layout readability / оптимизация пересечений | PARTIAL | Продуктовое качество | Детерминированные ряды и коридоры возвратов; не доказана оптимальность layout/отсутствие любых пересечений |
| Official XSD | SUPPORTED | §15.3.2 | Локальная cached XMLSchema; ошибки с line/column, без сети |
| Формальные языки условий / Script body / service implementations | NOT IN SCOPE | §8.4.6; §10.3 | Не генерируются и не выполняются; сторонние FormalExpression не упрощаются в AI |
| Timer/Signal/Error/Terminate/Boundary/Throw Message/Message End | NOT IN SCOPE | §10.5 | Не представлены текущим NodeType/event_definition |
| SubProcess, CallActivity, transactions, compensation, loop characteristics | NOT IN SCOPE | §10.3 | Циклы graphs поддержаны, loop/multi-instance execution markers — нет |
| Black-box Pool, nested lanes, multiple LaneSets per Process | NOT IN SCOPE | §9.3; §10.7 | Валидные BPMN, но за границами AI-модели PULSE |
| DataObject/Store, Associations, Groups, Annotations | NOT IN SCOPE | §§8,10 | Не генерируются; нельзя заявить Descriptive completeness |
| Correlation/message definitions, conversations/choreography/execution/BPEL | NOT IN SCOPE | §§9–11,13–14 | Не реализуются |

## E. XSD и reference integrity

Пять локальных XSD: BPMN20.xsd, Semantic.xsd, BPMNDI.xsd, DI.xsd, DC.xsd.
На 03.10.2026 побайтово совпали с normative files, указанными на
[OMG BPMN 2.0.2](https://www.omg.org/spec/BPMN/2.0.2).
Официальный префикс URL: `https://www.omg.org/spec/BPMN/20100501/`.
URL и SHA256 сохранены в `backend/app/schemas/origin.json`.
XSD parser запрещает network lookup/entities; компиляция схемы кешируется.

`validate_xsd(xml)` возвращает структурированные Issue(source=XSD,
spec_section=§15.3.2, line, column). Затем `validate_document` проверяет ссылки,
FlowNode membership, направления шлюзов, события и DI. Генератор проверяет
полный DI, importer — XSD/reference integrity без навязывания полного DI чужим
диаграммам. Историческое XML с неправильным gatewayDirection можно открыть
вручную; Doctor покажет ошибку, новый builder вычисляет правильное направление.

Положительный пример: `examples/bpmn-conformance/event-wait.bpmn` проходит
XSD и complete_di. Отрицательный: Bounds width="wide" возвращает XSD error
с line/column. Более важный отрицательный пример: None Catch проходит XSD,
но document validator возвращает INVALID_NONE_CATCH с §10.5.4. Тем самым XSD
не подменяет проверку семантики или бизнес-смысла.

## F. Диагностика и оставшиеся ограничения профиля

`source`: BPMN_SPEC, BUSINESS_LOGIC, MODEL_QUALITY, LAYOUT, XSD.
`severity`: error, warning, clarification, info. Нормативные правила получают
ссылку на раздел, бизнес-эвристики не приписываются стандарту. В Doctor видна
категория; для BPMN/XSD доступна ссылка на раздел в подсказке. Ошибки модели
считаются BUSINESS_LOGIC, независимо от её попыток сослаться на спецификацию.
Неизвестные события/элементы не получают выдуманный BPMN error: XSD проверяется,
после чего указывается выход за subset. Без поддерживаемого JSON бизнес-аудит
AI не проводится. Невалидная XML получает диагностику без вызова LLM.

Дополнительные ограничения PULSE: 2–150 nodes, хотя BPMN разрешает пустой
Process; обязательная роль каждого node; один раскрытый Process на Pool;
контролируемая связность и достижение sink; отдельные split/join для mixed
parallel/inclusive; консервативная проверка structured parallel region. Это
политика MODEL_QUALITY/BUSINESS_LOGIC, не обязательная семантика всего BPMN.
Локальная графовая проверка не доказывает отсутствие deadlock любого исполнения,
завершение циклов или взаимную исключительность произвольных текстовых условий.
OR-join general token semantics не реализованы. Попадание по словам
«одобрено/отказ» — эвристика; требует просмотра аналитиком.

Legacy FormalExpression из прежнего exporter=PULSE допускается для миграции:
тот serializer записывал туда русский текст. Язык/evaluatesToTypeRef не теряются:
такие условия и сторонние FormalExpression доступны только вручную. Новая
генерация использует non-executable Expression. AI-импорт не является
безусловным lossless converter всего BPMN; ручные snapshots/Undo сохраняют XML.

## G. Тесты и representative scenarios

Baseline: 228 backend и 36 browser tests. Новые проверки находятся в
`backend/tests/test_bpmn_conformance.py` и `frontend/tests/bpmn-conformance.spec.ts`.
Покрыты шесть task types, implicit process, empty activity path (Start→End),
None Throw, multiple starts, message start without sender, start/end pair,
Activity conditional/default и implicit merge, mixed XOR, multiple EBG inputs,
сломанные sourceRef/type/incoming/processRef/lane/default, duplicate IDs,
DI refs/type/completeness/duplicates/Bounds/waypoints/Plane, invalid XSD,
QName refs, None Catch, XSD Doctor, clarification category, foreign expression,
unknown timer scope, неправильный namespace, real process regression. Это
более 25 независимых позитивных/негативных сценариев.
Дополнительно 12 seeded random renamings проверяют, что serializer/importer
сохраняют все ссылки и ID без специальных имён demo.

Существующие tests сохранены; пять старых ожиданий пересмотрены по спецификации:
Start name warning, legal event-gateway multiple input, Catch требует message,
None Throw не отправляет сообщения. Вместо удаления тестов проверяются новые
корректные ожидания. Clarify, Modify, preview, ChangeSet, manual movement,
exact Undo и History проверяются существующими browser tests.

Повторный аудит пяти **вновь экспортированных** diagrams: basic, implicit,
none-intermediate, collaboration-loops, event-wait. Ни одной ошибки нового
document validator; импорт, визуальное отображение, экспорт и JSON roundtrip
проверены в bpmn-js. Скриншоты и `five-audit.json` — в examples/bpmn-conformance.
Сложные схемы при fit-viewport мелкие; zoom и панорамирование нужны для чтения.

Измерения на этой машине: warm build примерно 0.7–9 мс для этих пяти схем,
XSD/reference/DI около 0.4–4.3 мс; первая компиляция XSD около 10–20 мс.
Browser import+UI settling+JSON request примерно 0.9–1.8 с (это не чистое время
bpmn-js). В development журналах: validation/build/XSD duration_ms и
code/element/source/spec/severity. API keys в эти журналы не включаются.

## H. Реальный regression process

Исходный полный текст — `examples/bpmn-conformance/input.txt`; не упрощался.
Настроенный provider MultiAI, модель gpt-6.1-sol. Первый HTTP 200 прошёл
структурные проверки, но ручной semantic review обнаружил пропущенного
руководителя и преждевременный End у клиента после возврата договора.
Первый ProcessDefinition и report сохранены отдельно как real-first-*.
Общие инструкции извлечения усилены: не объединять явно разные роли,
не подменять человеческое решение gateway и не завершать ожидающую сторону
в цикле. Это общие правила, а не hard-coded ветви конкретного примера.

После одного **ручного бизнес-corrective** запроса снова HTTP 200, без fallback.
Результат: 2 Pool, 6 участников, 40 узлов, 43 Sequence Flow, 8 Message Flow;
параллельные юридическая/безопасности проверки, отдельное решение руководителя,
отказ, документная доработка и договорный цикл обеих сторон. XML/XSD/refs/DI
прошли; bpmn-js import/export повторно даёт тот же ProcessDefinition.
Первый запрос занял 130.9 с, исправленный — 128.48 с. Это два реальных API
вызова, а не успех с первого раза и не автоматическая проверка всей полноты
текста. После исправления автоматического graph retry не потребовалось.

Файлы: real-process.json, real-result.bpmn, real-verification.json,
real-result-browser.png. Сохранённый результат проверяется тестами offline;
pytest/Playwright не расходуют API и не требуют ключей.

## I. Архитектура

Сохранено: LLMProvider/LLMRouter → Pydantic ProcessDefinition → polish /
ProcessValidator / semantic checks → детерминированный BPMNBuilder → official
XSD → XML reference/DI checks → bpmn-js. LLM не генерирует raw XML.
Добавлен один отдельный bounded XML validation module и таблица spec evidence;
не создавался BPMN execution engine и не переписывался backend. Provider config,
MultiAI и Vertex fallback, ID preservation, exact snapshots/Undo не изменены.

## J. Как воспроизвести

В backend: `../.venv/Scripts/python.exe -m pytest -q`.
В frontend: `pnpm exec playwright test`, затем `pnpm run build`.
Полный браузерный набор использует реальный установленный Edge и mock transport
на отдельном порту 8001; импорт/рендеринг bpmn-js настоящие. Живые backend8000 /
frontend5173 не заменяются mock server. Результаты финального запуска — ниже
в итоговой записи проверки. Это инженерная проверка выбранного subset,
не сертификация OMG и не гарантия любого текста/модели/исполнения процесса.

## Итоговая запись проверки

- Backend: **275 passed** = 228 baseline + **47 новых**.
- Browser: **43 passed** = 36 baseline + **7 новых** (6 настоящих
  bpmn-js import/render/export/JSON roundtrips и проверка категорий Doctor).
- Всего **318 passed**; offline test runs не обращаются к LLM.
- В новом backend-наборе отдельно выделяются **27** случаев official
  XSD/XML/DI, включая 19 повреждённых документов; остальные проверки тоже
  используют document validation там, где создаётся XML.
- TypeScript и production build Vite прошли. Сохранилось предупреждение о
  размере общего JS chunk (~865 kB), не блокирующее сборку. FastAPI test client
  сообщает upstream deprecation для httpx; runtime workflow работает.
- Пять новых diagrams повторно проверены визуально; скриншоты находятся рядом.
- Реальный MultiAI regression: два HTTP 200 (первый + ручное исправление),
  итоговый XML без ошибок XSD/refs/DI, без fallback, импорт/экспорт bpmn-js успешен.
- Предыдущие реальные XML не переписывались для скрытия обнаруженных проблем.
  У исторического Vertex output есть неверный Diverging на mixed XOR:
  он остаётся доступен вручную, Doctor обнаруживает INVALID_GATEWAY_DIRECTION,
  а новый builder корректно выдаёт Mixed. Успешный импорт сам по себе не означает
  соответствие всей семантике BPMN.


## Дополнение: устойчивость pipeline (03.10.2026)

Normative coverage и поддерживаемые элементы не расширялись. Graph-free
Clarify отделён от typed extraction; ошибки serializer/XSD/DI — от model
corrective retry. XML integrity теперь проверяет дополнительные локальные
messageRef/eventDefinitionRef и все references до выхода для unsupported
subset. Assumptions хранятся в стандартной Documentation. Циклы с выходом
сохраняются; отсутствие представленного предела повторов — BUSINESS_WARNING.
[Отдельный аудит, контракты и реальные проверки](pipeline-resilience.md).
