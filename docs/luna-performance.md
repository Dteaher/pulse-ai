# PULSE: измерение времени и сравнение Luna

Дата: 14.10.2026. Ветка `codex/luna-performance`. Production `.env`, локальный рабочий `.env.local`, ключи и Amvera не менялись. Профиль тестов сохранён в игнорируемом `.env.benchmark`; API-ключи читаются только оттуда. Benchmarks изолированы через explicit Settings, cache и fallback выключены. Push/deploy не выполнялись.

**Статус: PARTIAL_BUDGET_EXHAUSTED.** Владелец ограничил новые платные запросы пятью API-вызовами, включая retries. Требуемые 27 полных генераций/три повтора не помещаются в этот предел (обычно минимум 54 HTTP API-вызова). Ровно пять вызовов выполнены; дальнейшие платные проверки остановлены. Текст задания обрывается после начала списка метрик, содержание последующих этапов не получено. Поэтому отдельного полного отчёта по неописанным этапам 3/4 нет.

## Где теряется время

Старые сохранённые Polza результаты: SIMPLE ambiguity 23.81 с + extraction 10.09 с; MEDIUM 12.91 + 18.70 с; COMPLEX 4.01 + 46.73 с. Builder занимает 10–24 мс. Замеры вложенные: Builder включает layout/XSD/reference/DI, их нельзя складывать как независимые стадии. Именно внешний LLM, а не построение XML, объясняет практически всё время ожидания этих примеров. Разброс старого/нового SIMPLE Polza (33.95 → 15.60 с) также показывает вариативность API: это не доказательство ускорения кода.

Подтверждены дефекты инструментирования: semantic_validation_ms включала обычный validator; layout отсутствовал как отдельный замер. Исправлены границы замеров. Все проверки XSD, DI, references, fidelity остаются включёнными, даже повторные проверки не удалены ради миллисекунд.

## Новые реальные запросы

| Маршрут / режим | Процесс | Полное время | Платных вызовов | BPMN / семантика |
|---|---|---:|---:|---|
| A: Polza / openai/gpt-6-luna, baseline | SIMPLE | 15.60 с | 2 | Passed |
| B: MultiAI / gpt-6-luna, baseline | SIMPLE | 35.55 с | 2 | Passed |
| B: MultiAI / gpt-6-luna, combined | SIMPLE | 39.13 с | 1 | Passed |

Официальный authenticated GET `/v1/models` вернул gpt-6-luna и gpt-5.6-luna как разные доступные ID. Для B выбран **gpt-6-luna**, соответствующий поколению маршрута A. Ответы B сообщили фактически model=gpt-6-luna; замены другой моделью не было. Каталог не считался доказательством inference: доступ подтверждён реальными ответами.

Все три SIMPLE сохранили действия/роль, условия и исходы XOR и fidelity ProcessDefinition → XML; native JSON Schema, finish_reason=stop, output не обрезан, корректировки не потребовались. Никаких текстов клиентов: использован существующий golden SIMPLE. Medium/Complex через новые маршруты не тестировались из-за лимита, трёх повторов нет. Маршрут C GPT-5.5 заново не запускался: старый референс сохранён в docs/polza-check.md (SIMPLE/MEDIUM timeout, COMPLEX 34.75 с успешно). Старые результаты нельзя выдавать за новые повторы baseline.

Расход Polza составил **0.17719250 ₽** по usage API. MultiAI не сообщил цену в ₽: стоимость оставлена null; SIMPLE baseline usage 5363 input / 961 output, combined 5273 / 1122. Общий денежный расход MultiAI не выдумывается; ранее владелец разрешил расход доступных токенов. Исчерпан именно количественный лимит 5, а не доказанный общий рублёвый лимит.

## Оптимизация и вывод

Проверен существующий флаг `LLM_COMBINED_PARSE_ENABLED` (default false): typed ParseDecision объединяет анализ неоднозначностей и extraction в один LLM-запрос; prompts/Schema сохраняют необходимые BPMN правила и критический Clarify gate. Новые регрессионные тесты проверяют все три golden процесса с флагом OFF/ON: ProcessDefinition и XML-семантика сохраняются, число запросов меняется с 2 до 1. Старые тесты подтверждают, что критичные вопросы по-прежнему блокируют XML.

**Wall-clock ускорение не подтверждено:** один combined SIMPLE занял 39.13 с против 35.55 с baseline (+10.1%). Снизилось количество API-вызовов, но не latency в этом измерении. Флаг по умолчанию оставлен выключенным, рабочий профиль не менялся. Нет оснований объявлять combined быстрее или переносить результат одного SIMPLE на сложные процессы. Polza сейчас быстрее B в этом малом сравнении; сравнение всего семейства моделей/провайдеров статистически не доказано.

## Инструментирование

`LLM_DETAILED_METRICS_ENABLED=false` по умолчанию, true только в benchmark. Ответ содержит `metadata.performance` с операциями и этапами. Для операций: UTC start/end, duration, provider, requested/response model, attempt, input/output/reasoning usage, finish_reason, фактический response_format и история его деградации, retry_reason, fallback. Stream отсутствует — TTFT null, а не выдуманный ноль. Для локальных этапов: monotonic start/end offsets, duration, iteration; provider=backend/model=null, так как XML не создаётся моделью.

Операции логируются allowlist-полями, payload/headers/API keys не проходят. Адаптер маскирует собственный ключ в строковых metric fields. XML error logging больше не печатает текст исключений с потенциальным содержимым клиентского XML. Стоимость и usage сохраняются nullable при отсутствии.

Response snapshot формируется перед сериализацией: serialization duration/request_total в metadata не являются полным временем доставки HTTP. Итоговый server-side profile после сериализации хранится в performance_profiles и журнале; frontend wall-clock не измерялся. Замеры этапов вложенные и повторные, не суммировать их вслепую. Если response_body не получен, нельзя определить настоящий response_model/finish_reason/TTFT.

## Лимиты benchmark

`--max-total-wire-calls 5` использует общий paid-wire-ledger.json во всём каталоге результатов. POST резервирует слот до передачи в сеть, включая форматные retries и ошибки подключения. Счётчик атомарно записывается под exclusive lock; шестой запрос блокируется до отправки. CLI model ID очищается только для имён файлов; настоящий ID API не подменяется. Конфигурация читается из `.env.benchmark`, module-level app import не открывает production `.env`. Профиль используется явно, поэтому production-настройки не затрагиваются.

## Проверки

Полный backend: **588 passed**, существующая TestClient deprecation warning. Новый instrumentation/combined regression покрывает mock usage/timestamps/format/TTFT/secret redaction, золотые SIMPLE/MEDIUM/COMPLEX, deadlines и неизменённые validators. Hard budget тестируется на пяти слотах и запрете шестого, в том числе concurrent lock. Frontend/UI не менялись; новые браузерные тесты в этом проходе не выполнялись. Предыдущие 76 browser tests относятся к прошлому проходу.

Артефакты: `.runtime/luna-performance/` (ignored). Метрики пяти API-вызовов без клиентских текстов: [машинный отчёт](luna-performance-results.json). Бизнес-логика/ProcessDefinition/XSD subset не ослаблены. Перед продолжением платных повторов потребуется новый лимит и оставшаяся часть задания.

Reasoning effort в метриках — отправленное значение. MultiAI не вернул reasoning tokens; эффективную внутреннюю интенсивность рассуждения по этой телеметрии подтвердить нельзя.
