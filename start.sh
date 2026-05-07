#!/bin/bash

# Start script for Render deployment
# Fixes: Worker restart logic, single worker to avoid memory issues

# Start RQ worker in background with automatic restart on failure
(
    while true; do
        echo "[$(date)] Starting RQ worker..."
        rq worker scraper-queue --url "$REDIS_URL"
        echo "[$(date)] Worker exited with code $?. Restarting in 5s..."
        sleep 5
    done
) &
WORKER_PID=$!

# Ensure worker is killed when this script receives SIGTERM
trap "kill $WORKER_PID 2>/dev/null; exit" SIGTERM SIGINT

# Start Gunicorn with single worker (Render free tier = 512MB RAM)
# Removed --threads to avoid Playwright event loop conflicts
gunicorn -b 0.0.0.0:$PORT --workers 1 --timeout 120 run:app

# Cleanup
kill $WORKER_PID 2>/dev/null
