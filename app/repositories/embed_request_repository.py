import json
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.embed import EmbedRequest, db
from app.api.utils import _utcnow

logger = logging.getLogger(__name__)


class EmbedRequestRepository:
    @staticmethod
    def find_by_url(url: str) -> Optional[EmbedRequest]:
        return EmbedRequest.query.filter_by(url=url).first()

    @staticmethod
    def get_cached(url: str):
        entry = EmbedRequest.query.filter_by(url=url).first()
        if not entry:
            return None, "miss"
        now = _utcnow()
        if entry.expires_at and entry.expires_at > now:
            try:
                return json.loads(entry.response_data), "fresh"
            except (json.JSONDecodeError, TypeError):
                return None, "miss"
        try:
            return json.loads(entry.response_data), "stale"
        except (json.JSONDecodeError, TypeError):
            return None, "miss"

    @staticmethod
    def upsert(url: str, data: dict, ttl_hours: int = 24) -> bool:
        expires_at = _utcnow() + timedelta(hours=ttl_hours)
        payload = json.dumps(data)
        now = _utcnow()

        try:
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
                entry = EmbedRequest.query.filter_by(url=url).first()
                if entry:
                    entry.response_data = payload
                    entry.expires_at = expires_at
                    entry.timestamp = now
                else:
                    db.session.add(EmbedRequest(
                        url=url, response_data=payload, expires_at=expires_at, timestamp=now,
                    ))
            return True
        except Exception as exc:
            logger.error("Failed to upsert embed request %s: %s", url, exc)
            return False

    @staticmethod
    def delete_by_url(url: str) -> bool:
        try:
            EmbedRequest.query.filter_by(url=url).delete()
            return True
        except Exception as exc:
            logger.error("Failed to delete embed request %s: %s", url, exc)
            return False

    @staticmethod
    def count() -> int:
        return EmbedRequest.query.count()
