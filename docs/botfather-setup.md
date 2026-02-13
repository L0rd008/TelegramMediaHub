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
Post once. It shows up everywhere — as if you sent it yourself.

I sync your messages across all your Telegram chats, groups, and channels. Photos, videos, documents, stickers — everything arrives as an original message, not a forward.

What makes me different:
— Your content looks native in every chat
— Replies stay threaded across conversations
— You control what goes where
— Private — no one sees forwarding tags
— Works in groups, channels, and private chats

Just add me and tap /start. You get full access free for 30 days.
```

*Characters: ~451 / 512*

---

## Bot About Text

> Set via: `/setabouttext` → select your bot → paste the text below

```
Sync messages across all your chats — they arrive as originals, not forwards. Try free for 30 days.
```

*Characters: ~99 / 120*

---

## Bot Commands

> Set via: `/setcommands` → select your bot → paste the block below exactly as-is

```
start - Start syncing this chat
stop - Stop syncing this chat
selfsend - See your own messages echoed back
broadcast - Control what you send and receive
subscribe - Go Premium
plan - Check your current plan
stats - See how your chat is doing
status - Dashboard (admin)
list - Browse connected chats (admin)
pause - Pause all syncing (admin)
resume - Resume syncing (admin)
edits - Handle edited messages (admin)
signature - Set a signature line (admin)
signatureurl - Set a signature link (admin)
signatureoff - Remove signature (admin)
remove - Disconnect a chat (admin)
grant - Give someone Premium (admin)
revoke - Remove someone's Premium (admin)
mute - Temporarily silence a user (admin)
unmute - Unsilence a user (admin)
ban - Permanently block a user (admin)
unban - Unblock a user (admin)
whois - Look up who sent something (admin)
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
