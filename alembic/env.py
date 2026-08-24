"""Wired to agentsys's own settings/models rather than alembic.ini's
[alembic] sqlalchemy.url -- the app already resolves its DB url from
.env/environment via agentsys.config.settings (see that file's docstrings
on why each setting is env-driven), so migrations should resolve it the
same way instead of duplicating the value in a second config file that
could drift out of sync.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# prepend_sys_path = src in alembic.ini puts src/ on sys.path, matching how
# the app itself is run (PYTHONPATH=src) -- this import is what makes
# target_metadata see every table: importing the models module is what
# registers them on SQLModel.metadata, same as main.py's
# `import agentsys.db.models  # noqa: F401` comment explains.
import agentsys.db.models  # noqa: F401
from agentsys.config import settings
from sqlmodel import SQLModel

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# psycopg's async driver prefix (postgresql+psycopg) works fine with
# Alembic's default sync engine -- psycopg3 supports both sync and async
# under the same driver name, so no url rewriting is needed here.
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
