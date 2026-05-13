from flask import Blueprint, jsonify, request, make_response
import uuid
import gzip
import time
import logging
import os
import traceback
from datetime import datetime, timezone
from functools import wraps

from app import limiter

bp = Blueprint("api", __name__)

# Track actual application start time for uptime calculations
_APP_START_TIME = time.time()


@bp.errorhandler(400)
def bad_request(error):
    return jsonify({
        "error": str(error.description) if hasattr(error, 'description') and error.description else "Bad request",
        "code": 400,
        "request_id": getattr(request, 'request_id', ''),
    }), 400


@bp.errorhandler(404)
def not_found(error):
    return jsonify({
        "error": "Resource not found",
        "code": 404,
        "request_id": getattr(request, 'request_id', ''),
    }), 404


@bp.errorhandler(422)
def unprocessable(error):
    return jsonify({
        "error": str(error.description) if hasattr(error, 'description') and error.description else "Unprocessable entity",
        "code": 422,
        "request_id": getattr(request, 'request_id', ''),
    }), 422


@bp.errorhandler(429)
def ratelimit_exceeded(error):
    return jsonify({
        "error": "Rate limit exceeded. Try again later.",
        "code": 429,
        "request_id": getattr(request, 'request_id', ''),
    }), 429


@bp.errorhandler(500)
def internal_server_error(error):
    """Handle internal server errors with detailed server-side logging."""
    request_id = getattr(request, 'request_id', 'unknown')
    exc_type, exc_value, tb = None, None, None
    
    try:
        import sys
        exc_info = sys.exc_info()
        exc_type = exc_info[0]
        exc_value = exc_info[1]
        tb = exc_info[2]
    except Exception:
        pass
    
    log = logging.getLogger(__name__)
    log.error(
        "Internal server error [request_id=%s, path=%s, method=%s, ip=%s, user_agent=%s]: %s: %s\n%s",
        request_id,
        request.path,
        request.method,
        request.remote_addr,
        request.headers.get('User-Agent', 'unknown'),
        type(exc_value).__name__ if exc_value else type(error).__name__,
        str(exc_value or error),
        ''.join(traceback.format_tb(tb)) if tb else traceback.format_exc(),
        exc_info=False,  # avoid double traceback since we formatted it above
    )
    
    return jsonify({
        "error": "Internal server error",
        "code": 500,
        "request_id": request_id,
    }), 500


# Request tracking
_request_start_times = {}

# Endpoints that should never be cached (mutations, sensitive data, or embed fetching)
_NO_CACHE_PATHS = ('/favorites', '/history', '/get-embed', '/reload-config', '/maintenance/cleanup-cache')

# Endpoints that benefit from aggressive caching
_CACHEABLE_PATH_PREFIXES = ('/animes', '/search', '/anime/', '/lancamentos', '/home/featured')


@bp.before_request
def before_request():
    """Store request start time and generate request ID."""
    _request_start_times[id(request)] = time.time()
    request.request_id = str(uuid.uuid4())[:8]


@bp.after_request
def after_request(response):
    """Add headers, timing, and smart Cache-Control headers."""
    # Add request ID header
    response.headers['X-Request-ID'] = getattr(request, 'request_id', '')

    # Add timing header
    start = _request_start_times.get(id(request), 0)
    if start:
        elapsed = (time.time() - start) * 1000  # ms
        response.headers['X-Response-Time'] = f"{elapsed:.1f}ms"

    # Smart Cache-Control: no-cache for sensitive/mutation endpoints,
    # aggressive caching for read-only catalog endpoints
    path = request.path
    
    if any(path.startswith(prefix) for prefix in _NO_CACHE_PATHS):
        # Security-sensitive or mutation endpoints: never cache
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    elif any(path.startswith(prefix) for prefix in _CACHEABLE_PATH_PREFIXES):
        # Read-only catalog endpoints: cache aggressively with stale-while-revalidate
        response.headers['Cache-Control'] = 'public, max-age=60, stale-while-revalidate=300'
    # For all other paths (health, stats, docs, etc.) leave cache headers untouched
    
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

    # Check Redis connectivity
    redis_status = "ok"
    try:
        from app import get_redis_conn
        get_redis_conn().ping()
    except Exception as e:
        redis_status = f"error: {str(e)}"

    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "render_free_tier": os.getenv("RENDER", "false"),
        "database": db_status,
        "redis": redis_status,
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

        # Uptime is calculated from app start time, not request start time
        uptime = time.time() - _APP_START_TIME

        return jsonify({
            "animes": animes,
            "episodes": episodes,
            "cache_entries": cache_entries,
            "cache_type": cache_type,
            "cache_timeout_seconds": cache_timeout,
            "uptime_seconds": round(uptime, 1),
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/docs")
@limiter.exempt
def api_docs():
    """API documentation endpoint listing all available endpoints."""
    return jsonify({
        "api_name": "Anime Embed Hunter API",
        "version": "1.0.0",
        "endpoints": [
            {
                "path": "/health",
                "method": "GET",
                "description": "Health check endpoint for monitoring. Reports database and Redis connectivity status.",
                "auth": "None",
                "rate_limit": "Exempt",
                "example": "GET /health",
                "response_example": {"status": "ok", "timestamp": "2026-01-01T00:00:00+00:00", "database": "ok", "redis": "ok"},
            },
            {
                "path": "/stats",
                "method": "GET",
                "description": "Monitoring stats including record counts, cache configuration, and application uptime.",
                "auth": "None",
                "rate_limit": "Exempt",
                "example": "GET /stats",
                "response_example": {"animes": 1000, "episodes": 5000, "cache_entries": 200, "cache_type": "redis", "cache_timeout_seconds": 300, "uptime_seconds": 3600.5},
            },
            {
                "path": "/docs",
                "method": "GET",
                "description": "This documentation endpoint. Returns a list of all available API endpoints with details.",
                "auth": "None",
                "rate_limit": "Exempt",
                "example": "GET /docs",
            },
            {
                "path": "/animes",
                "method": "GET",
                "description": "Browse the anime catalog. Supports pagination, filters (genre, studio, status, season), and sorting.",
                "auth": "None",
                "rate_limit": "120/min",
                "example": "GET /animes?page=1&per_page=20&sort=updated_at&order=desc&status=airing",
                "query_params": ["page", "per_page", "sort", "order", "genre", "studio", "status", "season", "year"],
            },
            {
                "path": "/animes/search",
                "method": "GET",
                "description": "Full-text search across anime titles, titles alternatives, and metadata.",
                "auth": "None",
                "rate_limit": "120/min",
                "example": "GET /animes/search?q=naruto&limit=10",
                "query_params": ["q", "limit"],
            },
            {
                "path": "/anime/<slug>",
                "method": "GET",
                "description": "Get detailed information about a specific anime by its slug, including episodes and metadata.",
                "auth": "None",
                "rate_limit": "120/min",
                "example": "GET /anime/one-piece",
            },
            {
                "path": "/anime/full",
                "method": "GET",
                "description": "Get full anime details with complete episode list and all associated data.",
                "auth": "None",
                "rate_limit": "120/min",
                "example": "GET /anime/full?slug=naruto",
                "query_params": ["slug"],
            },
            {
                "path": "/search",
                "method": "GET",
                "description": "General search endpoint that searches across anime and episodes. Broader scope than /animes/search.",
                "auth": "None",
                "rate_limit": "120/min",
                "example": "GET /search?q=attack%20on%20titan",
                "query_params": ["q"],
            },
            {
                "path": "/episode/<episode_id>/players",
                "method": "GET",
                "description": "Get all available video players/sources for a specific episode.",
                "auth": "None",
                "rate_limit": "120/min",
                "example": "GET /episode/12345/players",
            },
            {
                "path": "/lancamentos",
                "method": "GET",
                "description": "Get recent anime releases/newly added anime episodes.",
                "auth": "None",
                "rate_limit": "120/min",
                "example": "GET /lancamentos?page=1",
                "query_params": ["page", "limit"],
            },
            {
                "path": "/home/featured",
                "method": "GET",
                "description": "Get featured anime for the home page — curated selection of popular or trending anime.",
                "auth": "None",
                "rate_limit": "120/min",
                "example": "GET /home/featured",
            },
            {
                "path": "/get-embed",
                "method": "GET",
                "description": "Fetch a video embed URL for a specific episode. Resolves the streaming source and returns embed data.",
                "auth": "None",
                "rate_limit": "60/min",
                "example": "GET /get-embed?episode_id=12345&mirror=1",
                "query_params": ["episode_id", "mirror"],
            },
            {
                "path": "/favorites",
                "method": "GET",
                "description": "List the authenticated user's favorite anime. Requires X-API-Key header.",
                "auth": "X-API-Key required",
                "rate_limit": "120/min",
                "example": "GET /favorites?page=1&per_page=20",
                "headers": ["X-API-Key"],
                "query_params": ["page", "per_page"],
            },
            {
                "path": "/favorites",
                "method": "POST",
                "description": "Add an anime to favorites. Requires X-API-Key header and JSON body.",
                "auth": "X-API-Key required",
                "rate_limit": "120/min",
                "example": "POST /favorites",
                "headers": ["X-API-Key"],
                "body_example": {"anime_id": 42, "anime_slug": "one-piece"},
            },
            {
                "path": "/favorites/<fav_id>",
                "method": "DELETE",
                "description": "Remove a favorite by its ID. Requires X-API-Key header.",
                "auth": "X-API-Key required",
                "rate_limit": "120/min",
                "example": "DELETE /favorites/7",
                "headers": ["X-API-Key"],
            },
            {
                "path": "/history",
                "method": "GET",
                "description": "List the authenticated user's watch history. Requires X-API-Key header.",
                "auth": "X-API-Key required",
                "rate_limit": "120/min",
                "example": "GET /history?limit=20",
                "headers": ["X-API-Key"],
                "query_params": ["limit"],
            },
            {
                "path": "/history",
                "method": "POST",
                "description": "Record a watch history entry. Requires X-API-Key header and JSON body.",
                "auth": "X-API-Key required",
                "rate_limit": "120/min",
                "example": "POST /history",
                "headers": ["X-API-Key"],
                "body_example": {"anime_id": 42, "anime_slug": "one-piece", "episode_id": 101, "episode_number": 5},
            },
            {
                "path": "/history/<hist_id>",
                "method": "DELETE",
                "description": "Remove a history entry by its ID. Requires X-API-Key header.",
                "auth": "X-API-Key required",
                "rate_limit": "120/min",
                "example": "DELETE /history/3",
                "headers": ["X-API-Key"],
            },
            {
                "path": "/reload-config",
                "method": "POST",
                "description": "Reload application configuration at runtime. Protected by an API key.",
                "auth": "API key required",
                "rate_limit": "Exempt",
                "example": "POST /reload-config",
            },
            {
                "path": "/maintenance/cleanup-cache",
                "method": "POST",
                "description": "Trigger cache cleanup for maintenance. Clears stale cache entries.",
                "auth": "API key required",
                "rate_limit": "Exempt",
                "example": "POST /maintenance/cleanup-cache",
            },
        ],
    }), 200


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
