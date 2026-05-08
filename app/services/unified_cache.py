"""
Unified cache service that consolidates all caching strategies.

Strategies:
1. Volatile cache (Flask-Caching/Redis/RAM) - for fast, short-lived data (catalog, search)
2. Persistent cache (EmbedRequest DB table) - for durable, TTL-based data (embeds, home)
"""

import json
import logging
from datetime import timedelta
from typing import Optional, Tuple, Any

from flask import current_app

from app.models.embed import EmbedRequest, db
from app.api.utils import _utcnow

logger = logging.getLogger(__name__)

# Cache type constants
CACHE_TYPE_VOLATILE = "volatile"  # Flask-Caching (RAM/Redis)
CACHE_TYPE_PERSISTENT = "persistent"  # EmbedRequest DB table


def get_volatile_cache():
    """Get the Flask-Caching instance from the app."""
    from app import cache
    return cache


def volatile_get(key: str) -> Tuple[Optional[Any], bool]:
    """
    Get value from volatile cache (Flask-Caching).
    Returns: (value, hit) where hit is True if cache hit occurred.
    """
    cache = get_volatile_cache()
    value = cache.get(key)
    if value is not None:
        return value, True
    return None, False


def volatile_set(key: str, value: Any, timeout: int = None) -> bool:
    """
    Set value in volatile cache (Flask-Caching).
    Returns: True if successful, False otherwise.
    """
    cache = get_volatile_cache()
    if timeout is None:
        timeout = current_app.config.get("CACHE_DEFAULT_TIMEOUT", 300)
    try:
        cache.set(key, value, timeout=timeout)
        return True
    except Exception as exc:
        logger.warning("Failed to set volatile cache key %s: %s", key, exc)
        return False


def volatile_delete(key: str) -> bool:
    """Delete a key from volatile cache."""
    cache = get_volatile_cache()
    try:
        cache.delete(key)
        return True
    except Exception as exc:
        logger.warning("Failed to delete volatile cache key %s: %s", key, exc)
        return False


def persistent_get(url: str) -> Tuple[Optional[Any], str]:
    """
    Get value from persistent cache (EmbedRequest DB table).
    Returns: (value, status) where status is one of: "fresh", "stale", "miss"
    """
    entry = EmbedRequest.query.filter_by(url=url).first()
    if not entry:
        return None, "miss"

    now = _utcnow()
    if entry.expires_at and entry.expires_at > now:
        try:
            return json.loads(entry.response_data), "fresh"
        except (json.JSONDecodeError, TypeError):
            return None, "miss"

    # Expired but still has data - return as "stale"
    try:
        return json.loads(entry.response_data), "stale"
    except (json.JSONDecodeError, TypeError):
        return None, "miss"


def persistent_set(url: str, data: Any, ttl_hours: int = None) -> bool:
    """
    Set value in persistent cache (EmbedRequest DB table).
    Uses upsert to handle duplicates.
    Returns: True if successful, False otherwise.
    """
    if ttl_hours is None:
        ttl_hours = max(1, int(current_app.config.get("EMBED_CACHE_TTL_HOURS", 24)))

    expires_at = _utcnow() + timedelta(hours=ttl_hours)
    payload = json.dumps(data)
    now = _utcnow()

    try:
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        dialect = db.session.get_bind().dialect.name
        row = {
            "url": url,
            "response_data": payload,
            "expires_at": expires_at,
            "timestamp": now,
        }

        if dialect == "postgresql":
            stmt = postgresql_insert(EmbedRequest).values([row])
            stmt = stmt.on_conflict_do_update(
                index_elements=["url"],
                set_={
                    "response_data": stmt.excluded.response_data,
                    "expires_at": stmt.excluded.expires_at,
                    "timestamp": stmt.excluded.timestamp,
                },
            )
            db.session.execute(stmt)
        elif dialect == "sqlite":
            stmt = sqlite_insert(EmbedRequest).values([row])
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
            # Generic fallback
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
        return True
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to set persistent cache for %s: %s", url, exc)
        return False


def persistent_delete(url: str) -> bool:
    """Delete an entry from persistent cache."""
    try:
        EmbedRequest.query.filter_by(url=url).delete()
        db.session.commit()
        return True
    except Exception as exc:
        db.session.rollback()
        logger.error("Failed to delete persistent cache for %s: %s", url, exc)
        return False


# Unified cache interface


def get_cached(key: str, cache_type: str = CACHE_TYPE_VOLATILE) -> Tuple[Optional[Any], str]:
    """
    Unified cache getter.
    Returns: (value, status) where status is one of: "hit", "stale", "miss"
    """
    if cache_type == CACHE_TYPE_VOLATILE:
        value, hit = volatile_get(key)
        return value, "hit" if hit else "miss"
    elif cache_type == CACHE_TYPE_PERSISTENT:
        return persistent_get(key)
    return None, "miss"


def set_cached(key: str, value: Any, cache_type: str = CACHE_TYPE_VOLATILE, **kwargs) -> bool:
    """
    Unified cache setter.
    kwargs may include 'timeout' for volatile or 'ttl_hours' for persistent.
    """
    if cache_type == CACHE_TYPE_VOLATILE:
        return volatile_set(key, value, timeout=kwargs.get("timeout"))
    elif cache_type == CACHE_TYPE_PERSISTENT:
        return persistent_set(key, value, ttl_hours=kwargs.get("ttl_hours"))
    return False


def delete_cached(key: str, cache_type: str = CACHE_TYPE_VOLATILE) -> bool:
    """Unified cache deleter."""
    if cache_type == CACHE_TYPE_VOLATILE:
        return volatile_delete(key)
    elif cache_type == CACHE_TYPE_PERSISTENT:
        return persistent_delete(key)
    return False


def clear_expired_persistent(batch_size: int = None) -> int:
    """
    Clear expired entries from persistent cache.
    Returns: number of deleted entries.
    """
    from app.services.cache_maintenance import delete_expired_embed_cache
    if batch_size is None:
        batch_size = current_app.config.get("EMBED_CACHE_CLEANUP_BATCH_SIZE", 1000)
    return delete_expired_embed_cache(batch_size=batch_size)
