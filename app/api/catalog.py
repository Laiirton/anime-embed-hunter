import hashlib
import logging

from flask import jsonify, request
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app import cache, limiter
from app.models.embed import Anime
from app.api.routes import bp
from app.api.utils import (
    _resolve_catalog_order,
    _serialize_anime,
    check_api_key,
)
from app.api.validators import CatalogRequest, SearchRequest

logger = logging.getLogger(__name__)

# Cache TTLs (segundos)
CATALOG_CACHE_TTL = 120          # 2 min para listagem do catálogo
CATALOG_SEARCH_CACHE_TTL = 60    # 1 min para busca no catálogo


def _make_cache_key(prefix: str) -> str:
    """Gera chave de cache única baseada nos query params."""
    sorted_args = tuple(sorted(request.args.items(multi=True)))
    raw = f"{prefix}:{hashlib.md5(str(sorted_args).encode()).hexdigest()}"
    return raw


def _serialize_anime_list(animes: list) -> list[dict]:
    """
    Serializa lista de animes com contagem eficiente de episódios.
    Usa eager-loaded episodes_count do banco, evitando N+1.
    """
    return [_serialize_anime(anime) for anime in animes]


def _paginated_query(
    query,
    page: int,
    limit: int,
    order_clause,
    serialized: bool = True,
) -> dict:
    """
    Executa query paginada com COUNT otimizado (subquery em vez de query.count()
    que força avaliação completa).

    Retorna dict com {page, limit, total_pages, total_results, results, items_raw}.
    """
    # 1. Contagem otimizada — usa subquery para evitar carregar linhas
    count_subq = query.subquery()
    total_results = Anime.query.session.execute(
        select(func.count()).select_from(count_subq)
    ).scalar() or 0

    total_pages = max(1, (total_results + limit - 1) // limit)
    if page > total_pages:
        page = total_pages

    # 2. Paginação
    items = (
        query.order_by(order_clause)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return {
        "page": page,
        "limit": limit,
        "total_pages": total_pages,
        "total_results": total_results,
        "results": _serialize_anime_list(items),
        "items_raw": items,
    }


@bp.route("/animes", methods=["GET"])
@limiter.limit("120 per minute")
def get_animes():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Validate request parameters with Pydantic
        catalog_req = CatalogRequest(
            page=request.args.get("page") or request.args.get("pagina"),
            limit=request.args.get("limit"),
            search=request.args.get("search"),
            filter_letter=request.args.get("filter_letter"),
            filter_audio=request.args.get("filter_audio"),
            order=request.args.get("order") or request.args.get("filter_order"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    from app.api.utils import _build_catalog_filters
    filters, unsupported_filters = _build_catalog_filters()
    order_key, order_clause = _resolve_catalog_order()

    # Cache key única para os parâmetros desta requisição
    cache_key = _make_cache_key("animes")

    # Tenta cache
    cached = cache.get(cache_key)
    if cached:
        return jsonify({**cached, "cached": True}), 200

    try:
        query = Anime.query
        if filters:
            query = query.filter(*filters)

        result = _paginated_query(query, catalog_req.page, catalog_req.limit, order_clause)

        payload = {
            "page": result["page"],
            "limit": result["limit"],
            "total_pages": result["total_pages"],
            "total_results": result["total_results"],
            "order": order_key,
            "unsupported_filters": unsupported_filters,
            "results": result["results"],
            "cached": False,
        }

        # Cache assíncrono — não falha se Redis estiver off
        try:
            cache.set(cache_key, payload, timeout=CATALOG_CACHE_TTL)
        except Exception:
            logger.warning("Failed to cache catalog result", exc_info=True)

        return jsonify(payload), 200
    except SQLAlchemyError as exc:
        logger.error("Catalog query failed: %s", exc)
        return jsonify({"error": "Catalog query failed"}), 500


@bp.route("/animes/search", methods=["GET"])
@limiter.limit("120 per minute")
def animes_search():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Validate request parameters with Pydantic
        search_req = SearchRequest(
            q=request.args.get("q") or request.args.get("search"),
            page=request.args.get("page"),
            limit=request.args.get("limit"),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    cache_key = _make_cache_key(f"animes_search:{search_req.q}")

    cached = cache.get(cache_key)
    if cached:
        return jsonify({**cached, "cached": True}), 200

    try:
        query_builder = (
            Anime.query
            .filter(Anime.name.ilike(f"%{search_req.q}%", escape="\\"))
        )

        result = _paginated_query(query_builder, search_req.page, search_req.limit, Anime.name.asc())

        payload = {
            "query": search_req.q,
            "page": result["page"],
            "limit": result["limit"],
            "total_pages": result["total_pages"],
            "total_results": result["total_results"],
            "results": result["results"],
            "cached": False,
        }

        try:
            cache.set(cache_key, payload, timeout=CATALOG_SEARCH_CACHE_TTL)
        except Exception:
            logger.warning("Failed to cache search result", exc_info=True)

        return jsonify(payload), 200
    except SQLAlchemyError as exc:
        logger.error("Animes search failed: %s", exc)
        return jsonify({"error": "Animes search failed"}), 500