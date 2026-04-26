# BotFather Configuration Guide

Open [@BotFather](https://t.me/BotFather) in Telegram and configure your bot with the values below.

The bot itself also pushes its command list and descriptions to Telegram on startup (see `bot/services/bot_profile.py`), so once the code is deployed the in-client UI stays in sync automatically. The values in this guide are what the bot will push and what you'd paste manually if you ever need to do it by hand.

---

## Bot Name

```
MediaHub
```

> Set via: `/setname` → select your bot → paste the name above

---

## Bot Username

```
MediaHubDistBot
```

> This is set during `/newbot`. If already created, usernames cannot be changed.
> If taken, try: `MediaHubRelayBot`, `MediaHubSyncBot`, or `YourBrandMediaHubBot`.

---

## Bot Description

> Set via: `/setdescription` → select your bot → paste the text below.
> The bot also calls `setMyDescription` on startup with the same copy.

```
I'm an intermediary between you and every chat you connect me to.

• Private chat with me: I relay everything — text, media, files — to your network and bring everything back.
• Groups I'm in: all media relayed; text only when it's part of a thread that started with one of my messages. Each group also gets its own readable tag so recipients see which group content came from.
• Channels: media-only relay.

Free for the first month. Premium adds Sync Control (pause direction per chat) and removes daily caps.
```

*Characters: ~492 / 512*

---

## Bot About Text

> Set via: `/setabouttext` → select your bot → paste the text below.
> The bot also calls `setMyShortDescription` on startup with this copy.

```
Cross-chat relay. Send media or text once, it lands in every chat you've connected. Originals, never forwards.
```

*Characters: ~117 / 120*

---

## Bot Commands

> Set via: `/setcommands` → select your bot → paste the block below exactly as-is.
> The bot also calls `setMyCommands` on startup with this list (public commands only — admin commands stay invisible in the BotFather menu).

### Public commands (shown in the in-client `/` picker)

```
start - Connect this chat / show the guide
help - What I can do and how to use me
selfsend - Echo your messages back to this chat
broadcast - Pause / resume sync for this chat
stats - Your activity in the network
subscribe - Go Premium — see the plans
plan - Check your current plan
stop - Disconnect this chat
```

### Admin-only commands (NOT pushed to BotFather, kept invisible)

These are recognised by the bot when run by a user whose ID is in `ADMIN_USER_IDS`, but they don't appear in the `/` picker. Documented here for operator reference:

```
status - Live dashboard
list - All connected chats
pause - Stop all syncing
resume - Resume syncing
edits - Handle edited messages across chats
signature - Add a signature line to outgoing messages
signatureurl - Set the signature as a clickable link
signatureoff - Remove the signature
remove - Disconnect a chat from the network
grant - Give a chat Premium access
revoke - Remove a chat's Premium
mute - Silence a user across all chats
unmute - Lift a mute
ban - Permanently block a user
unban - Unblock a user
whois - Look up who's behind a user alias
banchat - Block all messages from a source chat (added 2026-04-26)
unbanchat - Lift a chat-level ban (added 2026-04-26)
chatwhois - Look up which chat is behind an alias (added 2026-04-26)
```

---

## Bot Settings Checklist

| Setting | Command | Value |
|---|---|---|
| Name | `/setname` | `MediaHub` |
| Description | `/setdescription` | *(see above — auto-pushed on startup)* |
| About | `/setabouttext` | *(see above — auto-pushed on startup)* |
| Commands | `/setcommands` | *(public list above — auto-pushed on startup)* |
| Inline mode | `/setinline` | **Off** (not used) |
| Group privacy | `/setprivacy` | **Disabled** — bot must read all messages in groups |
| Join groups | `/setjoingroups` | **Enabled** |
| Payments | — | **No setup needed** — Telegram Stars works automatically (see below) |

> **Critical:** Group privacy **must be disabled.** With privacy on, the bot only receives commands and misses all media + reply-thread text — nothing gets synced.

---

## Behaviour Notes for Operators

These are the rules the bot enforces (so the BotFather copy and the runtime behaviour match):

- **Private chat with the bot** → relay all message types in both directions. No restrictions.
- **Group / supergroup** → all media (photo, video, animation, audio, document, voice, video note, sticker, albums) is always relayed. Plain text is relayed *only* if it belongs to a reply chain rooted in a bot-relayed message — casual member chatter is dropped silently to keep the network from drowning in noise.
- **Channel** → media only. Plain-text channel posts are not relayed.
- **Each group / channel** also gets its own two-word alias (e.g. `misty_grove`). Outbound messages show `user_alias @ chat_alias` for group sources, so recipients see *who said what, in which group*.
- **`/selfsend` and `/broadcast`** are read-only for everyone, but in groups / channels the *mutation* path requires Telegram chat-admin status. Private chats are always allowed to mutate.
- **Defaults on first contact:** `broadcast=on, selfsend=off`. Adding the bot to a new group never inherits the adder's personal toggle state.
- **`/banchat <chat_id|reply>`** — admin-only. Drops every future message from the named source chat at the very top of the pipeline. Useful for runaway groups.

---

## Telegram Stars Payment Setup

**No BotFather configuration needed.**

Telegram Stars is Telegram's built-in virtual currency, automatically available to every bot. The payment providers listed under `/mybots` → Payments (Portmone, Stripe, etc.) are for **fiat currency** only and are **not** used here.

The bot uses `currency="XTR"` in `send_invoice`. The full payment flow is:

1. User taps `/subscribe` → bot sends a Stars invoice
2. User pays from their Star balance (Telegram handles the UI)
3. Telegram sends `pre_checkout_query` → bot approves it
4. Telegram sends `successful_payment` → bot activates the subscription

**No integration work needed. Deploy and `/subscribe` works.**

---

## When to re-run BotFather setup manually

The bot's `sync_bot_profile` startup hook keeps the description / about / command list in sync with code on every deploy, so you generally don't need to touch BotFather after the first setup. Manual re-run is only needed if:

- You renamed the bot (`/setname`) — the bot can't change its own display name.
- You changed group privacy / inline / join-groups settings — those aren't manageable via the Bot API.
- You're rotating tokens or moving to a new bot account — that's a fresh `/newbot` flow.
