# Metacritic Games

Сервис раз в час забирает новые игры с Metacritic, сохраняет карточки с оценками по
платформам, пишет LLM-резюме отзывов критиков и игроков и показывает всё это на сайте.

Тестовое задание Skytec Games (AI Automation Engineer). Бюджет нулевой: только бесплатные
тарифы, без карты.

## Архитектура

```
GitHub Actions (cron + workflow_dispatch)          Vercel (FastAPI)
  python -m app.worker run                          app/main.py: сайт и API
    ├─ backend.metacritic.com (app/metacritic)          │
    └─ Neon Postgres  <─────────────────────────────────┘
```

- `app/metacritic/` — клиент внутреннего API Metacritic (официального API нет). Как он
  устроен и где подвохи: [docs/metacritic-research.md](docs/metacritic-research.md).
- `app/models.py`, `migrations/` — схема БД, Alembic.
- `app/main.py` — сайт (Vercel находит `app` автоматически): список с поиском при вводе,
  фильтром по платформе и сортировкой, карточка игры с похожими играми, `/monitor` —
  мониторинг воркера в реальном времени и кнопка «Запустить сейчас».
- `app/worker/` — воркер; запускается в GitHub Actions ([.github/workflows/worker.yml](.github/workflows/worker.yml)).
- Почему так и какие у бесплатных тарифов лимиты: [docs/infrastructure.md](docs/infrastructure.md).

## Локальный запуск

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # Linux/macOS: .venv/bin/pip
cp .env.example .env                                 # вписать строки подключения Neon
alembic upgrade head
python -m app.worker new-releases                    # без БД: список New Releases
python -m app.worker game elden-ring                 # без БД: карточка игры в JSON
python -m app.worker run                             # прогон с записью в БД
python -m app.worker summarize --limit 20            # резюме для игр в БД, у которых их нет
uvicorn app.main:app --reload                        # сайт на http://127.0.0.1:8000
```

## Тесты

```bash
pytest                    # офлайн, на фикстурах из tests/fixtures/metacritic
python -m scripts.capture_fixtures   # обновить фикстуры
```

Тесты БД запускаются, только если задан `DATABASE_URL` в окружении (не из .env).

## Деплой

- **Neon:** создать проект, `DATABASE_URL` (pooled) и `DATABASE_URL_UNPOOLED` (direct).
- **GitHub Actions:** те же две строки и `GROQ_API_KEY` в Settings → Secrets → Actions.
  Без ключа LLM воркер работает, но резюме отзывов не пишет.
- **Vercel:** `DATABASE_URL`, `CRON_SECRET` и `GITHUB_DISPATCH_TOKEN` в переменных окружения проекта. В Deployment Protection
  отключить Vercel Authentication, иначе сайт будет закрыт логином.
- **Ежечасный запуск:** задача на cron-job.org раз в час делает `GET https://<сайт>/api/cron`
  с заголовком `Authorization: Bearer <CRON_SECRET>` (подробно в docs/infrastructure.md).
