"""BotConfig model – key/value store for runtime configuration."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.db.base import Base


class BotConfig(Base):
    __tablename__ = "bot_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"<BotConfig {self.key}={self.value!r}>"
