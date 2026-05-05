#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Run database migrations
flask db upgrade
