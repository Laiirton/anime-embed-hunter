import logging
from typing import Optional

from app.models.embed import Favorite, db
from app.api.utils import _utcnow

logger = logging.getLogger(__name__)


class FavoriteRepository:
    @staticmethod
    def find_by_id(fav_id: int) -> Optional[Favorite]:
        return Favorite.query.get(fav_id)

    @staticmethod
    def find_by_profile_and_url(profile_key: str, anime_url: str) -> Optional[Favorite]:
        return Favorite.query.filter_by(profile_key=profile_key, anime_url=anime_url).first()

    @staticmethod
    def paginate_by_profile(profile_key: str, page: int, limit: int):
        query = Favorite.query.filter_by(profile_key=profile_key).order_by(
            Favorite.updated_at.desc(), Favorite.id.desc()
        )
        total = query.count()
        items = query.offset((page - 1) * limit).limit(limit).all()
        return items, total

    @staticmethod
    def upsert(profile_key: str, anime_url: str, anime_name: str,
               image_url: str = None, anime_id: int = None):
        now = _utcnow()
        favorite = Favorite.query.filter_by(profile_key=profile_key, anime_url=anime_url).first()
        if favorite:
            favorite.anime_name = anime_name
            if image_url:
                favorite.image_url = image_url
            if anime_id is not None:
                favorite.anime_id = anime_id
            favorite.updated_at = now
            created = False
        else:
            favorite = Favorite(
                profile_key=profile_key,
                anime_id=anime_id,
                anime_name=anime_name,
                anime_url=anime_url,
                image_url=image_url or None,
            )
            db.session.add(favorite)
            created = True
        return favorite, created

    @staticmethod
    def delete_by_id_and_profile(fav_id: int, profile_key: str) -> bool:
        favorite = Favorite.query.filter_by(id=fav_id, profile_key=profile_key).first()
        if not favorite:
            return False
        db.session.delete(favorite)
        return True

    @staticmethod
    def count() -> int:
        return Favorite.query.count()
