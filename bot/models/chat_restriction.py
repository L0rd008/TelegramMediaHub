"""ChatRestriction model — ban / mute records for entire chats.

Mirrors :class:`bot.models.user_restriction.UserRestriction` but targets a
``chat_id`` instead of a ``user_id``.  Used to silence a noisy or abusive
group at the source: when a chat has an active ban, every message arriving
from that chat is dropped before reaching the distribution pipeline.

Schema deliberately matches UserRestriction so the moderation UX (set/clear
restriction, list active, count by type, expiry semantics) can be lifted
verbatim across both target types.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base


class ChatRestriction(Base):
    __tablename__ = "chat_restrictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    restriction_type: Mapped[str] = mapped_column(String(10), nullable=False)  # "ban" today; "mute" reserved
    restricted_by: Mapped[int] = mapped_column(BigInteger, nullable=False)  # admin user id
    restricted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # NULL = permanent
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        Index("idx_chat_restriction_chat_type", "chat_id", "restriction_type", "active"),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatRestriction chat={self.chat_id} type={self.restriction_type} "
            f"active={self.active}>"
        )
