# PULSE: Polza primary и проверенный MultiAI fallback

Дата: 14 октября 2026. Ветка: `codex/polza-provider`. Production `.env` и исходный хакатонный репозиторий не изменены; push/deploy не выполнялись.

**Статус: NEEDS FIXES — перед публичным запуском необходимо подтвердить внешний предел ожидания Amvera. Локальный маршрут готов и проверен; это не сообщение об ошибке генератора.**

## Выбор модели

Выбран резерв `multiai / gpt-6-astra`. Основной маршрут — `polza / openai/gpt-6-luna`. Authenticated `/v1/models` вернул HTTP 200 и exact ID Sol, Astra, Opus 5.5, GPT-5.5; доступ inference проверялся отдельно. Sol успешно прошла SIMPLE, но COMPLEX оборвался при установлении соединения. Astra успешно прошла SIMPLE, COMPLEX и MEDIUM через настоящий fallback. Это не доказывает превосходство Astra во всех задачах: выбор основан на конкретных результатах этого прохода.

| Новая полная генерация | Модель | Время | Результат |
|---|---|---:|---|
| SIMPLE | GPT-6 Sol | 74.14 с | HTTP PULSE 200, BPMN, семантика пройдена |
| COMPLEX | GPT-6 Sol | 10.10 с | HTTP PULSE 502, timeout до ответа модели |
| SIMPLE | GPT-6 Astra | 98.74 с | HTTP PULSE 200, BPMN, семантика пройдена |
| COMPLEX | GPT-6 Astra | 144.42 с | HTTP PULSE 200, BPMN, семантика пройдена |
| MEDIUM, primary timeout | GPT-6 Astra | 10.13 с | Переключение произошло, резерв: ConnectTimeout |
| MEDIUM, primary timeout, connect=30 с | GPT-6 Astra | 74.51 с | HTTP PULSE 200, настоящий fallback и BPMN |

Пять генераций соблюдены первоначально; шестой повтор отдельно разрешён владельцем. Astra/Claude дополнительные full runs не выполнялись сверх этих шести. Opus не тестировался: Astra прошла проверки. GPT-5.5 baseline из предыдущего прохода: SIMPLE и MEDIUM timeout, COMPLEX 34.75 с успешно. Новые сравнительные runs используют одинаковые тексты, DTO, validators, reasoning low и output 8000, cache/fallback отключены; резервные тесты выделены отдельно. Новый operation timeout 120 с, в старом baseline был 90 с — прямое статистическое сравнение задержек ограничено.

Каталог MultiAI показывает только коэффициенты: Sol 1.0, Astra 1.7, Opus 1.5, GPT-5.5 0.8. Цена в ₽ отсутствует. Владелец разрешил расход доступных токенов независимо от денежного тарифа. Стоимость MultiAI оставлена **null**, не нулём. Стоимость одной успешной генерации нельзя честно сравнить в рублях. В [машинном отчёте](polza-benchmark-results.json) сохранены duration и usage каждого доступного API-ответа; неответившие запросы не имеют известного usage/биллинга.

## BPMN и Business Coverage

Независимый evaluator проверяет исходные ожидания, не description сгенерированного процесса: действия и исполнители, позитивные/негативные ветви XOR, AND split/join, возврат именно к проверке, event-based ожидание, 2 Pool и 4/5 MessageFlow, fidelity ProcessDefinition → XML. Эталонные fixtures и мутации проверяются offline. Валидаторы, XSD, DI и pipeline не ослаблены.

Coverage через Polza: правильная SIMPLE — gaps=0, удалённая регистрация — gaps=1, правильная COMPLEX — gaps=0. Через Astra: удалённая регистрация — gaps=1, правильная COMPLEX — gaps=0. Первый запуск Coverage Astra завершился ProviderError; повтор прошёл. Его точная причина не была сохранена первым вариантом runner, поэтому она не приписывается бизнес-логике. Runner исправлен: последующие ошибки сохраняют safe reason и metrics без текста исключения/секретов.

Новые реальные Coverage через Polza стоили **0.13405457 ₽**: 0.02535579 ₽ / 3.86 с, 0.02999060 ₽ / 3.39 с, 0.07870818 ₽ / 6.30 с. Astra Coverage: 14.27 с (пропуск) и 34.86 с (правильная сложная схема); рублёвая стоимость неизвестна. Отдельные ошибки роли/условия/сообщения/цикла проверены offline через общий typed contract; реальная модель не тестировалась на каждом виде намеренного искажения. Эти несколько проверок не доказывают отсутствие любых будущих ложных срабатываний.

## Исправления

- Metadata генератора больше не подменяется reviewer. Добавлены requested/used identities, причина fallback, duration, nullable usage/cost и отдельная identity Coverage.
- Фактический response_model используется при наличии; secrets отсутствуют в metadata.
- Общий deadline теперь применяется независимо от performance flag. Истёкший budget не запускает резервный HTTP-запрос.
- Добавлен отдельный connect timeout (default 10 с, локальный/рекомендуемый профиль 30 с). Timeout phase фиксируется безопасно.
- Добавлен opt-in `PULSE_ENV_FILE`: локальный `.env.local` загружается явно, production `.env` сохранён. Пустые optional настройки корректно означают auto/inheritance, а не ошибку Pydantic.
- `LLM_TRUST_ENV` позволяет сохранить прежнее использование system proxy по умолчанию или явно выбрать direct transport. Локальный профиль/benchmark используют false; Windows system proxy мешал проверке loopback, что подтверждено HTTP health через прямое соединение. Глобальные proxy настройки компьютера не менялись.
- UI показывает реальную модель и провайдера из backend, отдельно Coverage и резерв, не выдаёт reviewer за генератор. Ошибка снимает loading и позволяет повторить.
- Benchmark воспроизводим, ограничивает full generation ledger и wire attempts, проверяет SIMPLE перед COMPLEX, COMPLEX перед MEDIUM, не перезаписывает результаты, не публикует исключения с данными запросов.

## Матрица отказов

Реально: искусственный primary timeout → настоящий MultiAI Astra → валидная MEDIUM BPMN, metadata fallback_reason=timeout. Timeout/429/500/502/503/504/network error, invalid JSON corrective retry, оба API недоступны, cooldown, истёкший/недостаточный budget проверены transport mocks и регрессионными тестами. Отдельные живые резервные генерации для каждой категории ошибки не запускались ради соблюдения согласованного лимита. Ни бесконечных retries, ни fallback после общего deadline нет.

## Локальное окружение и Amvera

`.env.local` исключён из Git, содержит Polza → Astra и включённую Coverage. Backend запущен на 127.0.0.1:8002 с `PULSE_ENV_FILE`; health/info 200 подтвердили маршрут без раскрытия ключей. Vertex сохранён, в активном маршруте отсутствует.

[Шаблон переменных Amvera](../deploy/amvera-polza.env.example): operation 120 с, connect 30 с, request budget 360 с, reserve 120 с; output limits сохранены, retry JSON=1/graph=1, Coverage включена с наследованием reasoning. Production требует workspace code ≥32 символов и API limits. Browser/Caddy по 500 с; Compose отдельно override deadline 450 с, это не настройка Amvera. Квоты/cache/cooldown process-local, нужен один worker/replica.

Публичный ingress Amvera timeout **не подтверждён**. До публикации нужно проверить маршрут reverse proxy, secret injection/access gate и фактический предел ожидания. Если ingress обрывает до типичных 144 с либо до request budget, следует уменьшать synchronous deadline только до измеренного предела или переходить к job ID/status/result. Асинхронные jobs не внедрялись: необходимость пока не доказана. Нельзя считать 500 с локального Caddy ограничением Amvera.

## Проверки и оставшиеся ограничения

- Backend: **576 passed**, одна существующая Starlette/TestClient deprecation warning.
- Полный browser suite: **76 passed**, включая реальные сохранённые SIMPLE/MEDIUM/COMPLEX Astra и Sol, bpmn-js, Clarify/Modify/ChangeSet/Undo/Doctor.
- После последней правки отображения повторно выполнены 6 failover/metadata UI-тестов.
- TypeScript и Vite build прошли. Есть предупреждение о размере lazy Modeler chunk (580 KB), не ошибка сборки.
- Screenshots рабочего редактора и подписи модели просмотрены.
- Scanner актуальных локальных secrets по tracked + nonignored Git файлам: 0 совпадений. `.env.local`/`.env.benchmark` ignored. Benchmark logs отключены, reports sanitised. Полного исторического аудита всех старых Git blobs не выполнялось.
- Недостаточно выборки для SLA/median/p90 и доказательства «самого надёжного» провайдера. Фактические ConnectTimeout показали внешнюю нестабильность, а не потерю действий backend.
- Не тестировалась живая chain primary generation → другое LLM Coverage failover; разделение provenance подтверждено mock-тестом, сами reviewer маршруты проверены реально отдельно.
- Full suite доказывает сохранение эталонных процессов, но не корректность любого произвольного LLM-ответа.

## Изменённые файлы

- `.env.example`
- `README.md`
- `backend/app/config.py`
- `backend/app/models.py`
- `backend/app/services/llm/factory.py`
- `backend/app/services/llm/openai_compatible_provider.py`
- `backend/app/services/llm/openai_provider.py`
- `backend/app/services/llm/router.py`
- `backend/app/services/llm/yandex_provider.py`
- `backend/tests/test_benchmark_semantics.py`
- `backend/tests/test_llm_router.py`
- `backend/tests/test_polza_final.py`
- `backend/tests/test_vertex_failover.py`
- `backend/tools/benchmark_semantics.py`
- `backend/tools/llm_benchmark.py`
- `deploy/amvera-polza.env.example`
- `docs/polza-benchmark-results.json`
- `examples/llm-benchmark/complex.json`
- `examples/llm-benchmark/complex.txt`
- `examples/llm-benchmark/medium.json`
- `examples/llm-benchmark/medium.txt`
- `examples/llm-benchmark/simple.json`
- `examples/llm-benchmark/simple.txt`
- `frontend/src/App.tsx`
- `frontend/src/services/modelLabel.ts`
- `frontend/src/styles.css`
- `frontend/src/types.ts`
- `frontend/tests/failover.spec.ts`
- `frontend/tests/live-benchmark.spec.ts`
- `frontend/tests/polza-metadata.spec.ts`

Локальные секретные профили и runtime artifacts не входят в коммиты.

Также сохранены `docs/polza-final.md` и `docs/polza-benchmark-results.json`.
