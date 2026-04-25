# Remediation Plan — Independent Audit (2026-04-25)

This is the result of an independent, end-to-end re-audit of the broadcasting
pipeline. It was produced **without** consulting the prior `broadcast-bug-report.md`
(which I was told not to use as a checklist). Findings overlap in some places and
diverge in others; this document is the source of truth for the fixes that follow.

The bugs are listed in fix order — earliest fixes are highest impact / lowest risk.

---

## B-2 — Signature cache is never invalidated

**Where:** `bot/services/distributor.py:374-412`,
`bot/handlers/admin.py` (`cmd_signature`, `cmd_signatureurl`, `cmd_signatureoff`),
`bot/handlers/callbacks.py` (`cb_admin_sigoff`).

**Symptom:** `_get_signature` caches the resolved signature in Redis under
`config:signature_cache` for 30 seconds. `Distributor.invalidate_signature_cache`
exists but is never called from any of the four signature-mutating call sites.
After an admin runs `/signature ...`, `/signatureurl ...`, or `/signatureoff`,
distributed messages keep using the stale signature for up to 30 seconds.

**Confidence:** High (verified by `Grep` — no callers of `invalidate_signature_cache`).

**Fix:** Call `await get_distributor().invalidate_signature_cache()` immediately
after each `await ConfigRepo.set_value("signature_*", ...)` in the four
admin-side handlers. Wrap in `try/except RuntimeError` to be safe in tests.

**Risk:** None — it just deletes a Redis key.

---

## B-3 — Edit redistribution drops reply threading

**Where:** `bot/handlers/edits.py:_handle_edit` vs. `bot/handlers/messages.py:_handle_content`.

**Symptom:** `_handle_content` runs reverse-lookup against `send_log` to populate
`normalized.reply_source_chat_id` / `reply_source_message_id` before calling
`distributor.distribute()`. `_handle_edit` does not. When `edit_redistribution=resend`
and the edited message is a reply, every redistributed copy loses its reply anchor
because `Distributor.distribute` looks up `msg.reply_source_*` per destination, and
those fields are `None`.

**Confidence:** High (read both handlers; missing block is unambiguous).

**Fix:** Extract the reply-detection block from `_handle_content` (lines 148–180)
into a shared helper `populate_reply_source(message, normalized, redis, bot_info)`
in `bot/services/replies.py` (new module) so both `_handle_content` and
`_handle_edit` call the same logic. Edits that aren't replies stay untouched.

**Risk:** Low — pure extraction. Only behavior change is that edited replies now
thread correctly, which is the intended behavior.

---

## B-1 — Mixed-type album `send_log` mapping is misaligned

**Where:** `bot/services/sender.py:send_media_group`, `bot/services/distributor.py:_process_task` (the `zip(result, msg.group_items)`).

**Symptom:** Sender groups items into compatibility buckets and processes them in
the order `[visual, audio, documents, other-singletons]`, appending sent
`Message`s in that bucket order. Distributor pairs them with `msg.group_items`
which is sorted by `source_message_id`. For a homogeneous album the orders
coincide. For an album that mixes a photo with a document, or has an animation
mid-stream, the sent order is reordered relative to the source order — so
`send_log` rows map the wrong source `message_id` to the destination
`message_id`. Reply threading and ban-cleanup downstream then resolve to the
wrong source.

**Confidence:** Medium-High. Album of `[photo_a, document_b, photo_c]`:
- `_split_by_compatibility` → `[[photo_a, photo_c], [document_b]]`
- send order in destination → `photo_a, photo_c` (as one media-group send),
  then `document_b` (as a second send) — destination sees A, C, B
- `msg.group_items` (sorted by source_message_id) → `[photo_a, document_b, photo_c]`
- `zip` pairs: `(dest_photo_a → A)`, `(dest_photo_c → B-source_doc)`,
  `(dest_doc_b → C-source_photo)` — last two are swapped.

**Fix:** Refactor `send_media_group` to track and return
`list[tuple[Message, NormalizedMessage]]` so each sent message is paired with
the source item it represents. Update `_process_task` to consume the paired
form when the result is a list of tuples. Single-frame and homogeneous albums
are unaffected functionally (they already produce correct order, but the new
contract makes the invariant explicit).

**Risk:** Medium — touches the sender return contract. We mitigate by:
- Keeping a single internal `all_pairs` list, building tuples at every site
  that previously appended to `all_results`.
- Distributor accepts `list[tuple[Message, NormalizedMessage]]` for the album
  path, `Message | None` for the single path. The `isinstance(result, list)`
  branch becomes the paired-iteration branch.

---

## B-7 — Wasteful (and slightly wrong) reply lookups

**Where:** `bot/handlers/messages.py:148-180`.

**Symptom:** `is_bot_reply = (reply.from_user is None or reply.from_user.id == bot_info.id)`.
For channel-post replies, `reply.from_user` is `None` — but `reply.sender_chat`
is set to the channel. We then run a reverse-lookup that almost always misses
(channel posts are not stored as bot-sent rows in `send_log`). Cheap but noisy
under heavy channel-post traffic, and it can mistakenly map a reply to a
random old `send_log` row in the same chat if the message_id collides.

**Confidence:** Medium. The collision case is unlikely but the wasted DB hit
is real.

**Fix:** Tighten the gate:
```python
is_bot_reply = (
    reply.from_user is not None and reply.from_user.id == bot_info.id
) or (
    reply.from_user is None and reply.sender_chat is None
)
```
This matches: (a) explicit bot author, or (b) author-stripped messages where
sender_chat is also missing — the indicator that the reply target was a
bot-sent redistributed message in a channel context. Real channel posts (which
have `sender_chat` set) are excluded.

**Risk:** Low. The previous behavior already had `reverse_lookup` returning `None`
in the channel case, so we are only avoiding wasted lookups, not changing
positive paths. We *do* still hit the same code path for legitimate bot
messages in channels (sender_chat is None for those because they're sent by
the bot user, not on behalf of a channel).

---

## B-6 — Silent media-group loss on flush race

**Where:** `bot/services/media_group.py:_flush_group:128-130`.

**Symptom:** After winning the `mgflushing:` NX lock, the pipeline reads the
buffer and finds it empty (a competing process already drained it, or the
2-second TTL elapsed before the flusher polled). The handler silently returns.
There's no log line, so an entire album dropping (because items expired before
flush) leaves no trace.

**Confidence:** High (read the code path). Impact in practice is low — the
2-second TTL and 0.5s poll mean items rarely expire — but we want a warning so
ops can detect tuning regressions.

**Fix:** Log at WARNING level when items are empty, and include the
media_group_id and any context we have. Not an incident, but a visible signal.

**Risk:** None.

---

## B-5 — Dead `bot_id` parameter in `is_duplicate`

**Where:** `bot/services/dedup.py:31-57`, `bot/handlers/messages.py:213`.

**Symptom:** `is_duplicate(redis, msg, bot_id)` accepts `bot_id` but never uses
it. `_handle_content` fetches `bot_info = await bot.get_me()` and passes it in.
The `get_me()` call is now needed for the reply-detection block above it
(B-3 fix moves this to a shared helper, but `bot_info` is still used), so the
fetch isn't wasted — only the parameter is.

**Confidence:** High.

**Fix:** Remove the unused `bot_id` parameter from `is_duplicate` and the
caller. Pure cleanup.

**Risk:** None. Internal-only.

---

## B-4 — Inconsistent premium gating between edits and new messages — **DEFERRED**

**Where:** `bot/handlers/edits.py:71-85` does a source-side `is_premium` check;
`bot/handlers/messages.py` does not (`Distributor.distribute` only checks
premium on the *destination* side, so a free source can still publish to
premium destinations and to itself).

**Symptom:** Edits redistribute differently than fresh messages — a free-tier
chat with an expired trial cannot publish edits at all (handler short-circuits),
but can publish *new* messages to premium destinations and to itself. From a
billing/policy standpoint this is asymmetric.

**This is a product question, not a clear bug.** The two reasonable resolutions
are:

1. Remove the source-side check from `edits.py` (treat edits like new messages —
   gating happens per-destination only).
2. Add a source-side check to `messages.py` (free senders can never broadcast).

Option 1 is the more permissive path and matches the apparent product intent
("free members can still sync their own messages"). Option 2 is the stricter
revenue path. **Skipping this fix until product confirms which is intended.**

**Risk if changed without confirmation:** Could either give away revenue (1)
or break free-tier messaging (2). Worth a deliberate decision rather than my
assumption.

---

## Fix order, summarized

| # | Bug | Files touched | Risk |
|---|-----|---------------|------|
| 1 | B-2 signature cache | `admin.py`, `callbacks.py` | None |
| 2 | B-3 reply in edits | `edits.py`, `messages.py`, new `services/replies.py` | Low |
| 3 | B-1 album mapping | `sender.py`, `distributor.py` | Medium |
| 4 | B-7 reply gate tighten | `messages.py`, `services/replies.py` | Low |
| 5 | B-6 flush warning | `media_group.py` | None |
| 6 | B-5 dead param | `dedup.py`, `messages.py` | None |
| – | B-4 premium gating | (deferred — product call) | – |

---

## End-to-end verification matrix

After all fixes land, walk through every sender → recipient combination:

```
Sender chat types:    private DM, group, supergroup, channel
Recipient chat types: private DM, group, supergroup, channel
Message types:        text, photo, video, animation, audio, document,
                      voice, video_note, sticker, album (homogeneous),
                      album (mixed-type — visuals + doc + animation)
```

Plus the cross-cutting features:

- Reply threading from bot-relayed message in any dest type
- Reply threading on an *edited* reply (B-3 path)
- Auto-forward channel→discussion-group secondary mapping (already correct)
- Anonymous admin attribution (already correct via normalizer)
- Self-message loop suppression (middleware drops correctly)

Verification approach: read each code path and confirm the data flow; py_compile
each touched file; spot-check the key invariants (paired send_log rows for
mixed albums; reply_source_* survives buffering).
