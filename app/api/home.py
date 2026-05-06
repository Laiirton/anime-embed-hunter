import json
import logging
from threading import Thread

from flask import current_app, jsonify, request

from app import cache, limiter
from app.models.embed import EmbedRequest
from app.services.scraper import ScraperService
from app.services.site_manager import site_manager
from app.utils.helpers import clean_name, extract_audio_type, format_info
from app.api.routes import bp
from app.api.utils import (
    _build_home_featured_cache_key,
    _parse_bool,
    _utcnow,
    check_api_key,
)
from app.api.db_utils import _save_to_embed_cache

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
        if scraper.match_pattern(item_url, url_patterns.get('episode', "")): item_type = "episode"
        elif scraper.match_pattern(item_url, url_patterns.get('anime_main', "")): item_type = "anime"
        elif scraper.match_pattern(item_url, url_patterns.get('movie', "")): item_type = "movie"
        
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

def _scrape_home_featured(force_refresh=False):
    cache_key = _build_home_featured_cache_key()
    persistent_key = f"persistent:{cache_key}"
    
    if not force_refresh:
        cached_payload = cache.get(cache_key)
        if cached_payload:
            # Check for unknown types in any section
            all_cached_items = []
            if "sections" in cached_payload:
                for s in cached_payload["sections"].values(): all_cached_items.extend(s)
            elif "results" in cached_payload:
                all_cached_items = cached_payload["results"]
            
            has_unknown = any(item.get("item_type") == "unknown" for item in all_cached_items)
            if not has_unknown:
                return {**cached_payload, "cached": True, "cache_source": "ram"}, 200

    db_entry = EmbedRequest.query.filter_by(url=persistent_key).first()
    now = _utcnow()
    
    if db_entry and not force_refresh:
        try:
            db_data = json.loads(db_entry.response_data)
            ttl_seconds = current_app.config.get("HOME_FEATURED_CACHE_TTL_SECONDS", 1800)
            is_stale = (now - db_entry.timestamp).total_seconds() > ttl_seconds
            
            all_cached_items = []
            if "sections" in db_data:
                for s in db_data["sections"].values(): all_cached_items.extend(s)
            elif "results" in db_data:
                all_cached_items = db_data["results"]

            has_unknown = any(item.get("item_type") == "unknown" for item in all_cached_items)
            if has_unknown:
                raise ValueError("Cache has unknown items, force synchronous refresh")

            if not is_stale:
                cache.set(cache_key, db_data, timeout=ttl_seconds)
                return {**db_data, "cached": True, "cache_source": "db"}, 200
            
            def background_refresh(app_context, url, config, site_key):
                with app_context:
                    try:
                        with ScraperService() as scraper:
                            context = scraper._get_context()
                            page = context.new_page()
                            try:
                                result = scraper.extract_home_sections(page, url, config)
                                if "error" not in result:
                                    url_patterns = getattr(config, 'url_patterns', {})
                                    sections_data = {}
                                    all_items_to_populate = []
                                    
                                    if "sections" in result:
                                        for section_name, items in result["sections"].items():
                                            processed = _process_featured_items(items, scraper, url_patterns)
                                            sections_data[section_name] = processed
                                            all_items_to_populate.extend(processed)
                                    else:
                                        # Fallback to old format if extract_home_sections returned flat list
                                        items = result.get("episode_urls", [])
                                        processed = _process_featured_items(items, scraper, url_patterns)
                                        sections_data["featured"] = processed
                                        all_items_to_populate.extend(processed)

                                    from app.services.cover_service import populate_covers_for_dicts
                                    populate_covers_for_dicts(all_items_to_populate)

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

                                    new_payload = {
                                        "source": site_key, 
                                        "url": url, 
                                        "sections": ordered_sections,
                                        "total_items": len(all_items_to_populate),
                                        "cached": False
                                    }
                                    cache.set(cache_key, new_payload, timeout=ttl_seconds)
                                    _save_to_embed_cache(persistent_key, new_payload)
                            finally:
                                context.close()
                    except Exception as e:
                        logger.error("Background refresh failed: %s", e)

            home_url = request.args.get("url", "https://animesdigital.org/home").strip()
            site_key, config = site_manager.get_config_for_url(home_url)
            if site_key:
                Thread(target=background_refresh, args=(current_app._get_current_object().app_context(), home_url, config, site_key)).start()
            
            return {**db_data, "cached": True, "cache_source": "db_stale"}, 200
        except Exception as e:
            logger.warning("Erro ao processar cache do banco para home: %s", e)

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

                from app.services.cover_service import populate_covers_for_dicts
                populate_covers_for_dicts(all_items_to_populate)

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
                
                cache.set(cache_key, payload, timeout=current_app.config.get("HOME_FEATURED_CACHE_TTL_SECONDS", 1800))
                _save_to_embed_cache(persistent_key, payload)
                
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

    force_refresh = _parse_bool(request.args.get("force"))
    payload, status = _scrape_home_featured(force_refresh=force_refresh)
    return jsonify(payload), status
