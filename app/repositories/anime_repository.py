import logging
from typing import Optional, List

from app.models.embed import Anime, db
from app.api.utils import _utcnow, _build_url
from app.utils.helpers import clean_name, format_info

logger = logging.getLogger(__name__)


class AnimeRepository:
    @staticmethod
    def find_by_id(anime_id: int) -> Optional[Anime]:
        return Anime.query.get(anime_id)

    @staticmethod
    def find_by_url(url: str) -> Optional[Anime]:
        return Anime.query.filter_by(url=url).first()

    @staticmethod
    def find_by_slug(slug: str) -> Optional[Anime]:
        normalized = slug.strip().strip("/")
        if not normalized:
            return None
        full_url = _build_url(f"/anime/{normalized}")
        anime = Anime.query.filter_by(url=full_url).first()
        if anime:
            return anime
        if "/" in normalized:
            suffix = f"/anime/{normalized}"
            return Anime.query.filter(Anime.url.ilike(f"%{suffix}")).first()
        return Anime.query.filter(Anime.url.ilike(f"%/anime/%/{normalized}")).first()

    @staticmethod
    def paginate(page: int, limit: int, filters=None, order=None):
        query = Anime.query
        if filters:
            query = query.filter(*filters)
        if order:
            query = query.order_by(order)
        else:
            query = query.order_by(Anime.name.asc())
        total = query.count()
        items = query.offset((page - 1) * limit).limit(limit).all()
        return items, total

    @staticmethod
    def search(query_str: str, limit: int = 50) -> List[Anime]:
        escaped = query_str.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return Anime.query.filter(
            Anime.name.ilike(f"%{escaped}%", escape="\\")
        ).order_by(Anime.name.asc()).limit(limit).all()

    @staticmethod
    def get_or_create(url: str, name: str = None, item_type: str = "series",
                      latest_episode_info: str = None) -> Anime:
        anime = Anime.query.filter_by(url=url).first()
        now = _utcnow()
        if not anime:
            safe_name = clean_name(name) if name else None
            anime = Anime(
                name=safe_name or "Unknown Anime",
                url=url,
                item_type=item_type,
                latest_episode_info=format_info(latest_episode_info) if latest_episode_info else None,
                last_scanned=now,
            )
            db.session.add(anime)
            db.session.flush()
        else:
            if name:
                safe_name = clean_name(name)
                if safe_name:
                    anime.name = safe_name
            if latest_episode_info:
                anime.latest_episode_info = format_info(latest_episode_info)
        return anime

    @staticmethod
    def delete(anime: Anime):
        db.session.delete(anime)

    @staticmethod
    def count() -> int:
        return Anime.query.count()
