# Проверка коммерческой копии

Локальная проверка 08.10.2026: Python 3.12, pnpm 11.19.0, отдельные backend/frontend. Платные API-ключи не использовались.

- Backend: **491 passed**, 1 warning. Полный suite после инженерных изменений.
- Browser: полный suite **68 passed**; после последних правок — **19 passed** в затронутых access/Clarify/Modify/home/performance сценариях, включая дополнительную проверку контекстной подсказки.
- TypeScript / production build: успешно; Vite предупреждает о большом лениво загружаемом Modeler chunk.
- `pip-audit`: известных уязвимостей runtime и dev lockfiles не найдено.
- `pnpm audit`: 0 известных уязвимостей, включая dev dependencies.
- Bandit: medium/high findings не обнаружены.
- История всех доступных исходных веток: **505 текстовых blobs**; известных форматов секретов и значений настроенных исходных ключей не найдено. Значения ключей не публикуются. Pattern scan не гарантирует отсутствие произвольных секретов.
- Edge: проверены главный экран, построение BPMN, ChangeSet, Apply и панель истории; мобильная ширина 390px без горизонтального переполнения. Mobile screenshot дополнительно проверен из реального browser test.

На Windows отсутствует Docker Engine: локальная сборка контейнеров не заявляется. GitHub Actions включает backend, browser/typecheck/build, security и container build + HTTP/access/BPMN smoke. Его результат будет добавлен после реального запуска; до этого CI не считается пройденным.

[CI runs](https://github.com/Dteaher/pulse-ai/actions). Container smoke не проверяет ваш публичный домен, сертификат или живую LLM.
