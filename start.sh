#!/bin/bash

# Start script for Render deployment
# Memory-optimized for free tier (512MB RAM)

# Only start RQ worker if Redis is available
if [ -n "$REDIS_URL" ]; then
    echo "[$(date)] Redis available, starting RQ worker..."
    (
        while true; do
            echo "[$(date)] Starting RQ worker..."
            rq worker scraper-queue --url "$REDIS_URL" --with-scheduler
            echo "[$(date)] Worker exited with code $?. Restarting in 10s..."
            sleep 10
        done
    ) &
    WORKER_PID=$!
    trap "kill $WORKER_PID 2>/dev/null; exit" SIGTERM SIGINT
else
    echo "[$(date)] No REDIS_URL - RQ worker disabled"
fi

# Start Gunicorn with single worker (Render free tier = 512MB RAM)
# --timeout 120 to allow long scraping operations
# Use max-requests to restart worker periodically (prevents memory leaks)
gunicorn -b 0.0.0.0:$PORT \
    --workers 1 \
    --timeout 120 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --keep-alive 5 \
    run:app

# Cleanup
kill $WORKER_PID 2>/dev/null
