import logging
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.models.embed import db, Anime
from app.utils.helpers import clean_name

logger = logging.getLogger(__name__)

_session = None
_cache = {}
_CACHE_TTL_SECONDS = 3600


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        retry_strategy = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
    return _session


def _get_cached(key):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL_SECONDS:
        return entry["value"]
    return None


def _set_cached(key, value):
    _cache[key] = {"value": value, "ts": time.time()}
    if len(_cache) > 500:
        now = time.time()
        stale = [k for k, v in _cache.items() if (now - v["ts"]) > _CACHE_TTL_SECONDS]
        for k in stale:
            del _cache[k]


def fetch_kitsu_metadata(anime_name):
    cached = _get_cached(anime_name)
    if cached:
        return cached

    try:
        session = _get_session()
        res = session.get(
            f'https://kitsu.io/api/edge/anime?filter[text]={requests.utils.quote(anime_name)}&include=genres',
            timeout=10
        )
        if res.status_code == 200:
            data = res.json().get('data', [])
            if data:
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

                result = {
                    "cover_url": cover_url,
                    "status": status,
                    "total_episodes": total_episodes,
                    "rating": rating,
                    "year": year
                }
                _set_cached(anime_name, result)
                return result
    except requests.exceptions.Timeout:
        logger.debug("Timeout fetching metadata for %s", anime_name)
    except requests.exceptions.ConnectionError as exc:
        logger.debug("Connection error fetching metadata for %s: %s", anime_name, exc)
    except Exception as e:
        logger.debug("Failed to fetch metadata for %s: %s", anime_name, e)
    return None


def populate_anime_metadata(animes):
    if not animes:
        return animes

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
            logger.error("Failed to save fetched metadata to DB: %s", e)

    return animes


def populate_anime_metadata_single(anime):
    return populate_anime_metadata([anime])[0]


def populate_metadata_for_dicts(items):
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
