from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


class DatabaseNotConfigured(RuntimeError):
    pass


def make_engine(url: str, *, serverless: bool) -> Engine:
    # prepare_threshold=None: pooled-эндпоинт Neon — PgBouncer в transaction mode,
    # серверные prepared statements psycopg там ломаются.
    connect_args = {"prepare_threshold": None}
    if serverless:
        # На Vercel инстансы живут непредсказуемо, а Neon засыпает: свой пул только
        # держал бы протухшие соединения. Пулингом занимается PgBouncer у Neon.
        return create_engine(url, poolclass=NullPool, connect_args=connect_args)
    return create_engine(url, pool_size=2, max_overflow=2, pool_pre_ping=True, connect_args=connect_args)


@lru_cache
def get_engine(serverless: bool = True) -> Engine:
    url = get_settings().sqlalchemy_url
    if not url:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    return make_engine(url, serverless=serverless)


@lru_cache
def _session_factory(serverless: bool) -> sessionmaker[Session]:
    return sessionmaker(get_engine(serverless), expire_on_commit=False)


@contextmanager
def session_scope(serverless: bool = True) -> Iterator[Session]:
    session = _session_factory(serverless)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope(serverless=True) as session:
        yield session
