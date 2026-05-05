import logging

from flask import current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app import limiter
from app.models.embed import Anime, Episode, Favorite, HistoryEntry, db
from app.utils.helpers import clean_name
from app.api.routes import bp
from app.api.utils import (
    _is_valid_http_url,
    _parse_positive_int,
    _resolve_profile_key,
    _utcnow,
    check_api_key,
)

logger = logging.getLogger(__name__)

@bp.route("/favorites", methods=["GET", "POST"])
@limiter.limit("120 per minute")
def favorites():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == "GET":
        default_limit = max(1, int(current_app.config.get("DEFAULT_PAGE_SIZE", 30)))
        max_limit = max(default_limit, int(current_app.config.get("MAX_PAGE_SIZE", 100)))
        page = _parse_positive_int(request.args.get("page"), 1, 1, 100000)
        limit = _parse_positive_int(request.args.get("limit"), default_limit, 1, max_limit)
        profile_key = _resolve_profile_key()

        try:
            query_builder = Favorite.query.filter_by(profile_key=profile_key).order_by(
                Favorite.updated_at.desc(),
                Favorite.id.desc(),
            )
            total_results = query_builder.count()
            total_pages = max(1, (total_results + limit - 1) // limit)
            if page > total_pages:
                page = total_pages

            items = query_builder.offset((page - 1) * limit).limit(limit).all()
            return jsonify(
                {
                    "user_id": profile_key,
                    "page": page,
                    "limit": limit,
                    "total_pages": total_pages,
                    "total_results": total_results,
                    "results": [item.to_dict() for item in items],
                }
            ), 200
        except SQLAlchemyError as exc:
            logger.error("Failed to fetch favorites: %s", exc)
            return jsonify({"error": "Failed to fetch favorites"}), 500

    payload = request.get_json(silent=True) or {}
    profile_key = _resolve_profile_key(payload)
    anime_url = (payload.get("url") or payload.get("anime_url") or "").strip()
    if not anime_url:
        return jsonify({"error": 'Field "url" is required'}), 400
    if not _is_valid_http_url(anime_url):
        return jsonify({"error": "Invalid url"}), 400

    image_url = (payload.get("image") or payload.get("image_url") or "").strip()
    if image_url and not _is_valid_http_url(image_url):
        return jsonify({"error": "Invalid image_url"}), 400

    try:
        anime = Anime.query.filter_by(url=anime_url).first()
        anime_name = clean_name(payload.get("name") or payload.get("anime_name")) or (
            anime.name if anime else None
        )
        if not anime_name:
            return jsonify({"error": 'Field "name" is required when anime is unknown'}), 400

        favorite = Favorite.query.filter_by(profile_key=profile_key, anime_url=anime_url).first()
        if favorite:
            favorite.anime_name = anime_name
            favorite.image_url = image_url or favorite.image_url
            favorite.anime_id = anime.id if anime else favorite.anime_id
            favorite.updated_at = _utcnow()
            created = False
        else:
            favorite = Favorite(
                profile_key=profile_key,
                anime_id=anime.id if anime else None,
                anime_name=anime_name,
                anime_url=anime_url,
                image_url=image_url or None,
            )
            db.session.add(favorite)
            created = True

        db.session.commit()
        return jsonify({"created": created, "favorite": favorite.to_dict()}), 201 if created else 200
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Failed to save favorite: %s", exc)
        return jsonify({"error": "Failed to save favorite"}), 500


@bp.route("/history", methods=["GET", "POST"])
@limiter.limit("120 per minute")
def history():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == "GET":
        default_limit = max(1, int(current_app.config.get("DEFAULT_PAGE_SIZE", 30)))
        max_limit = max(default_limit, int(current_app.config.get("MAX_PAGE_SIZE", 100)))
        page = _parse_positive_int(request.args.get("page"), 1, 1, 100000)
        limit = _parse_positive_int(request.args.get("limit"), default_limit, 1, max_limit)
        profile_key = _resolve_profile_key()

        try:
            query_builder = HistoryEntry.query.filter_by(profile_key=profile_key).order_by(
                HistoryEntry.last_seen.desc(),
                HistoryEntry.id.desc(),
            )
            total_results = query_builder.count()
            total_pages = max(1, (total_results + limit - 1) // limit)
            if page > total_pages:
                page = total_pages

            items = query_builder.offset((page - 1) * limit).limit(limit).all()
            return jsonify(
                {
                    "user_id": profile_key,
                    "page": page,
                    "limit": limit,
                    "total_pages": total_pages,
                    "total_results": total_results,
                    "results": [item.to_dict() for item in items],
                }
            ), 200
        except SQLAlchemyError as exc:
            logger.error("Failed to fetch history: %s", exc)
            return jsonify({"error": "Failed to fetch history"}), 500

    payload = request.get_json(silent=True) or {}
    profile_key = _resolve_profile_key(payload)
    content_url = (payload.get("url") or payload.get("content_url") or "").strip()
    if not content_url:
        return jsonify({"error": 'Field "url" is required'}), 400
    if not _is_valid_http_url(content_url):
        return jsonify({"error": "Invalid url"}), 400

    image_url = (payload.get("image") or payload.get("image_url") or "").strip()
    if image_url and not _is_valid_http_url(image_url):
        return jsonify({"error": "Invalid image_url"}), 400

    try:
        episode = Episode.query.filter_by(url=content_url).first()
        anime = None

        anime_url = (payload.get("anime_url") or "").strip()
        if episode and episode.anime:
            anime = episode.anime
        elif anime_url:
            anime = Anime.query.filter_by(url=anime_url).first()

        title = clean_name(payload.get("title")) or (episode.title if episode else None)
        if not title and anime:
            title = anime.name
        if not title:
            return jsonify({"error": 'Field "title" is required when item is unknown'}), 400

        history_entry = HistoryEntry.query.filter_by(
            profile_key=profile_key,
            content_url=content_url,
        ).first()

        if history_entry:
            history_entry.title = title
            history_entry.image_url = image_url or history_entry.image_url
            history_entry.anime_id = anime.id if anime else history_entry.anime_id
            history_entry.episode_id = episode.id if episode else history_entry.episode_id
            history_entry.watch_count = max(1, int(history_entry.watch_count or 1)) + 1
            history_entry.last_seen = _utcnow()
            created = False
        else:
            history_entry = HistoryEntry(
                profile_key=profile_key,
                anime_id=anime.id if anime else None,
                episode_id=episode.id if episode else None,
                title=title,
                content_url=content_url,
                image_url=image_url or None,
                watch_count=1,
            )
            db.session.add(history_entry)
            created = True

        db.session.commit()
        return jsonify({"created": created, "history": history_entry.to_dict()}), 201 if created else 200
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Failed to save history: %s", exc)
        return jsonify({"error": "Failed to save history"}), 500
