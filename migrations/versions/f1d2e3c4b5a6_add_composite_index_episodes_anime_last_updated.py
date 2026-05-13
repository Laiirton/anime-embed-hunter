"""Add composite index on episodes (anime_id, last_updated DESC)

Revision ID: f1d2e3c4b5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-05-12 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'f1d2e3c4b5a6'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET statement_timeout = '300s'")
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect == "postgresql":
        conn.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS ix_episodes_anime_last_updated
            ON episodes (anime_id, last_updated DESC);
        """))
    elif dialect == "sqlite":
        conn.execute(sa.text("""
            CREATE INDEX IF NOT EXISTS ix_episodes_anime_last_updated
            ON episodes (anime_id, last_updated DESC);
        """))

    op.execute("SET statement_timeout = '30s'")


def downgrade():
    op.execute("SET statement_timeout = '300s'")
    conn = op.get_bind()

    conn.execute(sa.text("DROP INDEX IF EXISTS ix_episodes_anime_last_updated;"))

    op.execute("SET statement_timeout = '30s'")
