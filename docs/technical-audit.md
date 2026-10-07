# Технический аудит коммерческой копии

Дата: 08.10.2026. Аудит и изменения выполнены в независимой копии, без изменений исходного проекта.

| Приоритет / наблюдение | Решение | Проверка |
|---|---|---|
| Высокий: публичные AI операции могли создавать неконтролируемую нагрузку и расходы | Shared workspace code, production fail-closed, operation/concurrency/body limits | ASGI/HTTP guard tests, browser access tests, container smoke |
| Высокий: пустые primary overrides перекрывали общую конфигурацию модели | Пустые PRIMARY_LLM_* не перекрывают LLM_* | Provider independence regression test |
| Средний: browser tests использовали Windows path и могли подключаться к старому приложению | Portable Python path, выделенные test ports, reuseExistingServer=false | Полный browser suite; Linux CI |
| Средний: ответ proxy HTML превращался в JSON parse error | Безопасная APIError с recovery message | Browser upstream-error test |
| Средний: монолит HTTP модуля и UI responsibilities | Выделены process routes, HistoryPanel, AccessGate, import/model helpers, CSS tokens/access | Backend/browser/typecheck/build |
| Низкий: нерелевантная Copilot-подсказка и одинаковые названия снимков | Подсказка только при существующей проверке документов, имя процесса в истории | Contextual suggestion + Modify/Undo tests |
| Низкий: browser benchmark переписывал исторические результаты | Вывод только в test artifact directory | Performance browser tests, чистый Git diff исходных метрик |

## Сохранённая архитектура

Контроллеры работают с общим LLMProvider; transport/Structured Output остаётся в adapters. Pydantic/graph/semantic/XML/XSD/DI проверки выполняются до результата. Работающий Clarify/Modify/ChangeSet/Undo/Doctor/TO-BE/import/export сохраняется и проверяется существующим suite. Не заявляется, что модельный аудит заменяет программные правила или экспертную проверку.

## Остаточный долг

App.tsx по-прежнему объединяет оркестрацию рабочего экрана: выделены только устойчивые границы без переписывания всей UI state machine. Валидация и layout — сложные предметные модули, требуют контроля регрессий при расширении subset. Большой bpmn-js chunk загружается лениво; предупреждение размера не скрыто искусственным повышением лимита.

Нет persistent проектов, SSO/RBAC, tenant isolation, distributed quotas, денежного учёта, замеров production SLA или доказанного бизнес-эффекта. Не выполнен новый benchmark внешней LLM в рамках этого прохода. Исторические benchmarks — сведения о прежних конфигурациях, а не обещание результата любой модели.

Реестр поддерживаемого BPMN subset и его ограничений сохранён в bpmn-2.0.2-conformance.md. Конфигурация демо и production описана в enterprise-readiness.md и deploy/README.md.
