# Архитектура

## Границы модулей

| Модуль | Ответственность |
|---|---|
| `backend/app/main.py` | Composition root, lifecycle, безопасные ошибки, telemetry, cache |
| `backend/app/process_routes.py` | HTTP-контракты generate/clarify/modify/audit/import/bpmn |
| `backend/app/security.py` | Код доступа, размер body, общие квоты и concurrency |
| `services/llm` | Общий provider contract, adapters, JSON validation, retry, failover |
| `models.py`, `preflight.py` | Каноническая модель, неоднозначности, ответы, допущения |
| `pipeline.py`, `validator.py`, `semantics.py` | Сборка pipeline и программные проверки |
| `bpmn.py`, `xml_validation.py` | XML, layout/DI, subset-импорт, локальные XSD |
| `frontend/src/services` | API, состояние версий, BPMN import check, telemetry |
| `frontend/src/components` | Редактор, preview, ChangeSet, история, доступ к workspace |

Modify создаёт кандидата без изменения активной версии. После проверок frontend показывает preview; только Apply меняет активную модель. Undo восстанавливает снимок. Ручные изменения редактора синхронизируются в AI-слой через subset-импорт. Неподдерживаемый импорт не должен молча терять элементы.

## AI-провайдеры

Модель настраивается только на backend: `LLM_*`; непустые `PRIMARY_LLM_*` имеют приоритет. Резерв использует `FALLBACK_LLM_*` и `LLM_FALLBACK_ENABLED=true`. Имена моделей/URLs берутся у соответствующего сервиса, а не из frontend. Для Yandex может потребоваться `YANDEX_FOLDER_ID`.

Основной интерфейс возвращает общие Pydantic DTO. Structured-output capabilities и преобразование transport-ответов находятся в adapters. Невалидный JSON не поступает в builder: ограниченная коррекция выполняется на текущем provider. Допускается fallback после исчерпания коррекций. Отдельная проверка графа имеет собственный лимит ремонта.

Router изолирован на запрос; deadline общий для попыток с резервом для fallback, если `LLM_PERFORMANCE_ENABLED=true`. 429 cooldown избегает немедленных повторных обращений к недоступной модели. Безопасные metadata показывают фактический provider/model, без ключей. Fallback может увеличить расходы: лимит операций не равен лимиту токенов или денег.

`LLM_MAX_RETRIES`, `LLM_GRAPH_MAX_RETRIES`, `LLM_TIMEOUT`, `LLM_REQUEST_BUDGET`, `LLM_MAX_TOKENS` ограничивают попытки. Дополнительные performance-флаги имеют operation-specific token budgets; при изменении конфигурации сначала выполните контролируемый тест. `LLM_BUSINESS_COVERAGE_ENABLED=true` добавляет модельную проверку соответствия тексту, а не формальное доказательство.

Cache хранит только валидированные результаты, ограничен объёмом/TTL и scope вкладки. Scope не является идентичностью пользователя. Cache/квоты находятся в одном процессе. Версии процесса — в памяти вкладки; это не серверное хранилище проектов.

Безопасность workspace-конфигурации фиксируется при запуске сервера. После изменения кода доступа или квот перезапустите backend. Ключи и модель могут перечитываться в development; это не управление секретами промышленного уровня.
