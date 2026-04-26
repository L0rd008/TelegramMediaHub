"""Mark the subscription/value-prop messaging refresh cutover.

Revision ID: 009
Create Date: 2026-04-26

This revision is intentionally a no-op against PostgreSQL. The change set
lives in code and Redis, not in relational schema, but it materially changes
how chats experience the trial/premium model:

1. Free-vs-premium copy is centralized in ``bot.services.value_prop`` so
   onboarding, /help, /plan, trial reminders, and daily nudges all describe
   the same product delta.
2. Trial reminders gain a distinct day-of-expiry message (``days_left == 0``)
   sent the first day a chat lands on free access.
3. The paywall nudge and /plan copy now describe the exact behavior change:
   outbound and /selfsend stay free, while Premium re-opens cross-chat inbound.

Why keep this in Alembic?
-------------------------

Even though no SQL runs, operators still need a concrete rollout marker:

- ``alembic history`` shows when subscription messaging semantics changed.
- ``alembic upgrade head`` remains the single deploy command after pulling a
  release, even for behavior-only changes.
- The ``alembic_version`` row becomes the fastest answer to "which reminder /
  value-prop copy is live on this server?" during support triage.

Operational notes
-----------------

- No Redis flush is required.
- Existing reminder dedup keys remain valid.
- The new day-of-expiry reminder uses the ``trial_remind:{chat_id}:0`` suffix
  so it cannot collide with the T-1 / T-3 / T-7 heads-up reminders.
"""

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No SQL changes — see module docstring.
    pass


def downgrade() -> None:
    # No SQL changes to revert. Rolling back would require restoring the
    # previous copy distribution across the subscription surfaces in code.
    pass
