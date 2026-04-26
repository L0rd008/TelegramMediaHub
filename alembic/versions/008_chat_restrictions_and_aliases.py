"""Add chat_restrictions and chat_aliases tables; backfill aliases.

Revision ID: 008
Create Date: 2026-04-26

This migration introduces two new persistent stores:

- ``chat_restrictions`` — mirrors the existing ``user_restrictions`` shape
  but keyed on ``chat_id``.  Backs the new ``/banchat`` and ``/unbanchat``
  admin commands.  ``ban`` is the only active type today; ``mute`` is
  reserved for future expansion.

- ``chat_aliases`` — mirrors ``user_aliases`` but for chats.  Every
  group / supergroup / channel the bot relays from gets a stable two-word
  alias displayed alongside the user alias on outbound messages, so
  recipients see *who* in *which group* the content came from.

Backfill: at upgrade time we generate aliases for every existing
group / supergroup / channel in the ``chats`` table so the moment the new
distributor code paths go live, attribution is consistent across old and
new traffic.  Aliases are guaranteed unique across both alias tables.

Downgrade is supported but lossy: dropping ``chat_aliases`` means new
aliases will be regenerated on next contact, and any operator notes
referencing old aliases become orphaned.
"""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


# Inline word lists so the migration is self-contained even if
# ``bot.services.alias_words`` moves later.  Kept short here on purpose —
# combinatorics give >5,000 unique pairs which covers every realistic
# install at upgrade time, and the runtime generator (which has the full
# lists) will produce richer aliases for future chats.
_ADJECTIVES = (
    "amber", "ancient", "arctic", "ashen", "azure", "blazing", "bold",
    "bright", "calm", "clear", "cobalt", "coral", "cosmic", "crimson",
    "crystal", "dawn", "deep", "distant", "dream", "dusky", "ember",
    "epic", "fair", "fierce", "flint", "frost", "gentle", "gilt",
    "glass", "golden", "grand", "gray", "harsh", "hidden", "hollow",
    "icy", "iron", "ivory", "jade", "keen", "kind", "lava", "light",
    "lone", "lucid", "lunar", "magic", "marble", "misty", "molten",
    "noble", "opal", "pale", "pearl", "pine", "polar", "prime",
    "quiet", "raven", "rocky", "royal", "ruby", "rune", "rust",
    "sage", "scarlet", "shadow", "sharp", "silk", "silver", "slate",
    "sleek", "smoky", "solar", "solid", "sonic", "starlit", "steady",
    "steel", "stern", "still", "stone", "storm", "subtle", "sunny",
    "swift", "teal", "thin", "thorn", "tidal", "timber", "tiny",
    "true", "twin", "ultra", "vast", "vivid", "void", "warm", "wary",
    "white", "wide", "wild", "wise",
)

_NOUNS = (
    "ace", "anchor", "anvil", "apex", "arrow", "atlas", "badge",
    "beacon", "blade", "blaze", "bolt", "branch", "breeze", "bridge",
    "brook", "canyon", "cape", "cedar", "chain", "charm", "cliff",
    "cloak", "cloud", "comet", "compass", "core", "cove", "crest",
    "crown", "current", "dagger", "delta", "dome", "dragon", "drift",
    "dune", "eagle", "echo", "edge", "elm", "ember", "fable", "falcon",
    "fang", "fern", "field", "flare", "flame", "flock", "forge", "fort",
    "fox", "frost", "gale", "gate", "gem", "ghost", "glacier", "glade",
    "globe", "gorge", "grove", "harbor", "harp", "haven", "hawk",
    "hearth", "helm", "horizon", "horn", "hound", "isle", "ivy",
    "javelin", "jewel", "key", "knight", "lake", "lance", "lantern",
    "lark", "leaf", "ledge", "light", "lily", "lion", "lodge", "loom",
    "lotus", "lynx", "mace", "mantle", "maple", "marsh", "mast",
    "meadow", "mesa", "mill", "mirror", "moat", "moon", "moth",
    "mound", "needle", "nest", "nexus", "node", "oak", "ocean",
    "onyx", "orbit", "otter", "owl", "panther", "path", "peak",
    "pearl", "pebble", "petal", "pier", "pike", "pine", "plume",
    "portal", "prism", "pulse", "quarry", "quartz", "quill", "rain",
    "ram", "range", "raptor", "raven", "ray", "reef", "ridge", "ring",
    "river", "robin", "rock", "rose", "rover", "rune", "sage", "sail",
    "scout", "seal", "shard", "shell", "shield", "shore", "silk",
    "sky", "slate", "slope", "smith", "snake", "snow", "spark",
    "spear", "spike", "spire", "spring", "spruce", "star", "steel",
    "stone", "storm", "stream", "sun", "surge", "sword", "talon",
    "thorn", "tide", "tiger", "timber", "torch", "tower", "trail",
    "trident", "tundra", "vale", "vault", "veil", "vine", "vista",
    "voice", "vortex", "warden", "wave", "wedge", "whale", "wind",
    "wing", "wolf", "wren", "zenith",
)


def _new_alias(taken: set[str]) -> str:
    """Generate a unique two-word alias not already in *taken*."""
    for _ in range(50):
        alias = f"{secrets.choice(_ADJECTIVES)}_{secrets.choice(_NOUNS)}"
        if alias not in taken:
            taken.add(alias)
            return alias
    fallback = f"{secrets.choice(_ADJECTIVES)}_{secrets.randbelow(99999)}"
    taken.add(fallback)
    return fallback


def upgrade() -> None:
    # 1. chat_restrictions ─────────────────────────────────────────────
    op.create_table(
        "chat_restrictions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("restriction_type", sa.String(length=10), nullable=False),
        sa.Column("restricted_by", sa.BigInteger, nullable=False),
        sa.Column(
            "restricted_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index(
        "idx_chat_restriction_chat_type",
        "chat_restrictions",
        ["chat_id", "restriction_type", "active"],
    )

    # 2. chat_aliases ──────────────────────────────────────────────────
    op.create_table(
        "chat_aliases",
        sa.Column("chat_id", sa.BigInteger, primary_key=True),
        sa.Column("alias", sa.String(length=40), unique=True, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 3. Backfill aliases for every existing group/supergroup/channel ──
    conn = op.get_bind()
    chat_rows = conn.execute(
        sa.text(
            "SELECT chat_id FROM chats "
            "WHERE chat_type IN ('group', 'supergroup', 'channel')"
        )
    ).fetchall()

    if chat_rows:
        # Pre-populate ``taken`` with every alias already in either table so
        # we never collide with a user alias.
        taken: set[str] = set()
        existing_user = conn.execute(
            sa.text("SELECT alias FROM user_aliases")
        ).fetchall()
        for (a,) in existing_user:
            taken.add(a)

        for (chat_id,) in chat_rows:
            alias = _new_alias(taken)
            conn.execute(
                sa.text(
                    "INSERT INTO chat_aliases (chat_id, alias) "
                    "VALUES (:cid, :alias) "
                    "ON CONFLICT (chat_id) DO NOTHING"
                ),
                {"cid": chat_id, "alias": alias},
            )


def downgrade() -> None:
    # Lossy: any operator notes that referenced old chat aliases will go
    # stale.  Aliases regenerate on first contact after downgrade so the
    # bot keeps working — they just won't match what was previously shown.
    op.drop_table("chat_aliases")
    op.drop_index(
        "idx_chat_restriction_chat_type",
        table_name="chat_restrictions",
    )
    op.drop_table("chat_restrictions")
