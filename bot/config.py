"""Bot configuration via pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration is read from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Bot ───────────────────────────────────────────────────────────
    BOT_TOKEN: str
    BOT_MODE: str = "polling"  # "polling" or "webhook"
    ADMIN_USER_IDS: str = ""  # comma-separated list of user IDs

    # ── Webhook ───────────────────────────────────────────────────────
    WEBHOOK_HOST: str = ""
    WEBHOOK_PORT: int = 8443
    WEBHOOK_PATH: str = "/webhook"
    WEBHOOK_SECRET: str = ""

    # ── Database ──────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://mediahub:password@localhost:5432/mediahub"

    # ── Redis ─────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Performance ───────────────────────────────────────────────────
    GLOBAL_RATE_LIMIT: int = 25
    WORKER_COUNT: int = 10

    # ── Logging ───────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    # ── Subscription ─────────────────────────────────────────────────
    TRIAL_DAYS: int = 30

    # ── Optional: Local Bot API Server ────────────────────────────────
    LOCAL_API_URL: str | None = None

    # ── Helpers ───────────────────────────────────────────────────────
    @property
    def admin_ids(self) -> list[int]:
        if not self.ADMIN_USER_IDS:
            return []
        return [int(uid.strip()) for uid in self.ADMIN_USER_IDS.split(",") if uid.strip()]

    @property
    def webhook_url(self) -> str:
        return f"https://{self.WEBHOOK_HOST}:{self.WEBHOOK_PORT}{self.WEBHOOK_PATH}"


settings = Settings()  # type: ignore[call-arg]
