# TelegramMediaHub

A Python Telegram bot built on **aiogram v3** that receives content from registered chats and redistributes it as **original (non-forwarded) messages** to all other registered destinations — with reply threading, broadcast control, deduplication, rate-limit safety, privacy guarantees, a monetisation layer via **Telegram Stars**, and scalability to 100 000 chats.

---

## ✨ Features

### Content Redistribution
- **All media types** — text, photo, video, animation/GIF, audio, document, voice, video note, sticker
- **Album support** — media groups are buffered in Redis and redistributed as intact albums
- **Privacy first** — never uses `forwardMessage` or `copyMessage`; always re-sends via `send*` with `file_id` reuse, so no forwarding metadata appears
- **Edit redistribution** — optionally re-send edited messages (configurable: `off` or `resend`)

### Reply Threading
- **Cross-chat replies** — when a user replies to a bot-sent message in any chat, the reply is distributed to all other chats **as a Telegram Reply** to the corresponding message in each destination
- **Reverse lookup** — uses the `send_log` table to map `(dest_chat_id, dest_message_id)` back to the original source, then resolves the bot's message in each destination
- **Graceful degradation** — uses `allow_sending_without_reply=True` so replies still send even if the target message was deleted or pruned from the 48-hour send_log window

### Broadcast Control
- **Per-chat muting** — `/mute out` pauses outgoing content (stops this chat from broadcasting); `/mute in` pauses incoming content (stops receiving from others)
- **Resume anytime** — `/unmute out` and `/unmute in` to resume
- **Premium-gated** — available during the free trial and for premium subscribers; paywalled after trial expiry

### Deduplication
- Content fingerprinting using `file_unique_id` (media) or SHA-256 (text)
- 24-hour Redis TTL prevents re-processing identical content
- Self-message middleware drops the bot's own messages to prevent redistribution loops

### Rate Limiting & Resilience
- **Global token bucket** — 25 messages/second via Redis sorted set
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

### Administration
- **Auto-registration** — bot auto-registers chats upon being added as member or admin (`my_chat_member`)
- **Configurable signature** — appended to messages, respects API char limits (4 096 text / 1 024 caption)
- **Paginated chat list** — browse active chats with role flags
- **Health endpoint** — `GET /health` returns queue size and Redis status (webhook mode)

### Infrastructure
- **Dual-mode** — long-polling (dev) or webhook with aiohttp (prod)
- **Async PostgreSQL** — via SQLAlchemy 2.0 async + asyncpg, connection pooling (20 + 10 overflow)
- **Redis** — dedup cache, rate-limit state, media-group buffer, subscription cache, nudge cooldowns
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

---

## 🤖 Bot Commands

### User Commands

| Command | Description |
|---|---|
| `/start` | Register this chat for sending and receiving content |
| `/stop` | Unregister this chat |
| `/selfsend on\|off` | Toggle whether you receive your own content back |
| `/mute in\|out` | Pause incoming or outgoing broadcasts (premium) |
| `/unmute in\|out` | Resume incoming or outgoing broadcasts (premium) |
| `/subscribe [chat_id]` | View premium plans and purchase via Telegram Stars |
| `/plan` | Show current subscription/trial status and broadcast state |

### Admin Commands (restricted to `ADMIN_USER_IDS`)

| Command | Description |
|---|---|
| `/status` | Bot status — active chats, queue size, signature, pause state |
| `/list [page]` | Paginated list of all active chats with role flags |
| `/signature <text>` | Set promotional signature text |
| `/signatureurl <url>` | Set signature as a URL |
| `/signatureoff` | Disable signature |
| `/pause` | Pause all content distribution |
| `/resume` | Resume distribution |
| `/edits off\|resend` | Set edit redistribution mode |
| `/remove <chat_id>` | Forcibly deactivate a chat |
| `/grant <chat_id> <plan>` | Grant a free subscription (week/month/year) |
| `/revoke <chat_id>` | Revoke active subscriptions for a chat |

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
│   │       ├── send_log_repo.py # Reverse lookup + dest resolution for reply threading
│   │       └── subscription_repo.py  # Subscription CRUD + trial queries
│   │
│   ├── models/
│   │   ├── chat.py              # Chat registry (with partial index)
│   │   ├── bot_config.py        # Key-value runtime config
│   │   ├── send_log.py          # Source → dest mapping (edits + reply threading)
│   │   └── subscription.py      # Telegram Stars subscriptions
│   │
│   ├── services/
│   │   ├── normalizer.py        # Message → NormalizedMessage (9 types)
│   │   ├── dedup.py             # Fingerprinting + Redis seen-cache
│   │   ├── rate_limiter.py      # Token bucket + circuit breaker
│   │   ├── sender.py            # NormalizedMessage → Bot API send* (with reply_parameters)
│   │   ├── distributor.py       # Fan-out worker pool + paywall + reply resolve + SendLogCleaner
│   │   ├── media_group.py       # Album buffer + auto-flusher
│   │   ├── signature.py         # Promotional signature appender
│   │   └── subscription.py      # Premium checks, nudges, trial reminders
│   │
│   ├── handlers/
│   │   ├── membership.py        # my_chat_member auto-registration
│   │   ├── start.py             # /start, /stop, /selfsend, /mute, /unmute
│   │   ├── admin.py             # Admin-only commands
│   │   ├── subscription.py      # /subscribe, /plan, payment callbacks
│   │   ├── edits.py             # Edit redistribution
│   │   └── messages.py          # Content redistribution pipeline + reply detection
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
│       └── 003_send_log_dest_index.py  # Reverse-lookup index for reply threading
│
├── docs/
│   └── botfather-setup.md      # BotFather configuration guide
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
| `bot_config` | Key-value store for runtime config (signature, pause state, edit mode) |
| `send_log` | Tracks source→destination message mapping for edit support and reply threading (48 h retention, dual-indexed) |
| `subscriptions` | Telegram Stars payment records with plan, expiry, and charge ID |

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
  ├─ normalize() → NormalizedMessage (or skip unsupported types)
  ├─ is_active_source? → drop if chat not registered
  ├─ media_group_id? → buffer in Redis (flush after 1s inactivity)
  ├─ is_duplicate? → drop if fingerprint seen in last 24h
  ├─ Reply detection: is reply to bot message? → reverse lookup in send_log
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
  ├─ Rate limiter: global token bucket + per-chat cooldown
  ├─ Build signature from config
  ├─ send_single() → correct Bot API send* call (with reply_parameters)
  ├─ Log to send_log
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
