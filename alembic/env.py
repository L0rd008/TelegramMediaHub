"""Alembic env.py – async migration runner."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from bot.config import settings
from bot.db.base import Base

# Import all models so they register on Base.metadata.  Anything missing here
# will be invisible to ``alembic revision --autogenerate`` and produce empty
# diffs, so keep this list in sync with bot/models/.
from bot.models.chat import Chat  # noqa: F401
from bot.models.bot_config import BotConfig  # noqa: F401
from bot.models.send_log import SendLog  # noqa: F401
from bot.models.subscription import Subscription  # noqa: F401
from bot.models.user_alias import UserAlias  # noqa: F401
from bot.models.user_restriction import UserRestriction  # noqa: F401
from bot.models.chat_alias import ChatAlias  # noqa: F401
from bot.models.chat_restriction import ChatRestriction  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = create_async_engine(settings.DATABASE_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
