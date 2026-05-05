from datetime import datetime, timedelta, timezone
import hmac
import json
import logging
import re
from threading import Lock
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app import cache, limiter
from app.models.embed import Anime, EmbedRequest, Episode, Favorite, HistoryEntry, db
from app.services.cache_maintenance import delete_expired_embed_cache
from app.services.scraper import ScraperService
from app.services.site_manager import site_manager
from app.utils.helpers import clean_name

bp = Blueprint("api", __name__)
logger = logging.getLogger(__name__)
_cache_cleanup_lock = Lock()
_last_embed_cache_cleanup_at = None


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def check_api_key():
    api_key = request.headers.get("X-API-KEY", "")
    expected = current_app.config["API_KEY"]
    return hmac.compare_digest(api_key, expected)


def _parse_bool(value):
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _escape_like_pattern(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_search_cache_key(query):
    return f"search:{query.lower()}"


def _build_home_featured_cache_key():
    return "home:featured"


def _parse_positive_int(value, default, minimum=1, maximum=1000):
    if value is None or value == "":
        return default

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(parsed, maximum))


def _serialize_anime(anime):
    slug = ""
    marker = "/anime/"
    if anime.url and marker in anime.url:
        slug = anime.url.split(marker, 1)[1].strip("/")
    return {
        "id": anime.id,
        "name": anime.name,
        "url": anime.url,
        "slug": slug,
        "item_type": anime.item_type,
        "last_scanned": anime.last_scanned.isoformat() if anime.last_scanned else None,
        "episodes_count": len(anime.episodes) if hasattr(anime, "episodes") else 0,
    }


def _serialize_episode(episode):
    payload = episode.to_dict()
    payload["id"] = episode.id
    payload["anime_id"] = episode.anime_id
    if episode.anime:
        payload["anime_name"] = episode.anime.name
        payload["anime_url"] = episode.anime.url
    else:
        payload["anime_name"] = None
        payload["anime_url"] = None
    return payload


def _resolve_profile_key(payload=None):
    payload = payload or {}
    raw = (
        request.headers.get("X-USER-ID")
        or request.args.get("user_id")
        or payload.get("user_id")
        or payload.get("profile_key")
        or "default"
    )
    normalized = re.sub(r"[^a-zA-Z0-9_.-]", "-", str(raw).strip())
    normalized = normalized[:120].strip("-")
    return normalized or "default"


def _infer_audio_filter_expression(filter_audio):
    value = (filter_audio or "").strip().lower()
    if value in {"dublado", "dub", "pt-br"}:
        return Anime.name.ilike("%dublado%")
    if value in {"legendado", "sub"}:
        return ~Anime.name.ilike("%dublado%")
    return None


def _build_catalog_filters():
    filters = []
    unsupported = []

    search = request.args.get("search")
    if search and search != "0":
        escaped = _escape_like_pattern(search.strip())
        filters.append(Anime.name.ilike(f"%{escaped}%", escape="\\"))

    letter = (request.args.get("filter_letter") or "").strip().lower()
    if letter and letter != "0":
        filters.append(Anime.name.ilike(f"{_escape_like_pattern(letter)}%", escape="\\"))

    audio_filter = _infer_audio_filter_expression(request.args.get("filter_audio"))
    if audio_filter is not None:
        filters.append(audio_filter)

    type_url = (request.args.get("type_url") or "").strip().lower()
    if type_url and type_url not in {"animes", "anime", "catalogo"}:
        unsupported.append("type_url")

    if request.args.get("filter_genre_add"):
        unsupported.append("filter_genre_add")
    if request.args.get("filter_genre_del"):
        unsupported.append("filter_genre_del")

    return filters, sorted(set(unsupported))


def _resolve_catalog_order():
    order_key = (
        request.args.get("order")
        or request.args.get("filter_order")
        or "name"
    ).strip().lower()

    mapping = {
        "name": Anime.name.asc(),
        "az": Anime.name.asc(),
        "name_asc": Anime.name.asc(),
        "za": Anime.name.desc(),
        "name_desc": Anime.name.desc(),
        "recent": Anime.last_scanned.desc(),
        "updated": Anime.last_scanned.desc(),
        "newest": Anime.last_scanned.desc(),
    }
    return order_key, mapping.get(order_key, Anime.name.asc())


def _resolve_anime_by_slug(slug):
    if not slug:
        return None

    normalized = slug.strip().strip("/")
    if not normalized:
        return None

    full_url_candidate = f"https://animesdigital.org/anime/{normalized}"
    anime = Anime.query.filter_by(url=full_url_candidate).first()
    if anime:
        return anime

    if "/" in normalized:
        suffix = f"/anime/{normalized}"
        return Anime.query.filter(Anime.url.ilike(f"%{suffix}")).first()

    return Anime.query.filter(Anime.url.ilike(f"%/anime/%/{normalized}")).first()


def _resolve_episode_url_by_id(episode_id):
    episode = (
        Episode.query.filter(Episode.url.like(f"%/video/%/{episode_id}%"))
        .order_by(Episode.last_updated.desc())
        .first()
    )
    if episode:
        return episode.url, episode

    prefix = (request.args.get("prefix") or "a").strip().lower()
    if not re.match(r"^[a-z0-9-]+$", prefix):
        prefix = "a"
    return f"https://animesdigital.org/video/{prefix}/{episode_id}", None


def _is_valid_http_url(value):
    try:
        parsed = urlparse(value)
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False
    return bool(parsed.netloc)


def _get_insert_builder(table):
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        return postgresql_insert(table), dialect
    if dialect == "sqlite":
        return sqlite_insert(table), dialect
    return None, dialect


def _cleanup_expired_embed_cache_if_needed(force=False):
    global _last_embed_cache_cleanup_at

    now = _utcnow()
    interval_seconds = max(
        60,
        int(current_app.config.get("EMBED_CACHE_CLEANUP_INTERVAL_SECONDS", 900)),
    )
    batch_size = max(
        100,
        int(current_app.config.get("EMBED_CACHE_CLEANUP_BATCH_SIZE", 1000)),
    )

    with _cache_cleanup_lock:
        if (
            not force
            and _last_embed_cache_cleanup_at
            and (now - _last_embed_cache_cleanup_at).total_seconds() < interval_seconds
        ):
            return 0

        try:
            deleted = delete_expired_embed_cache(batch_size=batch_size, now=now)
            _last_embed_cache_cleanup_at = now
            if deleted > 0:
                logger.info("Expired embed cache cleanup removed %s rows", deleted)
            return deleted
        except SQLAlchemyError as exc:
            db.session.rollback()
            logger.warning("Failed to cleanup expired embed cache: %s", exc)
            _last_embed_cache_cleanup_at = now
            return 0


def _load_embed_cache(url):
    entry = EmbedRequest.query.filter_by(url=url).first()
    if not entry:
        return None

    now = _utcnow()
    if not entry.expires_at or entry.expires_at <= now:
        return None

    try:
        return json.loads(entry.response_data)
    except json.JSONDecodeError:
        logger.warning("Invalid cached JSON for URL: %s", url)
        return None


def _save_to_embed_cache(url, data):
    ttl_hours = max(1, int(current_app.config.get("EMBED_CACHE_TTL_HOURS", 24)))
    expires_at = _utcnow() + timedelta(hours=ttl_hours)

    try:
        payload = json.dumps(data)
        now = _utcnow()
        row = {
            "url": url,
            "response_data": payload,
            "expires_at": expires_at,
            "timestamp": now,
        }
        insert_stmt, dialect = _get_insert_builder(EmbedRequest.__table__)
        if insert_stmt is not None and dialect in {"postgresql", "sqlite"}:
            stmt = insert_stmt.values([row])
            stmt = stmt.on_conflict_do_update(
                index_elements=["url"],
                set_={
                    "response_data": stmt.excluded.response_data,
                    "expires_at": stmt.excluded.expires_at,
                    "timestamp": stmt.excluded.timestamp,
                },
            )
            db.session.execute(stmt)
        else:
            entry = EmbedRequest.query.filter_by(url=url).first()
            if entry:
                entry.response_data = payload
                entry.expires_at = expires_at
                entry.timestamp = now
            else:
                db.session.add(
                    EmbedRequest(
                        url=url,
                        response_data=payload,
                        expires_at=expires_at,
                        timestamp=now,
                    )
                )

        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("DB error while saving embed cache: %s", exc)


def save_animes_to_db(anime_list):
    try:
        now = _utcnow()
        deduped = {}
        for item in anime_list:
            name = clean_name(item.get("name"))
            url = item.get("url")

            if not name or not url:
                continue
            deduped[url] = {
                "name": name,
                "url": url,
                "last_scanned": now,
            }

        if not deduped:
            return

        rows = list(deduped.values())
        insert_stmt, dialect = _get_insert_builder(Anime.__table__)
        if insert_stmt is not None and dialect in {"postgresql", "sqlite"}:
            stmt = insert_stmt.values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["url"],
                set_={
                    "name": stmt.excluded.name,
                    "last_scanned": stmt.excluded.last_scanned,
                },
            )
            db.session.execute(stmt)
        else:
            urls = [row["url"] for row in rows]
            existing = {
                anime.url: anime
                for anime in Anime.query.filter(Anime.url.in_(urls)).all()
            }
            for row in rows:
                anime = existing.get(row["url"])
                if anime:
                    anime.name = row["name"]
                    anime.last_scanned = row["last_scanned"]
                else:
                    db.session.add(
                        Anime(
                            name=row["name"],
                            url=row["url"],
                            last_scanned=row["last_scanned"],
                        )
                    )

        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Error saving animes to DB: %s", exc)


def _get_or_create_anime(anime_url, anime_title=None, item_type="series"):
    anime = Anime.query.filter_by(url=anime_url).first()

    if not anime:
        safe_title = clean_name(anime_title) if anime_title else None
        anime = Anime(
            name=safe_title or "Unknown Anime",
            url=anime_url,
            item_type=item_type,
            last_scanned=_utcnow(),
        )
        db.session.add(anime)
        db.session.flush()
        return anime

    if anime_title:
        safe_title = clean_name(anime_title)
        if safe_title:
            anime.name = safe_title
    
    if item_type and anime.item_type != item_type:
        anime.item_type = item_type

    return anime


def save_episodes_to_db(episode_list, anime_url=None, anime_title=None, item_type="series"):
    try:
        anime = None
        if anime_url:
            anime = _get_or_create_anime(anime_url, anime_title=anime_title, item_type=item_type)

        for item in episode_list:
            title = clean_name(item.get("title"))
            url = item.get("episode_url") or item.get("url")
            embed_url = item.get("embed_url")

            if not url or not embed_url:
                continue

            ep = Episode.query.filter_by(url=url).first()
            if ep:
                ep.title = title
                ep.embed_url = embed_url
                ep.last_updated = _utcnow()
                if anime and ep.anime_id != anime.id:
                    ep.anime_id = anime.id
            else:
                ep = Episode(
                    title=title,
                    url=url,
                    embed_url=embed_url,
                    anime_id=anime.id if anime else None,
                    last_updated=_utcnow(),
                )
                db.session.add(ep)

        if anime:
            anime.last_scanned = _utcnow()

        db.session.commit()
    except SQLAlchemyError as exc:
        db.session.rollback()
        logger.error("Error saving episodes to DB: %s", exc)


def _scrape_home_featured(force_refresh=False):
    cache_key = _build_home_featured_cache_key()
    
    # 1. Try RAM Cache (Fastest)
    if not force_refresh:
        cached_payload = cache.get(cache_key)
        if cached_payload:
            return {**cached_payload, "cached": True, "cache_source": "ram"}, 200

    home_url = request.args.get("url", "https://animesdigital.org/home").strip()
    
    # 2. Try DB Cache (Persistent after restart)
    if not force_refresh:
        db_cache = _load_embed_cache(f"persistent:{cache_key}")
        if db_cache:
            # We found it in DB. Let's update RAM cache for next time
            cache.set(
                cache_key,
                db_cache,
                timeout=current_app.config.get("HOME_FEATURED_CACHE_TTL_SECONDS", 1800),
            )
            return {**db_cache, "cached": True, "cache_source": "db"}, 200

    site_key, config = site_manager.get_config_for_url(home_url)
    if not site_key:
        return {"error": "URL domain not supported"}, 400

    url_patterns = config.get("url_patterns", {})

    try:
        with ScraperService() as scraper:
            context = scraper._get_context()
            page = context.new_page()
            try:
                result = scraper.extract_episodes(page, home_url, config, selector_key="home")
                if "error" in result:
                    return {"error": result["error"]}, 502

                featured = []
                for item in result.get("episode_urls", []):
                    item_url = item.get("url")
                    if not item_url:
                        continue

                    item_type = "unknown"
                    if scraper.match_pattern(item_url, url_patterns.get("episode", "")):
                        item_type = "episode"
                    elif scraper.match_pattern(item_url, url_patterns.get("anime_main", "")):
                        item_type = "anime"
                    elif scraper.match_pattern(item_url, url_patterns.get("movie", "")):
                        item_type = "movie"

                    featured.append(
                        {
                            "title": clean_name(item.get("title")),
                            "url": item_url,
                            "item_type": item_type,
                        }
                    )

                payload = {
                    "source": site_key,
                    "url": home_url,
                    "total_items": len(featured),
                    "results": featured,
                    "cached": False,
                }
                
                # Update RAM Cache
                cache.set(
                    cache_key,
                    payload,
                    timeout=current_app.config.get("HOME_FEATURED_CACHE_TTL_SECONDS", 1800),
                )
                
                # Update DB Cache (Persistent)
                _save_to_embed_cache(f"persistent:{cache_key}", payload)
                
                return payload, 200
            finally:
                context.close()
    except Exception as exc:
        logger.error("Unexpected error scraping home featured: %s", exc)
        return {"error": "Internal server error"}, 500


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


@bp.route("/anime/<path:slug>", methods=["GET"])
@limiter.limit("120 per minute")
def get_anime(slug):
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    anime = _resolve_anime_by_slug(slug)
    if not anime:
        return jsonify({"error": "Anime not found"}), 404

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


@bp.route("/home/featured", methods=["GET"])
@limiter.limit("30 per minute")
def get_home_featured():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    force_refresh = _parse_bool(request.args.get("force"))
    payload, status = _scrape_home_featured(force_refresh=force_refresh)
    return jsonify(payload), status


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


@bp.route("/search", methods=["GET"])
@limiter.limit("60 per minute")
def search_animes():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": 'Query parameter "q" is required'}), 400

    cache_key = _build_search_cache_key(query)
    cached = cache.get(cache_key)
    if cached:
        return jsonify({**cached, "cached": True}), 200

    try:
        escaped_query = _escape_like_pattern(query)
        results = (
            Anime.query.filter(Anime.name.ilike(f"%{escaped_query}%", escape="\\"))
            .limit(current_app.config.get("SEARCH_LIMIT", 50))
            .all()
        )
        payload = {
            "query": query,
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


@bp.route("/get-embed", methods=["GET"])
@limiter.limit("10 per minute")
def get_embed():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    target_url = request.args.get("url", "").strip()
    force_refresh = _parse_bool(request.args.get("force"))

    if not target_url:
        return jsonify({"error": 'Parameter "url" is required'}), 400

    if not _is_valid_http_url(target_url):
        return jsonify({"error": "Invalid URL"}), 400

    site_key, config = site_manager.get_config_for_url(target_url)
    if not site_key:
        return jsonify({"error": "URL domain not supported"}), 400

    _cleanup_expired_embed_cache_if_needed()

    if not force_refresh:
        cached_payload = _load_embed_cache(target_url)
        if cached_payload:
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "embed_requests"
            return jsonify(cached_payload), 200

    url_patterns = config.get("url_patterns", {})

    try:
        with ScraperService() as scraper:
            context = scraper._get_context()
            page = context.new_page()

            try:
                response_payload = {}

                if scraper.match_pattern(target_url, url_patterns.get("home", "")):
                    result = scraper.extract_episodes(page, target_url, config, selector_key="home")
                    if "error" in result:
                        return jsonify(result), 502

                    items = result.get("episode_urls", [])
                    embeds = []
                    for item in items:
                        ep_url = item["url"]
                        if scraper.match_pattern(ep_url, url_patterns.get("episode", "")):
                            embed_info = scraper.extract_embed(page, ep_url, config)
                            if "title" not in embed_info:
                                embed_info["title"] = item.get("title")
                            embed_info["item_type"] = "episode"
                            embeds.append(embed_info)
                        elif scraper.match_pattern(ep_url, url_patterns.get("anime_main", "")):
                            embeds.append(
                                {
                                    "title": item.get("title"),
                                    "url": ep_url,
                                    "item_type": "series",
                                    "note": "Main page link",
                                }
                            )
                        elif scraper.match_pattern(ep_url, url_patterns.get("movie", "")):
                            embed_info = scraper.extract_embed(page, ep_url, config)
                            if "title" not in embed_info:
                                embed_info["title"] = item.get("title")
                            embed_info["item_type"] = "movie"
                            embeds.append(embed_info)

                    save_episodes_to_db(embeds)

                    response_payload = {
                        "type": "home",
                        "source": site_key,
                        "url": target_url,
                        "total_scraped": len(embeds),
                        "results": embeds,
                        "cached": False,
                    }

                elif scraper.match_pattern(target_url, url_patterns.get("anime_main", "")):
                    anime = Anime.query.filter_by(url=target_url).first()
                    ttl_hours = current_app.config.get("EMBED_CACHE_TTL_HOURS", 24)

                    if not force_refresh and anime and anime.episodes:
                        max_age = timedelta(hours=ttl_hours)
                        if anime.last_scanned and (_utcnow() - anime.last_scanned) < max_age:
                            logger.info("Returning cached episodes for: %s", anime.name)
                            response_payload = {
                                "type": "anime_series",
                                "anime_title": anime.name,
                                "source_url": target_url,
                                "total_episodes": len(anime.episodes),
                                "episodes": [ep.to_dict() for ep in anime.episodes],
                                "cached": True,
                                "cache_source": "animes/episodes",
                            }
                            _save_to_embed_cache(target_url, response_payload)
                            return jsonify(response_payload), 200

                    result = scraper.extract_episodes(page, target_url, config)
                    if "error" in result:
                        return jsonify(result), 502

                    items = result.get("episode_urls", [])
                    embeds = []
                    for item in items:
                        ep_url = item["url"]
                        embed_info = scraper.extract_embed(page, ep_url, config)
                        if "title" not in embed_info:
                            embed_info["title"] = item.get("title")
                        embeds.append(embed_info)

                    save_episodes_to_db(
                        embeds,
                        anime_url=target_url,
                        anime_title=result.get("title"),
                    )

                    response_payload = {
                        "type": "anime_series",
                        "anime_title": clean_name(result.get("title")),
                        "source_url": target_url,
                        "total_episodes": result.get("total_items"),
                        "episodes": embeds,
                        "cached": False,
                    }

                elif scraper.match_pattern(
                    target_url,
                    config.get("selectors", {}).get("directory", {}).get("url_pattern", ""),
                ):
                    result = scraper.extract_directory(page, target_url, config)
                    if "error" in result:
                        return jsonify(result), 502

                    cleaned_animes = [
                        {"name": clean_name(a.get("name")), "url": a.get("url")}
                        for a in result.get("animes", [])
                    ]

                    save_animes_to_db(cleaned_animes)

                    response_payload = {
                        "type": "directory",
                        "source": site_key,
                        "url": target_url,
                        "total_pages": result.get("total_pages"),
                        "count": len(cleaned_animes),
                        "animes": cleaned_animes,
                        "cached": False,
                    }

                elif scraper.match_pattern(target_url, url_patterns.get("episode", "")):
                    embed_info = scraper.extract_embed(page, target_url, config)
                    save_episodes_to_db([embed_info])

                    response_payload = {
                        "type": "single_episode",
                        "title": clean_name(embed_info.get("title")),
                        "url": target_url,
                        "embed_url": embed_info.get("embed_url"),
                        "cached": False,
                    }

                elif scraper.match_pattern(target_url, url_patterns.get("movie", "")):
                    embed_info = scraper.extract_embed(page, target_url, config)
                    save_episodes_to_db(
                        [embed_info],
                        anime_url=target_url,
                        anime_title=embed_info.get("title"),
                        item_type="movie"
                    )

                    response_payload = {
                        "type": "single_movie",
                        "title": clean_name(embed_info.get("title")),
                        "url": target_url,
                        "embed_url": embed_info.get("embed_url"),
                        "cached": False,
                    }

                else:
                    return jsonify({"error": "URL pattern not recognized"}), 400

                if response_payload:
                    _save_to_embed_cache(target_url, response_payload)

                return jsonify(response_payload), 200
            finally:
                context.close()

    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/reload-config", methods=["POST"])
@limiter.limit("5 per minute")
def reload_config():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    if site_manager.reload_configs():
        cache.clear()
        return jsonify({"message": "Configs reloaded"}), 200
    return jsonify({"error": "Failed to reload configs"}), 500


@bp.route("/maintenance/cleanup-cache", methods=["POST"])
@limiter.limit("2 per minute")
def cleanup_cache():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    deleted = _cleanup_expired_embed_cache_if_needed(force=True)
    return jsonify({"deleted_rows": deleted}), 200


@bp.route("/health", methods=["GET"])
@limiter.exempt
def health():
    return jsonify({
        "status": "ok",
        "timestamp": _utcnow().isoformat(),
    }), 200
