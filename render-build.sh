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

# Run database migrations
flask db upgrade
