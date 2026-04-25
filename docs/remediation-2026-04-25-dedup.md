# Dedup remediation — 2026-04-25

## What the user reported

> The system is very inconsistent in relaying messages. No text messages
> getting relayed at all like 95 % of the time. The "don't relay media that
> was already sent" feature doesn't correctly work for albums (group messages).

Two distinct problems hidden behind one symptom: the dedup engine.

## Root causes

### 1. Cross-chat / cross-sender content collisions (the 95 % text drop)

The legacy `dedup.py` keyed every content fingerprint as
`dedup:media:{file_unique_id}` or `dedup:text:{sha256(text)}` — global across
the entire bot. With 24 h TTL.

Consequence: the *first* time anyone, anywhere, said "good morning", that
fingerprint was claimed for 24 h. Every subsequent "good morning" — from a
different user, in a different chat, via a different source — silently failed
the `SET NX` and was dropped. Same for "ok", "thanks", emoji reactions,
forwarded news headlines, shared memes, you name it.

For a multi-chat relay bot this is the wrong threat model. Webhook-retry
dedup needs `(chat_id, message_id)`; spam/repost dedup needs scoping to the
*source chat* it came from.

### 2. Album dedup didn't actually dedup re-uploads

The per-item dedup ran *before* buffering, with the same global key namespace
as singles:

```python
fp = compute_fingerprint(normalized)        # media:{fuid}
if not await redis.set(f"dedup:{fp}", "1", ex=DEDUP_TTL, nx=True):
    return  # drop
```

Two failure modes:

- **Partial album**: if a single item of a *new* album shared its
  `file_unique_id` with any prior message anywhere on the bot, that one
  item was dropped pre-buffer. The flusher then emitted the album with a
  hole.
- **Inconsistent re-upload detection**: a re-uploaded album had a fresh
  `media_group_id`; per-item dedup *might* catch all items, *might* catch
  some, depending on whether each item had been seen elsewhere first.

The `is_media_group_seen(media_group_id)` helper that was supposed to
catch group-level repeats only ever fired for the same physical upload event
(a `media_group_id` is unique per upload), so it could not detect re-uploads
at all.

## Fix

A three-layer dedup engine, each layer scoped to its threat model and to the
source chat. See `bot/services/dedup.py` for the full implementation.

| Layer | Key | TTL | Purpose |
| --- | --- | --- | --- |
| Update | `dup:upd:{chat_id}:{message_id}` | 60 s | Webhook retry guard. No false positives by construction. |
| Content (singles) | `dup:c:{chat_id}:media:{fuid}` / `dup:c:{chat_id}:text:{hash}` | 24 h | Repost guard within a single source chat. Cross-chat content does not collide. |
| Album | `dup:alb:{chat_id}:{sha256(sorted_fuids)}` | 24 h | Whole-album fingerprint evaluated at flush time, not per item. Re-uploads with fresh `media_group_id` are detected; partial overlaps don't trigger false drops. |

Helpers:

- `dup:mg:{chat_id}:{media_group_id}` — informational "seen" marker, scoped
  to chat for defence in depth. Not load-bearing for correctness; the album
  guard does the actual content-level work.

### Pipeline change in `bot/handlers/messages.py`

Before:

```
restriction → normalize → source check
            → media_group? mark mg / per-item dedup / buffer
            → singles: is_duplicate → distribute
```

After:

```
restriction → normalize → source check
            → is_duplicate_update    (webhook retry guard, very early)
            → reply detect (populate_reply_source)
            → media_group? mark mg / buffer  (no per-item content dedup)
            → singles: is_duplicate → distribute
```

The album content dedup now runs in `MediaGroupBuffer._flush_group` after
the items are assembled, so it judges the whole group as a unit.

## Why this fixes both symptoms

- **Text 95 % drop**: scoping content keys by `source_chat_id` eliminates
  cross-chat collisions. Two chats can both relay "good morning"; the same
  user repeating themselves is still deduped within the original chat.
- **Album dedup**: re-uploads of the same album files (in any order, with
  any new `media_group_id`) hash to the same `dup:alb` key and are dropped.
  Partial overlaps hash differently and pass through. Per-item pre-buffer
  dedup is removed, so the partial-album hole bug cannot occur.

## What did NOT change

- DB schema. The dedup engine is Redis-only. `alembic/versions/006_*.py`
  bumps the chain so operators can correlate the cutover, but contains no
  SQL.
- Self-message middleware. Loop prevention is unaffected.
- Reply threading, send_log mapping, signature handling, paywall.

## Verification

- New tests in `tests/test_dedup.py`:
  - `test_same_text_different_chats_both_relayed` — locks in the cross-chat
    fix (the explicit regression test for the 95 % drop bug).
  - `test_same_media_different_chats_both_relayed`
  - `test_redis_key_is_chat_scoped`
  - `TestIsDuplicateUpdate::*` — webhook retry guard
  - `TestGroupFingerprint::*` — order-independence and uniqueness
  - `TestIsAlbumDuplicate::*` — re-upload detection, cross-chat isolation,
    partial-overlap handling
- `tests/test_media_group.py::test_flush_drops_duplicate_album` — end-to-end
  buffer→flush→dedup path.
- `tests/test_system.py::test_message_handler_pipeline_order` — keeps the
  pipeline ordering enforced (now expects `is_duplicate_update` →
  `populate_reply_source` → `is_duplicate(`).

All sync + async dedup tests verified passing in the sandbox via a stub
harness (pytest is not available there).

## Operator notes

- After deploying, the legacy `dedup:*` keys keep occupying memory for up
  to 24 h. Optional cleanup:
  `redis-cli --scan --pattern 'dedup:*' | xargs redis-cli del`.
- `redis-cli --scan --pattern 'dup:*' | wc -l` after a few minutes of
  traffic gives a quick "is it working" signal.
- A spike in `Dropping webhook-retry update` debug logs is expected and
  benign — it means we're correctly absorbing redeliveries that previously
  fell through to content dedup.
