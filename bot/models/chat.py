"""Chat model – persistent registry of all chats the bot knows about."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base


class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    allow_self_send: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_source: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_destination: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    registered_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "idx_chats_active_dest",
            "active",
            "is_destination",
            postgresql_where=(active == True) & (is_destination == True),  # noqa: E712
        ),
    )

    def __repr__(self) -> str:
        return f"<Chat {self.chat_id} type={self.chat_type} active={self.active}>"
