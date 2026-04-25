"""Document the Redis dedup keyspace migration.

Revision ID: 006
Create Date: 2026-04-25

This migration is intentionally a no-op against PostgreSQL — the dedup engine
lives entirely in Redis — but it exists in the alembic chain so that:

1. Anyone walking ``alembic history`` sees a clear timestamp marking the cutover
   to the multi-layer, source-chat-scoped dedup model.
2. A single ``alembic upgrade head`` after pulling this branch leaves a record
   in the ``alembic_version`` table that the operator can correlate with the
   release notes when triaging "why is the bot suddenly relaying things that
   used to be deduped" reports.

== What changed in Redis (no DB action required) ==

OLD key namespaces (removed):

    dedup:{fingerprint}              — global, all chats, all senders
    dedup:mg:{media_group_id}        — global, ignored mg_id collisions

NEW key namespaces (introduced):

    dup:upd:{chat_id}:{message_id}   — webhook retry guard, 60 s TTL
    dup:c:{chat_id}:{fingerprint}    — per-chat content dedup, 24 h TTL
    dup:alb:{chat_id}:{album_hash}   — per-chat album dedup, 24 h TTL
    dup:mg:{chat_id}:{mg_id}         — per-chat mg_id seen marker, 24 h TTL

The old keys naturally expire on their existing 24 h TTL after deploy.  No
manual flush is required, but operators may run ``redis-cli --scan --pattern
'dedup:*' | xargs redis-cli del`` to clear them immediately.

== Why ==

The legacy global content dedup silently dropped roughly 95 % of legitimate
text traffic — every "ok", "thanks", and shared meme across the network was
suppressed after the first sighting in any chat.  Album re-uploads also leaked
through inconsistently because per-item dedup happened *before* buffering and
produced partial-album holes.  See ``docs/remediation-2026-04-25-dedup.md``
for the full root-cause writeup.
"""

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No SQL changes — the dedup model lives in Redis.  See module docstring
    # for the keyspace transition that accompanies this revision.
    pass


def downgrade() -> None:
    # No SQL changes to revert.  Code-side rollback would require reverting
    # ``bot/services/dedup.py`` to its pre-2026-04-25 state, which is NOT
    # recommended (regresses the cross-chat collision bug).
    pass
