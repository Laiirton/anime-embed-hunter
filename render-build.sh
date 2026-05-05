#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Caminho persistente para os navegadores no Render
export PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/src/.cache/ms-playwright
playwright install chromium

# Run database migrations
flask db upgrade
