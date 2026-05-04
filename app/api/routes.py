from datetime import datetime, timedelta, timezone
import hmac
import json
import logging
from threading import Lock
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app import cache, limiter
from app.models.embed import Anime, EmbedRequest, Episode, db
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


def _get_or_create_anime(anime_url, anime_title=None):
    anime = Anime.query.filter_by(url=anime_url).first()

    if not anime:
        safe_title = clean_name(anime_title) if anime_title else None
        anime = Anime(
            name=safe_title or "Unknown Anime",
            url=anime_url,
            last_scanned=_utcnow(),
        )
        db.session.add(anime)
        db.session.flush()
        return anime

    if anime_title:
        safe_title = clean_name(anime_title)
        if safe_title:
            anime.name = safe_title

    return anime


def save_episodes_to_db(episode_list, anime_url=None, anime_title=None):
    try:
        anime = None
        if anime_url:
            anime = _get_or_create_anime(anime_url, anime_title=anime_title)

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
                                    "item_type": "series_or_movie",
                                    "note": "Main page link",
                                }
                            )

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
