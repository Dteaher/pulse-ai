# Аудит конфигурации PULSE

Дата: 14 октября 2026 года. Проверен `pulse-ai`, не исходный хакатонный репозиторий.
`.env` и production-код не изменялись. API-ключи и код доступа не включены в отчёт.
Новые внешние LLM-запросы не выполнялись: использованы код, офлайн-проверки и сохранённые реальные измерения предыдущего E2E.

## 1. Фактическое состояние

В текущем `.env` **17 переменных**, все распознаются `Settings`. Повторяющихся записей и переопределений этих переменных окружением текущего процесса нет.
Primary: `multiai / gpt-5.5`, reasoning `low`; fallback: `vertex_gemini / gemini-2.5-flash`.

Сейчас включены performance policy, fallback и ограничения HTTP-операций. **Business coverage выключен**, потому что переменной нет в `.env`, default — false. Combined preparation и оба patch-режима также выключены. Structured output — auto, выходные токены не unlimited.

Доказательства: [Settings](../backend/app/config.py), [factory](../backend/app/services/llm/factory.py), безопасная локальная инвентаризация `.runtime/config-audit/inventory.json` и вычисленные настройки `.runtime/config-audit/evidence.json`.

## 2. Чтение и приоритет `.env`

`config.py:ROOT` — корень `pulse-ai`; `SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')` читает именно этот файл, независимо от каталога запуска.
`settings_customise_sources` задаёт приоритет: аргументы `Settings(...)` → окружение процесса → `.env` → file secrets → defaults.
Неизвестные переменные тихо игнорируются. Для текущих 17 таких переменных нет.

Дополнительный приоритет в factory:

- `PRIMARY_LLM_PROVIDER` перекрывает `LLM_PROVIDER`;
- непустые `PRIMARY_LLM_MODEL/API_KEY/BASE_URL` перекрывают соответствующие `LLM_*`;
- primary structured format берётся **из `LLM_STRUCTURED_OUTPUT`**. Поля `PRIMARY_LLM_STRUCTURED_OUTPUT` в Settings нет: такая запись была бы проигнорирована;
- `vertex-gemini` нормализуется в `vertex_gemini`; это не второй провайдер и не конфликт;
- MultiAI не выводит выбор модели из URL: `PRIMARY_LLM_MODEL` отправляется в поле `model` отдельно.

`main.py:current_connection` перечитывает Settings на следующий запрос, заменяет provider snapshot и очищает результатный cache при изменении settings fingerprint. Один выполняющийся запрос использует свой snapshot.
Но `RequestGuard`, startup-проверки production и объект `ResultCache(capacity, ttl)` создаются из стартовых Settings. Изменение auth/HTTP-лимитов/ёмкости/TTL требует перезапуска для согласованного применения; hot reload provider не обновляет эти объекты.

## 3. Каждая переменная текущего `.env`

Здесь default означает значение поля Settings, а не рекомендацию. Ключи показаны только как «задан».

| Переменная | Сейчас / default | Где применяется | Что включает и на что влияет |
|---|---|---|---|
| `PRIMARY_LLM_PROVIDER` | `multiai` / пусто | `factory.get_llm_provider`, `_create` | Выбирает adapter всех primary LLM-операций; перекрывает `LLM_PROVIDER`. Определяет API-возможности, качество, задержки и тариф внешнего сервиса. |
| `PRIMARY_LLM_BASE_URL` | `https://multiai.store/v1` / None | `factory._create`, `OpenAICompatibleProvider.__init__`, `_request_once` | Запросы идут на `/chat/completions`. При отсутствии используется `LLM_BASE_URL` или provider default. Влияет на доступность/маршрутизацию, не на BPMN Builder. |
| `PRIMARY_LLM_MODEL` | `gpt-5.5` / None | `factory._create`, `_request_once` | Поле `model` всех primary запросов. Перекрывает `LLM_MODEL`. Влияет на качество, время, стоимость и лимиты провайдера. |
| `PRIMARY_LLM_API_KEY` | задан / None | `factory._create`, `headers()` | Bearer-аутентификация MultiAI; пустое значение откатывается к `LLM_API_KEY`. Может определять квоты/доступ аккаунта; не меняет бизнес-граф. |
| `FALLBACK_LLM_PROVIDER` | `vertex-gemini` / пусто | `factory.get_llm_provider`, `_create`, `LLMRouter` | Создаёт Google Vertex adapter, если fallback enabled. На здоровом primary не вызывает резерв. Отдельная модель может дать другой граф; pipeline повторно его валидирует. |
| `FALLBACK_LLM_MODEL` | `gemini-2.5-flash` / пусто | `VertexGeminiProvider`, `_request_once` | Модель резервных запросов. Для этой семьи код **жёстко задаёт thinking_budget=0**, не `LLM_REASONING_EFFORT`. Это фактическая разница режимов primary/fallback. |
| `FALLBACK_LLM_API_KEY` | задан / пусто | `factory._create`, `genai.Client(vertexai=True, ...)` | Ключ Google Cloud Vertex Express, не MultiAI и не автоматически AI Studio. Доступ/квоты независимы от primary. |
| `LLM_FALLBACK_ENABLED` | true / true | `factory.get_llm_provider` | Разрешает создание цепочки primary→fallback. Без непустого fallback provider резерв всё равно отсутствует. Может добавить API-вызов и изменить используемую модель; повышает устойчивость только при рабочем резерве. |
| `LLM_MAX_RETRIES` | 2 / 2 | adapter `_request`, routes, `checked_coverage` | JSON/Pydantic corrective и верхняя граница graph corrective. При performance=true JSON retry **clamp до 1**: initial+1. Не означает два повтора timeout/429 и не является общим лимитом всех HTTP-вызовов. |
| `LLM_TIMEOUT` | 90 с / 90 с | HTTP client, `LLMRouter._invoke`, Vertex `_request_once` | MultiAI read/write timeout; connect/pool ≤10 с. Router дополнительно ограничивает целую LLM-операцию с внутренними повторами. Дольше — больше шанс дождаться ответа, но более долгое ожидание ошибки; не ускоряет модель. |
| `LLM_MAX_TOKENS` | 8000 / 16000 | adapter `token_budget`, `operation_budget` | Общий резерв output budget. При performance=true обычный extraction сейчас 8000, ambiguity 1500, coverage/Doctor 3000. Это **не лимит input/context**, не количество запросов и не фиксированная оплата 8000 токенов. |
| `LLM_REASONING_EFFORT` | `low` / None | `MultiAIProvider.request_options` | Передаёт reasoning_effort, если нет конкретного override операции. Сейчас low у ambiguity и extraction. На Gemini fallback не действует. Влияние другого уровня на качество/время в текущем живом прогоне не измерялось. |
| `LLM_PERFORMANCE_ENABLED` | true / false | factory policy, `_request`, prompts, router, cache | Включает operation budgets/reasoning overrides, общий deadline, cache/singleflight, clamp JSON retry; у Vertex — повторное использование клиента. **Не включает автоматически combined/patch, не отключает валидаторы.** Может влиять и на качество через budgets/retry. |
| `LLM_REQUEST_BUDGET` | 180 с / 180 с | factory → `LLMRouter._invoke` | Общий deadline для LLM-цепочки одного запроса; применяется только при performance=true. Не бюджет денег/токенов. При performance=false игнорируется. Не охватывает строго весь HTTP pipeline и не включает отсечку локальной сериализации/Builder. |
| `APP_ENV` | `development` / `development` | `main.create_app`, factory policy, route diagnostics | Управляет startup-требованиями доступа и логами. Production требует код ≥32 символов и API_LIMITS_ENABLED=true. При development проверки BPMN не ослабляются. Debug raw может работать только в development/dev/benchmark при отдельном флаге. |
| `API_LIMITS_ENABLED` | true / true | `security.RequestGuard` | Ограничивает дорогие HTTP generate/clarify/modify/audit: defaults 2 concurrent, 10/min, 100/day. **Считает операции приложения, не LLM HTTP-вызовы/токены.** Авторизация и размер body от этого флага не отключаются. Cache hit также проходит guard. |
| `PULSE_ACCESS_TOKEN` | пусто / пусто | `main.create_app`, `RequestGuard` | Сейчас код доступа выключен. При непустом значении нужен `Authorization: Bearer ...`. На inference/BPMN-качество не влияет. Режим production с текущим пустым значением не запустится. |

## 4. Важные переменные, отсутствующие в `.env`

| Переменная | Фактически/default | Действие и влияние |
|---|---|---|
| `LLM_BUSINESS_COVERAGE_ENABLED` | false | Нет отдельного сверяющего вызова по исходному тексту. При true добавляет `check_business_coverage` после структурной/семантической проверки. Больше контроля пропущенных действий/ролей/условий, но дополнительная задержка, стоимость и точка отказа. Это LLM-review, не математическое доказательство. |
| `LLM_STRUCTURED_OUTPUT` | None / auto | Сначала strict JSON Schema. Только на явном unsupported response-format 400/422 пробует JSON object, затем без response_format. Возможны ещё два HTTP-вызова без JSON corrective. true запрещает такой downgrade; false сразу JSON object с schema в prompt. Pydantic остаётся обязательным во всех вариантах. |
| `PRIMARY_LLM_OMIT_TOKEN_LIMIT` | false | Сейчас max_tokens передаётся. true удалит max_tokens/max_completion_tokens **primary**, operation budgets перестанут ограничивать выходной wire request; output-budget retry не выполняется. Это не «бесконечный вывод»: остаются внешние ограничения API, которых код не определяет. Fallback флаг не получает. |
| `LLM_GRAPH_MAX_RETRIES` | 1 | Routes используют min(LLM_MAX_RETRIES, LLM_GRAPH_MAX_RETRIES). Сейчас одна graph-корректировка после исходной модели, затем возможен validation fallback. |
| `LLM_FALLBACK_RESERVE` | 30 с | Primary allowance=min(timeout, остаток deadline−reserve); reserve=min(30, request_budget/3). Fallback использует остаток без этого резерва. |
| `LLM_PARSE_MAX_TOKENS` | 8000 | Output cap для extraction, clarification и старого preparation при performance=true. |
| `LLM_CLARIFY_MAX_TOKENS` | 1500 | Здесь главным образом **анализ вопросов ambiguity**, а не полный граф после `/clarify`: последнему нужен parse budget. |
| `LLM_MODIFY_MAX_TOKENS` | 8000 | Budget изменения графа и modification/preparation patch. |
| `LLM_DOCTOR_MAX_TOKENS` | 3000 | Budget и Doctor, и business coverage. Режимы разные: Doctor выдаёт мнения, coverage может блокировать генерацию. |
| `LLM_CORRECTIVE_MAX_TOKENS` | 8000 | Budget full/patch corrective при performance policy. |
| `LLM_PARSE_REASONING_EFFORT` | None | Override extraction, preparation, clarification. Без него действует общий low. |
| `LLM_CLARIFY_REASONING_EFFORT` | None | Override ambiguity analysis. |
| `LLM_MODIFY_REASONING_EFFORT` | None | Override modification/patch. |
| `LLM_DOCTOR_REASONING_EFFORT` | None | Override `audit`, **не business_coverage**, хотя token budget общий. |
| `LLM_CORRECTIVE_REASONING_EFFORT` | None | Override корректировок; MultiAI выбирает его и при `_corrective`=true. |
| `LLM_COMBINED_ENABLED` | false | Старый combined preparation: вопросы и готовая модель одним вызовом; при `explicit_unknown` возвращается к раздельным стадиям. Возможны 1 вместо 2 базовых вызовов, но меняется путь extraction/prompts. |
| `LLM_COMBINED_PARSE_ENABLED` | false | Новый ParseDecision/preparation_v2; может объединять preflight+parse. Выше приоритетом старого combined для не-modify. Отдельные parse budget/reasoning для preparation_v2 сейчас не применяются — см. ниже. |
| `LLM_PATCH_ENABLED` | false | Меняет Modify/full corrective на ProcessPatch; это иной способ создания кандидатуры, не только сокращение текста. Не автоматически включён performance. |
| `LLM_PATCH_CORRECTIVE_ENABLED` | false | Включает отдельный структурный repair вызов при подходящей ошибке и наличии graph retry. Требует supports_structural_repair. После patch снова полный pipeline и preservation audit. |
| `LLM_REPAIR_MAX_TOKENS` | 1600 | Output cap структурного repair, min(global, repair cap). |
| `LLM_OUTPUT_RETRY_ENABLED` | false | Один дополнительный extraction вызов при output_truncated, на первом ответе без correction, если omit=false, лимит можно поднять и осталось время. |
| `LLM_PARSE_RETRY_MAX_TOKENS` | 16000 | Верхний предел такого расширения: min(retry cap, текущий cap×1,5). Сам по себе ничего не включает. |
| `LLM_DEBUG_RAW_RESPONSE` | false | Не пишет raw запрос/ответ. true сохраняет локальную диагностику с редактированием секретов только в разрешённом APP_ENV; не повышает качество ответа. |
| `DETERMINISTIC_MODIFY_ENABLED` | false | При true точное одиночное переименование может обойти LLM; сложное изменение остаётся модельным. Не влияет на начальную генерацию. |
| `MAX_CLARIFICATION_ROUNDS` | 3 | Ограничивает пользовательские раунды уточнения; не количество HTTP API-запросов внутри них. |
| `PERFORMANCE_CACHE_ENTRIES` / `PERFORMANCE_CACHE_TTL` | 64 / 300 с | Ёмкость/TTL session cache; применяются при создании app. Дополнительный fixed memory cap 16 MB. Cache отключён для клиентов без валидного session header или при performance=false. |
| `API_MAX_CONCURRENT`, `API_REQUESTS_PER_MINUTE`, `API_REQUESTS_PER_DAY`, `API_MAX_BODY_BYTES` | 2 / 10 / 100 / 2 000 000 | Указанные HTTP-ограничения и максимальный body; не предел стоимости модели. Body лимит действует независимо от API_LIMITS_ENABLED. |
| `FALLBACK_LLM_BASE_URL`, `FALLBACK_LLM_STRUCTURED_OUTPUT`, `FALLBACK_LLM_FOLDER_ID` | пусто / None / пусто | Для текущего Vertex не используются: factory не передаёт URL/structured/folder в его constructor. Vertex всегда native JSON response schema. Поля нужны другим adapter. |
| `YANDEX_FOLDER_ID` | пусто | Не используется ни primary MultiAI, ни текущим Vertex fallback. |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY` | openai_compatible / пусто / пусто / пусто | Общие резервные значения Settings; сейчас перекрыты непустыми PRIMARY_LLM_* и не создают ещё одного primary. |

Operation-specific budgets работают только при performance=true. Для audit/ambiguity/coverage cap=min(global, configured). Для остальных `operation_budget` учитывает размер уже известного графа, но ceiling остаётся max(global, configured); это не неограниченное динамическое увеличение.

## 5. Coverage: что именно проверяется

Доказательство: [pipeline.py:generate_valid](../backend/app/pipeline.py), строка 65: вызов только при `not errors and source_text and provider is not None and provider.business_coverage_enabled`.
[business_coverage.py:coverage_issues](../backend/app/business_coverage.py) требует дословную source_excerpt из исходного текста/ответов и существующие node IDs. Подтверждённые gaps становятся error и запускают bounded corrective; неподтверждённый review не принимается.

Но scope ограничен:

- `/generate` передаёт body.text — coverage сработает;
- `/clarify` для генерации передаёт original_text, либо при старом DTO process.description: последняя не гарантированно равна исходному тексту;
- `/modify` и уточнение modify передают пустой source_text — coverage не сработает даже при enabled=true;
- продолжение готового draft без provider/source не вызывает coverage;
- `/bpmn` — детерминированный экспорт без LLM; `/audit` — отдельный Doctor-вызов, не coverage и не сверка с полным исходным текстом.

Coverage может иметь собственный corrective review, если reviewer не подтвердил excerpt/IDs (`common.checked_coverage`, максимум один повтор); внутри review остаётся adapter JSON corrective. Поэтому «coverage = всегда ровно один вызов» верно только на успешном первом ответе.

Если review проваливается по transport и включает fallback, Router меняет active provider; финальная metadata показывает последнего active provider. Она может описывать модель reviewer, а не автора исходного ProcessDefinition. В текущих coverage-disabled измерениях этого не было; это вывод из кода, не новый измеренный инцидент.

## 6. Retry, timeout и fallback не одно и то же

1. **Format downgrade** в auto structured: JSON Schema → JSON object → plain, только на явную unsupported format ошибку. До 3 wire calls на один `_request_once`.
2. **JSON/Pydantic corrective**: при performance=true initial+1, даже если LLM_MAX_RETRIES=2/3/5. Ошибка JSON после этого помечается retryable для Router.
3. **Graph corrective**: min(max_retries, graph_max_retries), сейчас один. Возвращается к модели с конкретными ошибками и previous_result. После исчерпания возможен fallback и новый ограниченный graph цикл.
4. **Output-budget retry**: отдельный выключенный сейчас механизм, не тот же JSON retry.
5. **Transport fallback**: timeout, network, 429, соответствующие 5xx/unavailable. Не повторяет автоматически первичный timeout дважды из-за LLM_MAX_RETRIES; пытается резерв.
6. **429 cooldown**: на Retry-After (если отсутствует, 60 с), process-local; повтор в cooldown не делает wire-вызов первичного provider. В API-логе может быть операция без реального HTTP. Ошибки fallback не гарантируют возврат к primary в рамках того же запроса.

`LLMRouter._invoke` считает общий deadline от начала первой LLM-операции. Он не сбрасывается на parse, coverage, graph retry или fallback. Резервированное время сокращает доступное primary время. HTTP connect/pool limits и внешний service/reverse proxy могут дать timeout раньше; изменение timeout не гарантирует устранения внешнего сбоя.

## 7. Cache и количество реальных вызовов

[main.py:cached](../backend/app/main.py), [ResultCache.get_or_compute](../backend/app/performance_cache.py): нужен `X-Pulse-Session` 16–128 допустимых символов; frontend отправляет его. Одинаковый запрос той же session и версии settings/code/prompts может получить cache hit — **0 новых LLM-вызовов**. Concurrent duplicate ждёт результат владельца; отдельный новый inference не запускается. Ошибки не кэшируются. Вопросы clarification_required тоже могут кэшироваться, а не только XML.

Это memory cache экземпляра процесса, не персистентная база и не общий cache всех пользователей. В нём хранятся полные результаты с бизнес-текстом; ключи хэшированы. Нет cache hit в предыдущем живом тесте, потому что harness не передавал session header.

Фактические данные предыдущего E2E (`.runtime/live-logic-audit/*/performance.json`):

| Процесс | Реальных API попыток | Операции | Время | Input / output tokens |
|---|---:|---|---:|---:|
| Простой, успешный | 2 | GPT ambiguity + GPT extraction | 16,3 с | 5363 / 1058 |
| Средний, успешный повтор | 2 | GPT ambiguity + GPT extraction | 26,5 с | 5765 / 2783 |
| Сложный, успешный | 2 | GPT ambiguity + GPT extraction | 74,0 с | 6227 / 5161 |
| Средний, первая неудачная попытка | 3 | GPT ambiguity 200 + GPT extraction timeout + Vertex extraction 429 | 123,6 с | usage неполон: нельзя считать timeout бесплатным |

Весь эксперимент: **9 реальных API попыток**, а не 3. Из них 8 MultiAI и 1 Vertex. Три успешных HTTP-запроса приложения получили XML; один нет. Metadata attempts=2 у успешного результата — число модельных операций; graph attempts=1 — один кандидат графа. Эти счётчики нельзя отождествлять.

Сложность увеличивает число токенов и задержку, но не автоматически количество calls. На текущем здоровом пути всех трёх — два вызова. С coverage=true и тем же раздельным pipeline ожидается **3** при отсутствии ошибок. Combined+coverage могло бы дать 2, combined без coverage 1, но это иной, не проверенный на этих GPT-5.5 сценариях режим. Critical questions останавливают extraction; дальнейшие `/clarify` — отдельные операции. Из-за format/JSON/graph/coverage retries, fallback и quota количество calls на ошибках не является фиксированным.

Для стоимости нет необходимости умножать `max_tokens` на тариф как фактический расход: это cap, оплаченный usage определяется провайдером. Рублёвую оценку без тарифа MultiAI и полных usage ошибочных запросов отчёт не выдумывает.

## 8. Несоответствия и скрытые зависимости

- Текущий `.env` не содержит неизвестных/дублированных записей. PRIMARY и legacy поля — алиасы с явным приоритетом, не два inference.
- `LLM_PERFORMANCE_ENABLED=false` одновременно отключит request budget, operation overrides и result cache, увеличит JSON retries до LLM_MAX_RETRIES. Это не нейтральный «отключить оптимизацию».
- `LLM_MAX_TOKENS` не жёсткая граница всех caps: parse override может быть выше global; ambiguity/coverage остаются ограничены global.
- `PRIMARY_LLM_OMIT_TOKEN_LIMIT=true` нейтрализует wire caps primary и выключает output-budget retry. Не рекомендуется как способ улучшить точность.
- **preparation_v2 mismatch:** factory maps token/reasoning для `preparation`, а adapter вызывает `preparation_v2`. В офлайн-probe parse cap=12000 и parse reasoning=medium дали extraction/preparation 12000/medium, но preparation_v2 **8000/low** — общий fallback. В текущем режиме combined_parse=false дефект не активен. Не исправлялся по условию read-only.
- **coverage reasoning mismatch:** `LLM_DOCTOR_REASONING_EFFORT=medium` даёт audit medium, но business_coverage остаётся global low. Офлайн-probe это подтвердил. Общий doctor token cap применяется обоим. Нет отдельной распознаваемой `LLM_BUSINESS_COVERAGE_REASONING_EFFORT`.
- Vertex не использует fallback structured/base URL, не принимает общий reasoning как MultiAI; Flash thinking_budget=0 прописан в коде. «Одинаковый .env reasoning для всех провайдеров» здесь неверен.
- Ни один из рассматриваемых performance-флагов не отключает Pydantic, графовые, семантические, XSD, DI или model/XML fidelity проверки. Отдельный boolean `semantic_check` pipeline не является выключателем проверки: validate_semantics вызывается и для генерации.
- LLM-инструкции extraction/modification/clarification при performance=true не сокращаются: `common.performance_prompt` сохраняет полный prompt_text и BPMN rules. Здесь нет подтверждения, что performance=true само по себе обрезает бизнес-смысл.
- Внешние API quota не снимаются local request limits. Нынешний резерв уже возвращал 429; один enabled=true не делает его надёжным.

## 9. Предлагаемый профиль: контроль полноты прежде минимального времени

Это **предложение**, не применённая конфигурация и не результат нового live benchmark. Универсальный математически оптимальный профиль по трём запросам не установлен.
Рекомендую оставить проверенный раздельный pipeline и low reasoning, добавить coverage и явные ограничения. Польза повышения reasoning для текущего MultiAI отдельно не доказана; medium/high стоит сравнивать A/B, не объявлять автоматически лучшим.

Существующие ключи оставить на месте, в этот блок они намеренно не включены:

```dotenv
PRIMARY_LLM_PROVIDER=multiai
PRIMARY_LLM_BASE_URL=https://multiai.store/v1
PRIMARY_LLM_MODEL=gpt-5.5
PRIMARY_LLM_OMIT_TOKEN_LIMIT=false
LLM_STRUCTURED_OUTPUT=true

FALLBACK_LLM_PROVIDER=vertex-gemini
FALLBACK_LLM_MODEL=gemini-2.5-flash
LLM_FALLBACK_ENABLED=true

LLM_BUSINESS_COVERAGE_ENABLED=true
LLM_PERFORMANCE_ENABLED=true
LLM_REASONING_EFFORT=low
LLM_MAX_RETRIES=1
LLM_GRAPH_MAX_RETRIES=1

LLM_TIMEOUT=150
LLM_REQUEST_BUDGET=420
LLM_FALLBACK_RESERVE=60

LLM_MAX_TOKENS=16000
LLM_PARSE_MAX_TOKENS=12000
LLM_CLARIFY_MAX_TOKENS=1500
LLM_MODIFY_MAX_TOKENS=12000
LLM_DOCTOR_MAX_TOKENS=6000
LLM_CORRECTIVE_MAX_TOKENS=12000
LLM_OUTPUT_RETRY_ENABLED=true
LLM_PARSE_RETRY_MAX_TOKENS=16000

LLM_COMBINED_ENABLED=false
LLM_COMBINED_PARSE_ENABLED=false
LLM_PATCH_ENABLED=false
LLM_PATCH_CORRECTIVE_ENABLED=false
LLM_DEBUG_RAW_RESPONSE=false

PERFORMANCE_CACHE_ENTRIES=64
PERFORMANCE_CACHE_TTL=300
```

Обоснование:

- Coverage даёт явную сверку с исходным текстом, отсутствующую сейчас; ожидается 3 calls на здоровую генерацию. Он может блокировать ответ при недоступном reviewer — это tradeoff контроля полноты и availability.
- Strict schema=true основан на уже успешных schema-запросах GPT-5.5: не допускает тихого format downgrade. При изменении поддержки API выдаст ошибку, не переключится на weaker format.
- retries=1 явно выражает уже действующий JSON предел при performance=true; graph предел остаётся тем же. Не раздуваем повторные full graphs.
- 12000 output для полного графа и 16000 retry — запас для более длинных процессов, не доказанный обязательный размер: предыдущий сложный результат уложился в 8000.
- 150/420/60 — предлагаемые временные пределы для раздельных стадий плюс coverage/fallback; не ускоряют ответы, увеличивают допустимое ожидание. Frontend сейчас ждёт до 500 с (`api.ts`); timeout reverse proxy/хостинга перед применением надо согласовать с общим deadline.
- Не меняем одновременно путь extraction на combined_v2/patch: сохраняем проверенную точность и избегаем выявленного v2 budget/reasoning mismatch.
- Резерв оставлен как сейчас, но его квоты надо подтвердить; новым primary-тестом нельзя доказать надёжность Vertex. Для оценки именно GPT полезно отдельно отличать fallback-результаты от primary.

`APP_ENV`, access token и HTTP лимиты не предлагается автоматически переносить из local в production. Для промышленного запуска нужен отдельный production профиль с непустым защищённым кодом, не его публикация в отчёте. Эти параметры не повышают BPMN-точность.

## 10. Проверка аудита

131 целевой тест config/provider/structured output/retry/cache/coverage/performance прошёл, одно прежнее предупреждение TestClient. Транспорт в этих тестах mock; новых платных запросов не было. Офлайн-проверки фабрики и request_options подтвердили effective caps и оба mismatch. Содержимое `.env` не выводилось; безопасная инвентаризация содержит для ключей только статус задан/пусто.

Локальные доказательства: `.runtime/config-audit/inventory.json`, `evidence.json`, `operation-probes.json`; источники живых измерений — предыдущий `.runtime/live-logic-audit/`.
