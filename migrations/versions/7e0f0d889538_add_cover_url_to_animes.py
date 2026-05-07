"""Add cover_url to animes

Revision ID: 7e0f0d889538
Revises: 9a2bbd8c1320
Create Date: 2026-05-05 04:37:23.949911

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '7e0f0d889538'
down_revision = '9a2bbd8c1320'
branch_labels = None
depends_on = None


def upgrade():
    # Set a longer statement timeout for Supabase
    op.execute("SET statement_timeout = '300s'")
    
    # For PostgreSQL, we don't need batch_alter_table
    # Adding a nullable column is fast in PostgreSQL
    conn = op.get_bind()
    
    # Check if cover_url column exists
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'animes' AND column_name = 'cover_url'
    """))
    if not result.first():
        op.add_column('animes', sa.Column('cover_url', sa.String(length=500), nullable=True))
    
    # Drop unique constraint on animes.url if it exists
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'animes_url_key' AND conrelid = 'animes'::regclass
            ) THEN
                ALTER TABLE animes DROP CONSTRAINT animes_url_key;
            END IF;
        END
        $$;
    """))
    
    # Drop GIN index if it exists
    conn.execute(sa.text("""
        DROP INDEX IF EXISTS ix_animes_name_trgm;
    """))
    
    # Drop unique constraint on embed_requests.url if it exists
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'embed_requests_url_key' AND conrelid = 'embed_requests'::regclass
            ) THEN
                ALTER TABLE embed_requests DROP CONSTRAINT embed_requests_url_key;
            END IF;
        END
        $$;
    """))
    
    # Drop unique constraint on episodes.url if it exists
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'episodes_url_key' AND conrelid = 'episodes'::regclass
            ) THEN
                ALTER TABLE episodes DROP CONSTRAINT episodes_url_key;
            END IF;
        END
        $$;
    """))
    
    # Drop index on episodes.anime_id if it exists
    conn.execute(sa.text("""
        DROP INDEX IF EXISTS ix_episodes_anime_id;
    """))
    
    # Reset statement timeout
    op.execute("SET statement_timeout = '30s'")


def downgrade():
    op.execute("SET statement_timeout = '300s'")
    
    conn = op.get_bind()
    
    # Recreate indexes and constraints
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_episodes_anime_id ON episodes(anime_id);
    """))
    
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'episodes_url_key' AND conrelid = 'episodes'::regclass
            ) THEN
                ALTER TABLE episodes ADD CONSTRAINT episodes_url_key UNIQUE (url);
            END IF;
        END
        $$;
    """))
    
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'embed_requests_url_key' AND conrelid = 'embed_requests'::regclass
            ) THEN
                ALTER TABLE embed_requests ADD CONSTRAINT embed_requests_url_key UNIQUE (url);
            END IF;
        END
        $$;
    """))
    
    # Recreate GIN index
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_animes_name_trgm ON animes USING gin (name gin_trgm_ops);
    """))
    
    conn.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint 
                WHERE conname = 'animes_url_key' AND conrelid = 'animes'::regclass
            ) THEN
                ALTER TABLE animes ADD CONSTRAINT animes_url_key UNIQUE (url);
            END IF;
        END
        $$;
    """))
    
    # Drop cover_url column if it exists
    conn.execute(sa.text("""
        ALTER TABLE animes DROP COLUMN IF EXISTS cover_url;
    """))
    
    op.execute("SET statement_timeout = '30s'")
