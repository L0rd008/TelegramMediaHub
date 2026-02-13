"""SendLog repository – lookups for reply threading and moderation."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.send_log import SendLog


class SendLogRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def reverse_lookup(
        self, dest_chat_id: int, dest_message_id: int
    ) -> tuple[int, int] | None:
        """Given a bot-sent message, return (source_chat_id, source_message_id).

        Returns None when the message is not found (e.g. pruned after 48 h).
        """
        result = await self._s.execute(
            select(SendLog.source_chat_id, SendLog.source_message_id)
            .where(
                SendLog.dest_chat_id == dest_chat_id,
                SendLog.dest_message_id == dest_message_id,
            )
            .limit(1)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return (row.source_chat_id, row.source_message_id)

    async def get_dest_message_id(
        self,
        source_chat_id: int,
        source_message_id: int,
        dest_chat_id: int,
    ) -> int | None:
        """Find the bot's message in *dest_chat_id* for a given source message.

        Returns the dest_message_id, or None if not found.
        """
        result = await self._s.execute(
            select(SendLog.dest_message_id)
            .where(
                SendLog.source_chat_id == source_chat_id,
                SendLog.source_message_id == source_message_id,
                SendLog.dest_chat_id == dest_chat_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_source_user_id(
        self, dest_chat_id: int, dest_message_id: int
    ) -> int | None:
        """Given a bot-sent message, return the original sender's user_id.

        Used for reply-based admin targeting on redistributed messages.
        """
        result = await self._s.execute(
            select(SendLog.source_user_id)
            .where(
                SendLog.dest_chat_id == dest_chat_id,
                SendLog.dest_message_id == dest_message_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_dest_messages_by_user(
        self, user_id: int
    ) -> list[tuple[int, int]]:
        """Return all (dest_chat_id, dest_message_id) pairs for a given source user.

        Used for ban cleanup — delete all redistributed messages from a user.
        """
        result = await self._s.execute(
            select(SendLog.dest_chat_id, SendLog.dest_message_id)
            .where(SendLog.source_user_id == user_id)
        )
        return [(row.dest_chat_id, row.dest_message_id) for row in result.all()]

    # ── Stats queries ────────────────────────────────────────────────

    async def count_messages_from_chat(self, chat_id: int) -> int:
        """Count messages sent FROM this chat (within send_log retention)."""
        result = await self._s.execute(
            select(func.count())
            .select_from(SendLog)
            .where(SendLog.source_chat_id == chat_id)
        )
        return result.scalar_one()

    async def count_messages_to_chat(self, chat_id: int) -> int:
        """Count messages sent TO this chat (within send_log retention)."""
        result = await self._s.execute(
            select(func.count())
            .select_from(SendLog)
            .where(SendLog.dest_chat_id == chat_id)
        )
        return result.scalar_one()

    async def count_total_distributed(self) -> int:
        """Total rows in send_log (all messages distributed within retention)."""
        result = await self._s.execute(
            select(func.count()).select_from(SendLog)
        )
        return result.scalar_one()

    async def count_unique_senders(self) -> int:
        """Distinct source_user_id values in send_log (within retention)."""
        result = await self._s.execute(
            select(func.count(func.distinct(SendLog.source_user_id)))
        )
        return result.scalar_one()
