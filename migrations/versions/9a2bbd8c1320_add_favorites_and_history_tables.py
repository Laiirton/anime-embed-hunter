"""add_favorites_and_history_tables

Revision ID: 9a2bbd8c1320
Revises: 83fe05b816ac
Create Date: 2026-05-04 05:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9a2bbd8c1320"
down_revision = "83fe05b816ac"
branch_labels = None
depends_on = None


def _has_index(inspector, table_name, index_name):
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "favorites" not in tables:
        op.create_table(
            "favorites",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("profile_key", sa.String(length=120), nullable=False),
            sa.Column("anime_id", sa.Integer(), nullable=True),
            sa.Column("anime_name", sa.String(length=255), nullable=False),
            sa.Column("anime_url", sa.String(length=500), nullable=False),
            sa.Column("image_url", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["anime_id"], ["animes.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("profile_key", "anime_url", name="uq_favorites_profile_anime_url"),
        )

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "favorites", "ix_favorites_profile_key"):
        op.create_index("ix_favorites_profile_key", "favorites", ["profile_key"], unique=False)
    if not _has_index(inspector, "favorites", "ix_favorites_anime_id"):
        op.create_index("ix_favorites_anime_id", "favorites", ["anime_id"], unique=False)
    if not _has_index(inspector, "favorites", "ix_favorites_updated_at"):
        op.create_index("ix_favorites_updated_at", "favorites", ["updated_at"], unique=False)

    if "history_entries" not in tables:
        op.create_table(
            "history_entries",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("profile_key", sa.String(length=120), nullable=False),
            sa.Column("anime_id", sa.Integer(), nullable=True),
            sa.Column("episode_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("content_url", sa.String(length=500), nullable=False),
            sa.Column("image_url", sa.Text(), nullable=True),
            sa.Column("watch_count", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["anime_id"], ["animes.id"]),
            sa.ForeignKeyConstraint(["episode_id"], ["episodes.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("profile_key", "content_url", name="uq_history_profile_content_url"),
        )

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "history_entries", "ix_history_entries_profile_key"):
        op.create_index(
            "ix_history_entries_profile_key",
            "history_entries",
            ["profile_key"],
            unique=False,
        )
    if not _has_index(inspector, "history_entries", "ix_history_entries_anime_id"):
        op.create_index("ix_history_entries_anime_id", "history_entries", ["anime_id"], unique=False)
    if not _has_index(inspector, "history_entries", "ix_history_entries_episode_id"):
        op.create_index(
            "ix_history_entries_episode_id",
            "history_entries",
            ["episode_id"],
            unique=False,
        )
    if not _has_index(inspector, "history_entries", "ix_history_entries_last_seen"):
        op.create_index(
            "ix_history_entries_last_seen",
            "history_entries",
            ["last_seen"],
            unique=False,
        )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "history_entries" in inspector.get_table_names():
        if _has_index(inspector, "history_entries", "ix_history_entries_last_seen"):
            op.drop_index("ix_history_entries_last_seen", table_name="history_entries")
        if _has_index(inspector, "history_entries", "ix_history_entries_episode_id"):
            op.drop_index("ix_history_entries_episode_id", table_name="history_entries")
        if _has_index(inspector, "history_entries", "ix_history_entries_anime_id"):
            op.drop_index("ix_history_entries_anime_id", table_name="history_entries")
        if _has_index(inspector, "history_entries", "ix_history_entries_profile_key"):
            op.drop_index("ix_history_entries_profile_key", table_name="history_entries")
        op.drop_table("history_entries")

    inspector = sa.inspect(bind)
    if "favorites" in inspector.get_table_names():
        if _has_index(inspector, "favorites", "ix_favorites_updated_at"):
            op.drop_index("ix_favorites_updated_at", table_name="favorites")
        if _has_index(inspector, "favorites", "ix_favorites_anime_id"):
            op.drop_index("ix_favorites_anime_id", table_name="favorites")
        if _has_index(inspector, "favorites", "ix_favorites_profile_key"):
            op.drop_index("ix_favorites_profile_key", table_name="favorites")
        op.drop_table("favorites")
