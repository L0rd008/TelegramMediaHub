# TelegramMediaHub — Broadcasting Bug Report & Implementation Plan

> **Scope:** Every message type (text, photo, video, animation/GIF, audio, document, voice, video note, sticker, album) from every Group A entity to every Group B entity. Reply threading. UI / visual attribution.
>
> **Date:** April 2026  
> **Audited files:** `messages.py`, `distributor.py`, `sender.py`, `normalizer.py`, `media_group.py`, `dedup.py`, `signature.py`, `alias.py`, `send_log_repo.py`, `chat_repo.py`, all Telegram Bot API docs.

---

## Executive Summary

| Severity | Count | Description |
|---|---|---|
| 🔴 Critical | 2 | Core functionality silently broken |
| 🟠 Major | 3 | Significant user-facing breakage or data loss |
| 🟡 Minor | 2 | Edge-case failures or silent misbehaviour |
| 🔵 UI/UX | 4 | Suboptimal but functional presentation |

---

## 🔴 Bug 1 — CRITICAL: Media-group reply threading is permanently broken

### Root cause

In `handlers/messages.py`, the reply-detection block (lines 119–142) is placed **after** the media-group early-return:

```python
# _handle_content()

# 4. Media group handling
if normalized.media_group_id:
    ...
    await buffer.add(normalized)
    return  # ← exits here for ALL album items

# 5b. Reply detection  ← NEVER REACHED for album items
reply = message.reply_to_message
if reply:
    origin = await sl_repo.reverse_lookup(...)
    if origin:
        normalized.reply_source_chat_id = origin[0]
        normalized.reply_source_message_id = origin[1]
```

Every album item is buffered and distributed **without** `reply_source_chat_id` or `reply_source_message_id` set. Consequently:

- User A sends an album as a reply to User B's message → the reply connection is lost in every destination.
- `send_media_group` in `sender.py` receives `reply_to_message_id=None` for all albums.
- Recipients see a plain album with no reply thread, even though the original was a direct reply.

This affects **every** source–destination pair where the source message is an album reply.

### Hypothesis A — Move reply detection above the media-group branch (recommended)

All items of a Telegram album share the same `reply_to_message`; detecting it on any item gives the same result. Moving the block to run before the media-group check:

1. Sets `normalized.reply_source_chat_id / reply_source_message_id` before `buffer.add()`.
2. `_to_dict` / `_from_dict` already use `asdict()` and `NormalizedMessage(**data)`, so both fields are serialised into Redis and deserialised correctly.
3. In `_flush_group`, construct the composite using the reply fields from `items[0]` (all items share the same reply context).

**Confidence: 97%** — Direct fix, minimal surface area, no new data paths.

### Hypothesis B — Store reply info separately in Redis alongside the buffer

Detect reply on the first item; store `reply_source_*` as a separate Redis key `mgreply:{media_group_id}`. Read it during flush.

**Confidence: 78%** — Works, but adds a new Redis key type and a race window if the first item arrives on a different worker. Strictly worse than A.

### Hypothesis C — Composite inherits reply from items[0] without pre-detection

Detect reply only in `_flush_group`, using `items[0].reply_to_message` — but that information is not present in `NormalizedMessage`; it is only available on the raw `Message` object at handler time.

**Confidence: 35%** — Requires passing the raw Message through the buffer, which defeats the serialisation design.

**Selected solution: Hypothesis A.**

### Implementation

**File: `bot/handlers/messages.py`**

```python
async def _handle_content(message: Message) -> None:
    # 0. Ignore bot commands
    ...
    # 0. Restriction check
    ...
    # 1. Normalize
    normalized = normalize(message)
    if normalized is None:
        return

    # 2. Source check
    async with async_session() as session:
        repo = ChatRepo(session)
        if not await repo.is_active_source(message.chat.id):
            return

    bot = message.bot
    if bot is None:
        return

    distributor = get_distributor()
    redis = distributor._redis

    # ── NEW: Reply detection runs for ALL message types, including albums ──
    reply = message.reply_to_message
    if reply:
        bot_info = await bot.get_me()
        # Only thread if the replied-to message was sent by this bot
        # (from_user is None for channel posts redistributed by bot; treat as candidate)
        is_bot_msg = (reply.from_user is None) or (reply.from_user.id == bot_info.id)
        if is_bot_msg:
            try:
                async with async_session() as session:
                    sl_repo = SendLogRepo(session)
                    origin = await sl_repo.reverse_lookup(message.chat.id, reply.message_id)
                if origin:
                    normalized.reply_source_chat_id = origin[0]
                    normalized.reply_source_message_id = origin[1]
            except Exception as e:
                logger.debug("Reply reverse-lookup failed: %s", e)

    # 4. Media group handling  (reply fields already populated above if applicable)
    if normalized.media_group_id:
        ...
        await buffer.add(normalized)
        return

    # 5. Dedup check
    ...
    # 6. Distribute
    await distributor.distribute(normalized)
```

**File: `bot/services/media_group.py` — `_flush_group`**

```python
composite = NormalizedMessage(
    message_type=MessageType.MEDIA_GROUP,
    source_chat_id=items[0].source_chat_id,
    source_message_id=items[0].source_message_id,
    source_user_id=items[0].source_user_id,
    media_group_id=media_group_id,
    group_items=items,
    # NEW: propagate reply context from first item (all items share the same reply)
    reply_source_chat_id=items[0].reply_source_chat_id,
    reply_source_message_id=items[0].reply_source_message_id,
)
```

Also remove the now-duplicate dedup bot_info lookup:

```python
# OLD (in _handle_content): bot_info = await bot.get_me()  before dedup
# NEW: bot_info is looked up once, earlier, during reply detection
```

---

## 🔴 Bug 2 — CRITICAL: Animation items inside albums are sent but never logged

### Root cause

In `sender.py → send_media_group`, the inner compatibility-splitting loop has a `case _:` fallback for types that cannot go into `sendMediaGroup` (animations, stickers, video notes):

```python
for i, item in enumerate(group):
    match item.message_type:
        case MessageType.PHOTO: ...
        case MessageType.VIDEO: ...
        case MessageType.AUDIO: ...
        case MessageType.DOCUMENT: ...
        case _:
            # Fallback: send as individual message
            await send_single(
                bot, item, chat_id, signature if i == 0 else None,
                sender_alias=sender_alias if i == 0 else None,
                redis=redis,
                allow_paid_broadcast=allow_paid_broadcast,
            )
            continue  # ← result discarded, never appended to all_results
```

`_split_by_compatibility` already places every animation in its own singleton group, so it always reaches this branch. The animation is sent successfully, but:

- The result (`Message`) is discarded.
- No `send_log` row is written for the animation.
- Reply threading to that animation is impossible.
- Ban-cleanup (`get_dest_messages_by_user`) misses those messages.

Additionally, after the fallback `continue`, `input_media` remains empty, so neither the `>= 2` nor the `== 1` branch fires, and `all_results` stays empty for that group.

### Hypothesis A — Capture and append the result inside `case _:` (recommended)

```python
case _:
    result = await send_single(
        bot, item, chat_id, signature if i == 0 else None,
        sender_alias=sender_alias if i == 0 else None,
        redis=redis,
        allow_paid_broadcast=allow_paid_broadcast,
    )
    if result:
        all_results.append(result)  # ← one-line fix
    continue
```

**Confidence: 99%** — Minimal change, directly mirrors how the `== 1` singleton branch works.

### Hypothesis B — Pre-extract "other" types before entering the loop

Move ANIMATION/STICKER/VIDEO_NOTE out of `_split_by_compatibility` into a separate pre-pass that immediately calls `send_single` and collects results before the main loop.

**Confidence: 90%** — Correct, but more refactoring than necessary.

**Selected solution: Hypothesis A.**

### Implementation

**File: `bot/services/sender.py` — `send_media_group`**

```python
case _:
    result = await send_single(
        bot, item, chat_id, signature if i == 0 else None,
        sender_alias=sender_alias if i == 0 else None,
        redis=redis,
        allow_paid_broadcast=allow_paid_broadcast,
    )
    if result:
        all_results.append(result)  # FIX: was silently discarded
    continue
```

---

## 🟠 Bug 3 — MAJOR: Channel posts and anonymous admin messages carry no sender attribution

### Root cause

`normalizer.py` captures `source_user_id` like this:

```python
source_user_id=message.from_user.id if message.from_user else None,
```

For two very common source types, `from_user` is absent or misleading:

| Source type | `from_user` | `sender_chat` | Effect |
|---|---|---|---|
| Channel post | `None` | The channel | `source_user_id = None` → no alias |
| Anonymous admin in group | Fake "GroupAnonymousBot" (id=1087968824) | The group | Alias created for anonymous bot ID |
| Channel-linked auto-forward | `None` | The original channel | `source_user_id = None` → no alias |

In the distributor:
```python
if msg.source_user_id:
    sender_alias = await get_alias(self._redis, msg.source_user_id)
```

When `source_user_id` is None, `sender_alias` stays None, and every redistributed channel post arrives with zero attribution. Recipients cannot tell which channel or group originated the content.

### Hypothesis A — Use `sender_chat` as attribution when `from_user` is None (recommended for channels)

Add `source_chat_title` and `source_chat_username` to `NormalizedMessage`. Populate them from `message.chat.title` / `message.chat.username`. Use them as the attribution line when no alias is available.

**Confidence: 95%** — Gives genuine, useful attribution ("From: @ChannelUsername") without abusing the alias table.

### Hypothesis B — Create a "chat alias" in `user_aliases` using the chat_id as a pseudo-user-id

Store negative chat_ids (e.g., `-1001234567890`) in `user_aliases`. The alias system would generate a pseudonym for the chat.

**Confidence: 60%** — Confuses the alias table's user-scoped semantics. "golden_arrow" for a channel is meaningless.

### Hypothesis C — Skip attribution entirely for channel posts (status quo)

**Confidence: 100%** that it "works", but attribution is silently absent. Not acceptable.

### Hypothesis D — Hybrid: user alias for users, chat name for channels/anonymous admins (recommended)

For messages where `source_user_id` is available → show pseudonym alias as today (clickable link).
For messages where `from_user` is None or is the anonymous admin bot → show `@username` or title of `sender_chat` / `message.chat`.

**Confidence: 96%** — Best UX. Shows meaningful context for all source types. No privacy concern for channels (they're public entities by nature).

**Selected solution: Hypothesis D.**

### Implementation

**File: `bot/services/normalizer.py`**

```python
ANONYMOUS_ADMIN_BOT_ID = 1087968824  # Telegram's "GroupAnonymousBot"

@dataclass
class NormalizedMessage:
    ...
    # NEW fields
    source_chat_title: str | None = None
    source_chat_username: str | None = None

def normalize(message: Message) -> NormalizedMessage | None:
    ...
    # Determine source_user_id; detect anonymous admin
    from_user = message.from_user
    sender_chat = message.sender_chat

    if from_user and from_user.id != ANONYMOUS_ADMIN_BOT_ID:
        source_user_id = from_user.id
        source_chat_title = None
        source_chat_username = None
    else:
        # Channel post or anonymous admin → attribute to the chat
        source_user_id = None
        # Use sender_chat if available (anonymous admin), else message.chat
        attr_chat = sender_chat or message.chat
        source_chat_title = attr_chat.title
        source_chat_username = attr_chat.username
    ...
```

**File: `bot/services/sender.py` — `send_single` and `send_media_group`**

```python
# In distributor._process_task, fall back to chat attribution:
sender_alias: str | None = None
source_chat_label: str | None = None

if msg.source_user_id:
    sender_alias = await get_alias(self._redis, msg.source_user_id)
elif msg.source_chat_username:
    source_chat_label = f"@{msg.source_chat_username}"
elif msg.source_chat_title:
    source_chat_label = msg.source_chat_title

result = await send_single(
    ...,
    sender_alias=sender_alias,
    source_chat_label=source_chat_label,  # NEW param
)
```

In `send_single`, use `source_chat_label` wherever `sender_alias` is used if the alias is absent:

```python
attribution = sender_alias or source_chat_label or ""
```

---

## 🟠 Bug 4 — MAJOR: `apply_signature` uses Python codepoint length, not UTF-16 units

### Root cause

`signature.py`:
```python
if len(full) <= max_len:
    return full
```

Telegram measures text length in **UTF-16 code units**. BMP characters = 1 unit. Astral-plane characters (most emoji, e.g., 🔥, 💬, 🎯) = **2 units**. Python's `len()` counts codepoints (1 per character regardless).

A message with 40 emoji followed by a long caption can have `len(full) = 4020` (Python) but `utf16_len(full) = 4060 + 40 = 4100 > 4096` (Telegram). This causes a `TelegramBadRequest: message is too long` that is caught by the generic error handler and the message is silently dropped.

The same UTF-16 helper already exists in `sender.py` (`_utf16_len`).

### Hypothesis A — Replace `len()` with `_utf16_len()` in `apply_signature` (recommended)

```python
from bot.services.sender import _utf16_len  # or move helper to text.py

def apply_signature(content, signature, max_len):
    full = f"{content}{separator}{signature}"
    if _utf16_len(full) <= max_len:
        return full
    # truncate content to fit
    ellipsis = "..."
    target = max_len - _utf16_len(separator) - _utf16_len(signature) - _utf16_len(ellipsis)
    # Binary-search or character-by-character trim to target UTF-16 units
    ...
```

**Confidence: 98%** — Fixes the root cause exactly. The `_utf16_len` helper is already tested and used elsewhere.

### Hypothesis B — Apply a conservative safety margin (e.g., max_len - 100)

Quick patch that avoids all edge cases without changing the truncation logic.

**Confidence: 80%** — Works for almost all cases but wastes space for normal messages and still fails for messages with > 100 astral chars.

**Selected solution: Hypothesis A.** Move `_utf16_len` to `bot/utils/text.py` so both `sender.py` and `signature.py` share one source of truth.

### Implementation

**File: `bot/utils/text.py`** — add:
```python
def utf16_len(s: str) -> int:
    """UTF-16 code unit length of s (as required by the Telegram Bot API)."""
    return len(s.encode("utf-16-le")) // 2
```

**File: `bot/services/signature.py`** — update:
```python
from bot.utils.text import utf16_len

def apply_signature(content, signature, max_len):
    ...
    full = f"{content}{separator}{signature}"
    if utf16_len(full) <= max_len:
        return full
    ellipsis = "..."
    reserved = utf16_len(separator) + utf16_len(signature) + utf16_len(ellipsis)
    target_content_units = max_len - reserved
    if target_content_units <= 0:
        return signature[:max_len]  # signature alone fits (approx)
    # Trim content to target UTF-16 units
    trimmed = []
    units = 0
    for ch in content:
        ch_units = utf16_len(ch)
        if units + ch_units > target_content_units:
            break
        trimmed.append(ch)
        units += ch_units
    return f"{''.join(trimmed)}{ellipsis}{separator}{signature}"
```

**File: `bot/services/sender.py`** — replace local `_utf16_len` with import from `bot.utils.text`:
```python
from bot.utils.text import utf16_len as _utf16_len
```

---

## 🟠 Bug 5 — MAJOR: Linked discussion group replies cannot be threaded (architectural gap)

### Root cause

When the bot sends a message to a **channel**, Telegram automatically forwards that post to the channel's linked **discussion group**. The bot logs the send as:

```
send_log: source_chat=A, source_msg=X, dest_chat=CHANNEL_ID, dest_msg=M_channel
```

The auto-forwarded copy in the discussion group has a **different** `message_id` (`M_group`) and a different `chat_id` (`DISCUSSION_GROUP_ID`). The bot never logs this mapping because it never called `send_message` to the discussion group.

When a user in the discussion group replies to `M_group`:
```python
reverse_lookup(DISCUSSION_GROUP_ID, M_group)  # returns None → reply threading fails
```

### Hypothesis A — Register the discussion group as an additional destination

After sending to a channel, query `getChatFullInfo` for `linked_chat_id`. Register that ID as a destination. The bot then sends both to the channel AND to the discussion group directly.

**Problem:** Telegram's auto-forward from channel to discussion group causes duplicates. The auto-forwarded copy and the bot's direct copy both appear.

**Confidence: 45%** — Creates double-posting. Not viable without suppression logic.

### Hypothesis B — Listen for auto-forwarded messages and build a secondary mapping

If the bot is also a member of the discussion group, it receives the auto-forward as an `is_automatic_forward=True` message. Hook a handler on `is_automatic_forward` messages to extract `forward_origin.message_id` (the channel message ID) and map it to `message_id` (the discussion group message ID). Store this as a secondary `send_log` row.

```python
send_log: source_chat=CHANNEL_ID, source_msg=M_channel,
          dest_chat=DISCUSSION_GROUP_ID, dest_msg=M_group
```

Now `get_dest_message_id(A, X, DISCUSSION_GROUP_ID)` chains:
- `A, X → CHANNEL_ID, M_channel` (already logged)
- `CHANNEL_ID, M_channel → DISCUSSION_GROUP_ID, M_group` (new secondary lookup)

**Confidence: 80%** — Correct once the bot is in the discussion group. Requires a two-hop send_log query or a pre-resolved mapping.

### Hypothesis C — Accept the limitation, document it; prioritise fixes 1–4 first

For the majority of deployments (group-to-group, private-chat-to-group), this is a non-issue. The channel↔discussion-group scenario is an advanced edge case.

**Confidence: 100%** — Always safe, no regressions.

### Hypothesis D — Hybrid: implement B asynchronously as a separate PR, ship C now

**Confidence: 95%** — Pragmatic. Fixes the critical bugs first, keeps the codebase stable.

**Selected solution: Hypothesis D.** Ship Bugs 1–4 fixes immediately. Implement the auto-forward mapping handler in a follow-up sprint.

---

## 🟡 Bug 6 — MINOR: `dedup_mw` self-message check may miss bot messages in channels

### Root cause

`dedup_mw.py` drops messages where `update.message.from_user.id == bot_id`. But when the bot sends to a **channel**, the channel post has `from_user = None`. The self-message check based on `from_user` misses those. The content-level fingerprint dedup in `dedup.py` catches it (the bot's redistributed `file_unique_id` is already in Redis), so there is no actual loop — but the middleware guard is not as strong as assumed.

**Impact:** Low. The fingerprint dedup is the authoritative loop-prevention mechanism.

**Fix (optional hardening):** Also check `update.channel_post.sender_chat.id == bot_chat_id` if `from_user` is None.

---

## 🟡 Bug 7 — MINOR: `reply_to_message` check is too narrow for channel posts redistributed to channels

### Root cause

```python
if reply.from_user and reply.from_user.id != bot_info.id:
    should_check = False
```

When the bot redistributes to a **channel**, the sent message is a channel post (`from_user = None`). If a channel operator then replies to that redistributed post:

- `reply.from_user = None` → `should_check = True` → correct, lookup proceeds ✓

This case actually works correctly. However the current comment in the code ("Only thread if the bot sent the message") is misleading since `from_user=None` passes the check for any reason, including non-bot channel posts. A missed lookup will simply return `None` and be ignored, so no user-visible bug — just a wasted DB query per reply to non-bot channel messages.

**Fix:** Add a guard using `reply.sender_chat` for disambiguation, or leave as-is (harmless).

---

## 🔵 UI/UX Issue 1 — Two-line attribution is visually cluttered

### Current format

```
[original message content]

golden_arrow          ← clickable link (alias)
— via @MediaHubDistBot  ← signature text
```

**Problems:**
- Recipients don't know what `golden_arrow` represents. No label.
- Two separate paragraphs means two visual blocks after the content.
- For channel posts (no alias), only the signature appears — no sender context at all.
- Signature text `— via @MediaHubDistBot` is a plain string; `@MediaHubDistBot` has no mention entity linking it to the bot.

### Proposed: Single combined attribution line

**Format:** `↗ golden_arrow · @MediaHubDistBot`

- Both alias and signature on one line, separated by ` · ` (U+00B7 MIDDLE DOT).
- `golden_arrow` gets a `text_link` entity → clickable link to bot.
- `@MediaHubDistBot` gets a `mention` entity → links to bot profile.
- For channel posts: `↗ @ChannelUsername · @MediaHubDistBot` (or title if no username).
- For anonymous admin: `↗ Admin · @MediaHubDistBot`.

**Entity construction:**

```python
attribution_text = f"↗ {alias_or_label} · {signature_handle}"
# Entity 1: text_link on alias_or_label → bot URL
# Entity 2: mention on signature_handle → bot username
```

**Result:**
```
[original message content]

↗ golden_arrow · @MediaHubDistBot
```

Clean, scannable, one line, clickable.

**Confidence in improvement: 96%** — Reduces visual noise; provides context for the alias; gives the signature a live link.

### Implementation impact

Change `raw_text` / `raw_caption` construction in `sender.py`:

```python
# OLD
raw_caption = f"{msg.caption}\n\n{alias_plain}" if msg.caption else alias_plain

# NEW
attribution_line = f"↗ {alias_plain} · {signature}" if signature else f"↗ {alias_plain}"
raw_caption = f"{msg.caption}\n\n{attribution_line}" if msg.caption else attribution_line
# signature is now embedded in attribution_line → do NOT call apply_signature on top of it
```

The `_build_alias_entity` already handles the clickable link on `alias_plain`. Add a second `mention` entity for the bot username span.

---

## 🔵 UI/UX Issue 2 — No source context for channel-originated messages

When a channel posts content, recipients see the content + signature but have no idea which channel it came from. In a multi-community setup (10 channels all connected), this makes attribution confusing.

**Fix (aligned with Bug 3's Hypothesis D):**
- Show `@ChannelUsername` or the channel title in the attribution line.
- Format: `↗ @SportsChannel · @MediaHubDistBot`

---

## 🔵 UI/UX Issue 3 — Video notes and stickers are completely anonymous

Video notes (`send_video_note`) and stickers (`send_sticker`) have no caption field in the Telegram API. There is literally no way to attach an alias or attribution to them.

**Options:**
A. Send a separate text message immediately after: `↗ golden_arrow sent this` — **intrusive, spammy**.
B. Wrap the video note in a document send with the note embedded — **not possible with file_id reuse**.
C. Accept the limitation; document it — **recommended**.

**Recommendation:** Accept. These message types are inherently caption-less by Telegram design. Add a note in the README.

**Confidence: 92%** — No clean API-compliant solution exists.

---

## 🔵 UI/UX Issue 4 — Reply visual in destination looks "out of context"

When the bot threads a reply correctly, the destination chat shows:
```
┌─ [bot's redistributed message from User B]
│
└─ [bot's redistributed message from User A, with alias]
```

This looks correct in a group context. However, in a **channel**, messages appear chronologically without reply headers being shown to subscribers in the same way as groups. Recipients in a channel see the reply reference in the message metadata but it's less prominent.

**No code fix needed.** This is a Telegram UX limitation for channels. The reply threading is correctly implemented via `ReplyParameters`.

---

## Consolidated Implementation Plan

### Phase 1 — Critical fixes (ship immediately)

| # | File | Change | Bug fixed |
|---|---|---|---|
| 1.1 | `handlers/messages.py` | Move reply detection before media-group branch | Bug 1 |
| 1.2 | `services/media_group.py` | Propagate `reply_source_*` fields in `_flush_group` composite | Bug 1 |
| 1.3 | `services/sender.py` | Capture result in `case _:` fallback, append to `all_results` | Bug 2 |

### Phase 2 — Major fixes (next sprint)

| # | File | Change | Bug fixed |
|---|---|---|---|
| 2.1 | `utils/text.py` | Add `utf16_len()` helper (exported) | Bug 4 |
| 2.2 | `services/signature.py` | Use `utf16_len` for length checks and truncation | Bug 4 |
| 2.3 | `services/sender.py` | Import `utf16_len` from `utils.text`; remove local `_utf16_len` | Bug 4 |
| 2.4 | `services/normalizer.py` | Add `source_chat_title`, `source_chat_username` fields; detect anon admin | Bug 3 |
| 2.5 | `services/distributor.py` | Pass `source_chat_label` to `send_single` when no alias | Bug 3 |
| 2.6 | `services/sender.py` | Accept + use `source_chat_label` param as fallback attribution | Bug 3 |

### Phase 3 — UI/UX overhaul (polish sprint)

| # | File | Change | Issue fixed |
|---|---|---|---|
| 3.1 | `services/sender.py` | Combine alias + signature into single attribution line `↗ alias · @bot` | UI 1 |
| 3.2 | `services/sender.py` | Add `mention` entity for bot username in attribution line | UI 1 |
| 3.3 | `services/sender.py` | Use `source_chat_label` in attribution line for channel posts | UI 2 |
| 3.4 | `README.md` | Document video note / sticker attribution limitation | UI 3 |

### Phase 4 — Advanced threading (future sprint)

| # | File | Change | Bug fixed |
|---|---|---|---|
| 4.1 | `handlers/messages.py` | New handler for `is_automatic_forward=True` channel posts | Bug 5 |
| 4.2 | `db/repositories/send_log_repo.py` | Add `insert_secondary_mapping()` for channel→discussion-group pairs | Bug 5 |
| 4.3 | `services/distributor.py` | Two-hop reply resolve: check direct dest, then secondary mapping | Bug 5 |

---

## Broadcast Matrix — Verified Working / Known-Broken

| Source → Destination | Messages | Albums | Reply threading | Attribution |
|---|---|---|---|---|
| User DM → User DM | ✅ | ✅ | ✅ | ✅ alias |
| User DM → Private group | ✅ | ✅ | ✅ | ✅ alias |
| User DM → Public group | ✅ | ✅ | ✅ | ✅ alias |
| User DM → Private channel | ✅ | ✅ | ✅ | ✅ alias |
| User DM → Public channel | ✅ | ✅ | ✅ | ✅ alias |
| Group (user post) → Any | ✅ | ✅ | ✅ (non-album) / **🔴 Bug 1** (album reply) | ✅ alias |
| Group (anon admin) → Any | ✅ | ✅ | ✅ / **🔴 Bug 1** | **🟠 Bug 3** wrong alias |
| Private channel → Any | ✅ | ✅ | ✅ / **🔴 Bug 1** | **🟠 Bug 3** no alias |
| Public channel → Any | ✅ | ✅ | ✅ / **🔴 Bug 1** | **🟠 Bug 3** no alias |
| Any → Channel w/ discussion group (reply) | ✅ | ✅ | **🟠 Bug 5** discussion replies | — |
| Album with animations | ✅ sent | **🔴 Bug 2** not logged | ❌ threading broken | ✅ first item |
| Protected content source | ✅ | ✅ | ✅ | ✅ |
| Protected content destination | ✅ | ✅ | ✅ | ✅ |
| Sticker, Video Note | ✅ | N/A | ✅ | ❌ by API design |

---

## Quick Reference — Exact Code Locations

| Bug | File | Lines |
|---|---|---|
| Bug 1 (media group reply) | `handlers/messages.py` | 86–111 (media group branch) vs 119–142 (reply block) |
| Bug 1 (composite) | `services/media_group.py` | 147–154 (`_flush_group` composite construction) |
| Bug 2 (animation not logged) | `services/sender.py` | 454–462 (`case _:` fallback in `send_media_group`) |
| Bug 3 (no channel alias) | `services/normalizer.py` | 97 (`source_user_id` assignment) |
| Bug 4 (utf16 length) | `services/signature.py` | 16–37 (`apply_signature`) |
| UI 1 (attribution format) | `services/sender.py` | 155–184 (alias construction in `send_single`) |
