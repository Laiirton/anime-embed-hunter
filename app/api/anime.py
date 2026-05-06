import logging

from flask import current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app import limiter
from app.models.embed import Episode
from app.api.routes import bp
from app.api.utils import (
    _parse_positive_int,
    _resolve_anime_by_slug,
    _serialize_anime,
    _serialize_episode,
    check_api_key,
)

logger = logging.getLogger(__name__)

@bp.route("/anime/<path:slug>", methods=["GET"])
@limiter.limit("120 per minute")
def get_anime(slug):
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    anime = _resolve_anime_by_slug(slug)
    if not anime:
        return jsonify({"error": "Anime not found"}), 404

    from app.services.metadata_service import populate_anime_metadata_single
    populate_anime_metadata_single(anime)

    default_limit = max(1, int(current_app.config.get("DEFAULT_PAGE_SIZE", 30)))
    max_limit = max(default_limit, int(current_app.config.get("MAX_PAGE_SIZE", 100)))
    page = _parse_positive_int(request.args.get("page"), 1, 1, 100000)
    limit = _parse_positive_int(request.args.get("limit"), default_limit, 1, max_limit)

    try:
        episode_query = Episode.query.filter_by(anime_id=anime.id)
        total_episodes = episode_query.count()
        total_pages = max(1, (total_episodes + limit - 1) // limit)
        if page > total_pages:
            page = total_pages

        episodes = (
            episode_query.order_by(Episode.id.asc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        payload = _serialize_anime(anime)
        payload.update(
            {
                "page": page,
                "limit": limit,
                "episodes_total_pages": total_pages,
                "episodes_total_results": total_episodes,
                "episodes": [_serialize_episode(ep) for ep in episodes],
            }
        )
        return jsonify(payload), 200
    except SQLAlchemyError as exc:
        logger.error("Anime lookup failed: %s", exc)
        return jsonify({"error": "Anime lookup failed"}), 500
