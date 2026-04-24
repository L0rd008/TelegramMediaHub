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
> If taken, try: `MediaHubRelayBot`, `MediaHubSyncBot`, or `YourBrandMediaHubBot`.

---

## Bot Description

> Set via: `/setdescription` → select your bot → paste the text below

```
You post in one chat. Your audience is in five.

MediaHub syncs your messages across all your connected Telegram chats — automatically, with zero forwarding tags. Photos, videos, voice notes, albums — everything arrives as an original message, not a forward.

Reply in any chat and it threads correctly everywhere else.

Built for channel + group combos, multi-community networks, and anyone tired of copy-pasting the same content into five different chats.

30 days free. Then about 1 star per hour.
```

*Characters: ~468 / 512*

---

## Bot About Text

> Set via: `/setabouttext` → select your bot → paste the text below

```
Post once. Shows up in all your chats — not as a forward, as if you sent it there. 30 days free.
```

*Characters: ~97 / 120*

---

## Bot Commands

> Set via: `/setcommands` → select your bot → paste the block below exactly as-is

```
start - Connect this chat to your network
stop - Disconnect and stop syncing
selfsend - Echo your own messages back to this chat
broadcast - Control what this chat sends and receives
subscribe - See plans and go Premium
plan - Your current plan and days remaining
stats - Message activity for this chat
help - How it works + all commands
status - Live dashboard (admin)
list - All connected chats (admin)
pause - Stop all syncing (admin)
resume - Resume syncing (admin)
edits - Handle edited messages across chats (admin)
signature - Add a signature line to outgoing messages (admin)
signatureurl - Set the signature as a clickable link (admin)
signatureoff - Remove the signature (admin)
remove - Disconnect a chat from the network (admin)
grant - Give a chat Premium access (admin)
revoke - Remove a chat's Premium (admin)
mute - Silence a user across all chats (admin)
unmute - Lift a mute (admin)
ban - Permanently block a user (admin)
unban - Unblock a user (admin)
whois - Look up who's behind an alias (admin)
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
| Group privacy | `/setprivacy` | **Disabled** — bot must read all messages in groups |
| Join groups | `/setjoingroups` | **Enabled** |
| Payments | — | **No setup needed** — Telegram Stars works automatically (see below) |

> **Critical:** Group privacy **must be disabled.** With privacy on, the bot only receives commands and misses all content — nothing gets synced.

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
