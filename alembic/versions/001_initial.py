"""Initial migration – create chats, bot_config, send_log tables.

Revision ID: 001
Create Date: 2026-02-12
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── chats ─────────────────────────────────────────────────────────
    op.create_table(
        "chats",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("chat_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("allow_self_send", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_source", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_destination", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("registered_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_chats_active_dest",
        "chats",
        ["active", "is_destination"],
        postgresql_where=sa.text("active = true AND is_destination = true"),
    )

    # ── bot_config ────────────────────────────────────────────────────
    op.create_table(
        "bot_config",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )

    # Seed default config
    op.execute(
        "INSERT INTO bot_config (key, value) VALUES "
        "('signature_enabled', 'true'), "
        "('signature_text', ''), "
        "('signature_url', ''), "
        "('edit_redistribution', 'off'), "
        "('paused', 'false')"
    )

    # ── send_log ──────────────────────────────────────────────────────
    op.create_table(
        "send_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("dest_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("dest_message_id", sa.BigInteger(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "idx_send_log_source",
        "send_log",
        ["source_chat_id", "source_message_id"],
    )


def downgrade() -> None:
    op.drop_table("send_log")
    op.drop_table("bot_config")
    op.drop_table("chats")
