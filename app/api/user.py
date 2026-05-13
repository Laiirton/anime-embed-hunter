import logging

from flask import jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app import limiter
from app.models.embed import Anime, Episode, Favorite, HistoryEntry, db
from app.repositories import AnimeRepository, EpisodeRepository, FavoriteRepository, HistoryRepository
from app.api.routes import bp
from app.api.utils import (
    _resolve_profile_key,
    _utcnow,
    check_api_key,
)
from app.api.validators import FavoriteRequest, HistoryRequest

logger = logging.getLogger(__name__)


def _parse_json_body():
    if request.content_type and 'application/json' in request.content_type:
        payload = request.get_json(silent=True)
        if payload is None:
            raise ValueError("Invalid JSON body")
    else:
        payload = request.get_json(silent=True) or {}
    return payload

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
            items, total_results = FavoriteRepository.paginate_by_profile(
                profile_key, catalog_req.page, catalog_req.limit
            )
            total_pages = max(1, (total_results + catalog_req.limit - 1) // catalog_req.limit)
            if catalog_req.page > total_pages:
                catalog_req.page = total_pages

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

    try:
        payload = _parse_json_body()
        fav_req = FavoriteRequest(
            url=payload.get("url") or payload.get("anime_url"),
            name=payload.get("name") or payload.get("anime_name"),
            image_url=payload.get("image") or payload.get("image_url"),
            user_id=payload.get("user_id"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    profile_key = _resolve_profile_key(fav_req.model_dump())
    anime_url = fav_req.url

    try:
        anime = AnimeRepository.find_by_url(anime_url)
        anime_name = fav_req.name or (anime.name if anime else None)
        if not anime_name:
            return jsonify({"error": 'Field "name" is required when anime is unknown'}), 400

        favorite, created = FavoriteRepository.upsert(
            profile_key=profile_key,
            anime_url=anime_url,
            anime_name=anime_name,
            image_url=fav_req.image_url,
            anime_id=anime.id if anime else None,
        )
        db.session.commit()
        return jsonify({"created": created, "favorite": favorite.to_dict()}), 201 if created else 200
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Failed to save favorite: %s", exc)
        return jsonify({"error": "Failed to save favorite"}), 500


@bp.route("/favorites/<int:fav_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
def delete_favorite(fav_id):
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    profile_key = _resolve_profile_key()

    try:
        deleted = FavoriteRepository.delete_by_id_and_profile(fav_id, profile_key)
        if not deleted:
            return jsonify({"error": "Favorite not found"}), 404

        db.session.commit()
        return jsonify({"deleted": True, "id": fav_id}), 200
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Failed to delete favorite %s: %s", fav_id, exc)
        return jsonify({"error": "Failed to delete favorite"}), 500


@bp.route("/history/<int:hist_id>", methods=["DELETE"])
@limiter.limit("30 per minute")
def delete_history(hist_id):
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    profile_key = _resolve_profile_key()

    try:
        deleted = HistoryRepository.delete_by_id_and_profile(hist_id, profile_key)
        if not deleted:
            return jsonify({"error": "History entry not found"}), 404

        db.session.commit()
        return jsonify({"deleted": True, "id": hist_id}), 200
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Failed to delete history entry %s: %s", hist_id, exc)
        return jsonify({"error": "Failed to delete history entry"}), 500


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
            items, total_results = HistoryRepository.paginate_by_profile(
                profile_key, catalog_req.page, catalog_req.limit
            )
            total_pages = max(1, (total_results + catalog_req.limit - 1) // catalog_req.limit)
            if catalog_req.page > total_pages:
                catalog_req.page = total_pages

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

    try:
        payload = _parse_json_body()
        hist_req = HistoryRequest(
            url=payload.get("url") or payload.get("content_url"),
            title=payload.get("title"),
            image_url=payload.get("image") or payload.get("image_url"),
            user_id=payload.get("user_id"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    profile_key = _resolve_profile_key(hist_req.model_dump())
    content_url = hist_req.url

    try:
        episode = EpisodeRepository.find_by_url(content_url)
        anime = None

        anime_url = (payload.get("anime_url") or "").strip()
        if episode and episode.anime:
            anime = episode.anime
        elif anime_url:
            anime = AnimeRepository.find_by_url(anime_url)

        title = hist_req.title or (episode.title if episode else None)
        if not title and anime:
            title = anime.name
        if not title:
            return jsonify({"error": 'Field "title" is required when item is unknown'}), 400

        history_entry, created = HistoryRepository.upsert(
            profile_key=profile_key,
            content_url=content_url,
            title=title,
            image_url=hist_req.image_url,
            anime_id=anime.id if anime else None,
            episode_id=episode.id if episode else None,
        )
        db.session.commit()
        return jsonify({"created": created, "history": history_entry.to_dict()}), 201 if created else 200
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Failed to save history: %s", exc)
        return jsonify({"error": "Failed to save history"}), 500
