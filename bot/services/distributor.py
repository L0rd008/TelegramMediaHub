"""Distribution engine – fan-out messages to all destinations via async worker pool."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.enums import ParseMode
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

# M-4: Redis key and TTL for the cached signature config
_SIGNATURE_CACHE_KEY = "config:signature_cache"
_SIGNATURE_CACHE_TTL = 30  # seconds — short so admin changes propagate quickly


@dataclass
class SendTask:
    """A unit of work for the worker pool."""

    message: NormalizedMessage
    dest_chat_id: int
    dest_chat_type: str = "private"
    retry_count: int = 0
    reply_to_message_id: int | None = None


class Distributor:
    """Fan-out engine with async worker pool.

    M-3 / L-3 note: The singleton is initialised eagerly in app.py's _on_startup()
    before any handlers are registered. The ordering guarantee is enforced by the
    startup lifecycle — do not call get_distributor() before startup completes.
    """

    def __init__(self, bot: Bot, redis: aioredis.Redis) -> None:
        self._bot = bot
        self._redis = redis
        self._queue: asyncio.Queue[SendTask] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        # H-5: track fire-and-forget tasks to prevent premature GC
        self._background_tasks: set[asyncio.Task] = set()
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
        # Cancel any remaining background tasks
        for t in list(self._background_tasks):
            t.cancel()
        self._background_tasks.clear()
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
                        self._fire_background(self._send_paywall_nudge(dest.chat_id))
                    continue  # Skip this destination

            # ── Reply threading: resolve per-destination ─────────
            reply_to: int | None = None
            if msg.reply_source_chat_id and msg.reply_source_message_id:
                try:
                    async with async_session() as session:
                        sl_repo = SendLogRepo(session)
                        reply_to = await sl_repo.get_dest_message_id(
                            msg.reply_source_chat_id,
                            msg.reply_source_message_id,
                            dest.chat_id,
                        )
                except Exception as e:
                    logger.debug(
                        "Reply dest lookup failed for (%d, %d) -> %d: %s",
                        msg.reply_source_chat_id,
                        msg.reply_source_message_id,
                        dest.chat_id,
                        e,
                    )

            await self._queue.put(
                SendTask(
                    message=msg,
                    dest_chat_id=dest.chat_id,
                    dest_chat_type=dest.chat_type,
                    reply_to_message_id=reply_to,
                )
            )

    def _fire_background(self, coro) -> None:
        """H-5: Schedule a background coroutine and track it to prevent GC.

        The task is automatically removed from the tracking set on completion,
        and any unhandled exceptions are logged at DEBUG level.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def _done_cb(t: asyncio.Task) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.debug("Background task error: %s", t.exception())

        task.add_done_callback(_done_cb)

    async def _send_paywall_nudge(self, chat_id: int) -> None:
        """Send the once-per-day "you missed messages today" nudge.

        Copy lives in :mod:`bot.services.value_prop` (the ``daily_nudge``
        builder) so the wording stays in sync with the trial reminders and
        the onboarding text.  Cadence is enforced by the caller via
        :func:`bot.services.subscription.should_nudge` (24 h Redis cooldown).
        """
        from bot.services.subscription import build_subscribe_button, get_missed_today
        from bot.services.value_prop import daily_nudge

        try:
            missed = await get_missed_today(self._redis, chat_id)
            text = daily_nudge(missed)
            await self._bot.send_message(
                chat_id,
                text,
                reply_markup=build_subscribe_button(),
                parse_mode=ParseMode.HTML,
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

            # H-1: Read allow_paid_broadcast from config (cached in Redis via _get_signature)
            allow_paid = await self._get_allow_paid_broadcast()

            # Build signature (M-4: Redis-cached to avoid per-message DB reads)
            signature = await self._get_signature()

            # Look up sender alias (user pseudonym) OR derive chat attribution label
            sender_alias: str | None = None
            sender_alias_url: str | None = None  # Premium real-name override
            source_chat_label: str | None = None
            source_chat_url: str | None = None

            # 2026-04-26: Premium real-name attribution.  Resolve once per
            # task so we can branch on it cheaply below.  ``real_links_active``
            # is True only when:
            #   - the source chat has ``real_links_enabled`` flipped on, AND
            #   - the source chat is currently Premium (paid or in trial).
            # Both conditions are required so a chat dropping out of Premium
            # silently reverts to alias-to-bot links — no policy surprise.
            real_links_active = await self._is_real_links_active(msg.source_chat_id)

            if msg.source_user_id:
                # Regular user message → resolve pseudonym alias
                from bot.services.alias import get_alias
                try:
                    sender_alias = await get_alias(self._redis, msg.source_user_id)
                except Exception as e:
                    logger.debug("Failed to resolve alias for user %d: %s", msg.source_user_id, e)

                # When real-links is active and the sender has a public
                # username, point the alias entity at their actual profile.
                # If they have NO public username we leave the URL unset and
                # send_single falls back to the bot link — that's the edge
                # case the spec called out (private accounts stay private).
                if real_links_active and msg.source_user_username:
                    sender_alias_url = f"https://t.me/{msg.source_user_username}"

                # 2026-04-26: when the user posted from a group/supergroup,
                # the visible attribution is ``user_alias @ chat_alias`` so
                # recipients see *both* who said it and where.  Channels and
                # private chats are handled below (different code paths).
                if sender_alias and msg.source_chat_type in ("group", "supergroup"):
                    try:
                        from bot.services.chat_alias import (
                            format_group_attribution,
                            get_chat_alias,
                        )
                        chat_alias = await get_chat_alias(
                            self._redis, msg.source_chat_id
                        )
                        sender_alias = format_group_attribution(
                            sender_alias, chat_alias
                        )
                    except Exception as e:
                        logger.debug(
                            "Failed to resolve chat alias for chat %d: %s",
                            msg.source_chat_id, e,
                        )
            else:
                # Channel post or anonymous admin → show source chat identity.
                # source_chat_username / source_chat_title are populated by
                # the normalizer when from_user is None or is GroupAnonymousBot.
                if msg.source_chat_username:
                    source_chat_label = f"@{msg.source_chat_username}"
                    # When real-links is active, link straight to the public
                    # group/channel.  When it's NOT active, we still keep the
                    # URL — the channel handle is already public information,
                    # so suppressing it would be theatre, not privacy.
                    source_chat_url = f"https://t.me/{msg.source_chat_username}"
                elif msg.source_chat_title:
                    source_chat_label = msg.source_chat_title
                    source_chat_url = None

                # Channels and anon-admin groups also get a stable chat alias
                # appended.  Helps recipients tell two channels with similar
                # titles apart, and gives anon admins a persistent identity
                # without revealing who they are.
                try:
                    from bot.services.chat_alias import get_chat_alias
                    chat_alias = await get_chat_alias(
                        self._redis, msg.source_chat_id
                    )
                    if source_chat_label:
                        source_chat_label = f"{source_chat_label} ({chat_alias})"
                    else:
                        source_chat_label = chat_alias
                except Exception as e:
                    logger.debug(
                        "Failed to resolve chat alias for chat %d: %s",
                        msg.source_chat_id, e,
                    )

            # Send — pass redis for C-1 (bot username cache) and H-1 (paid broadcast)
            result = await send_single(
                self._bot, msg, task.dest_chat_id, signature,
                reply_to_message_id=task.reply_to_message_id,
                sender_alias=sender_alias,
                sender_alias_url=sender_alias_url,
                redis=self._redis,
                allow_paid_broadcast=allow_paid,
                source_chat_label=source_chat_label,
                source_chat_url=source_chat_url,
            )

            # Log success
            self._rate_limiter.report_success(task.dest_chat_id)

            # B-1 fix: send_media_group now returns
            # list[tuple[Message, NormalizedMessage]] so each sent message is
            # paired with the specific source item it represents — correct
            # even when an album mixes types and the compatibility-bucket
            # split reorders sends relative to the original source order.
            # The previous shape (zip(result, msg.group_items)) silently
            # mismapped when buckets reordered (e.g. [photo, doc, photo]).
            if isinstance(result, list):
                for sent_msg, src_item in result:
                    if sent_msg and sent_msg.message_id:
                        await self._log_send_item(
                            src_chat_id=src_item.source_chat_id,
                            src_msg_id=src_item.source_message_id,
                            src_user_id=src_item.source_user_id,
                            dest_chat_id=task.dest_chat_id,
                            dest_message_id=sent_msg.message_id,
                        )
            elif result and result.message_id:
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
            error_msg = str(e).lower()
            # Permanent failures: chat gone, private, or bot kicked → deactivate
            _permanent = (
                "chat not found" in error_msg
                or "channel_private" in error_msg
                or "bot was kicked" in error_msg
                or "have no rights to send" in error_msg
                or "chat_write_forbidden" in error_msg
            )
            if _permanent:
                logger.warning(
                    "Permanent failure for chat %d – deactivating: %s",
                    task.dest_chat_id,
                    error_msg,
                )
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

    async def _get_allow_paid_broadcast(self) -> bool:
        """H-1: Read allow_paid_broadcast flag from config DB (30-second Redis cache).

        Defaults to False — the operator must explicitly enable paid broadcasts.
        Set config key 'allow_paid_broadcast' to 'true' in bot_config to enable.
        """
        cache_key = "config:allow_paid_broadcast"
        cached = await self._redis.get(cache_key)
        if cached is not None:
            val = cached if isinstance(cached, str) else cached.decode()
            return val == "true"

        async with async_session() as session:
            repo = ConfigRepo(session)
            raw = await repo.get_value("allow_paid_broadcast")

        result = (raw or "false").lower() == "true"
        await self._redis.set(cache_key, "true" if result else "false", ex=30)
        return result

    async def _get_signature(self) -> str | None:
        """M-4: Build the signature from config, cached in Redis for 30 seconds.

        This avoids one DB round-trip per message per worker at high throughput
        (previously ~250 reads/s at 10 workers × 25 msg/s).
        """
        # Check Redis cache first
        cached = await self._redis.get(_SIGNATURE_CACHE_KEY)
        if cached is not None:
            val = cached if isinstance(cached, str) else cached.decode()
            return val if val else None  # empty string → None (signature disabled)

        # Cache miss — read from DB
        async with async_session() as session:
            repo = ConfigRepo(session)
            enabled = await repo.get_bool("signature_enabled", default=True)
            if not enabled:
                await self._redis.set(_SIGNATURE_CACHE_KEY, "", ex=_SIGNATURE_CACHE_TTL)
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

        # Cache the result (empty string for "no signature")
        await self._redis.set(_SIGNATURE_CACHE_KEY, sig or "", ex=_SIGNATURE_CACHE_TTL)
        return sig

    async def invalidate_signature_cache(self) -> None:
        """Call after admin changes signature config so the new value takes effect immediately."""
        await self._redis.delete(_SIGNATURE_CACHE_KEY)

    # ── Premium real-name attribution helper (alembic 010) ──────────────

    async def _is_real_links_active(self, source_chat_id: int) -> bool:
        """True iff the source chat has Premium real-link attribution enabled.

        Two conditions must both hold:

        - ``Chat.real_links_enabled`` — the source chat opted in via
          ``/identity`` (chat-admin only in groups, premium-gated at the
          command level).
        - The chat is currently Premium — paid subscription or in trial.

        We re-check the Premium status here (not just trust ``real_links_enabled``)
        so a chat that toggled the flag during trial reverts to alias-to-bot
        the moment trial expires, without requiring a manual flip-off.
        Cheap because both lookups hit Redis caches in the common path.
        """
        from bot.services.subscription import is_premium

        try:
            async with async_session() as session:
                chat_repo = ChatRepo(session)
                chat = await chat_repo.get_chat(source_chat_id)
            if chat is None or not chat.real_links_enabled:
                return False
            return await is_premium(self._redis, source_chat_id, chat.registered_at)
        except Exception as e:
            # Never let an attribution-resolution failure break message relay.
            # Falling back to alias-to-bot is the safe default.
            logger.debug(
                "real_links_active check failed for chat %d: %s",
                source_chat_id, e,
            )
            return False

    async def _log_send(
        self, msg: NormalizedMessage, dest_chat_id: int, dest_message_id: int
    ) -> None:
        """Log a single-message send to send_log."""
        await self._log_send_item(
            src_chat_id=msg.source_chat_id,
            src_msg_id=msg.source_message_id,
            src_user_id=msg.source_user_id,
            dest_chat_id=dest_chat_id,
            dest_message_id=dest_message_id,
        )

    async def _log_send_item(
        self,
        src_chat_id: int,
        src_msg_id: int,
        src_user_id: int | None,
        dest_chat_id: int,
        dest_message_id: int,
    ) -> None:
        """Insert one row into send_log."""
        try:
            from bot.models.send_log import SendLog

            async with async_session() as session:
                log = SendLog(
                    source_chat_id=src_chat_id,
                    source_message_id=src_msg_id,
                    source_user_id=src_user_id,
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
    """Periodic background task to prune send_log rows older than 48 hours.

    L-3: The cleaner runs _cleanup() immediately on first tick (no initial sleep),
    so it performs one DB write shortly after startup. This is intentional — it
    ensures stale rows from a previous run are pruned without waiting an hour.
    """

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
        from sqlalchemy import delete

        from bot.models.send_log import SendLog

        # send_log.sent_at is stored as a naive UTC timestamp in the current schema,
        # so the cutoff must also be naive to avoid asyncpg "offset-naive and
        # offset-aware datetimes" failures during cleanup.
        cutoff = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) - timedelta(hours=SEND_LOG_MAX_AGE_HOURS)
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


# ── Test helpers ──────────────────────────────────────────────────────

def _reset_distributor_for_testing() -> None:
    """L-1: Reset the distributor singleton. For use in tests only."""
    global _distributor
    _distributor = None
