from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from agentsys.config import settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
    return _engine


def init_db() -> None:
    """Creates any tables that don't exist yet -- convenient for a brand-new
    dev DB or the test suite, but NOT a substitute for `alembic upgrade
    head`: create_all() never alters an existing table, so a schema change
    (a new column, a new NOT NULL constraint) shipped without also running
    the migration leaves the app running against a stale schema instead of
    failing at startup. Real deployments run migrations explicitly (see the
    Dockerfile CMD / docker-compose.yml worker command) before this is ever
    called."""
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Session:
    return Session(get_engine())
