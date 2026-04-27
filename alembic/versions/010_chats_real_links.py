"""Add chats.real_links_enabled column.

Revision ID: 010
Create Date: 2026-04-26

Per-chat boolean controlling whether the alias-tag entity that the bot
attaches to outbound messages targets the actual user / group profile
instead of the bot itself.

Default: ``False``. Premium-gated at the application layer (the
``/identity`` command refuses to flip it to ``True`` for non-premium chats).

Why a column and not a Redis flag
=================================

This setting is consulted on every outbound send call (one Redis lookup per
fan-out target was an option), but the value changes rarely (manual user
toggle) and the chat row is already loaded by the distributor for the
paywall check. Folding it into ``Chat`` means zero extra IO on the hot path
and ensures the value is consistent across worker restarts and Redis flushes.

Why default ``False``
=====================

- The alias system was added specifically to give pseudonymity by default.
  Flipping this on globally would silently break that contract for every
  existing chat.
- Surfacing identity is a consent decision. Making it opt-in keeps the
  user (or group admin) in control rather than relying on them noticing
  the change.
- Free chats get told about the feature via the value-prop block in
  ``/help`` / ``/plan`` and the trial reminders, but never have it forced on.
"""

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chats",
        sa.Column(
            "real_links_enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Drop the server_default after backfill so future inserts use the model
    # default (False) and there's no double-source-of-truth surprise.
    op.alter_column("chats", "real_links_enabled", server_default=None)


def downgrade() -> None:
    op.drop_column("chats", "real_links_enabled")
