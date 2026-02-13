"""Chat repository – CRUD operations for the chats table."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.chat import Chat


class ChatRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_chat(
        self,
        chat_id: int,
        chat_type: str,
        title: str | None = None,
        username: str | None = None,
    ) -> Chat:
        """Insert or reactivate a chat."""
        stmt = (
            pg_insert(Chat)
            .values(
                chat_id=chat_id,
                chat_type=chat_type,
                title=title,
                username=username,
                active=True,
            )
            .on_conflict_do_update(
                index_elements=["chat_id"],
                set_={
                    "chat_type": chat_type,
                    "title": title,
                    "username": username,
                    "active": True,
                },
            )
            .returning(Chat)
        )
        result = await self._s.execute(stmt)
        await self._s.commit()
        return result.scalar_one()

    async def deactivate_chat(self, chat_id: int) -> None:
        """Soft-delete: mark a chat inactive."""
        await self._s.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(active=False)
        )
        await self._s.commit()

    async def get_active_destinations(self) -> list[Chat]:
        """Return all active chats that are destinations."""
        result = await self._s.execute(
            select(Chat).where(Chat.active == True, Chat.is_destination == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def get_active_sources(self) -> list[Chat]:
        """Return all active chats that are sources."""
        result = await self._s.execute(
            select(Chat).where(Chat.active == True, Chat.is_source == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def is_active_source(self, chat_id: int) -> bool:
        """Check if a chat is an active source."""
        result = await self._s.execute(
            select(Chat.chat_id).where(
                Chat.chat_id == chat_id,
                Chat.active == True,  # noqa: E712
                Chat.is_source == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none() is not None

    async def update_chat_id(self, old_id: int, new_id: int) -> None:
        """Handle group→supergroup migration."""
        # Check if new_id already exists
        existing = await self._s.execute(
            select(Chat).where(Chat.chat_id == new_id)
        )
        if existing.scalar_one_or_none():
            # Deactivate old, keep new
            await self.deactivate_chat(old_id)
        else:
            await self._s.execute(
                update(Chat).where(Chat.chat_id == old_id).values(chat_id=new_id)
            )
            await self._s.commit()

    async def list_all_active(self, offset: int = 0, limit: int = 20) -> list[Chat]:
        """Paginated list of active chats."""
        result = await self._s.execute(
            select(Chat)
            .where(Chat.active == True)  # noqa: E712
            .order_by(Chat.registered_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_active(self) -> int:
        """Count active chats."""
        result = await self._s.execute(
            select(func.count()).select_from(Chat).where(Chat.active == True)  # noqa: E712
        )
        return result.scalar_one()

    async def count_by_type(self) -> dict[str, int]:
        """Count active chats grouped by chat_type."""
        result = await self._s.execute(
            select(Chat.chat_type, func.count())
            .where(Chat.active == True)  # noqa: E712
            .group_by(Chat.chat_type)
        )
        return {row[0]: row[1] for row in result.all()}

    async def count_sources(self) -> int:
        """Count active chats with is_source=True."""
        result = await self._s.execute(
            select(func.count())
            .select_from(Chat)
            .where(Chat.active == True, Chat.is_source == True)  # noqa: E712
        )
        return result.scalar_one()

    async def count_destinations(self) -> int:
        """Count active chats with is_destination=True."""
        result = await self._s.execute(
            select(func.count())
            .select_from(Chat)
            .where(Chat.active == True, Chat.is_destination == True)  # noqa: E712
        )
        return result.scalar_one()

    async def toggle_self_send(self, chat_id: int, enabled: bool) -> None:
        """Toggle allow_self_send for a chat."""
        await self._s.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(allow_self_send=enabled)
        )
        await self._s.commit()

    async def get_chat(self, chat_id: int) -> Chat | None:
        """Get a single chat by ID."""
        result = await self._s.execute(
            select(Chat).where(Chat.chat_id == chat_id)
        )
        return result.scalar_one_or_none()

    async def toggle_source(self, chat_id: int, enabled: bool) -> None:
        """Toggle is_source (outgoing broadcasting) for a chat."""
        await self._s.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(is_source=enabled)
        )
        await self._s.commit()

    async def toggle_destination(self, chat_id: int, enabled: bool) -> None:
        """Toggle is_destination (incoming broadcasting) for a chat."""
        await self._s.execute(
            update(Chat).where(Chat.chat_id == chat_id).values(is_destination=enabled)
        )
        await self._s.commit()
