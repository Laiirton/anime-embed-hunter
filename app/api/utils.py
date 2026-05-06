import hmac
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import current_app, request
from app.models.embed import Anime, Episode

def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def check_api_key():
    api_key = request.headers.get("X-API-KEY", "")
    expected = current_app.config["API_KEY"]
    return hmac.compare_digest(api_key, expected)

def _parse_bool(value):
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def _escape_like_pattern(value):
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

def _build_search_cache_key(query):
    return f"search:{query.lower()}"

def _build_home_featured_cache_key():
    return "home:featured"

def _parse_positive_int(value, default, minimum=1, maximum=1000):
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))

def _serialize_anime(anime):
    slug = ""
    marker = "/anime/"
    if anime.url and marker in anime.url:
        slug = anime.url.split(marker, 1)[1].strip("/")
    return {
        "id": anime.id,
        "name": anime.name,
        "url": anime.url,
        "slug": slug,
        "cover_url": getattr(anime, "cover_url", None),
        "status": getattr(anime, "status", None),
        "total_episodes": getattr(anime, "total_episodes", 0),
        "synopsis": getattr(anime, "synopsis", None),
        "rating": getattr(anime, "rating", None),
        "year": getattr(anime, "year", None),
        "genres": getattr(anime, "genres", None),
        "audio_type": getattr(anime, "audio_type", None),
        "latest_episode_info": getattr(anime, "latest_episode_info", None),
        "item_type": anime.item_type,
        "last_scanned": anime.last_scanned.isoformat() if anime.last_scanned else None,
        "episodes_count": len(anime.episodes) if hasattr(anime, "episodes") else 0,
    }

def _serialize_episode(episode):
    payload = episode.to_dict()
    payload["id"] = episode.id
    payload["anime_id"] = episode.anime_id
    if episode.anime:
        payload["anime_name"] = episode.anime.name
        payload["anime_url"] = episode.anime.url
    else:
        payload["anime_name"] = None
        payload["anime_url"] = None
    return payload

def _resolve_profile_key(payload=None):
    payload = payload or {}
    raw = (
        request.headers.get("X-USER-ID")
        or request.args.get("user_id")
        or payload.get("user_id")
        or payload.get("profile_key")
        or "default"
    )
    normalized = re.sub(r"[^a-zA-Z0-9_.-]", "-", str(raw).strip())
    normalized = normalized[:120].strip("-")
    return normalized or "default"

def _infer_audio_filter_expression(filter_audio):
    value = (filter_audio or "").strip().lower()
    if value in {"dublado", "dub", "pt-br"}:
        return Anime.name.ilike("%dublado%")
    if value in {"legendado", "sub"}:
        return ~Anime.name.ilike("%dublado%")
    return None

def _build_catalog_filters():
    filters = []
    unsupported = []

    search = request.args.get("search")
    if search and search != "0":
        escaped = _escape_like_pattern(search.strip())
        filters.append(Anime.name.ilike(f"%{escaped}%", escape="\\"))

    letter = (request.args.get("filter_letter") or "").strip().lower()
    if letter and letter != "0":
        filters.append(Anime.name.ilike(f"{_escape_like_pattern(letter)}%", escape="\\"))

    audio_filter = _infer_audio_filter_expression(request.args.get("filter_audio"))
    if audio_filter is not None:
        filters.append(audio_filter)

    type_url = (request.args.get("type_url") or "").strip().lower()
    if type_url and type_url not in {"animes", "anime", "catalogo"}:
        unsupported.append("type_url")

    if request.args.get("filter_genre_add"):
        unsupported.append("filter_genre_add")
    if request.args.get("filter_genre_del"):
        unsupported.append("filter_genre_del")

    return filters, sorted(set(unsupported))

def _resolve_catalog_order():
    order_key = (
        request.args.get("order")
        or request.args.get("filter_order")
        or "name"
    ).strip().lower()

    mapping = {
        "name": Anime.name.asc(),
        "az": Anime.name.asc(),
        "name_asc": Anime.name.asc(),
        "za": Anime.name.desc(),
        "name_desc": Anime.name.desc(),
        "recent": Anime.last_scanned.desc(),
        "updated": Anime.last_scanned.desc(),
        "newest": Anime.last_scanned.desc(),
    }
    return order_key, mapping.get(order_key, Anime.name.asc())

def _resolve_anime_by_slug(slug):
    if not slug:
        return None

    normalized = slug.strip().strip("/")
    if not normalized:
        return None

    full_url_candidate = f"https://animesdigital.org/anime/{normalized}"
    anime = Anime.query.filter_by(url=full_url_candidate).first()
    if anime:
        return anime

    if "/" in normalized:
        suffix = f"/anime/{normalized}"
        return Anime.query.filter(Anime.url.ilike(f"%{suffix}")).first()

    return Anime.query.filter(Anime.url.ilike(f"%/anime/%/{normalized}")).first()

def _resolve_episode_url_by_id(episode_id):
    episode = (
        Episode.query.filter(Episode.url.like(f"%/video/%/{episode_id}%"))
        .order_by(Episode.last_updated.desc())
        .first()
    )
    if episode:
        return episode.url, episode

    prefix = (request.args.get("prefix") or "a").strip().lower()
    if not re.match(r"^[a-z0-9-]+$", prefix):
        prefix = "a"
    return f"https://animesdigital.org/video/{prefix}/{episode_id}", None

def _is_valid_http_url(value):
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    return bool(parsed.netloc)
