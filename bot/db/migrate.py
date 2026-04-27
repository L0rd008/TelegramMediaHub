"""Self-applying alembic migrations on bot startup.

Why this exists
===============

The startup hook in ``bot.app._on_startup`` historically called only
``Base.metadata.create_all`` to materialise tables.  That operation creates
*missing tables* but **does NOT add columns to existing tables** — meaning
any migration that does an ``op.add_column`` (or ``alter_column``,
``drop_column``, etc.) is silently ignored on a deploy that doesn't manually
run ``alembic upgrade head``.

That's how 2026-04-26's deploy started crashing on every chat with::

    UndefinedColumnError: column "real_links_enabled" of relation "chats"
    does not exist

— alembic revision 009 added the column to the model, but the running DB
still had the pre-009 ``chats`` schema.  ``create_all`` saw the table and
moved on.

The fix is to run ``alembic upgrade head`` programmatically as the *first*
thing the bot does on startup, before any model-touching code paths, so
the schema is always at least as new as the code.  The previous
``create_all`` call is kept as a belt-and-braces fallback for fresh
databases that haven't been alembic-stamped yet.

Cost: a few hundred milliseconds on each container start, and the alembic
config file must ship with the image.  Both are trivially worth it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.config import settings

logger = logging.getLogger(__name__)


def _alembic_config() -> Config:
    """Build the alembic Config used by the in-process upgrade.

    The bot must work from any working directory (``docker run`` sets
    ``/app`` as the cwd by convention, but tests run from the repo root,
    and a future operator may run the bot from somewhere else entirely).
    Locate the alembic.ini relative to *this file* rather than relying on
    cwd-based resolution.
    """
    # bot/db/migrate.py → repo root → alembic.ini
    repo_root = Path(__file__).resolve().parent.parent.parent
    ini_path = repo_root / "alembic.ini"
    if not ini_path.exists():
        # Some deploy layouts ship the .ini next to the alembic/ directory
        # but not at the repo root.  Try one more place.
        alt = repo_root / "alembic" / "alembic.ini"
        if alt.exists():
            ini_path = alt
        else:
            raise FileNotFoundError(
                f"alembic.ini not found at {ini_path} or {alt}; cannot "
                "self-migrate on startup."
            )

    cfg = Config(str(ini_path))
    # Override the URL hard-coded in alembic.ini with the one the bot
    # actually uses, so dev / staging / prod all migrate against their own DB
    # without per-environment .ini files.
    cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    # And tell alembic where the migration scripts live (relative to the .ini).
    cfg.set_main_option("script_location", str(ini_path.parent / "alembic"))
    return cfg


def _do_upgrade_head(_conn) -> None:
    """Synchronous callback handed to ``connection.run_sync``.

    Alembic's ``command.upgrade`` is sync — we run it inside a sync wrapper
    so it cooperates with the async engine's connection pool.
    """
    cfg = _alembic_config()
    command.upgrade(cfg, "head")


async def upgrade_to_head(engine: AsyncEngine) -> None:
    """Bring the connected DB up to ``head``.  Logs progress and re-raises
    on failure so the bot doesn't keep running against a broken schema.

    Idempotent — running it on an already-current DB is a no-op.

    Operators can disable this by setting ``DISABLE_AUTO_MIGRATE=1`` in the
    environment, e.g. when running migrations from a separate CI job and
    not wanting startup to touch the schema.
    """
    if os.environ.get("DISABLE_AUTO_MIGRATE") == "1":
        logger.info(
            "DISABLE_AUTO_MIGRATE=1 — skipping alembic upgrade head on startup."
        )
        return

    logger.info("Running alembic upgrade head…")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_do_upgrade_head)
        logger.info("Schema is at head.")
    except Exception:
        logger.exception(
            "Alembic upgrade failed — the bot will continue starting but "
            "model/schema mismatches may cause errors on every request. "
            "Inspect the migration error above and run "
            "`alembic upgrade head` manually."
        )
        raise
