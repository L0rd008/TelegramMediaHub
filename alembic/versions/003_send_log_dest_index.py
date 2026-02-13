"""Add reverse-lookup index on send_log(dest_chat_id, dest_message_id).

Revision ID: 003
Create Date: 2026-02-13
"""

from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_send_log_dest",
        "send_log",
        ["dest_chat_id", "dest_message_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_send_log_dest", table_name="send_log")
