"""Widen user_aliases.alias column and regenerate existing aliases.

Revision ID: 005
Create Date: 2026-02-15
"""

import secrets

import sqlalchemy as sa
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


# Inline word lists so the migration is self-contained.
_ADJECTIVES = (
    "amber", "ancient", "arctic", "ashen", "astral", "azure", "bitter",
    "blazing", "bold", "brave", "bright", "bronze", "calm", "cedar",
    "clear", "clever", "cobalt", "cold", "coral", "cosmic", "crimson",
    "crisp", "crystal", "dark", "dawn", "deep", "delta", "distant",
    "divine", "dream", "drift", "dusk", "dusty", "eager", "echo",
    "elder", "ember", "epic", "ever", "faded", "fair", "fallen",
    "feral", "fierce", "final", "flash", "fleet", "flint", "foggy",
    "forge", "free", "fresh", "frost", "frozen", "gentle", "ghost",
    "gilt", "glass", "gleam", "glow", "golden", "grand", "granite",
    "gray", "green", "grim", "harsh", "haze", "hidden", "hollow",
    "humble", "hushed", "icy", "idle", "iron", "ivory", "jade",
    "keen", "kind", "last", "lava", "light", "lime", "lone", "lost",
    "loud", "lucid", "lucky", "lunar", "magic", "major", "maple",
    "marble", "marsh", "meek", "mild", "mint", "misty", "molten",
    "moon", "moss", "muted", "narrow", "neon", "nimble", "noble",
    "north", "nova", "oak", "odd", "opal", "open", "outer", "pale",
    "peak", "pearl", "pine", "plain", "plum", "polar", "prime",
    "proud", "pure", "quick", "quiet", "rapid", "rare", "raven",
    "raw", "red", "rich", "risen", "rocky", "rogue", "rough",
    "royal", "ruby", "rune", "rush", "rust", "safe", "sage", "salt",
    "scarlet", "shade", "shadow", "sharp", "shell", "shy", "silk",
    "silver", "slate", "sleek", "slim", "slow", "smart", "smoky",
    "solar", "solid", "sonic", "south", "spare", "stark", "steady",
    "steel", "steep", "stern", "still", "stone", "storm", "stout",
    "strong", "subtle", "sunny", "super", "sure", "sweet", "swift",
    "tall", "tame", "teal", "thick", "thin", "thorn", "tidal",
    "tight", "timber", "tiny", "torn", "tough", "true", "twin",
    "ultra", "upper", "vast", "vivid", "void", "warm", "wary",
    "west", "white", "wide", "wild", "wise", "worn", "young", "zinc",
)

_NOUNS = (
    "ace", "anchor", "anvil", "apex", "arrow", "atlas", "badge",
    "beacon", "blade", "blaze", "bolt", "bow", "branch", "breeze",
    "bridge", "brook", "canyon", "cape", "cedar", "chain", "charm",
    "cinder", "claw", "cliff", "cloak", "cloud", "cobra", "comet",
    "compass", "core", "cove", "crane", "crest", "cross", "crown",
    "crystal", "current", "dagger", "dawn", "delta", "den", "dome",
    "dove", "dragon", "drift", "dune", "eagle", "echo", "edge",
    "elm", "ember", "envoy", "fable", "falcon", "fang", "fern",
    "field", "flare", "flame", "flock", "flux", "forge", "fort",
    "fox", "frost", "fury", "gale", "gate", "gaze", "gem", "ghost",
    "glacier", "glade", "glass", "glider", "globe", "gorge", "grove",
    "guard", "gust", "halo", "harbor", "harp", "haven", "hawk",
    "hearth", "helm", "herald", "heron", "hill", "hook", "horizon",
    "horn", "hound", "hunter", "isle", "ivy", "jade", "javelin",
    "jet", "jewel", "key", "kindle", "knight", "lake", "lance",
    "lantern", "lark", "leaf", "ledge", "light", "lily", "lion",
    "lodge", "loom", "lotus", "lynx", "mace", "mane", "mantle",
    "maple", "marsh", "mast", "meadow", "mesa", "mill", "mirror",
    "moat", "moon", "moth", "mound", "myth", "needle", "nest",
    "nexus", "node", "oak", "oar", "ocean", "onyx", "orbit",
    "otter", "owl", "palm", "panther", "path", "peak", "pearl",
    "pebble", "petal", "pier", "pike", "pine", "plume", "portal",
    "prism", "pulse", "quarry", "quartz", "quest", "quill", "rain",
    "ram", "range", "raptor", "raven", "ray", "reef", "ridge",
    "ring", "river", "robin", "rock", "rose", "rover", "rune",
    "sage", "sail", "scar", "scout", "seal", "seed", "shard",
    "shell", "shield", "shore", "silk", "skull", "sky", "slate",
    "slope", "smith", "snake", "snow", "spark", "spear", "sphinx",
    "spike", "spiral", "spirit", "spire", "spring", "spruce", "spur",
    "star", "steel", "stone", "storm", "stream", "sun", "surge",
    "sword", "talon", "thorn", "throne", "tide", "tiger", "timber",
    "torch", "tower", "trail", "trident", "tundra", "vale", "vapor",
    "vault", "veil", "venom", "vine", "viper", "vista", "voice",
    "vortex", "warden", "wave", "web", "wedge", "whale", "wind",
    "wing", "wolf", "wren", "zenith",
)


def _new_alias(existing: set[str]) -> str:
    """Generate a unique two-word alias not already in *existing*."""
    for _ in range(50):
        alias = f"{secrets.choice(_ADJECTIVES)}_{secrets.choice(_NOUNS)}"
        if alias not in existing:
            existing.add(alias)
            return alias
    # Fallback with numeric suffix
    alias = f"{secrets.choice(_ADJECTIVES)}_{secrets.randbelow(9999)}"
    existing.add(alias)
    return alias


def upgrade() -> None:
    # 1. Widen the column
    op.alter_column(
        "user_aliases", "alias",
        existing_type=sa.String(12),
        type_=sa.String(40),
        existing_nullable=False,
    )

    # 2. Regenerate all existing u-* aliases
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT user_id, alias FROM user_aliases WHERE alias LIKE 'u-%'")
    ).fetchall()

    if rows:
        taken: set[str] = set()
        # Pre-populate with non-u-* aliases that already exist
        existing_aliases = conn.execute(
            sa.text("SELECT alias FROM user_aliases WHERE alias NOT LIKE 'u-%'")
        ).fetchall()
        for (a,) in existing_aliases:
            taken.add(a)

        for user_id, _old_alias in rows:
            new = _new_alias(taken)
            conn.execute(
                sa.text(
                    "UPDATE user_aliases SET alias = :new WHERE user_id = :uid"
                ),
                {"new": new, "uid": user_id},
            )


def downgrade() -> None:
    # Shrink column back (will truncate long aliases)
    op.alter_column(
        "user_aliases", "alias",
        existing_type=sa.String(40),
        type_=sa.String(12),
        existing_nullable=False,
    )
