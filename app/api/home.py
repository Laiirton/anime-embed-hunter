import logging
from flask import jsonify, request, current_app
from app import limiter, get_scraper_queue
from app.services.scraper import ScraperService
from app.services.site_manager import site_manager
from app.services.unified_cache import (
    volatile_get,
    volatile_set,
    persistent_get,
    persistent_set,
)
from app.utils.helpers import clean_name, extract_audio_type, format_info
from app.api.routes import bp
from app.api.utils import (
    _build_home_featured_cache_key,
    check_api_key,
)
from app.api.validators import HomeFeaturedRequest

logger = logging.getLogger(__name__)


def _process_featured_items(items, scraper, url_patterns):
    processed = []
    seen_urls = set()
    seen_items = set() # (title, info)
    
    for item in items:
        item_url = item.get("url")
        if not item_url or item_url in seen_urls: 
            continue
            
        item_type = "unknown"
        if scraper.match_pattern(item_url, url_patterns.get('episode', "")):
            item_type = "episode"
        elif scraper.match_pattern(item_url, url_patterns.get('anime_main', "")):
            item_type = "anime"
        elif scraper.match_pattern(item_url, url_patterns.get('movie', "")):
            item_type = "movie"
        
        title_raw = item.get("title", "")
        audio_type = extract_audio_type(title_raw)
        title = clean_name(title_raw)
        info = format_info(item.get("info"))
        
        # Deduplicate by Title + Info + Audio to avoid Dubbed/Subbed duplicates on home
        item_key = (title, info, audio_type)
        if item_key in seen_items:
            continue
            
        seen_urls.add(item_url)
        seen_items.add(item_key)
        
        processed.append({
            "title": title,
            "url": item_url,
            "cover_url": item.get("cover_url"),
            "item_type": item_type,
            "info": info,
            "audio_type": audio_type
        })
    return processed


def _has_unknown_items(payload):
    """Check if payload has any items with unknown type."""
    all_items = []
    if "sections" in payload:
        for s in payload["sections"].values():
            all_items.extend(s)
    elif "results" in payload:
        all_items = payload["results"]
    return any(item.get("item_type") == "unknown" for item in all_items)


def _scrape_home_featured(url=None, force_refresh=False):
    if url is None:
        url = "https://animesdigital.org/home"
    cache_key = _build_home_featured_cache_key()
    ttl_seconds = current_app.config.get("HOME_FEATURED_CACHE_TTL_SECONDS", 1800)
    
    # 1. Tentar cache volátil (RAM/Redis) primeiro
    if not force_refresh:
        cached_payload, status = volatile_get(cache_key)
        if cached_payload and status == "hit":
            if not _has_unknown_items(cached_payload):
                result = {**cached_payload, "cached": True, "cache_source": "volatile"}
                return result, 200
    
    # 2. Tentar cache persistente (DB) com Stale-While-Revalidate
    if not force_refresh:
        cached_payload, status = persistent_get(cache_key)
        if cached_payload:
            if status == "fresh" and not _has_unknown_items(cached_payload):
                # Salva no cache volátil para próximas requisições
                volatile_set(cache_key, cached_payload, timeout=ttl_seconds)
                result = {**cached_payload, "cached": True, "cache_source": "persistent"}
                return result, 200
            
            elif status == "stale":
                # Dados obsoletos - enfileira refresh no RQ e retorna stale
                home_url = request.args.get("url", "https://animesdigital.org/home").strip()
                site_key, config = site_manager.get_config_for_url(home_url)
                if site_key:
                    get_scraper_queue().enqueue(
                        'app.tasks.scraper.run_background_refresh',
                        home_url, config.model_dump(), site_key, cache_key, ttl_seconds
                    )
                
                if not _has_unknown_items(cached_payload):
                    result = {**cached_payload, "cached": True, "cache_source": "persistent_stale"}
                    return result, 200
    
    # 3. Cache miss ou force_refresh - faz scrape síncrono
    home_url = request.args.get("url", "https://animesdigital.org/home").strip()
    site_key, config = site_manager.get_config_for_url(home_url)
    if not site_key:
        return {"error": "URL domain not supported"}, 400

    try:
        with ScraperService() as scraper:
            context = scraper._get_context()
            page = context.new_page()
            try:
                result = scraper.extract_home_sections(page, home_url, config)
                if "error" in result:
                    return {"error": result["error"]}, 502
                
                url_patterns = getattr(config, 'url_patterns', {})
                sections_data = {}
                all_items_to_populate = []
                
                if "sections" in result:
                    for section_name, items in result["sections"].items():
                        processed = _process_featured_items(items, scraper, url_patterns)
                        sections_data[section_name] = processed
                        all_items_to_populate.extend(processed)
                else:
                    items = result.get("episode_urls", [])
                    processed = _process_featured_items(items, scraper, url_patterns)
                    sections_data["featured"] = processed
                    all_items_to_populate.extend(processed)
                
                from app.services.metadata_service import populate_metadata_for_dicts
                populate_metadata_for_dicts(all_items_to_populate)
                
                # Save to DB
                from app.api.db_utils import save_animes_to_db, save_episodes_to_db
                animes_to_save = [item for item in all_items_to_populate if item["item_type"] in ["anime", "movie"]]
                episodes_to_save = [item for item in all_items_to_populate if item["item_type"] == "episode"]
                
                if animes_to_save:
                    save_animes_to_db(animes_to_save)
                if episodes_to_save:
                    save_episodes_to_db(episodes_to_save)
                
                # Organize sections in the requested order
                ordered_sections = {}
                for key in ["releases", "latest_episodes", "latest_animes", "latest_movies"]:
                    if key in sections_data:
                        ordered_sections[key] = sections_data[key]
                
                # Add any other sections that might exist
                for key, value in sections_data.items():
                    if key not in ordered_sections:
                        ordered_sections[key] = value
                
                payload = {
                    "source": site_key, 
                    "url": home_url, 
                    "sections": ordered_sections,
                    "total_items": len(all_items_to_populate),
                    "cached": False
                }
                
                # Salva em ambos caches (volátil e persistente)
                volatile_set(cache_key, payload, timeout=ttl_seconds)
                persistent_set(cache_key, payload, ttl_hours=ttl_seconds // 3600)
                
                return payload, 200
            finally:
                context.close()
    except Exception as exc:
        logger.error("Unexpected error scraping home featured: %s", exc)
        return {"error": "Internal server error"}, 500


@bp.route("/home/featured", methods=["GET"])
@limiter.limit("30 per minute")
def get_home_featured():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Validate request parameters with Pydantic
        home_req = HomeFeaturedRequest(
            url=request.args.get("url"),
            force=request.args.get("force")
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    payload, status = _scrape_home_featured(
        url=home_req.url,
        force_refresh=home_req.force
    )
    return jsonify(payload), status
