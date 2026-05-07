"""Add latest_episode_info and episode_info fields

Revision ID: 64eb3ba48bbb
Revises: 7e0f0d889538
Create Date: 2026-05-06 17:00:17.992666

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '64eb3ba48bbb'
down_revision = '7e0f0d889538'
branch_labels = None
depends_on = None


def upgrade():
    # Set longer statement timeout for Supabase
    op.execute("SET statement_timeout = '300s'")
    
    conn = op.get_bind()
    
    # Add column to animes - check if exists first
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'animes' AND column_name = 'latest_episode_info'
    """))
    if not result.first():
        op.add_column('animes', sa.Column('latest_episode_info', sa.String(length=100), nullable=True))
    
    # Add column to episodes - check if exists first
    result = conn.execute(sa.text("""
        SELECT column_name FROM information_schema.columns 
        WHERE table_name = 'episodes' AND column_name = 'info'
    """))
    if not result.first():
        op.add_column('episodes', sa.Column('info', sa.String(length=100), nullable=True))
    
    # Create index if not exists
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_episodes_anime_id ON episodes(anime_id);
    """))
    
    # Reset statement timeout
    op.execute("SET statement_timeout = '30s'")


def downgrade():
    conn = op.get_bind()
    
    # Drop index if exists
    conn.execute(sa.text("""
        DROP INDEX IF EXISTS idx_episodes_anime_id;
    """))
    
    # Drop column from episodes if exists
    conn.execute(sa.text("""
        ALTER TABLE episodes DROP COLUMN IF EXISTS info;
    """))
    
    # Drop column from animes if exists
    conn.execute(sa.text("""
        ALTER TABLE animes DROP COLUMN IF EXISTS latest_episode_info;
    """))
