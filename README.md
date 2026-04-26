# TelegramMediaHub

A Python Telegram bot built on **aiogram v3** that receives content from registered chats and redistributes it as **original (non-forwarded) messages** to all other registered destinations — with reply threading, broadcast control, deduplication, rate-limit safety, privacy guarantees, a monetisation layer via **Telegram Stars**, and scalability to 100 000 chats.

---

## ✨ Features

### Content Redistribution
- **All media types** — photo, video, animation/GIF, audio, document, voice, video note, sticker; plus text in private chats and bot-rooted reply threads
- **Per-chat-type relay rules** (2026-04-25):
  - *Private chat with the bot* → relay everything (text + media), both directions
  - *Group / supergroup* → relay all media, but text only when it's part of a reply chain rooted in a bot-relayed message (avoids echoing casual member chatter)
  - *Channel* → media only, no text
- **Album support** — media groups are buffered in Redis and redistributed as intact albums (photo + video); animations are always sent individually since `sendMediaGroup` does not accept `InputMediaAnimation`
- **Privacy first** — never uses `forwardMessage` or `copyMessage`; always re-sends via `send*` with `file_id` reuse, so no forwarding metadata appears
- **Edit redistribution** — optionally re-send edited messages (configurable: `off` or `resend`); the same group text gate applies, so edits can't be used to bypass the rule

### Reply Threading
- **Cross-chat replies** — when a user replies to a bot-sent message in any chat, the reply is distributed to all other chats **as a Telegram Reply** to the corresponding message in each destination
- **Full album threading** — every frame in a redistributed album is individually logged to `send_log`, so replying to any photo or video in the album (not just the first) resolves correctly
- **Reverse lookup** — uses the `send_log` table to map `(dest_chat_id, dest_message_id)` back to the original source, then resolves the bot's message in each destination
- **Graceful degradation** — uses `allow_sending_without_reply=True` so replies still send even if the target message was deleted or pruned from the 48-hour send_log window

### Broadcast Control
- **Per-chat control** — `/broadcast off out` pauses outgoing content; `/broadcast off in` pauses incoming content
- **Resume anytime** — `/broadcast on out` and `/broadcast on in` to resume
- **Premium-gated** — available during the free trial and for premium subscribers; paywalled after trial expiry
- **Admin-only in groups** — in a group/supergroup, `/broadcast` and `/selfsend` require chat admin or creator status (`getChatMember` lookup); private chats are unrestricted
- **Defaults on registration** — every newly added chat starts at `broadcast=on, selfsend=off`, regardless of who added the bot

### Moderation & Aliases
- **User aliases** — every user gets a persistent two-word pseudonym (e.g. `golden_arrow`) that appears as a clickable link to the bot on every redistributed message
- **Chat aliases (2026-04-26)** — every group / supergroup / channel that the bot relays from also gets its own two-word alias (e.g. `misty_grove`). When a message originates in a group, the visible attribution is `user_alias @ chat_alias` so recipients see *who said it, in which group*. For channel posts and anonymous group admins the chat alias appears parenthesised next to the channel handle/title.
- **Alias on every message** — text messages, photos, videos, animations, audio, documents, and voice messages all carry the sender's alias; stickers and video notes are excluded (no caption support)
- **Correct entity offsets** — alias link entities use UTF-16 code unit offsets as required by the Bot API, so emoji and other astral-plane characters before the alias do not shift the link
- **Mute (admin)** — `/mute <user_id|reply> <duration>` silences a user for a specified time (30m, 2h, 7d, etc.)
- **Ban (admin)** — `/ban <user_id|reply>` permanently blocks a user with the choice to delete all their past messages or keep them
- **Banchat (admin)** — `/banchat <chat_id|reply>` blocks every message originating from a specific group / channel at the very top of `_handle_content`. Cached in Redis (`chat_restrict:{chat_id}`, 5 min TTL) so the lookup is one Redis hit per message
- **Unbanchat (admin)** — `/unbanchat <chat_id|reply>` lifts the chat ban
- **Reply-based targeting** — admin commands that target a **user** (mute, ban, unmute, unban, whois) resolve `from_user.id`; commands that target a **chat** (remove, grant, revoke, banchat, unbanchat) correctly resolve `sender_chat.id` for channels/groups or fall back to `from_user.id` for private chats
- **Alias lookup** — `/whois <name>` reveals the user behind a pseudonym; `/chatwhois <name>` reveals the chat. If you accidentally pass a chat alias to `/whois` it tells you to use `/chatwhois` instead (and vice-versa). Spaces and underscores are interchangeable

### Deduplication
Three independent guards, scoped per source chat so cross-chat content never collides:

- **Update-level guard** — `dup:upd:{chat_id}:{message_id}` with a 60 s TTL. Catches Telegram webhook redeliveries cheaply and without false positives.
- **Content-level guard (singles)** — `dup:c:{chat_id}:media:{file_unique_id}` or `dup:c:{chat_id}:text:{sha256}` with a 24 h TTL. Stops repost spam *within the same source chat* while letting two distinct chats freely share identical phrases or memes. (The pre-2026-04-25 implementation deduped globally and dropped roughly 95% of legitimate text traffic.)
- **Album-level guard** — `dup:alb:{chat_id}:{sha256(sorted_file_unique_ids)}` evaluated at flush time, not per item. Re-uploading an album with a fresh `media_group_id` but the same files is detected; mixed/partial re-uploads are not falsely collapsed.
- Self-message middleware drops the bot's own messages to prevent redistribution loops.

### Rate Limiting & Resilience
- **Global token bucket** — 25 messages/second via atomic Redis Lua script (TOCTOU-safe across multiple workers)
- **Per-chat cooldown** — 1 s for private/channels, 3 s for groups/supergroups
- **429 backoff** — automatic `retry_after` sleep and re-enqueue (up to 3 retries)
- **Circuit breaker** — per-chat pause after 3 consecutive errors (5 min), global pause after 5× 429 in 60 s (30 s)
- **Auto-deactivation** — 403 Forbidden or "chat not found" → soft-deletes the chat
- **Migration handling** — `TelegramMigrateThisChat` → updates registry and re-enqueues

### Monetisation (Telegram Stars)
- **Free trial** — configurable via `TRIAL_DAYS` (default: 30 days)
- **Three plans** — 1 Week (250 ⭐), 1 Month (750 ⭐, "Best Value"), 1 Year (10 000 ⭐)
- **Paywall** — cross-chat messages are gated after trial; self-to-self remains free
- **Nudge system** — daily "You missed X messages" prompt with subscribe button
- **Trial reminders** — background task sends 7-day, 3-day, 1-day warnings
- **Subscription stacking** — buying a second plan extends from the current expiry date
- **Cached premium checks** — Redis-backed with 5-min TTL to avoid DB round-trips
- **Paid broadcast** — `allow_paid_broadcast` flag (Bot API 9.6) toggleable via `config:allow_paid_broadcast` Redis key, defaults to `false`

### Interactive Buttons
- **Button-driven interface** — every command provides inline keyboard buttons for quick actions
- **Settings panel** — `/start` menu with Settings, Plan, and Subscribe buttons
- **Confirmation prompts** — destructive actions (stop, remove, ban) require button confirmation
- **Toggle panels** — `/selfsend` and `/broadcast` show current state with toggle buttons when called without args
- **Admin dashboard** — `/status` includes action buttons; `/list` has pagination buttons
- **Mute presets** — `/mute` by reply offers 30m / 2h / 1d / 7d preset buttons
- **Contextual actions** — `/whois` shows Mute/Ban buttons; `/plan` shows relevant next-step buttons

### Administration
- **Auto-registration** — bot auto-registers chats upon being added as member or admin (`my_chat_member`)
- **Configurable signature** — entity-safe, appended to messages respecting API char limits (4 096 text / 1 024 caption); entities that overflow truncated content are automatically dropped
- **Paginated chat list** — browse active chats with role flags and inline pagination
- **Health endpoint** — `GET /health` returns queue size and Redis status (webhook mode)
- **Bot API 9.6** — handles `ManagedBotCreated`, `ManagedBotPaused`, `ManagedBotResumed` update types

### Infrastructure
- **Dual-mode** — long-polling (dev) or webhook with aiohttp (prod)
- **Async PostgreSQL** — via SQLAlchemy 2.0 async + asyncpg, connection pooling (20 + 10 overflow)
- **Redis** — dedup cache (per-chat, three layers: `dup:upd:*` 60 s, `dup:c:*` 24 h, `dup:alb:*` 24 h), rate-limit state, media-group buffer, subscription cache, nudge cooldowns, bot username cache (1 h TTL), signature cache (30 s TTL)
- **Alembic migrations** — versioned schema evolution
- **Docker Compose** — one-command deploy with health-checked Postgres 16 and Redis 7
- **Send-log cleanup** — background task prunes `send_log` rows older than 48 h (hourly)
- **Graceful shutdown** — drains worker pool, stops background tasks, closes connection pools

---

## 🚀 Quick Start

### 1. Clone and configure

```bash
git clone <repo-url>
cd TelegramMediaHub
cp .env.example .env
# Edit .env — at minimum set BOT_TOKEN and ADMIN_USER_IDS
```

### 2. Run with Docker (recommended)

```bash
docker-compose up -d
```

### 3. Run locally (requires PostgreSQL + Redis)

```bash
pip install -r requirements.txt
alembic upgrade head
python -m bot
```

---

## ⚙️ Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | **Yes** | — | Bot token from [@BotFather](https://t.me/BotFather) |
| `BOT_MODE` | No | `polling` | `polling` (dev) or `webhook` (prod) |
| `ADMIN_USER_IDS` | No | — | Comma-separated Telegram user IDs for admin commands |
| `DATABASE_URL` | No | `postgresql+asyncpg://mediahub:password@localhost:5432/mediahub` | Async PostgreSQL DSN |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection string |
| `GLOBAL_RATE_LIMIT` | No | `25` | Max messages/second globally |
| `WORKER_COUNT` | No | `10` | Async worker pool size for distribution |
| `TRIAL_DAYS` | No | `30` | Free trial duration in days |
| `LOG_LEVEL` | No | `INFO` | Python logging level |
| `WEBHOOK_HOST` | No | — | Public hostname for webhook mode |
| `WEBHOOK_PORT` | No | `8443` | Webhook listener port |
| `WEBHOOK_PUBLIC_PORT` | No | — | Public-facing port for the webhook URL (use `443` behind a reverse proxy; defaults to `WEBHOOK_PORT` if unset) |
| `WEBHOOK_PATH` | No | `/webhook` | Webhook URL path |
| `WEBHOOK_SECRET` | No | — | Secret token for webhook verification |
| `LOCAL_API_URL` | No | — | Local Bot API server URL (optional, for large files) |

> **Redis runtime config** — `allow_paid_broadcast` is toggled at runtime via the `config:allow_paid_broadcast` Redis key (set to `"true"` / `"false"`), not an env var, so no restart is needed when changing it.

---

## 🤖 Bot Commands

### User Commands

| Command | Description |
|---|---|
| `/start` | Start syncing this chat |
| `/stop` | Stop syncing this chat |
| `/selfsend [on\|off]` | See your own messages echoed back |
| `/broadcast [off\|on in\|out]` | Control what you send and receive (Premium) |
| `/subscribe [chat_id]` | Go Premium |
| `/plan` | Check your current plan |
| `/stats` | See how your chat is doing |
| `/help` | Quick guide and command list |

### Admin Commands (restricted to `ADMIN_USER_IDS`)

| Command | Description |
|---|---|
| `/status` | Dashboard |
| `/list [page]` | Browse connected chats |
| `/signature <text>` | Set a signature line |
| `/signatureurl <url>` | Set a signature link |
| `/signatureoff` | Remove signature |
| `/pause` | Pause all syncing |
| `/resume` | Resume syncing |
| `/edits [off\|resend]` | Handle edited messages |
| `/remove <chat_id\|reply>` | Disconnect a chat (reply resolves the **chat** that sent the message) |
| `/grant <chat_id> <plan>` or reply + `/grant <plan>` | Give someone Premium (reply resolves **chat_id**) |
| `/revoke <chat_id\|reply>` | Remove someone's Premium (reply resolves **chat_id**) |
| `/mute <user_id\|reply> [duration]` | Temporarily silence a user (reply resolves **user_id**) |
| `/unmute <user_id\|reply>` | Unsilence a user |
| `/ban <user_id\|reply>` | Permanently block a user (with or without message deletion) |
| `/unban <user_id\|reply>` | Unblock a user |
| `/whois <name>` | Look up a user by their alias name |

> **Reply targeting note:** Commands that operate on a *chat* (remove, grant, revoke) read `sender_chat.id` for channel/group posts. Commands that operate on a *user* (mute, ban, unmute, unban) read `from_user.id`. Both fall back to a `send_log` reverse lookup when replying to a bot-sent message.

---

## 🗂️ Project Architecture

```
TelegramMediaHub/
├── bot/
│   ├── __main__.py              # Entry point: python -m bot
│   ├── app.py                   # Application factory (polling / webhook)
│   ├── config.py                # pydantic-settings configuration
│   │
│   ├── db/
│   │   ├── base.py              # SQLAlchemy DeclarativeBase
│   │   ├── engine.py            # Async engine + session factory
│   │   └── repositories/
│   │       ├── chat_repo.py     # Chat CRUD (upsert, deactivate, migrate, toggle)
│   │       ├── config_repo.py   # Key-value config CRUD
│   │       ├── send_log_repo.py # Reverse lookup (source_user_id + source_chat_id) for reply threading + moderation
│   │       ├── alias_repo.py    # User alias CRUD (get_or_create, lookup_by_alias)
│   │       ├── restriction_repo.py  # Mute/ban restriction CRUD
│   │       └── subscription_repo.py  # Subscription CRUD + trial queries
│   │
│   ├── models/
│   │   ├── chat.py              # Chat registry (with partial index)
│   │   ├── bot_config.py        # Key-value runtime config
│   │   ├── send_log.py          # Source → dest mapping (edits + reply threading + user tracking); one row per sent message including each album frame
│   │   ├── user_alias.py        # Persistent user pseudonyms
│   │   ├── user_restriction.py  # Mute/ban records
│   │   └── subscription.py      # Telegram Stars subscriptions
│   │
│   ├── services/
│   │   ├── normalizer.py        # Message → NormalizedMessage (9 types + source_user_id)
│   │   ├── dedup.py             # Fingerprinting + Redis seen-cache
│   │   ├── rate_limiter.py      # Atomic Lua token bucket + per-chat cooldown + circuit breaker
│   │   ├── sender.py            # NormalizedMessage → Bot API send* (UTF-16 entity offsets, entity clipping, alias link)
│   │   ├── distributor.py       # Fan-out worker pool + paywall + reply resolve + alias + SendLogCleaner; logs all album frames
│   │   ├── media_group.py       # Album buffer + atomic flush lock (multi-process safe)
│   │   ├── signature.py         # Promotional signature appender (respects char limits)
│   │   ├── keyboards.py         # Centralized inline keyboard builders for all commands
│   │   ├── alias.py             # Sender alias service (cached Redis lookup + clickable link formatting)
│   │   ├── alias_words.py       # Adjective/noun word lists for readable alias generation
│   │   ├── moderation.py        # Restriction checks, duration parser, cache invalidation
│   │   └── subscription.py      # Premium checks, nudges, trial reminders
│   │
│   ├── handlers/
│   │   ├── membership.py        # my_chat_member auto-registration
│   │   ├── start.py             # /start, /stop, /selfsend, /broadcast, /stats (with button panels)
│   │   ├── admin.py             # Admin commands + moderation; _resolve_target_chat for chat ops, _resolve_target_user for user ops
│   │   ├── callbacks.py         # Unified callback query handler (all parse_mode=HTML set explicitly)
│   │   ├── subscription.py      # /subscribe, /plan, payment callbacks
│   │   ├── edits.py             # Edit redistribution (source-side premium check)
│   │   ├── messages.py          # Content redistribution pipeline + reply detection + restriction check
│   │   └── managed_bot.py       # Bot API 9.6 ManagedBot* event stubs
│   │
│   ├── middleware/
│   │   ├── db_session_mw.py     # DB session injection
│   │   ├── logging_mw.py        # Structured update logging with timing
│   │   └── dedup_mw.py          # Self-message loop prevention
│   │
│   └── utils/
│       ├── enums.py             # MessageType enum
│       └── text.py              # SHA-256 hashing, text truncation
│
├── alembic/
│   ├── env.py                   # Async-aware migration runner
│   ├── script.py.mako           # Migration template
│   └── versions/
│       ├── 001_initial.py       # chats, bot_config, send_log tables
│       ├── 002_subscriptions.py # subscriptions table
│       ├── 003_send_log_dest_index.py  # Reverse-lookup index for reply threading
│       ├── 004_moderation.py    # user_aliases, user_restrictions + send_log.source_user_id
│       └── 005_readable_aliases.py  # Widen alias column + regenerate to two-word format
│
├── docs/
│   ├── botfather-setup.md      # BotFather configuration guide
│   └── operations-runbook.md   # Hetzner deployment and ops guide
├── alembic.ini
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── .gitignore
```

---

## 📊 Database Schema

| Table | Purpose |
|---|---|
| `chats` | Registry of all known chats (with `active`, `is_source`, `is_destination` flags) |
| `bot_config` | Key-value store for runtime config (signature, pause state, edit mode, allow_paid_broadcast) |
| `send_log` | Tracks source→destination message mapping for edits, reply threading, and moderation (48 h retention, triple-indexed). **One row per sent message** — every frame in a redistributed album has its own row. |
| `subscriptions` | Telegram Stars payment records with plan, expiry, and charge ID |
| `user_aliases` | Persistent random pseudonyms for sender identification (user_id → alias) |
| `user_restrictions` | Mute/ban records with type, expiry, and admin who issued it |

---

## 🔄 Message Flow

```
Incoming message/channel_post
  │
  ├─ SelfMessageMiddleware → drop if from bot's own ID
  ├─ LoggingMiddleware → log update type/chat/timing
  ├─ DbSessionMiddleware → inject async session
  │
  ▼
messages_router
  │
  ├─ Restriction check: is_user_restricted? → drop if muted/banned
  ├─ normalize() → NormalizedMessage (or skip unsupported types)
  ├─ is_active_source? → drop if chat not registered
  ├─ is_duplicate_update? → drop redelivered webhook updates (60 s window)
  ├─ Reply detection: is reply to bot message? → reverse lookup in send_log
  ├─ media_group_id? → buffer in Redis; album-level dedup runs at flush time
  ├─ is_duplicate? (singles) → drop if same content seen from this chat in last 24 h
  │
  ▼
distributor.distribute()
  │
  ├─ Check global pause
  ├─ Query active destinations
  ├─ For each destination:
  │   ├─ Skip self-send (unless allowed)
  │   ├─ Paywall check (trial/premium) → nudge if expired
  │   ├─ Reply resolve: find bot's message ID in this dest via send_log
  │   └─ Enqueue SendTask (with reply_to_message_id if applicable)
  │
  ▼
Worker pool (configurable, default 10)
  │
  ├─ Rate limiter: atomic Lua token bucket (25 msg/s) + per-chat cooldown
  ├─ Build signature from config (Redis-cached 30 s)
  ├─ Resolve sender alias (Redis-cached)
  ├─ send_single() or send_media_group() → correct Bot API send* call
  │   ├─ ANIMATION always sent individually (not via sendMediaGroup)
  │   ├─ Entity offsets computed in UTF-16 code units
  │   └─ Entities clipped if signature truncated content
  ├─ Log to send_log: one row per message; albums log every frame
  │
  └─ Error handling:
      ├─ 429 → sleep retry_after, re-enqueue
      ├─ 403 → deactivate chat
      ├─ migrate → update DB, re-enqueue
      └─ circuit breaker → pause after repeated failures
```

---

## 📜 License

See [LICENSE](LICENSE) for details.
