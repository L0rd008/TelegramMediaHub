# BotFather Configuration Guide

Open [@BotFather](https://t.me/BotFather) in Telegram and configure your bot with the values below.

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
> If `MediaHubDistBot` is taken, try: `MediaHubRelayBot`, `MediaHubSyncBot`, or `YourBrandMediaHubBot`.

---

## Bot Description

> Set via: `/setdescription` → select your bot → paste the text below

```
I redistribute content across your Telegram chats — as original messages, never forwards.

✅ All media types: photos, videos, GIFs, audio, docs, stickers
✅ Albums stay intact
✅ Reply threading — replies follow conversations across chats
✅ Broadcast control — pause/resume what you send and receive
✅ Sender aliases — identify who sent what
✅ Moderation — mute and ban abusive senders
✅ Duplicate detection — no spam
✅ Privacy first — no forwarding metadata

Add me to any group or channel, and I'll keep every registered chat in sync — automatically.

Free 30-day trial. Premium via Telegram Stars ⭐
```

*Characters: ~497 / 512*

---

## Bot About Text

> Set via: `/setabouttext` → select your bot → paste the text below

```
Syncs content across your Telegram chats as original messages — with reply threading and moderation. Free trial, then ⭐ Premium.
```

*Characters: ~113 / 120*

---

## Bot Commands

> Set via: `/setcommands` → select your bot → paste the block below exactly as-is

```
start - Register this chat and show quick-action menu
stop - Unregister this chat (with confirmation)
selfsend - Toggle self-send or show toggle panel
broadcast - Control broadcasts or show broadcast panel
subscribe - View premium plans and subscribe
plan - Show subscription status with action buttons
status - Bot status with action buttons (admin)
list - Paginated chat list with navigation (admin)
signature - Set signature text (admin)
signatureurl - Set signature URL (admin)
signatureoff - Disable signature (admin)
pause - Pause distribution (admin)
resume - Resume distribution (admin)
edits - Set edit mode or show toggle panel (admin)
remove - Remove a chat (admin)
grant - Grant a subscription (admin)
revoke - Revoke a subscription (admin)
mute - Mute a user with preset durations (admin)
unmute - Unmute a user (admin)
ban - Ban a user with confirmation (admin)
unban - Unban a user (admin)
whois - Look up a user by alias (admin)
```

---

## Bot Settings Checklist

| Setting | Command | Value |
|---|---|---|
| Name | `/setname` | `MediaHub` |
| Description | `/setdescription` | *(see above)* |
| About | `/setabouttext` | *(see above)* |
| Commands | `/setcommands` | *(see above)* |
| Inline mode | `/setinline` | **Off** (not used) |
| Group privacy | `/setprivacy` | **Disabled** (bot must read all messages) |
| Join groups | `/setjoingroups` | **Enabled** |
| Payments | — | **No setup needed** — Telegram Stars works automatically (see below) |

> **Important:** Group privacy **must** be disabled so the bot can read messages in groups, otherwise it will only see commands.

---

## Telegram Stars Payment Setup

**No BotFather configuration needed.** Telegram Stars is Telegram's built-in virtual currency and is automatically available to every bot — there is no provider to connect.

The payment providers listed under `/mybots` → Payments (Portmone, Smart Glocal, etc.) are for **fiat currency** payments only and are **not** needed for Stars.

Our bot uses `currency="XTR"` in `send_invoice`, which tells Telegram to process the payment as Stars. Everything is handled natively:

1. Bot sends a Stars invoice → Telegram shows the payment UI
2. User pays with their Star balance
3. Telegram calls the `pre_checkout_query` → bot approves
4. Telegram confirms → bot receives `successful_payment` and activates the subscription

**You're all set — just deploy the bot and `/subscribe` will work.**
