"""SendLog model – tracks source→dest message mapping for edit support."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base


class SendLog(Base):
    __tablename__ = "send_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dest_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dest_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_send_log_source", "source_chat_id", "source_message_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<SendLog src=({self.source_chat_id},{self.source_message_id}) "
            f"dest=({self.dest_chat_id},{self.dest_message_id})>"
        )
