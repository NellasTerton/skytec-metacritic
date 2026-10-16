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
- `app/media.py` — трейлер с YouTube (поиск через yt-dlp, без API-ключа) и скриншоты из Steam
  Store API: у Metacritic видео есть не у всех игр, а скриншотов нет вовсе.
- `app/main.py` — сайт (Vercel находит `app` автоматически): список с поиском при вводе,
  фильтром по платформе и сортировкой, карточка игры с похожими играми, `/monitor` —
  мониторинг воркера в реальном времени и кнопка «Запустить сейчас».
- `app/telegram.py` — бот [@skytecmetacriticbot](https://t.me/skytecmetacriticbot): на /start
  приветствие с кнопкой, открывающей сайт как Telegram Mini App. Внутри Telegram сайт берёт цвета
  темы клиента, показывает системную «Назад» и открывает внешние ссылки в браузере.
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
python -m app.worker media --limit 40                # трейлеры и скриншоты для игр без них
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
- **Telegram-бот (необязательно):** `TELEGRAM_BOT_TOKEN` и `TELEGRAM_WEBHOOK_SECRET` (любая
  случайная строка) в Vercel, затем один раз `python -m scripts.telegram_setup` с теми же
  переменными — он ставит вебхук, команду /start, описание бота и кнопку меню с Mini App.
