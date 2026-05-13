import json
import logging
from datetime import timedelta

from app import create_app
from app.services.scraper import ScraperService
from app.api.db_utils import save_episodes_to_db
from app.services.unified_cache import volatile_set, persistent_set
from app.models.embed import EmbedRequest, db
from app.api.utils import _utcnow

logger = logging.getLogger(__name__)


def _save_embed_cache(target_url: str, embed_info: dict, ttl_hours: int = 24) -> None:
    """Save scraped embed data to both persistent cache (DB) and volatile cache."""
    try:
        expires_at = _utcnow() + timedelta(hours=ttl_hours)
        payload = {
            "episode_url": target_url,
            "embed_url": embed_info.get("embed_url", ""),
            "title": embed_info.get("title", ""),
            "total_players": 1,
            "players": [],
            "cached_at": _utcnow().isoformat(),
        }
        persistent_set(target_url, payload, ttl_hours=ttl_hours)
        volatile_set(
            f"players:{target_url}",
            payload,
            timeout=min(ttl_hours * 3600, 3600),  # volatile cache max 1h
        )
        logger.info("Cached embed data for %s (expires: %s)", target_url, expires_at.isoformat())
    except Exception as e:
        logger.warning("Failed to cache embed data for %s: %s", target_url, e)


def _update_embed_cache_if_recent(target_url: str) -> bool:
    """Check if the embed cache entry is still fresh. Returns True if cache exists."""
    entry = EmbedRequest.query.filter_by(url=target_url).first()
    if entry and entry.expires_at and entry.expires_at > _utcnow():
        try:
            return json.loads(entry.response_data)
        except (json.JSONDecodeError, TypeError):
            pass
    return False


def run_background_refresh(url, config, site_key, cache_key, ttl_seconds):
    """Background refresh of home featured cache (runs in RQ worker)."""
    app = create_app()
    with app.app_context():
        logger.info("Starting background refresh for %s", url)
        try:
            with ScraperService() as scraper:
                context = scraper._get_context()
                page = context.new_page()
                try:
                    from app.services.featured_service import process_home_sections

                    url_patterns = (
                        config.get("url_patterns", {})
                        if isinstance(config, dict)
                        else getattr(config, "url_patterns", {})
                    )
                    payload = process_home_sections(scraper, page, url, config, url_patterns)
                    if "error" in payload:
                        logger.error("Background refresh scrape error: %s", payload["error"])
                        return

                    payload["source"] = site_key
                    volatile_set(cache_key, payload, timeout=ttl_seconds)
                    persistent_set(cache_key, payload, ttl_hours=ttl_seconds // 3600)
                    logger.info("Background refresh completed for %s", url)
                finally:
                    context.close()
        except Exception as e:
            logger.error("Background refresh failed: %s", e)


def _scrape_with_retry(scraper, page, target_url, config, max_retries: int = 2):
    """Execute scraping with retry logic and structured result."""
    for attempt in range(max_retries + 1):
        try:
            url_patterns = getattr(config, "url_patterns", {})

            # Case 1: Home page
            if scraper.match_pattern(target_url, url_patterns.get("home", "")):
                result = scraper.extract_episodes(
                    page, target_url, config, selector_key="home"
                )
                if "error" not in result:
                    save_episodes_to_db(result.get("episode_urls", []))
                return result

            # Case 2: Episode page
            elif scraper.match_pattern(target_url, url_patterns.get("episode", "")):
                embed_info = scraper.extract_embed(page, target_url, config)
                if "embed_url" in embed_info:
                    save_episodes_to_db([embed_info])
                    _save_embed_cache(target_url, embed_info)
                return embed_info

            # Case 3: Anime main page
            elif scraper.match_pattern(target_url, url_patterns.get("anime_main", "")):
                result = scraper.extract_episodes(
                    page, target_url, config, selector_key="anime_main"
                )
                if "error" not in result:
                    save_episodes_to_db(
                        result.get("episode_urls", []),
                        anime_url=target_url,
                        anime_metadata=result.get("metadata"),
                    )
                return result

            # Case 4: Movie page
            elif scraper.match_pattern(target_url, url_patterns.get("movie", "")):
                embed_info = scraper.extract_embed(page, target_url, config)
                if "embed_url" in embed_info:
                    save_episodes_to_db([embed_info])
                    _save_embed_cache(target_url, embed_info)
                return embed_info

            else:
                logger.warning("URL pattern not matched: %s", target_url)
                return {"error": "URL pattern not recognized", "url": target_url}

        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    "Scraper attempt %d/%d failed for %s: %s. Retrying...",
                    attempt + 1,
                    max_retries + 1,
                    target_url,
                    e,
                )
                import time

                time.sleep(2**attempt)
            else:
                logger.error("All %d scraping attempts failed for %s: %s", max_retries + 1, target_url, e)
                raise


def run_scraper_task(target_url, config, app=None):
    """Main scraper task - runs in RQ worker context."""
    if app is None:
        app = create_app()

    with app.app_context():
        logger.info("Starting scraper task for %s (force=False)", target_url)

        # Check if we already have fresh cached data
        if _update_embed_cache_if_recent(target_url):
            logger.info("Fresh cache exists for %s, skipping scrape", target_url)
            return {"status": "cached", "url": target_url}

        try:
            with ScraperService() as scraper:
                context = scraper._get_context()
                page = context.new_page()
                try:
                    result = _scrape_with_retry(scraper, page, target_url, config)
                    logger.info("Scraper task finished for %s", target_url)
                    return {"status": "success", "url": target_url, "result": result}
                finally:
                    page.close()
                    context.close()
        except Exception as e:
            logger.error("Error scraping %s: %s", target_url, e)
            raise
