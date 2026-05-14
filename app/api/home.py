import logging
import time
from flask import jsonify, request, current_app
from app import limiter, get_scraper_queue
from app.services.site_manager import site_manager
from app.services.unified_cache import (
    volatile_get,
    volatile_set,
    persistent_get,
    persistent_set,
)
from app.utils.helpers import clean_name, extract_audio_type, format_info
from app.api.routes import bp
from app.api.utils import (
    _build_home_featured_cache_key,
    _build_url,
    _utcnow,
    check_api_key,
)
from app.api.validators import HomeFeaturedRequest

logger = logging.getLogger(__name__)

_DEFAULT_HOME_PATH = "/home"


def _default_home_url():
    return _build_url(_DEFAULT_HOME_PATH)


def _is_payload_stale(payload, ttl_seconds):
    """Check if cached payload is beyond 80% of its TTL based on cached_at timestamp."""
    cached_at = payload.get("cached_at")
    if not cached_at:
        return True
    age = time.time() - cached_at
    return age > ttl_seconds * 0.8


@bp.route("/home/featured", methods=["GET"])
@limiter.limit("30 per minute")
def get_home_featured():
    """
    Return home featured data from cache. Scraping runs ONLY in the RQ worker.
    Web process NEVER starts Playwright — prevents OOM on Render free tier (512MB).
    """
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        home_req = HomeFeaturedRequest(
            url=request.args.get("url"),
            force=request.args.get("force")
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    home_url = home_req.url or _default_home_url()
    cache_key = _build_home_featured_cache_key()
    ttl_seconds = current_app.config.get("HOME_FEATURED_CACHE_TTL_SECONDS", 1800)

    site_key, config = site_manager.get_config_for_url(home_url)
    if not site_key:
        return jsonify({"error": "URL domain not supported"}), 400

    # 1. Volatile cache (Redis/RAM) — fastest
    if not home_req.force:
        cached_payload, status = volatile_get(cache_key)
        if cached_payload and status == "hit" and not _is_payload_stale(cached_payload, ttl_seconds):
            return jsonify({**cached_payload, "cached": True, "cache_source": "volatile"}), 200

    # 2. Persistent cache (DB) with stale-while-revalidate
    if not home_req.force:
        cached_payload, status = persistent_get(cache_key)
        if cached_payload:
            if status == "fresh" and not _is_payload_stale(cached_payload, ttl_seconds):
                volatile_set(cache_key, cached_payload, timeout=ttl_seconds)
                return jsonify({**cached_payload, "cached": True, "cache_source": "persistent"}), 200

            elif status == "stale":
                # Stale — enqueue background refresh, return stale data
                get_scraper_queue().enqueue(
                    'app.tasks.scraper.run_background_refresh',
                    home_url, config.model_dump(), site_key, cache_key, ttl_seconds
                )
                return jsonify({**cached_payload, "cached": True, "cache_source": "persistent_stale"}), 200

    # 3. Cache miss — enqueue scraping, don't block the web process
    get_scraper_queue().enqueue(
        'app.tasks.scraper.run_background_refresh',
        home_url, config.model_dump(), site_key, cache_key, ttl_seconds
    )

    return jsonify({
        "message": "Scraping enqueued — check back in ~30 seconds for fresh data.",
        "url": home_url,
        "status": "enqueued",
    }), 202
