# MultiAI structured output: диагностика 04.10.2026

С текущим неизменённым extraction-промптом Claude и Gemini через MultiAI не соблюдают переданную ProcessDefinition даже при `json_schema strict=true`. Реальная причина отклонения — схема ответа, а не потеря текста при разборе OpenAI envelope. По этим ответам нельзя доказать, теряет ли MultiAI параметр при маршрутизации или игнорирует его upstream; наблюдаемое нарушение относится к этой интеграции, а не ко всем API этих моделей.

`.env`, production primary/fallback, тайм-ауты приложения, prompts, canonical schema и validators не изменены. Ни одна модель не назначена в production автоматически.

## Метод

- Один файл `examples/performance/simple.txt`, production extraction prompt + BPMN rules, одна ProcessDefinition, `max_tokens=8000`, `reasoning_effort=low`.
- Cache/fallback OFF. В начальном и transport benchmark — прямой `_request_once`, без ambiguity round trip и corrective. На каждый simple тест — общий предел 60 секунд; отменяется весь запрос.
- Для сравнения transport меняется только `response_format` в перехватчике benchmark. Текст messages не меняется и JSON Schema не дописывается в prompt для json_object. Это изолированный тест влияния transport, не готовый новый production режим.
- В повторной фазе сообщения первой попытки у трёх моделей имеют одинаковый SHA-256: `18d56ca12a31a9ccbbf5d23388f10f06002e09515a5b4f694556b40e874d14bc`. Единственное последующее изменение messages — существующий corrective prompt.
- Все сырые ответы сохранялись до parsing/validation/corrective в локальную игнорируемую `.llm-debug/`. Headers, request messages и API keys не сохраняются. Секретные поля и известный ключ редактируются, включая в model content.
- Полные безопасные измерения, shapes и первые 20 уникальных schema errors: [structured-output-results.json](structured-output-results.json). Raw content в этот отчёт не включён.

## Начальные ответы и transport сравнение

`JSON` означает синтаксически валидный сырой content; `Pydantic` проверяет JSON после допустимого unwrap. Prefix/suffix в таблице означает самостоятельный поясняющий текст, не Markdown fence.

| Model / transport | HTTP | Секунды | JSON | Fence | Prefix/suffix | Truncated | Pydantic | Output tokens | Основная причина |
|---|---:|---:|---|---|---|---|---|---:|---|
| GPT‑6.1 Sol / schema strict=true, первый тест | — | 60.01 | — | — | — | неизвестно | — | — | общий benchmark timeout, ответа нет |
| GPT‑6.1 Sol / schema strict=true, повтор | 200 | 34.14 | да | нет | нет | нет | да | 847 | успешно с первой попытки |
| Claude Sonnet 5.5 / schema strict=true | 200 | 29.36 | да | нет | нет | нет | нет | 1318 | SCHEMA_FAIL |
| Claude Sonnet 5.5 / json_object | 200 | 32.18 | да | нет | нет | нет | нет | 1443 | SCHEMA_FAIL |
| Claude Sonnet 5.5 / schema strict=false | 200 | 28.90 | да | нет | нет | нет | нет | 1299 | SCHEMA_FAIL |
| Gemini 3.1 Pro / schema strict=true | 200 | 25.50 | нет; после unwrap да | да | нет | нет | нет | 3305 | JSON_PARSE_FAIL → SCHEMA_FAIL |
| Gemini 3.1 Pro / json_object | 200 | 24.03 | нет; после unwrap да | да | нет | нет | нет | 3184 | JSON_PARSE_FAIL → SCHEMA_FAIL |
| Gemini 3.1 Pro / schema strict=false | 200 | 20.60 | нет; после unwrap да | да | нет | нет | нет | 2382 | JSON_PARSE_FAIL → SCHEMA_FAIL |
| Gemini 3.8 Flash / schema strict=true | 200 | 24.25 | да | нет | нет | нет | нет | 1407 (reasoning 1230) | SCHEMA_FAIL; обязательные вложенные поля отсутствуют |

Tokens — usage, сообщённый провайдером, не самостоятельный подсчёт. У Flash `completion_tokens=1407`, а `output_tokens=0`; эти поля явно противоречивы. Raw usage сохранён. По output_tokens нельзя самостоятельно заключить об обрезании, особенно если туда включено reasoning.

## Raw shapes

Все три модели вернули один choice и строковый `choices[0].message.content`. Пустого content, content blocks, parsed, tool_calls или нескольких choices в полученных реальных ответах нет.

- GPT‑6.1 Sol: стандартный OpenAI chat envelope (`choices`, `model`, `object`, `usage`, `created`, `id`); `message.content` — обычный JSON; отдельного reasoning field нет.
- Claude: `choices`, `model`, `object`, `usage`; message содержит `content`, `role`, а также **`reasoning_content`**. Адаптер читает content и не подменяет его reasoning.
- Gemini 3.1 Pro: стандартный envelope, `message.content` — ```json fence вокруг JSON; отдельного reasoning field нет.
- Все полученные responses: `finish_reason=stop`. JSON после извлечения завершён; TRUNCATED не обнаружен. У GPT первого timeout ответа/finish_reason нет, поэтому его truncation неизвестна.

## Pydantic: точные ошибки

Claude strict=true, первые 20 уникальных ошибок:

1. `lanes`: extra_forbidden.
2. `id`: missing.
3. `name`: missing.
4. `nodes.0.pool_id`: extra_forbidden.
5. `nodes.0.lane_id`: extra_forbidden.
6. `nodes.1.pool_id`: extra_forbidden.
7. `nodes.1.lane_id`: extra_forbidden.
8. `nodes.1.confidence`: literal_error; допустимы high/medium/confirmation_required, возвращено explicit.
9. `nodes.2.pool_id`: extra_forbidden.
10. `nodes.2.lane_id`: extra_forbidden.
11. `nodes.2.confidence`: literal_error.
12. `nodes.3.pool_id`: extra_forbidden.
13. `nodes.3.lane_id`: extra_forbidden.
14. `nodes.3.confidence`: literal_error.
15. `nodes.4.pool_id`: extra_forbidden.
16. `nodes.4.lane_id`: extra_forbidden.
17. `nodes.4.confidence`: literal_error.
18. `nodes.5.pool_id`: extra_forbidden.
19. `nodes.5.lane_id`: extra_forbidden.
20. `nodes.5.confidence`: literal_error.

Gemini strict=true после unwrap: `id`, `name`, верхнеуровневые `nodes`, `flows` missing; `participants.0.roles`, `pools.0.lanes`, `pools.0.nodes`, `pools.0.flows` extra_forbidden. Всего восемь уникальных ошибок в этом ответе. JSON_PARSE_FAIL имеет точную причину: `Expecting value; line 1, column 1, position 0`, Markdown fence.

Gemini после corrective перенесла nodes/flows на верхний уровень, но использовала `nodes[*].participant` вместо обязательного `participant_id` и добавила `flows[*].source_text`. Первые 20 уникальных ошибок этой попытки включены в JSON-отчёт.

## Schema

- Native JSON Schema: 5768 символов в компактной сериализации; strict=true в baseline.
- 52 required entries суммарно по определениям; максимальная глубина schema instance paths с развёрнутыми references — 4.
- 2 nullable anyOf; 10 enum definitions, включая NodeType, event_definition, decision_basis, confidence, participant kind/type и ambiguity/assumption enums.
- 8 object definitions; `additionalProperties=false` у всех.
- Схема не менялась и не ослаблялась. Нельзя доказать её «чрезмерную сложность» только по невалидным ответам. strict=false не исправил результат; GPT с той же схемой прошла.
- Claude/Gemini возвращают HTTP 200 даже при нарушении явных обязательных полей и additionalProperties=false. Native schema enforcement на этих маршрутах фактически не обеспечен.

## Исправления и границы

На реальных ответах не обнаружен bug извлечения `message.content`: content был прочитан правильно. Существовавший unwrap уже снимал fence Gemini, но схема всё равно не проходила.

При аудите обнаружен отдельный parser bug: старый поиск продолжал сканирование после повреждённого внешнего объекта и мог принять полный вложенный ProcessDefinition. Исправлено: декодируется только первый кандидат; damaged outer JSON, multiple objects, массивы и небезопасные wrappers отклоняются. Это не объясняет schema failures MultiAI и не «чинит» их.

Safe unwrap разрешает один полностью parseable объект в корректном fence либо коротком тексте до/после. После него обязательна прежняя строгая Pydantic validation. Никаких добавлений полей, enum coercion, удаления узлов, достраивания flows или исправления бизнес-смысла.

В адаптере добавлен безопасный разбор text blocks и parsed object для совместимых shape, с тем же canonical validation. Unsupported blocks, неоднозначные choices и tool_calls отклоняются. Никаких provider-specific типов наружу.

Per-model structured transport/capability registry **не добавлен**: ни json_object, ни strict=false не улучшили пригодность Claude/Gemini. Model IDs присутствуют только в benchmark-списке, не в production ветвлениях.

`LLM_DEBUG_RAW_RESPONSE=false` по умолчанию; factory разрешает сохранение только в development/dev/benchmark. Даже с true при APP_ENV=production raw capture отключён. В `.env` ничего не добавлено. Локальный benchmark намеренно сохраняет raw; наличие `.gitignore` проверено. Диагностика HTTP failure также сохраняется безопасно; без HTTP response при timeout сырого content нет.

## После изменений: initial → validation → corrective

| Model | Initial | Corrective | Общая latency | Итог |
|---|---|---|---:|---|
| GPT‑6.1 Sol | pass, 1 HTTP call | не потребовался | 34.14 сек | пригодность structured output подтверждена на simple; latency нестабильна, первый тест timeout |
| Claude Sonnet 5.5 | SCHEMA_FAIL | запрос начат, отменён по общему бюджету 60 сек; ответа нет | 60.01 сек | reject текущей интеграции для primary |
| Gemini 3.1 Pro | SCHEMA_FAIL, 18.43 сек | SCHEMA_FAIL, 26.53 сек | 44.98 сек | reject текущей интеграции для primary |

Это ограниченный benchmark, не статистическая оценка долговременной стабильности.

Полученный GPT ProcessDefinition реально прошёл normalization, graph/semantic validation, BPMNBuilder, XSD и complete DI. Simple business oracle подтвердил роль оператора, регистрацию, проверку, два результата и XOR с двумя условиями. BPMN импортирован в настоящий bpmn-js в Microsoft Edge: 23 rendered elements, 0 error boxes, 0 page errors. Сделан и просмотрен screenshot; raw/BPMN/screenshot остаются локально в `.llm-debug/gpt-proof/`.

## Gemini 3.8 Flash и early stop

- Simple: reject, SCHEMA_FAIL за 24.25 сек; missing `participants.0.id/name`, `nodes.0.id/type/participant_id`, `flows.0.id/source/target`, `pools.0.id/name`, `assumptions.0.id/text`.
- Medium: не запускался, simple не прошёл.
- Complex: не запускался по той же причине.
- Roles/tasks/conditions/MessageFlow/loops/gateway quality, XSD/DI/bpmn-js для Flash не подтверждены: валидного ProcessDefinition нет. Corrective для initial Flash не запускался согласно stage 1 early stop.

## Проверки

- 33 новых backend cases: valid raw JSON, fence, prefix/suffix, multiple objects, damaged/truncated JSON, пустой content, wrong shape, schema fail, safe unwrap rejection, blocks/parsed/reasoning, 20 unique errors, redaction, dev-only flag, capture before corrective и HTTP failure.
- **Backend: 450 passed.** Все старые тесты сохранены.
- **Browser: 63 passed в реальном Microsoft Edge.** Дополнительно реальный GPT BPMN импортирован отдельной browser проверкой.
- **Production build: passed** (TypeScript + Vite). Существующее предупреждение о размере lazy Modeler chunk остаётся.

Запуск isolated диагностики из корня:

```powershell
.venv/Scripts/python.exe backend/tools/diagnose_structured.py
.venv/Scripts/python.exe backend/tools/diagnose_structured.py --models claude-sonnet-5-5,gemini-3.1-pro --modes json_object,schema_relaxed --label transports
.venv/Scripts/python.exe backend/tools/diagnose_structured.py --corrective --label after-fix
.venv/Scripts/python.exe backend/tools/diagnose_structured.py --models gemini-3.8-flash --label flash-simple --pipeline
```

Каждый запуск делает реальные API-запросы. Raw локальные результаты намеренно не входят в Git; безопасный отчёт содержит только метрики и структуру, без model content и credentials.
