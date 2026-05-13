import hashlib
import json
import logging

from flask import jsonify, request, current_app

from app import cache, limiter, get_scraper_queue
from app.tasks.scraper import run_scraper_task
from app.services.site_manager import site_manager
from app.api.routes import bp
from app.api.utils import check_api_key
from app.api.validators import EmbedRequestModel
from app.services.unified_cache import persistent_get, persistent_set
from app.models.embed import EmbedRequest

logger = logging.getLogger(__name__)

# Cache TTL for embed players (seconds)
EMBED_PLAYERS_CACHE_TTL = 600  # 10 minutes


@bp.route("/get-embed", methods=["GET"])
@limiter.limit("10 per minute")
def get_embed():
    """Scrape an episode page for embed URL. Returns 202 if enqueued, 200 if found in cache."""
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        embed_req = EmbedRequestModel(
            url=request.args.get("url"),
            force=request.args.get("force"),
        )
        target_url = embed_req.url
        force_refresh = embed_req.force
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    site_key, config = site_manager.get_config_for_url(target_url)
    if not site_key:
        return jsonify({"error": "URL domain not supported"}), 400

    # Try persistent cache first (database)
    if not force_refresh:
        cached_payload, status = persistent_get(target_url)

        if status == "fresh":
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "persistent_cache"
            return jsonify(cached_payload), 200

        elif status == "stale":
            # Enqueue refresh in background, return stale data
            get_scraper_queue().enqueue(run_scraper_task, target_url, config)
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "persistent_cache (stale)"
            return jsonify(cached_payload), 200

    # Cache miss or force_refresh — enqueue scraping task
    get_scraper_queue().enqueue(run_scraper_task, target_url, config)
    return jsonify({"message": "Scraping task enqueued", "target": target_url, "estimated_seconds": 30}), 202


@bp.route("/get-embed/<int:episode_id>", methods=["GET"])
@limiter.limit("30 per minute")
def get_embed_by_episode_id(episode_id):
    """Get embed URL for a specific episode by ID from the database cache."""
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    prefix = request.args.get("prefix", "a")

    # Resolve episode URL from database
    from app.models.embed import Episode
    from app.repositories.episode_repository import EpisodeRepository

    episode_url, episode = EpisodeRepository.find_by_id_with_prefix(str(episode_id), prefix)

    if not episode:
        # Episode not in DB — check if we have cached embed data for the resolved URL
        cached, status = persistent_get(episode_url)
        if cached and status == "fresh":
            return jsonify({**cached, "cached": True, "cache_source": "persistent_cache", "episode_id": episode_id}), 200
        return jsonify({
            "error": "Episode not found",
            "resolved_url": episode_url,
            "hint": f"Use GET /episode/{episode_id}/players to scrape the episode page",
        }), 404

    # Episode found in DB — check for cached embed data
    if episode.embed_url:
        try:
            players = json.loads(episode.embed_url)
            return jsonify({
                "cached": True,
                "cache_source": "database",
                "episode_id": episode.id,
                "episode_url": episode.url,
                "players": players,
                "title": episode.title,
                "total_players": len(players),
            }), 200
        except json.JSONDecodeError:
            # embed_url might be a single URL (legacy)
            return jsonify({
                "cached": True,
                "cache_source": "database (legacy)",
                "episode_id": episode.id,
                "episode_url": episode.url,
                "players": [{"position": 1, "label": "Player 1", "embed_url": episode.embed_url}],
                "title": episode.title,
                "total_players": 1,
            }), 200

    # No cached data — enqueue scraping
    site_key, config = site_manager.get_config_for_url(episode.url)
    if site_key:
        get_scraper_queue().enqueue(run_scraper_task, episode.url, config)
        return jsonify({
            "message": "Episode found but no embed data cached. Scraper enqueued.",
            "episode_id": episode.id,
            "episode_url": episode.url,
            "estimated_seconds": 30,
        }), 202

    return jsonify({"error": "No site config for episode URL"}), 400
