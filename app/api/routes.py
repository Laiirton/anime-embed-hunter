from flask import Blueprint, jsonify, request, make_response
import uuid
import gzip
import time
import logging
import os
from datetime import datetime, timezone
from functools import wraps

from app import limiter

bp = Blueprint("api", __name__)

# Request tracking
_request_start_times = {}

@bp.before_request
def before_request():
    """Store request start time and generate request ID."""
    _request_start_times[id(request)] = time.time()
    request.request_id = str(uuid.uuid4())[:8]

@bp.after_request
def after_request(response):
    """Add headers and timing headers."""
    # Add request ID header
    response.headers['X-Request-ID'] = getattr(request, 'request_id', '')

    # Add timing header
    start = _request_start_times.get(id(request), 0)
    if start:
        elapsed = (time.time() - start) * 1000  # ms
        response.headers['X-Response-Time'] = f"{elapsed:.1f}ms"

    # Add cache control header for API responses
    if request.path.startswith('/'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'

    return response

def gzip_compressed(func):
    """Decorator to enable GZIP compression for responses > 1KB."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        response = func(*args, **kwargs)
        # Only compress if response is large enough
        if isinstance(response, tuple):
            data = response[0]
        else:
            data = response.get_data()

        if isinstance(data, str):
            data = data.encode('utf-8')

        if len(data) > 1024:
            compressed = gzip.compress(data)
            if len(compressed) < len(data):
                response = make_response(compressed)
                response.headers['Content-Encoding'] = 'gzip'
                response.headers['Content-Type'] = 'application/json'
                return response
        return response
    return wrapper

@bp.route("/health")
@limiter.exempt
def health_check():
    """Health check endpoint for Render."""
    # Check DB connectivity
    db_status = "ok"
    try:
        from app.models.embed import db
        db.session.execute(db.text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {str(e)}"

    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "render_free_tier": os.getenv("RENDER", "false"),
        "database": db_status,
    }), 200

@bp.route("/stats")
@limiter.exempt
def stats():
    """Monitoring endpoint for stats."""
    try:
        from app.models.embed import db, Anime, Episode, EmbedRequest
        from flask import current_app

        # Count records
        animes = Anime.query.count()
        episodes = Episode.query.count()
        cache_entries = EmbedRequest.query.count()

        # Cache stats
        cache_type = current_app.config.get("CACHE_TYPE", "flask_caching.backends.simplecache.SimpleCache")
        cache_timeout = current_app.config.get("CACHE_DEFAULT_TIMEOUT", 300)

        return jsonify({
            "animes": animes,
            "episodes": episodes,
            "cache_entries": cache_entries,
            "cache_type": cache_type,
            "cache_timeout_seconds": cache_timeout,
            "uptime_seconds": round(time.time() - _request_start_times.get(id(request), time.time()), 1),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Import modularized routes to register them with the blueprint
from app.api import (
    catalog,
    anime,
    home,
    episode,
    user,
    search,
    embed,
    maintenance
)
