# Sonnet 5 как резерв MultiAI

Точный ID claude-sonnet-5 подтверждён авторизованным каталогом. Один complex golden text — тот же, что в complex-four-models. Реальный резерв LLMRouter после тестового timeout Polza, явная JSON Schema только в fallback prompt, reasoning low, без output token cap, cache off. Рабочая Polza и .env не изменены.

84,51 секунды, HTTP 200, BPMN получена. Три реальных POST к MultiAI (анализ, извлечение, исправление); искусственный timeout Polza не выполнялся по сети. Шесть ролей, отдельные действия, XOR, AND split/join, два цикла, событийное ожидание, пять MessageFlow, XML fidelity — passed. Реальный импорт сохранённого XML в bpmn-js в Edge: все узлы видны, резерв обозначен, ошибок страницы нет; 1 browser test passed. Цена MultiAI неизвестна.

Предыдущие одиночные прогоны того же текста: Opus 5 — 79,89 с, Polza Luna — 54,60 с, оба passed. Sonnet прошёл, но в этом прогоне не оказался быстрее. Выборка из одного прогона не доказывает стабильность.
