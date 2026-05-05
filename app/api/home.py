import json
import logging
from threading import Thread

from flask import current_app, jsonify, request

from app import cache, limiter
from app.models.embed import EmbedRequest
from app.services.scraper import ScraperService
from app.services.site_manager import site_manager
from app.utils.helpers import clean_name
from app.api.routes import bp
from app.api.utils import (
    _build_home_featured_cache_key,
    _parse_bool,
    _utcnow,
    check_api_key,
)
from app.api.db_utils import _save_to_embed_cache

logger = logging.getLogger(__name__)

def _scrape_home_featured(force_refresh=False):
    cache_key = _build_home_featured_cache_key()
    persistent_key = f"persistent:{cache_key}"
    
    if not force_refresh:
        cached_payload = cache.get(cache_key)
        if cached_payload:
            return {**cached_payload, "cached": True, "cache_source": "ram"}, 200

    db_entry = EmbedRequest.query.filter_by(url=persistent_key).first()
    now = _utcnow()
    
    if db_entry and not force_refresh:
        try:
            db_data = json.loads(db_entry.response_data)
            ttl_seconds = current_app.config.get("HOME_FEATURED_CACHE_TTL_SECONDS", 1800)
            is_stale = (now - db_entry.timestamp).total_seconds() > ttl_seconds
            
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
                                result = scraper.extract_episodes(page, url, config, selector_key="home")
                                if "error" not in result:
                                    featured = []
                                    url_patterns = config.get("url_patterns", {})
                                    for item in result.get("episode_urls", []):
                                        item_url = item.get("url")
                                        if not item_url: continue
                                        item_type = "unknown"
                                        if scraper.match_pattern(item_url, url_patterns.get("episode", "")): item_type = "episode"
                                        elif scraper.match_pattern(item_url, url_patterns.get("anime_main", "")): item_type = "anime"
                                        elif scraper.match_pattern(item_url, url_patterns.get("movie", "")): item_type = "movie"
                                        featured.append({"title": clean_name(item.get("title")), "url": item_url, "item_type": item_type})
                                    
                                    new_payload = {"source": site_key, "url": url, "total_items": len(featured), "results": featured, "cached": False}
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
                result = scraper.extract_episodes(page, home_url, config, selector_key="home")
                if "error" in result:
                    return {"error": result["error"]}, 502

                featured = []
                url_patterns = config.get("url_patterns", {})
                for item in result.get("episode_urls", []):
                    item_url = item.get("url")
                    if not item_url: continue
                    item_type = "unknown"
                    if scraper.match_pattern(item_url, url_patterns.get("episode", "")): item_type = "episode"
                    elif scraper.match_pattern(item_url, url_patterns.get("anime_main", "")): item_type = "anime"
                    elif scraper.match_pattern(item_url, url_patterns.get("movie", "")): item_type = "movie"
                    featured.append({"title": clean_name(item.get("title")), "url": item_url, "item_type": item_type})

                payload = {"source": site_key, "url": home_url, "total_items": len(featured), "results": featured, "cached": False}
                
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
