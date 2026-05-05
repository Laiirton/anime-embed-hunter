#!/usr/bin/env bash
# exit on error
set -o errexit

echo "--- Installing dependencies ---"
pip install -r requirements.txt

echo "--- Installing Playwright browsers ---"
# Install chromium only to save space/time
playwright install chromium

echo "--- Running migrations ---"
# Ensure FLASK_APP is set or use --app
# If DATABASE_URL is missing, it will use SQLite in the ephemeral instance/ folder
flask --app wsgi.py db upgrade

echo "--- Build finished ---"
