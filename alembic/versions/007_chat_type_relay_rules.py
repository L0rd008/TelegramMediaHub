"""Mark the per-chat-type relay rules + bot-rooted thread tracker cutover.

Revision ID: 007
Create Date: 2026-04-26

This migration is intentionally a no-op against PostgreSQL — the new behaviour
lives entirely in code and Redis — but it exists in the alembic chain so:

1. ``alembic history`` shows a clear timestamp marking when groups stopped
   relaying generic member text.
2. After ``alembic upgrade head`` the ``alembic_version`` row is the
   correlation point operators can quote when triaging "why did the bot
   suddenly stop relaying group chat".

== What changed in code (no DB action required) ==

bot/services/threads.py (NEW)
    Two-step lookup that decides if an incoming reply is part of a chain
    rooted in a bot-relayed message.  Backed by Redis Sets:

        thread:{chat_id}    SET<message_id>     TTL 24 h

    Refreshed on every membership add.  No manual cleanup.

bot/services/auth.py (NEW)
    ``caller_can_manage(message)`` — getChatMember-based admin gate for the
    /selfsend and /broadcast slash commands and their inline-button mirrors.

bot/handlers/messages.py
    Per-chat-type text gate:
      - private        → relay all (text + media), unchanged
      - group / sgrp   → relay all media; relay text only if reply chain is
                         rooted in a bot-relayed message
      - channel        → media only, no text

bot/handlers/edits.py
    Same gate applied to the edit redistribution path so edits cannot be used
    to smuggle text past the rule.

bot/handlers/start.py
    /start now branches into one of three onboarding bodies based on chat
    type (_onboarding_private / _onboarding_group / _onboarding_channel).

bot/handlers/callbacks.py
    cb_selfsend and cb_broadcast_toggle gained the same admin gate.

bot/services/bot_profile.py (NEW)
    Pushes the public command list, short description and long description
    to Telegram on startup so BotFather UI is always in sync with code.

== Why the no-op pattern ==

Rolling out a code change against an alembic chain that has SQL revisions
between deploys is fragile: in-flight services may run against a database
schema one migration ahead or behind their code.  Bumping the chain on
behaviour changes — even when no DDL fires — means

    git checkout <release-tag>
    alembic upgrade head

is the *single* deploy command, and the alembic_version row is a perfect
proxy for "which behaviour is live".

== Operator notes ==

After deploy, monitor for:

- Spike in ``Dropping group text msg ... (not in bot-rooted thread)`` debug
  logs.  Expected and benign — that's group chatter the bot is now
  correctly ignoring.
- Drop in cross-chat traffic volume from groups proportional to how chatty
  those groups were.
"""

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No SQL changes — see module docstring.
    pass


def downgrade() -> None:
    # No SQL changes to revert.  Code-side rollback would re-enable the
    # all-text-relay-from-groups behaviour, which is NOT recommended (returns
    # the network to noisy mirror-of-every-conversation state).
    pass
