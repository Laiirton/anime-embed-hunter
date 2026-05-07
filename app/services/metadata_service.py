import logging
import requests
from concurrent.futures import ThreadPoolExecutor
from app.models.embed import db, Anime
from app.utils.helpers import clean_name

logger = logging.getLogger(__name__)

def fetch_kitsu_metadata(anime_name):
    """Fetch full metadata for an anime from Kitsu API."""
    try:
        # Search by name with genres included
        res = requests.get(
            f'https://kitsu.io/api/edge/anime?filter[text]={requests.utils.quote(anime_name)}&include=genres',
            timeout=10
        )
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data:
                # Try to find best match by comparing names
                best_match = data[0]
                for item in data:
                    attr = item.get('attributes', {})
                    titles = [
                        attr.get('canonicalTitle'),
                        attr.get('en'),
                        attr.get('en_jp'),
                        attr.get('ja_jp')
                    ]
                    if any(t and clean_name(t.lower()) == clean_name(anime_name.lower()) for t in titles):
                        best_match = item
                        break
                
                attr = best_match.get('attributes', {})
                
                # Extract fields
                poster = attr.get('posterImage', {})
                cover_url = poster.get('medium') or poster.get('original') or poster.get('large')
                
                status_map = {
                    "current": "Lançamento",
                    "finished": "Finalizado",
                    "tba": "TBA",
                    "unreleased": "Não Lançado",
                    "upcoming": "Em Breve"
                }
                status = status_map.get(attr.get('status'), attr.get('status'))
                
                total_episodes = attr.get('episodeCount')
                rating = attr.get('averageRating')
                
                year = None
                start_date = attr.get('startDate')
                if start_date:
                    try:
                        year = int(start_date.split('-')[0])
                    except (ValueError, IndexError):
                        pass

                return {
                    "cover_url": cover_url,
                    "status": status,
                    "total_episodes": total_episodes,
                    "rating": rating,
                    "year": year
                }
    except Exception as e:
        logger.debug(f"Failed to fetch metadata for {anime_name}: {e}")
    return None

def populate_anime_metadata(animes):
    """
    Given a list of Anime objects, fetches missing metadata concurrently.
    """
    if not animes:
        return animes

    # Filtrar animes que faltam metadados (total_episodes, status ou genres)
    missing_metadata = [a for a in animes if not a.total_episodes or not a.status or not a.genres]
    
    if not missing_metadata:
        return animes

    updates = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_kitsu_metadata, anime.name): anime
            for anime in missing_metadata
        }
        for future in futures:
            anime = futures[future]
            metadata = future.result()
            if metadata:
                updates[anime.id] = metadata

    if updates:
        for anime in missing_metadata:
            if anime.id in updates:
                m = updates[anime.id]
                if m.get("cover_url"): anime.cover_url = m["cover_url"]
                if m.get("status"): anime.status = m["status"]
                if m.get("total_episodes"): anime.total_episodes = m["total_episodes"]
                if m.get("synopsis"): anime.synopsis = m["synopsis"]
                if m.get("rating"): anime.rating = m["rating"]
                if m.get("year"): anime.year = m["year"]
                if m.get("genres"): anime.genres = m["genres"]
        
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to save fetched metadata to DB: {e}")

    return animes

def populate_anime_metadata_single(anime):
    """Helper to populate metadata for a single anime object."""
    return populate_anime_metadata([anime])[0]

def populate_metadata_for_dicts(items):
    """
    Given a list of dictionaries, fetches missing metadata concurrently and mutates them.
    """
    if not items:
        return items

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(fetch_kitsu_metadata, item.get("name") or item.get("title")): item
            for item in items
        }
        for future in futures:
            metadata = future.result()
            if metadata:
                item = futures[future]
                item.update(metadata)

    return items
