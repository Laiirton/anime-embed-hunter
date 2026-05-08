import logging

from flask import jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app import limiter
from app.models.embed import Anime, Episode, Favorite, HistoryEntry, db
from app.api.routes import bp
from app.api.utils import (
    _resolve_profile_key,
    _utcnow,
    check_api_key,
)
from app.api.validators import FavoriteRequest, HistoryRequest

logger = logging.getLogger(__name__)

@bp.route("/favorites", methods=["GET", "POST"])
@limiter.limit("120 per minute")
def favorites():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == "GET":
        try:
            from app.api.validators import CatalogRequest
            catalog_req = CatalogRequest(
                page=request.args.get("page"),
                limit=request.args.get("limit"),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        profile_key = _resolve_profile_key()

        try:
            query_builder = Favorite.query.filter_by(profile_key=profile_key).order_by(
                Favorite.updated_at.desc(),
                Favorite.id.desc(),
            )
            total_results = query_builder.count()
            total_pages = max(1, (total_results + catalog_req.limit - 1) // catalog_req.limit)
            if catalog_req.page > total_pages:
                catalog_req.page = total_pages

            items = query_builder.offset((catalog_req.page - 1) * catalog_req.limit).limit(catalog_req.limit).all()
            return jsonify(
                {
                    "user_id": profile_key,
                    "page": catalog_req.page,
                    "limit": catalog_req.limit,
                    "total_pages": total_pages,
                    "total_results": total_results,
                    "results": [item.to_dict() for item in items],
                }
            ), 200
        except SQLAlchemyError as exc:
            logger.error("Failed to fetch favorites: %s", exc)
            return jsonify({"error": "Failed to fetch favorites"}), 500

    payload = request.get_json(silent=True) or {}
    try:
        fav_req = FavoriteRequest(
            url=payload.get("url") or payload.get("anime_url"),
            name=payload.get("name") or payload.get("anime_name"),
            image_url=payload.get("image") or payload.get("image_url"),
            user_id=payload.get("user_id"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    profile_key = _resolve_profile_key(fav_req.dict())
    anime_url = fav_req.url
    if not anime_url:
        return jsonify({"error": 'Field "url" is required'}), 400

    try:
        anime = Anime.query.filter_by(url=anime_url).first()
        anime_name = fav_req.name or (anime.name if anime else None)
        if not anime_name:
            return jsonify({"error": 'Field "name" is required when anime is unknown'}), 400

        favorite = Favorite.query.filter_by(profile_key=profile_key, anime_url=anime_url).first()
        if favorite:
            favorite.anime_name = anime_name
            favorite.image_url = fav_req.image_url or favorite.image_url
            favorite.anime_id = anime.id if anime else favorite.anime_id
            favorite.updated_at = _utcnow()
            created = False
        else:
            favorite = Favorite(
                profile_key=profile_key,
                anime_id=anime.id if anime else None,
                anime_name=anime_name,
                anime_url=anime_url,
                image_url=fav_req.image_url or None,
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
        try:
            from app.api.validators import CatalogRequest
            catalog_req = CatalogRequest(
                page=request.args.get("page"),
                limit=request.args.get("limit"),
            )
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        profile_key = _resolve_profile_key()

        try:
            query_builder = HistoryEntry.query.filter_by(profile_key=profile_key).order_by(
                HistoryEntry.last_seen.desc(),
                HistoryEntry.id.desc(),
            )
            total_results = query_builder.count()
            total_pages = max(1, (total_results + catalog_req.limit - 1) // catalog_req.limit)
            if catalog_req.page > total_pages:
                catalog_req.page = total_pages

            items = query_builder.offset((catalog_req.page - 1) * catalog_req.limit).limit(catalog_req.limit).all()
            return jsonify(
                {
                    "user_id": profile_key,
                    "page": catalog_req.page,
                    "limit": catalog_req.limit,
                    "total_pages": total_pages,
                    "total_results": total_results,
                    "results": [item.to_dict() for item in items],
                }
            ), 200
        except SQLAlchemyError as exc:
            logger.error("Failed to fetch history: %s", exc)
            return jsonify({"error": "Failed to fetch history"}), 500

    payload = request.get_json(silent=True) or {}
    try:
        hist_req = HistoryRequest(
            url=payload.get("url") or payload.get("content_url"),
            title=payload.get("title"),
            image_url=payload.get("image") or payload.get("image_url"),
            user_id=payload.get("user_id"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    profile_key = _resolve_profile_key(hist_req.dict())
    content_url = hist_req.url
    if not content_url:
        return jsonify({"error": 'Field "url" is required'}), 400

    try:
        episode = Episode.query.filter_by(url=content_url).first()
        anime = None

        anime_url = (payload.get("anime_url") or "").strip()
        if episode and episode.anime:
            anime = episode.anime
        elif anime_url:
            anime = Anime.query.filter_by(url=anime_url).first()

        title = hist_req.title or (episode.title if episode else None)
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
            history_entry.image_url = hist_req.image_url or history_entry.image_url
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
                image_url=hist_req.image_url or None,
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
