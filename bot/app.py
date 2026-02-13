"""Application factory – builds the Bot, Dispatcher, registers routers/middleware,
and starts either webhook or polling mode."""

from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)
from aiohttp import web

from bot.config import settings

logger = logging.getLogger(__name__)


def _create_bot() -> Bot:
    """Construct the Bot instance (optionally pointing to a local API server)."""
    session = None
    if settings.LOCAL_API_URL:
        from aiogram.client.telegram import TelegramAPIServer

        session = AiohttpSession(
            api=TelegramAPIServer.from_base(settings.LOCAL_API_URL)
        )
    return Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def _register_routers(dp: Dispatcher) -> None:
    """Import and include all routers."""
    from bot.handlers.membership import membership_router
    from bot.handlers.start import start_router
    from bot.handlers.admin import admin_router
    from bot.handlers.subscription import subscription_router
    from bot.handlers.edits import edits_router
    from bot.handlers.messages import messages_router

    dp.include_router(membership_router)
    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(subscription_router)
    dp.include_router(edits_router)
    dp.include_router(messages_router)  # must be last (catch-all)


def _register_middleware(dp: Dispatcher) -> None:
    """Register all middleware on the dispatcher."""
    from bot.middleware.logging_mw import LoggingMiddleware
    from bot.middleware.db_session_mw import DbSessionMiddleware
    from bot.middleware.dedup_mw import SelfMessageMiddleware

    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(DbSessionMiddleware())
    dp.message.outer_middleware(SelfMessageMiddleware())
    dp.channel_post.outer_middleware(SelfMessageMiddleware())


async def _on_startup(bot: Bot, redis: aioredis.Redis, dp: Dispatcher) -> None:
    """Run on startup – create tables if needed, start workers."""
    from bot.db.engine import engine
    from bot.db.base import Base

    # Import models so they register on metadata
    from bot.models.chat import Chat  # noqa: F401
    from bot.models.bot_config import BotConfig  # noqa: F401
    from bot.models.send_log import SendLog  # noqa: F401
    from bot.models.subscription import Subscription  # noqa: F401
    from bot.models.user_alias import UserAlias  # noqa: F401
    from bot.models.user_restriction import UserRestriction  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables ensured.")

    # Seed default config if empty
    from bot.db.engine import async_session
    from bot.db.repositories.config_repo import ConfigRepo

    async with async_session() as session:
        repo = ConfigRepo(session)
        if await repo.get_value("signature_enabled") is None:
            await repo.set_value("signature_enabled", "true")
            await repo.set_value("signature_text", "")
            await repo.set_value("signature_url", "")
            await repo.set_value("edit_redistribution", "off")
            await repo.set_value("paused", "false")
            await session.commit()

    # Start distribution workers
    from bot.services.distributor import get_distributor

    distributor = get_distributor(bot, redis)
    dp["distributor"] = distributor
    dp["redis"] = redis
    dp["bot_info"] = await bot.get_me()
    await distributor.start_workers()

    # Start media group flusher
    from bot.services.media_group import get_media_group_buffer

    mgb = get_media_group_buffer(redis, distributor)
    dp["media_group_buffer"] = mgb
    await mgb.start_flusher()

    # Start send_log cleaner (prune rows older than 48h)
    from bot.services.distributor import SendLogCleaner

    cleaner = SendLogCleaner()
    dp["send_log_cleaner"] = cleaner
    await cleaner.start()

    # Start trial-expiry reminder task
    from bot.services.subscription import TrialReminderTask

    reminder = TrialReminderTask(bot, redis)
    dp["trial_reminder"] = reminder
    await reminder.start()

    bot_info = dp["bot_info"]
    logger.info("Bot @%s (id=%d) started.", bot_info.username, bot_info.id)


async def _on_shutdown(dp: Dispatcher) -> None:
    """Graceful shutdown – drain workers, close pools."""
    logger.info("Shutting down…")
    distributor = dp.get("distributor")
    if distributor:
        await distributor.stop_workers()

    mgb = dp.get("media_group_buffer")
    if mgb:
        await mgb.stop_flusher()

    cleaner = dp.get("send_log_cleaner")
    if cleaner:
        await cleaner.stop()

    reminder = dp.get("trial_reminder")
    if reminder:
        await reminder.stop()

    redis: aioredis.Redis | None = dp.get("redis")
    if redis:
        await redis.close()

    from bot.db.engine import engine

    await engine.dispose()
    logger.info("Shutdown complete.")


async def main() -> None:
    """Entry point."""
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    bot = _create_bot()
    dp = Dispatcher()
    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    _register_middleware(dp)
    _register_routers(dp)

    async def on_startup(*_args: object, **_kwargs: object) -> None:
        await _on_startup(bot, redis, dp)

    async def on_shutdown(*_args: object, **_kwargs: object) -> None:
        await _on_shutdown(dp)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    if settings.BOT_MODE == "webhook":
        await _run_webhook(bot, dp)
    else:
        await _run_polling(bot, dp)


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    """Long-polling mode (development)."""
    logger.info("Starting in POLLING mode.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(
        bot,
        allowed_updates=[
            "message",
            "channel_post",
            "edited_message",
            "edited_channel_post",
            "my_chat_member",
            "callback_query",
            "pre_checkout_query",
        ],
    )


async def _health_handler(request: web.Request) -> web.Response:
    """Health check endpoint for monitoring / container probes."""
    dp: Dispatcher | None = request.app.get("dp")
    info: dict = {"status": "ok"}
    if dp:
        distributor = dp.get("distributor")
        if distributor:
            info["queue_size"] = distributor.queue_size
        redis_conn = dp.get("redis")
        if redis_conn:
            try:
                await redis_conn.ping()
                info["redis"] = "ok"
            except Exception:
                info["redis"] = "error"
    return web.json_response(info)


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    """Webhook mode (production)."""
    logger.info("Starting in WEBHOOK mode at %s", settings.webhook_url)
    await bot.set_webhook(
        url=settings.webhook_url,
        secret_token=settings.WEBHOOK_SECRET or None,
        allowed_updates=[
            "message",
            "channel_post",
            "edited_message",
            "edited_channel_post",
            "my_chat_member",
            "callback_query",
            "pre_checkout_query",
        ],
        drop_pending_updates=True,
        max_connections=40,
    )
    app = web.Application()

    # Health check endpoint (no auth required)
    app.router.add_get("/health", _health_handler)

    handler = SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=settings.WEBHOOK_SECRET or None)
    handler.register(app, path=settings.WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app["dp"] = dp  # Make dispatcher accessible to health handler
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.WEBHOOK_PORT)
    await site.start()
    logger.info("Webhook server listening on port %d", settings.WEBHOOK_PORT)
    # Keep running until interrupted
    await asyncio.Event().wait()
