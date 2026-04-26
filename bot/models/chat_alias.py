"""ChatAlias model — persistent random pseudonyms for source-chat identification.

Every group, supergroup, and channel that the bot relays from gets its own
two-word alias (e.g. ``misty_grove``).  When a message is relayed into the
network, the source-chat alias is shown alongside the user's alias so
recipients can tell which group the content originated in.

Aliases live in a separate table from :class:`bot.models.user_alias.UserAlias`
even though both draw from the same word lists.  Reasons:

- Different ID space (chat IDs are negative for groups, can collide with user
  IDs in unrelated bots if we ever multi-tenant).
- Lets us evolve the chat-alias generator independently (e.g. add a leading
  ``g-`` prefix later without touching user aliases).
- Makes "is this name a chat or a user?" lookups O(1) per side.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base


class ChatAlias(Base):
    __tablename__ = "chat_aliases"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    alias: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<ChatAlias chat={self.chat_id} alias={self.alias}>"
