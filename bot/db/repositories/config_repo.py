"""Config repository – key/value CRUD for bot_config table."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.bot_config import BotConfig


class ConfigRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_value(self, key: str) -> str | None:
        """Get a config value by key."""
        result = await self._s.execute(
            select(BotConfig.value).where(BotConfig.key == key)
        )
        return result.scalar_one_or_none()

    async def set_value(self, key: str, value: str) -> None:
        """Upsert a config value."""
        stmt = (
            pg_insert(BotConfig)
            .values(key=key, value=value)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value},
            )
        )
        await self._s.execute(stmt)
        await self._s.commit()

    async def get_bool(self, key: str, default: bool = False) -> bool:
        """Get a boolean config value."""
        val = await self.get_value(key)
        if val is None:
            return default
        return val.lower() in ("true", "1", "yes")

    async def get_all(self) -> dict[str, str]:
        """Get all config values as a dict."""
        result = await self._s.execute(select(BotConfig))
        return {row.key: row.value for row in result.scalars().all()}
