import logging

from flask import current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app import cache, limiter
from app.models.embed import Anime
from app.api.routes import bp
from app.api.utils import (
    _build_search_cache_key,
    _escape_like_pattern,
    check_api_key,
)
from app.api.validators import SearchRequest

logger = logging.getLogger(__name__)

@bp.route("/search", methods=["GET"])
@limiter.limit("60 per minute")
def search_animes():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Validate request parameters with Pydantic
        search_req = SearchRequest(
            q=request.args.get("q"),
            page=request.args.get("page"),
            limit=request.args.get("limit"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    cache_key = _build_search_cache_key(search_req.q)
    cached = cache.get(cache_key)
    if cached:
        return jsonify({**cached, "cached": True}), 200

    try:
        escaped_query = _escape_like_pattern(search_req.q)
        results = (
            Anime.query.filter(Anime.name.ilike(f"%{escaped_query}%", escape="\\"))
            .limit(current_app.config.get("SEARCH_LIMIT", 50))
            .all()
        )
        
        from app.services.metadata_service import populate_anime_metadata
        populate_anime_metadata(results)
        
        payload = {
            "query": search_req.q,
            "total_found": len(results),
            "results": [anime.to_dict() for anime in results],
            "cached": False,
        }

        cache.set(
            cache_key,
            payload,
            timeout=current_app.config.get("SEARCH_CACHE_TTL_SECONDS", 120),
        )
        return jsonify(payload), 200
    except SQLAlchemyError as exc:
        logger.error("Search error: %s", exc)
        return jsonify({"error": "Search failed"}), 500
