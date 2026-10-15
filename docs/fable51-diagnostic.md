# Диагностика формата Fable 5.1

Два отдельных прямых extraction-запроса к MultiAI `claude-fable-5-1` на SIMPLE golden text: reasoning low, без output token cap, без retries и без изменения production-конфигурации. Текст ответа и ключи не сохранялись.

В первом ответе JSON синтаксически разобран, но Pydantic отклонил структуру: `status` и `accepted_assumptions` — extra_forbidden; верхнеуровневые `nodes` и `flows` — missing; `pools.0.lanes`, `pools.0.nodes`, `pools.0.flows` — extra_forbidden. Это несовместимость возвращённой структуры с ProcessDefinition, а не ошибка чтения content или BPMN Builder.

Повторный запрос с той же strict JSON Schema, дополнительно явно указанной в system prompt, прошёл `validated_json` и Pydantic ProcessDefinition. Бизнес-семантика и XML в этом диагностическом тесте не проверялись. Один успешный ответ не доказывает устойчивость на MEDIUM/COMPLEX. На основании проверки нельзя делать вывод о внутренних механизмах MultiAI: известно только, что переданная через response_format схема не была соблюдена в первом ответе.

Никакого автоматического переноса узлов из pools или ослабления валидатора не выполнялось. Производственный код и .env не менялись. Рекомендация для отдельного исправления совместимости: опционально дублировать авторитетную схему в system prompt для маршрутов, не соблюдающих native structured output, затем повторить полноценные семантические тесты.
