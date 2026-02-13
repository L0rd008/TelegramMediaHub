"""SendLog repository – lookups for reply threading."""

from __future__ import annotations

from sqlalchemy import select
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
