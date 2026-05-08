import logging
from app import create_app
from app.services.scraper import ScraperService
from app.api.db_utils import save_episodes_to_db
from app.services.unified_cache import volatile_set, persistent_set

logger = logging.getLogger(__name__)

def run_background_refresh(url, config, site_key, cache_key, ttl_seconds):
    """Background refresh of home featured cache (runs in RQ worker)."""
    from app.api.home import _process_featured_items
    from app.services.metadata_service import populate_metadata_for_dicts
    from app.api.db_utils import save_animes_to_db, save_episodes_to_db

    app = create_app()
    with app.app_context():
        logger.info("Starting background refresh for %s", url)
        try:
            with ScraperService() as scraper:
                context = scraper._get_context()
                page = context.new_page()
                try:
                    result = scraper.extract_home_sections(page, url, config)
                    if "error" in result:
                        logger.error("Background refresh scrape error: %s", result["error"])
                        return

                    url_patterns = config.get('url_patterns', {}) if isinstance(config, dict) else getattr(config, 'url_patterns', {})
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

                    populate_metadata_for_dicts(all_items_to_populate)

                    animes_to_save = [item for item in all_items_to_populate if item["item_type"] in ["anime", "movie"]]
                    episodes_to_save = [item for item in all_items_to_populate if item["item_type"] == "episode"]

                    if animes_to_save:
                        save_animes_to_db(animes_to_save)
                    if episodes_to_save:
                        save_episodes_to_db(episodes_to_save)

                    ordered_sections = {}
                    for key in ["releases", "latest_episodes", "latest_animes", "latest_movies"]:
                        if key in sections_data:
                            ordered_sections[key] = sections_data[key]

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
                    volatile_set(cache_key, new_payload, timeout=ttl_seconds)
                    persistent_set(cache_key, new_payload, ttl_hours=ttl_seconds // 3600)
                    logger.info("Background refresh completed for %s", url)
                finally:
                    context.close()
        except Exception as e:
            logger.error("Background refresh failed: %s", e)


def run_scraper_task(target_url, config, app=None):
    if app is None:
        app = create_app()
    
    with app.app_context():
        logger.info(f"Starting scraper task for {target_url}")
        try:
            with ScraperService() as scraper:
                context = scraper._get_context()
                page = context.new_page()
                try:
                    # Acesso aos atributos de SiteConfig (instância Pydantic)
                    url_patterns = getattr(config, 'url_patterns', {})
                    
                    # Caso 1: Home
                    if scraper.match_pattern(target_url, url_patterns.get("home", "")):
                        result = scraper.extract_episodes(page, target_url, config, selector_key="home")
                        if "error" not in result:
                            save_episodes_to_db(result.get("episode_urls", []))
                    
                    # Caso 2: Episode
                    elif scraper.match_pattern(target_url, url_patterns.get("episode", "")):
                        embed_info = scraper.extract_embed(page, target_url, config)
                        if "embed_url" in embed_info:
                            save_episodes_to_db([embed_info])

                    # Caso 3: Anime Main
                    elif scraper.match_pattern(target_url, url_patterns.get("anime_main", "")):
                        result = scraper.extract_episodes(page, target_url, config, selector_key="anime_main")
                        if "error" not in result:
                            # Passamos o target_url como anime_url para garantir a criação do Anime no banco
                            save_episodes_to_db(
                                result.get("episode_urls", []), 
                                anime_url=target_url,
                                anime_metadata=result.get("metadata")
                            )

                    # Caso 4: Movie
                    elif scraper.match_pattern(target_url, url_patterns.get("movie", "")):
                        embed_info = scraper.extract_embed(page, target_url, config)
                        if "embed_url" in embed_info:
                            save_episodes_to_db([embed_info])
                    
                    logger.info(f"Scraper task finished for {target_url}")
                finally:
                    page.close()
                    context.close()
        except Exception as e:
            logger.error(f"Error scraping {target_url}: {e}")
            raise
