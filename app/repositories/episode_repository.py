import logging
from typing import Optional, List

from sqlalchemy.orm import selectinload

from app.models.embed import Episode, db
from app.api.utils import _utcnow, _build_url
from app.utils.helpers import clean_name, extract_audio_type, format_info

logger = logging.getLogger(__name__)


class EpisodeRepository:
    @staticmethod
    def find_by_id(episode_id: int) -> Optional[Episode]:
        return Episode.query.get(episode_id)

    @staticmethod
    def find_by_url(url: str) -> Optional[Episode]:
        return Episode.query.filter_by(url=url).first()

    @staticmethod
    def find_by_id_with_prefix(episode_id: str, prefix: str = "a"):
        episode = (
            Episode.query.filter(Episode.url.like(f"%/video/%/{episode_id}%"))
            .order_by(Episode.last_updated.desc())
            .first()
        )
        if episode:
            return episode.url.rstrip("/:"), episode
        episode_id_clean = episode_id.rstrip("/:")
        return _build_url(f"/video/{prefix}/{episode_id_clean}"), None

    @staticmethod
    def paginate_recent(page: int, limit: int):
        query_builder = (
            Episode.query
            .options(selectinload(Episode.anime))
            .order_by(Episode.last_updated.desc(), Episode.id.desc())
        )
        total = query_builder.count()
        items = query_builder.offset((page - 1) * limit).limit(limit).all()
        return items, total

    @staticmethod
    def find_by_anime_id(anime_id: int, page: int = 1, limit: int = 30):
        query = Episode.query.filter_by(anime_id=anime_id).order_by(Episode.id.desc())
        total = query.count()
        items = query.offset((page - 1) * limit).limit(limit).all()
        return items, total

    @staticmethod
    def delete(episode: Episode):
        db.session.delete(episode)

    @staticmethod
    def count() -> int:
        return Episode.query.count()
