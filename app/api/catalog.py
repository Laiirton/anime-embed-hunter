import logging

from flask import current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app import limiter
from app.models.embed import Anime
from app.api.routes import bp
from app.api.utils import (
    _build_catalog_filters,
    _parse_positive_int,
    _resolve_catalog_order,
    _serialize_anime,
    check_api_key,
    _escape_like_pattern,
)

logger = logging.getLogger(__name__)

@bp.route("/catalog", methods=["GET"])
@limiter.limit("120 per minute")
def get_catalog():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    default_limit = max(1, int(current_app.config.get("DEFAULT_PAGE_SIZE", 30)))
    max_limit = max(default_limit, int(current_app.config.get("MAX_PAGE_SIZE", 100)))
    page = _parse_positive_int(request.args.get("page") or request.args.get("pagina"), 1, 1, 100000)
    limit = _parse_positive_int(request.args.get("limit"), default_limit, 1, max_limit)

    filters, unsupported_filters = _build_catalog_filters()
    order_key, order_clause = _resolve_catalog_order()

    try:
        query = Anime.query
        if filters:
            query = query.filter(*filters)

        total_results = query.count()
        total_pages = max(1, (total_results + limit - 1) // limit)
        if page > total_pages:
            page = total_pages

        items = (
            query.order_by(order_clause)
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        return jsonify(
            {
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
                "total_results": total_results,
                "order": order_key,
                "unsupported_filters": unsupported_filters,
                "results": [_serialize_anime(anime) for anime in items],
            }
        ), 200
    except SQLAlchemyError as exc:
        logger.error("Catalog query failed: %s", exc)
        return jsonify({"error": "Catalog query failed"}), 500


@bp.route("/catalog/search", methods=["GET"])
@limiter.limit("120 per minute")
def catalog_search():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    query = (request.args.get("q") or request.args.get("search") or "").strip()
    if not query:
        return jsonify({"error": 'Query parameter "q" is required'}), 400

    default_limit = max(1, int(current_app.config.get("DEFAULT_PAGE_SIZE", 30)))
    max_limit = max(default_limit, int(current_app.config.get("MAX_PAGE_SIZE", 100)))
    page = _parse_positive_int(request.args.get("page"), 1, 1, 100000)
    limit = _parse_positive_int(request.args.get("limit"), default_limit, 1, max_limit)

    escaped = _escape_like_pattern(query)
    try:
        query_builder = Anime.query.filter(Anime.name.ilike(f"%{escaped}%", escape="\\"))
        total_results = query_builder.count()
        total_pages = max(1, (total_results + limit - 1) // limit)
        if page > total_pages:
            page = total_pages

        items = (
            query_builder.order_by(Anime.name.asc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        return jsonify(
            {
                "query": query,
                "page": page,
                "limit": limit,
                "total_pages": total_pages,
                "total_results": total_results,
                "results": [_serialize_anime(anime) for anime in items],
            }
        ), 200
    except SQLAlchemyError as exc:
        logger.error("Catalog search failed: %s", exc)
        return jsonify({"error": "Catalog search failed"}), 500
