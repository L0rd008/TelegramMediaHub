"""UserRestriction model – mute / ban records for moderation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base


class UserRestriction(Base):
    __tablename__ = "user_restrictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    restriction_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "mute" or "ban"
    restricted_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    restricted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # NULL = permanent
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("idx_restriction_user_type", "user_id", "restriction_type", "active"),
    )

    def __repr__(self) -> str:
        return (
            f"<UserRestriction user={self.user_id} type={self.restriction_type} "
            f"active={self.active}>"
        )
