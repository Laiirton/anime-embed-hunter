#!/usr/bin/env bash
# exit on error
set -o errexit

# Upgrade pip to ensure faster dependency resolution
python -m pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

# Use Render's persistent cache directory to avoid downloading Chromium every time
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.render/ms-playwright
playwright install chromium

# Fix alembic_version table if it references deleted migrations
echo "Checking and fixing alembic_version table..."
python migrations/fix_alembic_version.py || echo "Warning: Could not fix alembic_version, will try upgrade anyway"

# Run database migrations with increased statement timeout
echo "Running database migrations..."
export SQLALCHEMY_ENGINE_OPTIONS='{"connect_args": {"options": "-c statement_timeout=300000"}}'
flask db upgrade

echo "Build completed successfully!"
