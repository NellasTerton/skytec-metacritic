from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")

    # Neon: DATABASE_URL — pooled-эндпоинт (через PgBouncer), DATABASE_URL_UNPOOLED — прямой,
    # для миграций. Имена совпадают с теми, что выставляет интеграция Neon в Vercel.
    database_url: str | None = None
    database_url_unpooled: str | None = None

    # Граница «сегодня» для логики выборки игр.
    service_tz: str = "Europe/Moscow"

    metacritic_concurrency: int = 5
    metacritic_timeout: float = 30.0
    metacritic_retries: int = 3

    # LLM для резюме отзывов: любой из провайдеров, порядок = порядок фолбэка.
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-flash-lite-latest"
    llm_base_url: str | None = None  # любой другой OpenAI-совместимый провайдер
    llm_api_key: str | None = None
    llm_model: str | None = None
    summaries_per_run: int = 20

    # Летсплеи (доп. часть 1 ТЗ): поиск — YouTube Data API, вывод — Gemini.
    youtube_api_key: str | None = None
    letsplay_models: str = "gemini-flash-lite-latest,gemini-flash-latest"  # порядок = фолбэк при 503/429

    # Запуск воркера с сайта: внешний cron дёргает /api/cron с CRON_SECRET, сайт вызывает
    # workflow_dispatch fine-grained токеном (только этот репозиторий, Actions: read & write).
    cron_secret: str | None = None
    github_dispatch_token: str | None = None
    # Не github_*: pydantic читает поля из переменных окружения, а GITHUB_REPOSITORY,
    # GITHUB_WORKFLOW и GITHUB_REF GitHub Actions выставляет в каждом job сам.
    worker_repository: str = "NellasTerton/skytec-metacritic"
    worker_workflow: str = "worker.yml"
    worker_ref: str = "main"
    # Автоматический запуск пропускается, если такой уже был за последние N минут
    # (внешний cron и запасной cron GitHub не дают двух прогонов в час).
    auto_run_min_interval_minutes: int = 50

    log_level: str = "INFO"

    @property
    def sqlalchemy_url(self) -> str | None:
        return to_sqlalchemy_url(self.database_url)

    @property
    def migrations_url(self) -> str | None:
        return to_sqlalchemy_url(self.database_url_unpooled or self.database_url)


def to_sqlalchemy_url(url: str | None) -> str | None:
    """Neon отдаёт `postgres://` / `postgresql://`; SQLAlchemy нужен явный драйвер psycopg3."""
    if not url:
        return None
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
