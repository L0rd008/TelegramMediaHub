"""Add user_aliases, user_restrictions tables and send_log.source_user_id column.

Revision ID: 004
Create Date: 2026-02-13
"""

import sqlalchemy as sa
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- user_aliases --
    op.create_table(
        "user_aliases",
        sa.Column("user_id", sa.BigInteger, primary_key=True),
        sa.Column("alias", sa.String(12), unique=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # -- user_restrictions --
    op.create_table(
        "user_restrictions",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column("user_id", sa.BigInteger, nullable=False),
        sa.Column("restriction_type", sa.String(10), nullable=False),
        sa.Column("restricted_by", sa.BigInteger, nullable=False),
        sa.Column(
            "restricted_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column(
            "active", sa.Boolean, default=True, nullable=False
        ),
    )
    op.create_index(
        "idx_restriction_user_type",
        "user_restrictions",
        ["user_id", "restriction_type", "active"],
    )

    # -- send_log: add source_user_id --
    op.add_column(
        "send_log",
        sa.Column("source_user_id", sa.BigInteger, nullable=True),
    )
    op.create_index(
        "idx_send_log_user", "send_log", ["source_user_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_send_log_user", table_name="send_log")
    op.drop_column("send_log", "source_user_id")
    op.drop_index(
        "idx_restriction_user_type", table_name="user_restrictions"
    )
    op.drop_table("user_restrictions")
    op.drop_table("user_aliases")
