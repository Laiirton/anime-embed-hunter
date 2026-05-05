import logging
import requests
from concurrent.futures import ThreadPoolExecutor
from app.models.embed import db

logger = logging.getLogger(__name__)

def fetch_single_cover(anime_id, anime_name):
    """Fetch cover for a single anime from Kitsu API."""
    try:
        res = requests.get(
            f'https://kitsu.io/api/edge/anime?filter[text]={requests.utils.quote(anime_name)}',
            timeout=5
        )
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                # Get medium or original
                images = data[0].get('attributes', {}).get('posterImage', {})
                cover = images.get('medium') or images.get('original') or images.get('large')
                return anime_id, cover
    except Exception as e:
        logger.debug(f"Failed to fetch cover for {anime_name}: {e}")
    return anime_id, None

def populate_covers(animes):
    """
    Given a list of Anime objects, checks which ones lack a cover_url,
    fetches them concurrently from Kitsu API, updates the objects and commits.
    Returns the updated list.
    """
    missing_covers = [anime for anime in animes if not anime.cover_url]
    
    if not missing_covers:
        return animes

    updates = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(fetch_single_cover, anime.id, anime.name) 
            for anime in missing_covers
        ]
        for future in futures:
            aid, cover = future.result()
            if cover:
                updates[aid] = cover

    if updates:
        for anime in missing_covers:
            if anime.id in updates:
                anime.cover_url = updates[anime.id]
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to save fetched covers to DB: {e}")

    return animes

def populate_cover(anime):
    """Helper to populate a single anime."""
    populate_covers([anime])
    return anime

def populate_covers_for_dicts(items):
    """
    Given a list of dictionaries (with 'title' and 'cover_url'),
    fetches missing covers concurrently from Kitsu API and mutates the dictionaries.
    """
    missing_covers = [item for item in items if not item.get("cover_url")]
    
    if not missing_covers:
        return items

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_single_cover, idx, item.get("title") or item.get("name")): item
            for idx, item in enumerate(missing_covers)
        }
        for future in futures:
            idx, cover = future.result()
            if cover:
                item = futures[future]
                item["cover_url"] = cover

    return items
