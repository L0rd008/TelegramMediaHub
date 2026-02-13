"""Distribution engine – fan-out messages to all destinations via async worker pool."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

# aiogram renamed this exception in different 3.x releases.
try:
    from aiogram.exceptions import TelegramMigrateToChat  # type: ignore
except ImportError:  # pragma: no cover - fallback for older/newer aiogram
    from aiogram.exceptions import TelegramMigrateThisChat as TelegramMigrateToChat  # type: ignore

from bot.config import settings
from bot.db.engine import async_session
from bot.db.repositories.chat_repo import ChatRepo
from bot.db.repositories.config_repo import ConfigRepo
from bot.db.repositories.send_log_repo import SendLogRepo
from bot.services.normalizer import NormalizedMessage
from bot.services.rate_limiter import RateLimiter
from bot.services.sender import send_single
from bot.services.signature import apply_signature

logger = logging.getLogger(__name__)


@dataclass
class SendTask:
    """A unit of work for the worker pool."""

    message: NormalizedMessage
    dest_chat_id: int
    dest_chat_type: str = "private"
    retry_count: int = 0
    reply_to_message_id: int | None = None


class Distributor:
    """Fan-out engine with async worker pool."""

    def __init__(self, bot: Bot, redis: aioredis.Redis) -> None:
        self._bot = bot
        self._redis = redis
        self._queue: asyncio.Queue[SendTask] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._rate_limiter = RateLimiter(redis, settings.GLOBAL_RATE_LIMIT)
        self._running = False

    async def start_workers(self) -> None:
        """Start the worker pool."""
        self._running = True
        for i in range(settings.WORKER_COUNT):
            task = asyncio.create_task(self._worker(i), name=f"worker-{i}")
            self._workers.append(task)
        logger.info("Started %d distribution workers.", settings.WORKER_COUNT)

    async def stop_workers(self) -> None:
        """Drain queue and stop workers."""
        self._running = False
        # Push poison pills to unblock workers
        for _ in self._workers:
            await self._queue.put(None)  # type: ignore[arg-type]
        # Wait for workers to finish
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("All workers stopped.")

    async def distribute(self, msg: NormalizedMessage) -> None:
        """Fan out a message to all active destinations."""
        from bot.services.subscription import (
            build_subscribe_button,
            get_missed_today,
            is_premium,
            record_missed,
            should_nudge,
        )

        async with async_session() as session:
            # Check if paused
            config_repo = ConfigRepo(session)
            if await config_repo.get_bool("paused"):
                logger.debug("Distribution paused, skipping message %d", msg.source_message_id)
                return

            chat_repo = ChatRepo(session)
            destinations = await chat_repo.get_active_destinations()

        for dest in destinations:
            # Skip self-send if not allowed
            if dest.chat_id == msg.source_chat_id and not dest.allow_self_send:
                continue

            # ── Paywall check (cross-chat only) ──────────────────
            if dest.chat_id != msg.source_chat_id:
                if not await is_premium(self._redis, dest.chat_id, dest.registered_at):
                    await record_missed(self._redis, dest.chat_id)

                    # Send a nudge at most once per day
                    if await should_nudge(self._redis, dest.chat_id):
                        asyncio.create_task(
                            self._send_paywall_nudge(dest.chat_id)
                        )
                    continue  # Skip this destination

            # ── Reply threading: resolve per-destination ─────────
            reply_to: int | None = None
            if msg.reply_source_chat_id and msg.reply_source_message_id:
                async with async_session() as session:
                    sl_repo = SendLogRepo(session)
                    reply_to = await sl_repo.get_dest_message_id(
                        msg.reply_source_chat_id,
                        msg.reply_source_message_id,
                        dest.chat_id,
                    )

            await self._queue.put(
                SendTask(
                    message=msg,
                    dest_chat_id=dest.chat_id,
                    dest_chat_type=dest.chat_type,
                    reply_to_message_id=reply_to,
                )
            )

    async def _send_paywall_nudge(self, chat_id: int) -> None:
        """Fire-and-forget: send a tasteful upgrade nudge to a chat."""
        from bot.services.subscription import build_subscribe_button, get_missed_today

        try:
            missed = await get_missed_today(self._redis, chat_id)
            missed_text = f"<b>{missed} message{'s' if missed != 1 else ''}</b>"
            text = (
                f"🔒 You missed {missed_text} from your network today.\n\n"
                "Premium includes cross-chat content, reply threading, "
                "broadcast control, and sender aliases — from just <b>25 ⭐/day</b>."
            )
            await self._bot.send_message(
                chat_id,
                text,
                reply_markup=build_subscribe_button(),
            )
        except Exception as e:
            logger.debug("Failed to send paywall nudge to %d: %s", chat_id, e)

    async def _worker(self, worker_id: int) -> None:
        """Consume send tasks from the queue."""
        logger.debug("Worker %d started.", worker_id)
        while self._running:
            task = await self._queue.get()
            if task is None:  # Poison pill
                break
            try:
                await self._process_task(task)
            except Exception as e:
                logger.error("Worker %d unhandled error: %s", worker_id, e)
            finally:
                self._queue.task_done()
        logger.debug("Worker %d stopped.", worker_id)

    async def _process_task(self, task: SendTask) -> None:
        """Process a single send task with rate limiting and error handling."""
        msg = task.message

        try:
            # Acquire rate limit slot
            await self._rate_limiter.acquire(task.dest_chat_id, task.dest_chat_type)

            # Build signature
            signature = await self._get_signature()

            # Look up sender alias
            sender_alias: str | None = None
            if msg.source_user_id:
                from bot.services.alias import get_alias
                try:
                    sender_alias = await get_alias(self._redis, msg.source_user_id)
                except Exception as e:
                    logger.debug("Failed to resolve alias for user %d: %s", msg.source_user_id, e)

            # Send
            result = await send_single(
                self._bot, msg, task.dest_chat_id, signature,
                reply_to_message_id=task.reply_to_message_id,
                sender_alias=sender_alias,
            )

            # Log success
            self._rate_limiter.report_success(task.dest_chat_id)

            # Optionally log to send_log for edit tracking
            if result and result.message_id:
                await self._log_send(msg, task.dest_chat_id, result.message_id)

        except TelegramRetryAfter as e:
            # 429 – rate limited by Telegram
            logger.warning(
                "429 for chat %d, retry_after=%ds",
                task.dest_chat_id,
                e.retry_after,
            )
            self._rate_limiter.report_429(e.retry_after)
            await asyncio.sleep(e.retry_after + 1)
            # Re-enqueue
            if task.retry_count < 3:
                task.retry_count += 1
                await self._queue.put(task)

        except TelegramForbiddenError:
            # 403 – bot was blocked or removed
            logger.warning("403 for chat %d – deactivating", task.dest_chat_id)
            self._rate_limiter.report_error(task.dest_chat_id)
            async with async_session() as session:
                repo = ChatRepo(session)
                await repo.deactivate_chat(task.dest_chat_id)

        except TelegramMigrateToChat as e:
            # Group migrated to supergroup – update DB and re-enqueue
            new_chat_id = e.migrate_to_chat_id
            logger.warning(
                "Chat %d migrated to %d – updating registry and re-enqueueing",
                task.dest_chat_id,
                new_chat_id,
            )
            async with async_session() as session:
                repo = ChatRepo(session)
                await repo.update_chat_id(task.dest_chat_id, new_chat_id)
            # Re-enqueue with the new chat_id
            if task.retry_count < 3:
                task.dest_chat_id = new_chat_id
                task.dest_chat_type = "supergroup"
                task.retry_count += 1
                await self._queue.put(task)

        except TelegramBadRequest as e:
            error_msg = str(e)
            if "chat not found" in error_msg.lower():
                logger.warning("Chat %d not found – deactivating", task.dest_chat_id)
                async with async_session() as session:
                    repo = ChatRepo(session)
                    await repo.deactivate_chat(task.dest_chat_id)
            else:
                logger.error(
                    "Bad request sending to %d: %s",
                    task.dest_chat_id,
                    error_msg,
                )
                self._rate_limiter.report_error(task.dest_chat_id)

        except Exception as e:
            logger.error(
                "Unexpected error sending to %d: %s",
                task.dest_chat_id,
                e,
            )
            self._rate_limiter.report_error(task.dest_chat_id)

    async def _get_signature(self) -> str | None:
        """Build the signature from config."""
        async with async_session() as session:
            repo = ConfigRepo(session)
            enabled = await repo.get_bool("signature_enabled", default=True)
            if not enabled:
                return None

            text = await repo.get_value("signature_text")
            url = await repo.get_value("signature_url")

            if text:
                sig = text
            elif url:
                sig = url
            else:
                # Default: bot promotion signature
                bot_info = await self._bot.get_me()
                sig = f"— via @{bot_info.username}" if bot_info.username else None

            return sig

    async def _log_send(
        self, msg: NormalizedMessage, dest_chat_id: int, dest_message_id: int
    ) -> None:
        """Log the send to send_log table."""
        try:
            from bot.models.send_log import SendLog

            async with async_session() as session:
                log = SendLog(
                    source_chat_id=msg.source_chat_id,
                    source_message_id=msg.source_message_id,
                    source_user_id=msg.source_user_id,
                    dest_chat_id=dest_chat_id,
                    dest_message_id=dest_message_id,
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.debug("Failed to log send: %s", e)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()


# ── Send log cleanup ──────────────────────────────────────────────────

SEND_LOG_MAX_AGE_HOURS = 48
SEND_LOG_CLEANUP_INTERVAL = 3600  # Run every hour


class SendLogCleaner:
    """Periodic background task to prune send_log rows older than 48 hours."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="send-log-cleaner")
        logger.info("Send log cleaner started (prune >%dh).", SEND_LOG_MAX_AGE_HOURS)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Send log cleaner stopped.")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._cleanup()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Send log cleanup error: %s", e)
            await asyncio.sleep(SEND_LOG_CLEANUP_INTERVAL)

    async def _cleanup(self) -> None:
        from datetime import datetime, timedelta

        from sqlalchemy import delete

        from bot.models.send_log import SendLog

        # Use naive UTC timestamp because send_log.sent_at is stored without tzinfo.
        cutoff = datetime.utcnow() - timedelta(hours=SEND_LOG_MAX_AGE_HOURS)
        async with async_session() as session:
            result = await session.execute(
                delete(SendLog).where(SendLog.sent_at < cutoff)
            )
            await session.commit()
            deleted = result.rowcount  # type: ignore[union-attr]
            if deleted:
                logger.info("Pruned %d send_log rows older than %dh.", deleted, SEND_LOG_MAX_AGE_HOURS)


# ── Singleton accessor ────────────────────────────────────────────────

_distributor: Distributor | None = None


def get_distributor(bot: Bot | None = None, redis: aioredis.Redis | None = None) -> Distributor:
    global _distributor
    if _distributor is None:
        if bot is None or redis is None:
            raise RuntimeError("Distributor not initialized")
        _distributor = Distributor(bot, redis)
    return _distributor
