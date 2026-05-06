import logging
import re

from flask import current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app import limiter
from app.models.embed import Episode
from app.services.scraper import ScraperService
from app.services.site_manager import site_manager
from app.api.routes import bp
from app.api.utils import (
    _parse_positive_int,
    _resolve_episode_url_by_id,
    _serialize_episode,
    check_api_key,
)

logger = logging.getLogger(__name__)

@bp.route("/episode/<episode_id>/players", methods=["GET"])
@limiter.limit("20 per minute")
def get_episode_players(episode_id):
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    if not re.fullmatch(r"\d+", str(episode_id)):
        return jsonify({"error": "Episode id must be numeric"}), 400

    episode_url, episode = _resolve_episode_url_by_id(episode_id)
    site_key, config = site_manager.get_config_for_url(episode_url)
    if not site_key:
        return jsonify({"error": "URL domain not supported"}), 400

    try:
        with ScraperService() as scraper:
            context = scraper._get_context()
            page = context.new_page()
            try:
                payload = scraper.extract_episode_players(page, episode_url, config)
                if "error" in payload:
                    return jsonify(payload), 502

                payload["source"] = site_key
                payload["episode_id"] = int(episode_id)
                payload["cached"] = False
                if episode:
                    payload["database_episode"] = _serialize_episode(episode)
                return jsonify(payload), 200
            finally:
                page.close()
                context.close()
    except Exception as exc:
        logger.error("Episode players lookup failed: %s", exc)
        return jsonify({"error": "Internal server error"}), 500

@bp.route("/lancamentos", methods=["GET"])
@limiter.limit("120 per minute")
def get_lancamentos():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    default_limit = max(1, int(current_app.config.get("DEFAULT_PAGE_SIZE", 30)))
    max_limit = max(default_limit, int(current_app.config.get("MAX_PAGE_SIZE", 100)))
    page = _parse_positive_int(request.args.get("page"), 1, 1, 100000)
    limit = _parse_positive_int(request.args.get("limit"), default_limit, 1, max_limit)

    try:
        query_builder = Episode.query.order_by(Episode.last_updated.desc(), Episode.id.desc())
        total_results = query_builder.count()
        total_pages = max(1, (total_results + limit - 1) // limit)
        if page > total_pages:
            page = total_pages

        episodes = query_builder.offset((page - 1) * limit).limit(limit).all()
        return jsonify(
            {
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
                "total_results": total_results,
                "results": [_serialize_episode(ep) for ep in episodes],
            }
        ), 200
    except SQLAlchemyError as exc:
        logger.error("Failed to fetch releases: %s", exc)
        return jsonify({"error": "Failed to fetch releases"}), 500
