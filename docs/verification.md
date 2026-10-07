# Проверка коммерческой копии

08.10.2026: независимая рабочая копия, Python 3.12, pnpm 11.19.0. Настоящие платные API-ключи не использовались.

## Подтверждённые результаты

| Проверка | Локально (Windows / Edge) | GitHub Actions (Ubuntu 24.04 / Chromium) |
|---|---|---|
| Backend suite | 491 passed, 1 warning | Успешно |
| Browser suite | 69 passed | Успешно |
| TypeScript / production build | Успешно | Успешно |
| Dependency / source security checks | Успешно | Успешно |
| Container build + HTTP/access/mock BPMN smoke | Docker Engine отсутствует | Успешно |

**Все четыре CI jobs прошли** на инженерном commit `ad68d040d26adc11ccb579fb061e6ab471baa018`: [Quality run 37702274347](https://github.com/Dteaher/pulse-ai/actions/runs/37702274347). Изменения этой итоговой записи и группировки Dependabot не меняют приложение. Текущие проверки следующих коммитов отображаются в [Actions](https://github.com/Dteaher/pulse-ai/actions).

Первый CI выявил зависимость старого теста смены provider от переменной окружения CI; тест изолирован и повторный полный запуск прошёл. Не пропускались сценарии и не ослаблялись assertions. В timing-тесте увеличен резерв для jitter планировщика при сохранении реального timeout.

## Security

- pip-audit: известных уязвимостей runtime и dev lockfiles не найдено.
- pnpm audit: 0 известных уязвимостей, включая dev dependencies.
- Bandit: medium/high findings не обнаружены.
- При переносе исходной истории проверены 505 текстовых blobs всех доступных веток: известных форматов секретов и значений настроенных исходных ключей не найдено. Последующие новые коммиты дополнительно сканируются CI. Значения ключей не публикуются.

Pattern scan не гарантирует отсутствие произвольных секретов; dependency audit зависит от известных advisory databases. Production raw response logging выключен; общий workspace code и operation quotas не заменяют RBAC и денежный учёт.

## Визуальная и функциональная проверка

Edge: главный экран → пример → BPMN → ChangeSet → Apply → история. Мобильная ширина 390px проверена без горизонтального переполнения; screenshot дополнительно просмотрен из реального browser test. Опубликованный README, изображения и Mermaid проверены на GitHub.

Browser suite покрывает Clarify, Modify/preview/cancel, Undo, Doctor/TO-BE, экспорт/импорт, loops/collaboration/events, provider metadata и безопасные ошибки. Fixtures не являются свежим benchmark внешних LLM.

Остались некритичный deprecation warning тестового Starlette/httpx и предупреждение Vite о большом лениво загружаемом Modeler chunk. Container smoke не проверяет публичный домен, TLS сертификат или живую LLM на вашем сервере.
