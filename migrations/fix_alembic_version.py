#!/usr/bin/env python
"""
Fix alembic_version table when it references a deleted or non-existent migration.

Usage:
    python migrations/fix_alembic_version.py
"""

import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.core.config import Config
from sqlalchemy import create_engine, text

def fix_alembic_version():
    database_url = Config.SQLALCHEMY_DATABASE_URI
    if not database_url:
        print("ERROR: No DATABASE_URL found")
        sys.exit(1)
    
    print(f"Connecting to database...")
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        # Check current version
        try:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            current_version = result.scalar()
            print(f"Current alembic_version: {current_version}")
        except Exception as e:
            print(f"ERROR reading alembic_version: {e}")
            sys.exit(1)
        
        # Get the latest migration file revision
        versions_dir = os.path.join(os.path.dirname(__file__), "versions")
        latest_revision = None
        
        for filename in os.listdir(versions_dir):
            if filename.endswith('.py') and not filename.startswith('__'):
                filepath = os.path.join(versions_dir, filename)
                with open(filepath, 'r') as f:
                    content = f.read()
                    # Look for revision = 'xxxxx'
                    import re
                    match = re.search(r"revision\s*=\s*['\"]([^'\"]+)['\"]", content)
                    if match:
                        revision = match.group(1)
                        if latest_revision is None or filename > latest_revision:
                            latest_revision = revision
        
        if not latest_revision:
            print("ERROR: Could not find any migration revisions")
            sys.exit(1)
        
        print(f"Latest migration revision: {latest_revision}")
        
        # Check if current version is valid
        if current_version == latest_revision:
            print("alembic_version is already correct!")
            return
        
        # Update to latest
        try:
            conn.execute(text("UPDATE alembic_version SET version_num = :rev"), {"rev": latest_revision})
            conn.commit()
            print(f"SUCCESS: Updated alembic_version to {latest_revision}")
        except Exception as e:
            print(f"ERROR updating alembic_version: {e}")
            sys.exit(1)

if __name__ == "__main__":
    fix_alembic_version()
