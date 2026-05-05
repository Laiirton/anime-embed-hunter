import json
import logging
from datetime import timedelta
from threading import Lock

from flask import current_app
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from app.models.embed import Anime, EmbedRequest, Episode, db
from app.services.cache_maintenance import delete_expired_embed_cache
from app.utils.helpers import clean_name
from app.api.utils import _utcnow

logger = logging.getLogger(__name__)
_cache_cleanup_lock = Lock()
_last_embed_cache_cleanup_at = None

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
