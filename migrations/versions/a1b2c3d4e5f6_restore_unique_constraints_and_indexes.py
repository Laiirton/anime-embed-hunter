"""Restore unique constraints and performance indexes

Revision ID: a1b2c3d4e5f6
Revises: 64eb3ba48bbb
Create Date: 2026-05-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = '64eb3ba48bbb'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET statement_timeout = '300s'")
    conn = op.get_bind()
    dialect = conn.dialect.name

    # ── Recriar UNIQUE constraints que foram dropados ──
    # Estes são necessários para ON CONFLICT (url) DO UPDATE funcionar

    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_animes_url' AND conrelid = 'animes'::regclass
            ) THEN
                ALTER TABLE animes ADD CONSTRAINT uq_animes_url UNIQUE (url);
            END IF;
        END
        $$;
    """))

    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_episodes_url' AND conrelid = 'episodes'::regclass
            ) THEN
                ALTER TABLE episodes ADD CONSTRAINT uq_episodes_url UNIQUE (url);
            END IF;
        END
        $$;
    """))

    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_embed_requests_url' AND conrelid = 'embed_requests'::regclass
            ) THEN
                ALTER TABLE embed_requests ADD CONSTRAINT uq_embed_requests_url UNIQUE (url);
            END IF;
        END
        $$;
    """))

    # ── Recriar GIN trigram index para buscas ILIKE ──
    if dialect == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        conn.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS ix_animes_name_trgm
            ON animes USING gin (name gin_trgm_ops);
        """))

    # ── Índices de performance ──
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_episodes_last_updated
        ON episodes (last_updated DESC);
    """))

    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_animes_last_scanned
        ON animes (last_scanned DESC);
    """))

    # ── Índices compostos para favorites/history ──
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_favorites_profile_updated
        ON favorites (profile_key, updated_at DESC);
    """))

    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_history_profile_last_seen
        ON history_entries (profile_key, last_seen DESC);
    """))

    op.execute("SET statement_timeout = '30s'")


def downgrade():
    op.execute("SET statement_timeout = '300s'")
    conn = op.get_bind()

    conn.execute(sa.text("DROP INDEX IF EXISTS ix_history_profile_last_seen;"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_favorites_profile_updated;"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_animes_last_scanned;"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_episodes_last_updated;"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_animes_name_trgm;"))

    conn.execute(sa.text("ALTER TABLE embed_requests DROP CONSTRAINT IF EXISTS uq_embed_requests_url;"))
    conn.execute(sa.text("ALTER TABLE episodes DROP CONSTRAINT IF EXISTS uq_episodes_url;"))
    conn.execute(sa.text("ALTER TABLE animes DROP CONSTRAINT IF EXISTS uq_animes_url;"))

    op.execute("SET statement_timeout = '30s'")
