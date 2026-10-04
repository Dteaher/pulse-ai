# PULSE: второй performance pass

Дата: 4 октября 2026. Ключи и production primary не менялись. Исследование направлено на дорогие LLM round trips; validator, normalization, BPMN builder, XSD и DI не упрощались.

**Итог:** доказано ускорение короткого структурного исправления и точного переименования. Ускорение первой генерации сложного процесса в production не доказано. Поэтому bounded patch и combined parse остаются выключенными. Включено только безопасное точное переименование через существующий Modify/preview/ChangeSet/Undo.

## 19 пунктов результата

| № | Проверка | Результат |
|---|---|---|
| 1 | Complex baseline | Исторический cache miss: 325,68 с, три MultiAI вызова, 9028 output tokens. |
| 2 | Почему full corrective дорогой | Повторная генерация всего ProcessDefinition. Свежий одинаковый контроль: 175,22 с и 5686 output tokens; исходная extraction также может превышать 180 с. |
| 3 | Patch latency | После уточнения формата: MultiAI 11,67 с; Vertex 2,52 с. Это контроль с внесённым дубликатом перехода, а не полная генерация из текста. |
| 4 | Patch output tokens | MultiAI: 161; Vertex: 48. Данные API usage, не оценка токенизатора. |
| 5 | Patch success/failure | Исправленный prompt: 2/2 контроля успешны с exact canonical preservation. Первый MultiAI контроль не прошёл Pydantic после JSON retry (58,39 с, 274 tokens). Сохранён как отрицательный результат. |
| 6 | Шесть ролей | В успешных patch-контролях все шесть исходных определений ролей сохранены точно, включая отдельного руководителя и менеджера. |
| 7 | Два loops | Оба логических цикла сохранены. В collaboration это четыре SCC по отдельным pools; их набор задач сравнивается до/после. |
| 8 | Messages | Все восемь Message Flow, их ID, endpoints и labels сохранены точно. |
| 9 | Combined result | Новый tagged response испытан реальными API. Simple/ambiguity работают; complex не прошёл. Старый combined не включён, новый тоже выключен. |
| 10 | Lossless wire | Evidence-reference codec проходит exact roundtrip на 12 goldens и 100 property examples. Оценка выигрыша только 30/4273 tokens (~0,7%); в adapters не внедрён. |
| 11 | GPT-6.1 Sol | Separate: simple 68,57 с; ambiguous 21,96 с; complex timeout 187,57 с. Production primary сохранён. |
| 12 | Gemini | Separate: simple 8,19 с; ambiguous 3,51 с; complex 37,14 с, но вместо BPMN заданы лишние critical questions. |
| 13 | Quality comparison | Обе модели проходят 2/3 ожидаемых исходов в separate cohort. Faster не означает equal quality: complex не прошёл ни у одной в этом новом cache-miss сравнении. |
| 14 | First-pass success | Из двух запросов, где ожидается граф (simple/complex), 1/2 у каждой модели/режима. Ambiguous должен возвращать вопросы и не считается failed graph. |
| 15 | Corrective rate | Separate: 0/3 full correctives у каждой модели, потому что complex не дошёл до готового графа. Vertex combined: 1/3, full corrective не спас результат. Ни один обычный запрос не дал eligible local patch; production patch success rate неизвестен. |
| 16 | Production configuration | MultiAI/gpt-6.1-sol primary; Vertex/gemini-2.5-flash fallback; separate gate; new/old patch и combined false; exact Modify true. |
| 17 | Backend | 417 passed; включая 12 замороженных семантических fingerprints, guards, tagged schema, exact command routing, property tests и metrics. |
| 18 | Browser | 63 passed; новые реальные XML imports и rename preview/apply/Undo. Скриншоты изолированы от старых OneDrive PNG, assertions не отключались. |
| 19 | Build | TypeScript + Vite passed. Initial JS 278,95 KB; прежнее предупреждение о Modeler chunk 580,23 KB сохраняется. |

Замеры включают сеть и API провайдера. Они не отделяют собственное время вычисления модели от транспорта/очереди. На каждый текст/модель/режим сделан один замер: это не percentile и не доказательство стабильности. Все сравнительные API-запросы имеют отключённые cache и fallback, поэтому модель в результате не подменяется.

## Bounded structural corrective

Новый `RepairPatch` не использует прежний широкий `ProcessPatch`. Разрешены только:

- перенаправление endpoint существующего локального Sequence Flow;
- новый технический converging gateway и необходимые локальные Sequence Flow;
- изменение типа конкретного converging gateway, указанного в `PARALLEL_JOIN_MISMATCH`;
- удаление позднего **точного** дубликата перехода с сохранением первого ID;
- преобразование конкретного диагностированного cross-pool Sequence Flow без condition/default в Message Flow.

Удаление задач, событий, pools/participants, изменение названий/evidence/assumptions, условий и default запрещены. Ошибка условия на event gateway уходит в full corrective: сам факт такой ошибки ещё не доказывает, что условие можно безопасно удалить. Нет восстановления ролей по словарю и нет hardcode участников примера в application code.

Контекст ограничен 24 nodes/40 flows, содержит validator errors, affected IDs, один hop связей, минимальные роли и evidence. Unknown/unsupported errors и слишком большой neighbourhood обходят эксперимент. У неизвестного adapter отсутствует capability, поэтому фиктивного patch-вызова не возникает.

Полная исходная версия не мутируется. Проверяются ID, полевая whitelist, связь операций с диагностикой, глобальная уникальность новых ID, неизменность всех защищённых объектов и сохранность task SCC. Затем выполняется обычный pipeline с normalization, graph + semantic validation, builder, XSD и complete DI. После normalization повторно сравниваются защищённые объекты. Ошибка, schema failure или fingerprint mismatch не публикуются; используется прежний full corrective. Транспорт и JSON corrective остаются внутри adapter/router.

В одинаковом fault-control шесть ролей/25 tasks/43 Sequence Flow/8 messages сохраняются точно. MultiAI patch быстрее full corrective примерно в 15 раз и выдаёт в 35 раз меньше tokens. **Это не 15-кратное ускорение PULSE целиком.** У обычной сложной генерации в новом сравнении были timeout/unsupported graph errors; потому coverage и экономия production corrective ещё не подтверждены.

## Новый combined schema и simple fast path

`ParseDecision.result` — discriminated union двух взаимоисключающих branches:

```json
{"result":{"status":"ready","process":{}}}
```

или `clarification_required` с analysis и минимум одним critical question. Это примеры формы, `{}` не является валидной ProcessDefinition. `ready` с critical questions, две ветки одновременно и questions без critical запрещены Pydantic.

OpenAI-compatible schema использует nested anyOf с literal status; Vertex projection сохраняет literals как singleton enums. Canonical ProcessDefinition и внешний API-контракт прежние. Это новый путь, а не включение прежнего combined implementation. Любой готовый граф проходит все прежние проверки. Финальная schema/prompt дополнительно уточнены после контрольных измерений; поскольку complex уже провалил quality gate, повторный массовый замер этой выключенной версии не использован для заявления о выигрыше.

| Режим | MultiAI | Vertex |
|---|---:|---:|
| Separate simple | 68,57 с, BPMN | 8,19 с, BPMN |
| Combined simple | 37,69 с, BPMN | 6,00 с, BPMN |
| Separate ambiguous | 21,96 с, вопросы | 3,51 с, вопросы |
| Combined ambiguous | 48,41 с, вопросы | 4,39 с, вопросы |
| Separate complex | 187,57 с, timeout | 37,14 с, лишние вопросы |
| Combined complex | 180,01 с, timeout | 43,91 с, graph validation failure |

Оба режима сохранили вопросы о порядке проверок и исполнителе повторной проверки в ambiguous fixture. В complex separate Vertex спросил, кто решает после проверок, хотя руководитель уже указан, и что происходит после повторной отправки договора. Combined Vertex имел cross-pool sequence flows, неправильные message event definitions и недостижимые действия; full corrective не прошёл. Результаты не публикуются как успешные BPMN.

Дополнительный отдельный MultiAI контроль с более длинным timeout не получил ответа из-за транспортного timeout примерно за 10,71 с; он не включён в равный 180-секундный cohort. Primary не меняется, operation routing и автоматический simple fast path не включены. Дополнительного LLM-классификатора сложности нет.

## Output breakdown и lossless experiment

4273 — **оценка** размера полной canonical JSON одного frozen complex графа через `o200k_base`. Это не реальная тарификация MultiAI/Vertex. Исторические 9028 output tokens относятся ко всему запросу с двумя генерациями графа и отдельным анализом.

| Компонент | Оценка standalone JSON tokens |
|---|---:|
| Nodes, включая labels/evidence | 2298 |
| Sequence flows | 1323 |
| Participants | 159 |
| Pools | 36 |
| Message Flow | 210 |
| Description | 232 |
| Assumptions | 6 |
| Ambiguities | 5 |
| ID/name | 14 |

Группы включают собственный JSON overhead и не суммируются в точный provider usage. Marginal contribution evidence: 454 estimated tokens; duplicate labels: 26 estimated tokens, с пересечением с nodes/flows. Provider/cache metadata находится вне ProcessDefinition и модели повторно не передаётся.

Уже используются participant IDs, имя роли не повторяется в каждом node. Description хранится один раз. Повторяющийся длинный source_text в исследовательском codec заменяется evidence reference. Exact roundtrip сохраняет каждое поле, Unicode, порядок, null/default, ID, evidence и условия. Проверены отрицательные/out-of-range/bool references. Получено 4243 против 4273 estimated tokens; выигрыш слишком мал для новой schema и дополнительного generation risk. Codec остаётся только в `backend/tools`, а transport DTO не меняется. Фиктивного runtime-флага для неинтегрированного формата нет.

## Exact Modify

Единственное включённое ускорение — точное одиночное переименование роли/участника/дорожки/задачи. Старое имя должно совпасть целиком и однозначно; имя нового объекта не должно конфликтовать с существующим. Составные команды, fuzzy/грамматическое сопоставление, удаление задач и семантическая оптимизация продолжают использовать LLM/Clarify.

Новый ответ использует обычные ProcessDefinition, ChangeSet, XML; metadata=null честно означает отсутствие обращения к LLM. Preview/apply/версии/Undo не меняются. Все IDs, responsibility, evidence, conditions, messages и loops остаются прежними; изменяется только requested name. Full pipeline проверяет даже такое изменение.

Cache-miss control на шестиролевом графе: **55,00 мс**, 0 LLM calls; работающий backend с production `.env`: HTTP 200, **80,74 мс**, один ChangeSet, BPMN. Это ускорение только понятной команды rename, не всех Modify (предыдущие 80,39 с относились к LLM Modify).

## Метрики, конфигурация и воспроизведение

`PerformanceProfile` записывает patch/full counters, success/rejected/failure, реальные usage tokens. `corrective_metrics` считает rates по cache-miss cohort, исключая cache/single-flight hits. Без patch attempts success/quality rejection rate = null, а не «100%» или «0%». При неизвестном usage отдельные token metrics тоже null. Benchmarks отдельно считают `model_quality_pass_rate` и `combined_call_quality_pass_rate` с явным размером выборки.

У revised injected controls empirical patch success 2/2. В обычном cohort patch attempts=0: распространённые complex проблемы либо не получили граф, либо слишком широки для локального patch. Эти выборки не смешиваются. Полные input/output counts, first-pass validation, quality invariants и rates: `examples/performance-v2/audited-results.json`. Исходные отрицательные результаты не переписываются.

```dotenv
PRIMARY_LLM_PROVIDER=multiai
PRIMARY_LLM_MODEL=gpt-6.1-sol
FALLBACK_LLM_PROVIDER=vertex_gemini
FALLBACK_LLM_MODEL=gemini-2.5-flash
LLM_FALLBACK_ENABLED=true
LLM_PERFORMANCE_ENABLED=true
LLM_COMBINED_ENABLED=false
LLM_PATCH_ENABLED=false
LLM_PATCH_CORRECTIVE_ENABLED=false
LLM_REPAIR_MAX_TOKENS=1600
LLM_COMBINED_PARSE_ENABLED=false
DETERMINISTIC_MODIFY_ENABLED=true
LLM_TIMEOUT=180
LLM_REQUEST_BUDGET=270
LLM_FALLBACK_RESERVE=60
LLM_MAX_TOKENS=8000
```

API keys/base URLs не печатаются и не меняются. Настройка содержит ключи только в ignored `.env`. Сохраняются Vertex fallback и ограниченный общий бюджет; простое увеличение timeout не выдано за ускорение.

Из корня проекта, после установки `backend/requirements-dev.txt` в `.venv`:

```powershell
.venv/Scripts/python.exe backend/tools/benchmark_repair.py --modes patch --prefix experiment-
.venv/Scripts/python.exe backend/tools/benchmark_models.py --providers multiai,vertex_gemini --cases simple,complex,ambiguous
.venv/Scripts/python.exe backend/tools/benchmark_rename.py
.venv/Scripts/python.exe backend/tools/analyze_output.py
.venv/Scripts/python.exe backend/tools/summarize_performance_v2.py
```

Первые два инструмента выполняют реальные API-запросы. Settings snapshots изолированы, production `.env` они не переписывают. Генерируемые данные — примеры хакатона; headers/credentials не сохраняются. Интерфейс визуально проверен в Edge, реальные patch XML открыты bpmn-js, rename preview/apply/Undo проверены browser automation. Doctor остался lazy. Никакого ускорения за счёт отключения validation, удаления evidence или принятия неправильного графа нет.
