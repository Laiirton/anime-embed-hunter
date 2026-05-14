import hashlib
import json
import logging
import re
import time

from flask import current_app, jsonify, make_response, request
from werkzeug.http import http_date
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app import cache, limiter, get_scraper_queue
from app.models.embed import Episode, Anime
from app.services.site_manager import site_manager
from app.api.routes import bp
from app.api.utils import (
    _build_url,
    _escape_like_pattern,
    _parse_positive_int,
    _serialize_episode,
    check_api_key,
)
from app.api.validators import EpisodePlayersRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Cache TTL constants (seconds)
_EPISODE_DETAIL_CACHE_TTL = 300       # 5 minutes
_EPISODE_PLAYERS_CACHE_TTL = 600      # 10 minutes
_LANCAMENTOS_CACHE_TTL = 60           # 1 minute

# Rate-limit per request
_EPISODE_DETAIL_RATE = "30 per minute"
_EPISODE_PLAYERS_RATE = "60 per minute"


def _make_cached_response(payload, status=200, ttl=None):
    """Wrap payload in a response with cache-control headers."""
    resp = make_response(jsonify(payload), status)
    if ttl:
        resp.headers["Cache-Control"] = f"public, max-age={ttl}"
        resp.headers["Expires"] = http_date(time.time() + ttl)
        resp.headers["Vary"] = "Accept-Encoding"
    else:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


def _resolve_episode_from_db(episode_id, prefix=None):
    """Find an episode in the database, or construct a fallback URL."""
    episode_id = str(episode_id).strip().rstrip("/:").replace(":", "/")
    episode = (
        Episode.query.filter(Episode.url.like(f"%/video/%/{episode_id}%"))
        .order_by(Episode.last_updated.desc())
        .first()
    )
    if episode:
        return episode, episode.url.rstrip("/:")

    if prefix is None:
        prefix = (request.args.get("prefix") or "a").strip().lower()
        if not prefix or not re.match(r"^[a-z0-9-]+$", prefix):
            prefix = "a"

    constructed_url = _build_url(f"/video/{prefix}/{episode_id}")
    return None, constructed_url


def _serialize_episode_full(episode):
    """Full serialization for a single episode including nested anime data."""
    payload = {
        "id": episode.id,
        "title": episode.title,
        "url": episode.url,
        "embed_url": episode.embed_url,
        "info": episode.info,
        "audio_type": episode.audio_type,
        "last_updated": episode.last_updated.isoformat() if episode.last_updated else None,
    }
    if episode.anime:
        anime = episode.anime
        slug = ""
        marker = "/anime/"
        if anime.url and marker in anime.url:
            slug = anime.url.split(marker, 1)[1].strip("/")
        payload["anime"] = {
            "name": anime.name,
            "url": anime.url,
            "slug": slug,
            "cover_url": getattr(anime, "cover_url", None),
            "status": getattr(anime, "status", None),
            "total_episodes": getattr(anime, "total_episodes", 0),
            "synopsis": getattr(anime, "synopsis", None),
            "rating": getattr(anime, "rating", None),
            "year": getattr(anime, "year", None),
            "genres": getattr(anime, "genres", None),
            "audio_type": getattr(anime, "audio_type", None),
            "item_type": anime.item_type,
            "last_scanned": anime.last_scanned.isoformat() if anime.last_scanned else None,
        }
    else:
        payload["anime"] = None
    return payload


# ---------------------------------------------------------------------------
#  GET  /episode/<episode_id>
# ---------------------------------------------------------------------------

@bp.route("/episode/<episode_id>", methods=["GET"])
@limiter.limit(_EPISODE_DETAIL_RATE)
def get_episode_detail(episode_id):
    """Return episode details from DB only — no scraping in web process."""
    try:
        prefix = request.args.get("prefix", "a")
        episode_id = str(episode_id)
        EpisodePlayersRequest(episode_id=episode_id, prefix=prefix)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    episode, constructed_url = _resolve_episode_from_db(episode_id, prefix)
    if not episode:
        return jsonify({
            "error": "Episode not found",
            "resolved_url": constructed_url,
            "hint": "Use GET /episode/<id>/players to trigger scraping for this episode",
        }), 404

    return jsonify(_serialize_episode_full(episode)), 200


# ---------------------------------------------------------------------------
#  GET  /episode/<episode_id>/players
# ---------------------------------------------------------------------------

@bp.route("/episode/<episode_id>/players", methods=["GET"])
@limiter.limit(_EPISODE_PLAYERS_RATE)
def get_episode_players(episode_id):
    """
    Return players from cache. If no cache, enqueue scraping via RQ.
    Scraping NEVER runs in this process (avoids OOM on Render free tier).
    """
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        prefix = request.args.get("prefix") or "a"
        ep_req = EpisodePlayersRequest(
            episode_id=episode_id,
            prefix=prefix,
        )
        episode_id = ep_req.episode_id
        prefix = ep_req.prefix
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Flask-Cache lookup
    raw_prefix = prefix if prefix else "a"
    players_cache_key = f"players:{episode_id}:{raw_prefix}"
    cached_players = cache.get(players_cache_key)
    if cached_players is not None:
        cached_players["cached"] = True
        cached_players["cache_age"] = "cached"
        cached_players["cache_ttl"] = _EPISODE_PLAYERS_CACHE_TTL
        return _make_cached_response(cached_players, ttl=_EPISODE_PLAYERS_CACHE_TTL)

    episode_url, episode = _resolve_episode_url_by_id(episode_id, prefix)
    site_key, config = site_manager.get_config_for_url(episode_url)
    if not site_key:
        return jsonify({"error": "URL domain not supported"}), 400

    # DB cache lookup (embed_url stored as JSON)
    if episode and episode.embed_url:
        try:
            players = json.loads(episode.embed_url)
            payload = {
                "cached": True,
                "cache_age": "database",
                "episode_id": int(episode_id),
                "episode_url": episode_url,
                "players": players,
                "source": site_key,
                "title": episode.title,
                "total_players": len(players),
            }
            cache.set(players_cache_key, payload, timeout=_EPISODE_PLAYERS_CACHE_TTL)
            return _make_cached_response(payload, ttl=_EPISODE_PLAYERS_CACHE_TTL)
        except json.JSONDecodeError:
            pass

    # No cache — enqueue scraping in RQ worker (separate process)
    from app.tasks.scraper import run_scraper_task
    get_scraper_queue().enqueue(run_scraper_task, episode_url, config)

    # If we have old/stale data, return it with a hint
    from app.services.unified_cache import persistent_get
    cached_payload, status = persistent_get(episode_url)
    if cached_payload and status in ("fresh", "stale"):
        payload = cached_payload.copy()
        payload["cached"] = True
        payload["cache_age"] = "stale_refreshing"
        return _make_cached_response(payload, ttl=30)

    return jsonify({
        "message": "No cached player data. Scraper enqueued — check back in ~30 seconds.",
        "episode_id": int(episode_id),
        "episode_url": episode_url,
        "status": "enqueued",
    }), 202


# ---------------------------------------------------------------------------
#  GET  /lancamentos
# ---------------------------------------------------------------------------

@bp.route("/lancamentos", methods=["GET"])
@limiter.limit("120 per minute")
def get_lancamentos():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    default_limit = max(1, int(current_app.config.get("DEFAULT_PAGE_SIZE", 30)))
    max_limit = max(default_limit, int(current_app.config.get("MAX_PAGE_SIZE", 100)))
    page = _parse_positive_int(request.args.get("page"), 1, 1, 100000)
    limit = _parse_positive_int(request.args.get("limit"), default_limit, 1, max_limit)

    # Filters
    audio_type = request.args.get("audio_type")
    search_term = request.args.get("search")

    sorted_args = tuple(sorted(request.args.items(multi=True)))
    cache_key = f"lancamentos:{hashlib.md5(str(sorted_args).encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return _make_cached_response(cached, ttl=_LANCAMENTOS_CACHE_TTL)

    try:
        query_builder = (
            Episode.query
            .options(selectinload(Episode.anime))
            .order_by(Episode.last_updated.desc(), Episode.id.desc())
        )

        # Apply audio type filter
        if audio_type:
            audio_type_clean = audio_type.strip().lower()
            if audio_type_clean in ("dublado", "dub", "pt-br"):
                query_builder = query_builder.filter(Episode.audio_type == "Dublado")
            elif audio_type_clean in ("legendado", "sub", "leg"):
                query_builder = query_builder.filter(Episode.audio_type == "Legendado")

        # Apply search filter on episode title
        if search_term:
            escaped = _escape_like_pattern(search_term.strip())
            query_builder = query_builder.filter(
                Episode.title.ilike(f"%{escaped}%", escape="\\")
            )

        total_results = query_builder.count()
        total_pages = max(1, (total_results + limit - 1) // limit)
        if page > total_pages:
            page = total_pages

        episodes = query_builder.offset((page - 1) * limit).limit(limit).all()
        payload = {
            "page": page,
            "limit": limit,
            "total_pages": total_pages,
            "total_results": total_results,
            "results": [_serialize_episode(ep) for ep in episodes],
        }
        cache.set(cache_key, payload, timeout=_LANCAMENTOS_CACHE_TTL)
        return jsonify(payload), 200
    except SQLAlchemyError as exc:
        logger.error("Failed to fetch releases: %s", exc)
        return jsonify({"error": "Failed to fetch releases"}), 500
