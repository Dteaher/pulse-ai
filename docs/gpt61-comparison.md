# MultiAI GPT-6.1 Sol: сравнение с Polza Luna

14 октября 2026. Локальный `.env.local` переключён на `FALLBACK_LLM_MODEL=gpt-6.1-sol`; ключи и production `.env` сохранены. Живой `/api/llm/info` подтвердил Polza Luna primary и MultiAI GPT-6.1 Sol fallback. Каталог MultiAI с действующим ключом: HTTP 200, exact ID `gpt-6.1-sol`, availability=available, tariff multiplier=1. Наличие проверено также inference: успешные ответы сообщили `model=gpt-6.1-sol`.

| Сценарий | Polza GPT-6 Luna, ранее | MultiAI GPT-6.1 Sol, новый запрос |
|---|---|---|
| Простой: последовательность и XOR | 33.95 с, BPMN | 33.85 с, BPMN |
| Средний: 2 Pool, сообщения, цикл | 31.64 с, BPMN | 97.01 с, BPMN |
| Сложный: 6 ролей, AND/XOR, 2 цикла | 50.79 с, BPMN | 128.46 с, timeout, BPMN нет |

В сложном запросе ambiguity API ответил HTTP 200 за 8.42 с. Extraction не вернул ответ в пределах operation timeout 120 с; PULSE вернул HTTP 502. Это не внешний HTTP 502 от MultiAI. ProcessDefinition отсутствует, поэтому семантическое качество сложного результата оценивать нельзя.

Использованы те же неизменённые тексты `examples/llm-benchmark`, DTO/validators/Builder и независимый semantic evaluator. Reasoning low, output 8000, cache/fallback/Business Coverage в сравнении выключены. Старый Polza проход использовал operation timeout 90 с; новый GPT-6.1 — 120 с и connect timeout 30 с. Никаких повторных Polza вызовов и новых попыток сложной генерации не было. Всего 3 новых полных генерации / 6 запросов MultiAI (preflight + extraction).

Простой результат прошёл проверки действий, роли, обоих исходов и условий XOR. Средний: роли, условия XOR, исправление документов с возвратом к проверке, event-based ожидание, 2 Pool, 4 MessageFlow. В обоих отсутствуют расхождения normalized ProcessDefinition → XML. Строгая JSON Schema реально использована в успешных вызовах. Обрезания output в полученных ответах нет.

Проверки: 9 offline тестов semantic benchmark и 3 браузерных теста в Edge. Две полученные схемы импортированы настоящим bpmn-js, все узлы найдены на полотне. Для сложного тайм-аута UI показал ошибку, снял loading и разрешил повтор, без выдуманной диаграммы. Полный backend suite не повторялся: production pipeline не менялся, предыдущие 576 passed относятся к предыдущему проходу. В runner добавлен только явный диагностический флаг `--continue-after-failed-complex`, позволяющий запустить MEDIUM после неответившего COMPLEX при успешном SIMPLE; ограничения количества вызовов сохраняются.

MultiAI usage: SIMPLE 5363 input / 948 output; MEDIUM 5752 / 2631. Для COMPLEX известен usage preflight, но биллинг оборвавшегося extraction неизвестен. Rub price API не возвращает; `null` не заменяется нулём. Сохранённые старые Polza стоимости: SIMPLE 0.15072495 ₽, MEDIUM 0.18378733 ₽, COMPLEX 0.44135415 ₽. Подробные агрегаты: `docs/gpt61-comparison.json`; raw trace/performance/XML: `.runtime/gpt61-check` (ignored, без ключей).

Вывод: в этих запросах GPT-6.1 Sol сопоставима по скорости с Luna только на простом процессе, медленнее на среднем и не дала результата на сложном. Объективных оснований считать её лучшим резервом по сравнению с проверенной Astra пока нет. По прямому запросу владельца локальный fallback оставлен на GPT-6.1 Sol; рекомендуемый Amvera template с Astra не менялся. Реальное переключение primary error → GPT-6.1 в этом проходе не тестировалось: сравнение намеренно изолировано от fallback. Public readiness остаётся NEEDS FIXES, включая неподтверждённый ingress timeout Amvera.

Повтор benchmark (из корня):

```powershell
.venv/Scripts/python.exe backend/tools/llm_benchmark.py --model gpt-6.1-sol --case simple --output .runtime/new-approved-benchmark --max-generations 3
```

Новый платный проход запускать только по отдельному запросу, чтобы не создавать случайные повторы. Production deploy и push не выполнялись.
