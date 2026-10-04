# Performance review — 4 октября 2026

**Основная цель пока не достигнута:** стабильное ускорение первоначальной генерации
сложного процесса с тем же MultiAI `gpt-6.1-sol` не подтверждено. Ускоренные варианты,
терявшие действия или объединявшие отдельные роли, отвергнуты. Финальная конфигурация
сохраняет полные extraction/modify/clarify/BPMN-инструкции. Включены проверенные
улучшения транспорта, контекста, кэша, наблюдаемости и загрузки интерфейса.

Измерения: реальные API, сохранённые одинаковые тексты и SHA-256 входов.
Начальные запросы — cache miss; повторные запросы измерены отдельно.
Это отдельные прогоны, а не статистически надёжные p50/p95 или гарантии SLA.
Время зависит от доступности внешнего сервиса. Ключи, заголовки авторизации и `.env`
в доказательства не записываются.

## Отчёт по 30 пунктам

1. **Причина 274 секунд.** В старом `exact-complex` замере не учитывался целиком
   corrective/другие вызовы: существовал необъяснённый интервал около 140 секунд.
   Новый baseline воспроизвёл тот же текст за **325,68 с**: три успешных HTTP 200
   от MultiAI, без fallback. Анализ — 18,44 с, extraction — 160,11 с, полный
   corrective — 145,62 с. Это последовательная генерация двух больших JSON,
   а не медленный XSD или layout. Reasoning уже был `low`.

2. **Before breakdown:** см. `examples/performance/before-complex.json`.
   Нормализация 1,28 мс, validation 3,54 мс, build 38,43 мс; локальная работа
   на несколько порядков меньше API. Новые профили фиксируют все попытки.

3. **After breakdown:** замер кандидата `after-safe-complex.json` — **369,72 с**:
   analysis 40,62 с, extraction 167,38 с, corrective 161,63 с; build 39,45 мс.
   HTTP 200 и XML получены, но руководитель и менеджер объединены в одну роль.
   **Кандидат отвергнут.** Короткий full-graph corrective заменён полными правилами.
   Этот замер нельзя выдавать за ускорение или качество финальной версии.
   Сложный процесс с восстановленным полным corrective ещё не имеет нового
   принятого сравнительного замера. В диагностических опытах бюджет 600 с позволял
   измерять реальную задержку, а не подменять её ранним fallback.

4. **LLM calls before:** complex 3, simple 2, medium 1, ambiguous 1.
5. **LLM calls after, измеренный кандидат:** 3 / 2 / 1 / 1 соответственно.
   Объединённые вызовы исследованы, но выключены: устойчивого выигрыша без
   бизнес-регрессий нет. Повторный cache hit — **0 API calls**.
6. **Input tokens complex:** 14 781 → 10 644 в отвергнутом кандидате;
   это экономия контекста, не доказательство ускорения. После возвращения полных
   corrective-инструкций эту экономию нельзя целиком приписывать финальной версии.
7. **Output tokens complex:** 9 028 → 9 831. В обоих случаях модель заново
   выдаёт большой граф; сокращение входа не уменьшило время его выдачи.
8. **Reasoning effort:** `low` до и после, параметр принят MultiAI HTTP 200.
   `medium`: ConnectTimeout с fallback; `high`: timeout 180 с с fallback и ошибкой
   графа. Эти прогоны не доказывают поддержку/качество уровней или ускорение.
   Адаптивное переключение не включено; overrides доступны через `.env`.
   Реальное внутреннее reasoning внешнего сервиса не наблюдается.
9. **Graph corrective rate:** 1 из 2 запросов, построивших граф, в baseline и
   в двухэтапном кандидате. Clarify-only запросы не входят в знаменатель.
   Это маленькая выборка, не production rate.
10. **First-pass graph success:** 1/2 до и 1/2 у кандидата; semantic acceptance
    сложного кандидата отрицательная. Нет доказанного улучшения first pass.
11. **Fallback rate:** 0/4 в основной таблице до/после. В остальных опытах
    fallback явно указан в metadata и не включён в «ускорение MultiAI».
    Реальный Vertex также проверен: при ConnectTimeout primary цикл уточнений
    дал XML за 19,02 с (`ops-before-clarify-complete.json`), что не является
    latency исходной модели.
12. **Simple:** 53,35 → 54,35 с, MultiAI, HTTP 200, BPMN, без fallback.
    Финальный smoke работающего backend: **58,80 с**, MultiAI, два вызова,
    HTTP 200 и BPMN (`live-smoke.json`). Цель 10–15 с не достигнута.
13. **Medium:** 29,71 → 35,89 с, один критичный вопрос, без графа.
    В сохранённом тексте оператор одновременно «завершает процесс» после отправки
    и позже архивирует подписанный договор. Это контроль Clarify, а не успешная
    средняя генерация. Цель средней генерации 20–30 с этим тестом не доказана.
14. **Complex:** 325,68 → 369,72 с, кандидат не принят по бизнес-смыслу.
    Цель 30–60 с и существенное ускорение исходного 274-секундного кейса
    **не достигнуты**. Локальный кэш не используется для сокрытия этого результата.
15. **Ambiguous / Clarify:** исходный запрос 21,87 → 25,23 с, критичные вопросы,
    process/XML отсутствуют. Дополнительные реальные раунды сохранены в `ops-*`:
    неуказанный исполнитель доработки и неуказанный исход повторной проверки
    требуют отдельных ответов. Неопределённость не снимается ради скорости.
    Транспортные ошибки и ответ через Vertex маркируются отдельно.
    На финальном дополнительном раунде primary вернул вопрос о противоречии
    между старым ответом «оператор оформляет договор» и новым «менеджер оформляет
    договор». Это корректный отказ угадывать; завершённый primary цикл на этом
    наборе конфликтующих ответов не подтверждён. Старый полный UI roundtrip
    остаётся проверен тестом с сохранённым реальным результатом.
16. **Modify:** исходный коммит `4251110` **50,45 → 80,39 с**, тот же ProcessDefinition и команда
    переименования, MultiAI, два вызова, XML получен. Число сохранённых ID и ChangeSet
    записаны в `ops-original-modify.json` / `ops-after-modify.json` (8/8 ID сохранены).
    `ops-before-*` — дополнительные контрольные замеры текущего кода с flag off,
    а не исходного коммита; они не подменяют настоящий baseline `ops-original-*`.
    Ускорение до 15–30 с не подтверждено. Delta/combined modify выключен.
17. **Doctor:** исходный коммит `4251110` **45,89 → 61,65 с**, один MultiAI вызов.
    Начальный Clarify по тем же ответам: **34,68 → 44,50 с**, критичный вопрос
    без графа в обоих случаях. Локальные проверки остаются
    отдельными и строгими. Меньший output budget не обеспечил меньшую задержку.
18. **Cache miss:** первые `after-safe` запросы — 54,35 с simple,
    35,89 с medium Clarify, 25,23 с ambiguous Clarify. Нет выдачи mock/cache за LLM.
19. **Cache hit:** сохранённые повторные simple/medium/ambiguous — около 8–15 мс,
    0 calls, те же process/XML. Только та же вкладка/session, тот же запрос,
    ответы, операция, настройки, модели и версия кода/промптов/XSD.
    Ошибки не кэшируются. LRU 64 результата, TTL 300 с, лимит payload 16 MB,
    максимум 128 удерживаемых in-flight Futures. Одинаковые одновременные
    запросы разделяют одну операцию. Anonymous запросы без session не кэшируются.
    Повтор на работающем backend: **21,78 мс**, `cache.hit=true`, attempts=0,
    побайтово тот же XML и тот же ProcessDefinition (`live-smoke-cache.json`).
20. **Prompts:** убран повтор JSON Schema при native structured output;
    Doctor ограничен восемью содержательными findings. Полные BPMN, extraction,
    modification и clarification правила сохранены. Короткие extraction/prompts,
    compact wire и короткий full-graph corrective не приняты; доказательства
    неудач сохранены. Partial patches и combined preparation — эксперимент,
    `LLM_PATCH_ENABLED=false`, `LLM_COMBINED_ENABLED=false`.
21. **Context:** extraction больше не получает вторую копию original_text
    внутри PreflightState; остаются answers/analysis/assumptions. Modify/Doctor
    получают lossless canonical DTO без default/null полей. Corrective получает
    предыдущий граф, конкретные коды и связанные ID, ответы и допущения.
    Evidence, роли, сообщения, условия и бизнес-действия не вырезаются из DTO.
    XML/DI/history/tracebacks в LLM-контекст не добавляются.
22. **Timeout/retry:** production общий budget **270 с**, резерв fallback **60 с**,
    текущий provider timeout 180 с, connect/pool максимум 10 с.
    Primary/fallback не запускаются одновременно. 429/network/timeout/5xx сразу
    ведут к резерву; quota cooldown сохраняется. JSON corrective максимум один
    в performance mode, graph corrective один; обязательная повторная валидация.
    Это ограничение ожидания/устойчивость, не обещание ответа за 60 секунд.
23. **Persistent HTTP:** httpx и Vertex async client повторно используются в
    одном event loop, закрываются при shutdown. Настройки фиксируются на запрос;
    hot reload `.env` не закрывает соединение текущего запроса. Отдельный тест
    проверяет завершение старого запроса перед освобождением транспорта.
24. **Lazy Doctor:** не вызывается автоматически в generate/clarify/modify;
    запускается только по запросу пользователя. Схема не ждёт необязательный audit.
25. **XSD:** существующий local XMLSchema singleton сохранён, все проверки
    остаются обязательными. Дополнительно native JSON schemas имеют LRU cache16.
    Reference/XSD/DI и build измеряются отдельно, но spans вложенные:
    **их нельзя складывать как взаимоисключающие интервалы**.
26. **Tests:** cache isolation/expiry/eviction/byte limit/failure/singleflight;
    budgets/transport pooling/shutdown/env reload; JSON repair/failover;
    canonical DTO/patch-ID guards; critical gate; 12 golden business graphs
    с локальной validation/XSD/полным DI и замороженными fingerprints полного
    графа из исходного коммита `4251110` (включая роли, evidence, условия, сообщения,
    ID и допущения); длинные роли; сохранение всех правил
    full-graph prompts. Экспериментальный compact wire тестируется только как
    отвергнутый benchmark инструмент, runtime его не импортирует.
    Browser: реальные сохранённые MultiAI XML, локальные import timings,
    rapid-submit guard и сохранение session scope.
27. **Backend:** **362 passed**, исходные 313 тестов сохранены + 49 новых cases.
28. **Browser:** **58 passed**, исходные 55 + 3 новых. Один промежуточный прогон
    не смог записать скриншот в OneDrive; повтор этого теста и полный прогон прошли.
29. **Production build:** TypeScript/Vite прошли. Начальный JS **866,19 → 278,95 KB**,
    gzip **257,59 → 85,07 KB**. Modeler 580,23 KB загружается по требованию;
    весь редактор не удалён и общий размер приложения существенно не уменьшился.
    Предупреждение Vite о крупном Modeler chunk сохранено, не замаскировано.
    Browser import timings сохраняются локально в `frontend-*.json`.
30. **Quality:** ProcessValidator, semantic validators, BPMNBuilder, reference,
    XSD/DI checks, ChangeSet/Undo не ослаблены; старые assertions не удалены.
    Невалидный граф не публикуется. XML validity не равна полноте бизнес-модели:
    реальные кандидаты с потерей цикла или объединением ролей были отвергнуты.
    Поэтому основной performance Definition of Done пока не выполнен.

## Повторение проверки

Из backend, с заполненным локальным `.env`:

```powershell
../.venv/Scripts/python.exe -X utf8 tools/benchmark_performance.py --phase new-run --cases complex,simple,medium,ambiguous --effort low --combined false --optimized --budget 600 --cache-hit
../.venv/Scripts/python.exe -X utf8 tools/benchmark_operations.py
../.venv/Scripts/python.exe -m pytest -q
```

Реальные benchmark-команды обращаются к провайдерам и используют их квоту.
`600` — диагностический budget для честного primary сравнения; production `.env`
использует 270. Не меняйте код между miss/hit: изменение версии намеренно
инвалидирует результат. Не перезаписывайте исторические фазы before/after-safe.

Frontend: `pnpm build`, `pnpm test`. Измерения `pulse.frontend_bpmn_import.check`
и `.render` доступны в стандартном Performance API браузера; process content
и credentials туда не записываются.

Файлы `combined-*`, `optimized-two-stage-*`, `patch-*`, `compact-*`,
`quality-full-*`, `after-safe-complex*` — **история опытов**, а не обещания
production-качества. Следующий критерий приёмки: повторяемый реальный primary
прогон со всеми шестью отдельными ролями, обоими циклами, условиями и сообщениями,
меньшим числом дорогих вызовов и меньшей latency. Текущие данные этого не доказывают.
