import logging
from typing import Optional

from app.models.embed import HistoryEntry, db
from app.api.utils import _utcnow

logger = logging.getLogger(__name__)


class HistoryRepository:
    @staticmethod
    def find_by_id(hist_id: int) -> Optional[HistoryEntry]:
        return HistoryEntry.query.get(hist_id)

    @staticmethod
    def find_by_profile_and_url(profile_key: str, content_url: str) -> Optional[HistoryEntry]:
        return HistoryEntry.query.filter_by(profile_key=profile_key, content_url=content_url).first()

    @staticmethod
    def paginate_by_profile(profile_key: str, page: int, limit: int):
        query = HistoryEntry.query.filter_by(profile_key=profile_key).order_by(
            HistoryEntry.last_seen.desc(), HistoryEntry.id.desc()
        )
        total = query.count()
        items = query.offset((page - 1) * limit).limit(limit).all()
        return items, total

    @staticmethod
    def upsert(profile_key: str, content_url: str, title: str,
               image_url: str = None, anime_id: int = None, episode_id: int = None):
        now = _utcnow()
        entry = HistoryEntry.query.filter_by(profile_key=profile_key, content_url=content_url).first()
        if entry:
            entry.title = title
            if image_url:
                entry.image_url = image_url
            if anime_id is not None:
                entry.anime_id = anime_id
            if episode_id is not None:
                entry.episode_id = episode_id
            entry.watch_count = max(1, int(entry.watch_count or 1)) + 1
            entry.last_seen = now
            created = False
        else:
            entry = HistoryEntry(
                profile_key=profile_key,
                anime_id=anime_id,
                episode_id=episode_id,
                title=title,
                content_url=content_url,
                image_url=image_url or None,
                watch_count=1,
            )
            db.session.add(entry)
            created = True
        return entry, created

    @staticmethod
    def delete_by_id_and_profile(hist_id: int, profile_key: str) -> bool:
        entry = HistoryEntry.query.filter_by(id=hist_id, profile_key=profile_key).first()
        if not entry:
            return False
        db.session.delete(entry)
        return True

    @staticmethod
    def count() -> int:
        return HistoryEntry.query.count()
