"""UserAlias model – persistent random pseudonyms for sender identification."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base


class UserAlias(Base):
    __tablename__ = "user_aliases"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alias: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<UserAlias user={self.user_id} alias={self.alias}>"
