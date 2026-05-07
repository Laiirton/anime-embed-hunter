"""initial_schema

Revision ID: 83fe05b816ac
Revises: 
Create Date: 2026-05-04 03:15:32.938953

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "83fe05b816ac"
down_revision = None
branch_labels = None
depends_on = None


def _has_index(inspector, table_name, index_name):
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def upgrade():
    # Set longer statement timeout for Supabase
    op.execute("SET statement_timeout = '300s'")
    
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # Create animes table if not exists
    if "animes" not in tables:
        op.create_table(
            "animes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("url", sa.String(length=500), nullable=False),
            sa.Column("item_type", sa.String(length=50), nullable=True),
            sa.Column("last_scanned", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("url"),
        )

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "animes", "ix_animes_url"):
        op.create_index("ix_animes_url", "animes", ["url"], unique=True)

    # Create episodes table if not exists
    if "episodes" not in tables:
        op.create_table(
            "episodes",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("anime_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("url", sa.String(length=500), nullable=False),
            sa.Column("embed_url", sa.Text(), nullable=True),
            sa.Column("last_updated", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["anime_id"], ["animes.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("url"),
        )

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "episodes", "ix_episodes_url"):
        op.create_index("ix_episodes_url", "episodes", ["url"], unique=True)
    if not _has_index(inspector, "episodes", "ix_episodes_anime_id"):
        op.create_index("ix_episodes_anime_id", "episodes", ["anime_id"], unique=False)

    # Create or update embed_requests table
    if "embed_requests" not in tables:
        op.create_table(
            "embed_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("url", sa.String(length=500), nullable=False),
            sa.Column("response_data", sa.Text(), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("url"),
        )
    else:
        # Check if expires_at column exists and add if not
        columns = {c["name"] for c in inspector.get_columns("embed_requests")}
        if "expires_at" not in columns:
            op.add_column("embed_requests", sa.Column("expires_at", sa.DateTime(), nullable=True))
            op.execute(sa.text("UPDATE embed_requests SET expires_at = COALESCE(timestamp + interval '24 hours', CURRENT_TIMESTAMP + interval '24 hours')"))
            # Make it non-nullable after populating
            op.alter_column("embed_requests", "expires_at", existing_type=sa.DateTime(), nullable=False)

    inspector = sa.inspect(bind)
    if not _has_index(inspector, "embed_requests", "ix_embed_requests_url"):
        op.create_index("ix_embed_requests_url", "embed_requests", ["url"], unique=True)
    if not _has_index(inspector, "embed_requests", "ix_embed_requests_expires_at"):
        op.create_index("ix_embed_requests_expires_at", "embed_requests", ["expires_at"], unique=False)

    # Enable pg_trgm extension for PostgreSQL
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_animes_name_trgm "
            "ON animes USING gin (name gin_trgm_ops)"
        )
    
    # Reset statement timeout
    op.execute("SET statement_timeout = '30s'")


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_animes_name_trgm")

    if "embed_requests" in inspector.get_table_names():
        if _has_index(inspector, "embed_requests", "ix_embed_requests_expires_at"):
            op.drop_index("ix_embed_requests_expires_at", table_name="embed_requests")
        if _has_index(inspector, "embed_requests", "ix_embed_requests_url"):
            op.drop_index("ix_embed_requests_url", table_name="embed_requests")
        op.drop_table("embed_requests")

    inspector = sa.inspect(bind)
    if "episodes" in inspector.get_table_names():
        if _has_index(inspector, "episodes", "ix_episodes_anime_id"):
            op.drop_index("ix_episodes_anime_id", table_name="episodes")
        if _has_index(inspector, "episodes", "ix_episodes_url"):
            op.drop_index("ix_episodes_url", table_name="episodes")
        op.drop_table("episodes")

    inspector = sa.inspect(bind)
    if "animes" in inspector.get_table_names():
        if _has_index(inspector, "animes", "ix_animes_url"):
            op.drop_index("ix_animes_url", table_name="animes")
        op.drop_table("animes")
