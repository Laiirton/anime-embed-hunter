import logging
from app import create_app
from app.services.scraper import ScraperService
from app.api.db_utils import save_episodes_to_db

logger = logging.getLogger(__name__)

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
