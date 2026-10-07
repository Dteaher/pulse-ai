# Закрытое демо на сервере

Docker Engine + Compose, домен с A/AAAA, порты 80/443. `.env` создаётся из `.env.example`; секреты не входят в образы. Реальную LLM включайте только после настройки budget у её provider.

Обязательно задайте случайный **PULSE_ACCESS_TOKEN** длиной не менее 32 символов в секретах хостинга. Это общий код доступа к workspace, не API-ключ модели и не аккаунт пользователя. Compose включает production и ограничения операций. После изменения доступа/квот перезапустите backend.

```sh
docker compose up -d --build
docker compose ps
curl --fail https://pulse.example.org/api/health
```

Замените домен своим и установите `SITE_ADDRESS=pulse.example.org`. Caddy выпускает HTTPS сертификат; TLS volumes сохраняются. Для локального теста: `SITE_ADDRESS=http://localhost`. Откройте страницу и введите workspace code. Frontend и API работают на одном origin; отдельный CORS не нужен. Backend-порт не публикуется.

Backend работает без root, с read-only filesystem и `/tmp`. Proxy доверяется только внутри приватной Compose-сети. При внешней публикации backend нужно отдельно ограничить trusted proxies. Vite dev/preview не является production-сервером.

Для PaaS: production env, секреты, reverse proxy `/api/*` с сохранением пути; startup `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`, workdir `backend`. Не используйте несколько workers/реплик с текущими process-local квотами. Общий срок ожидания в Compose 450s, proxy/browser 500s — верхняя граница, не целевая скорость.

После запуска: health 200; POST process без кода 401; проверка доступа с неверным кодом 401; с правильным 200; генерация примером → редактирование → экспорт. Health проверяет доступность приложения, не гарантирует доступность внешней модели.

[Ограничения и требования для B2B](../docs/enterprise-readiness.md). CI проверяет сборку контейнеров и mock smoke; это не проверка production TLS, домена или внешнего AI на вашем сервере.
