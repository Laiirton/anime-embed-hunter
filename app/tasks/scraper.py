import logging
from app import create_app
from app.services.scraper import ScraperService
from app.api.db_utils import save_episodes_to_db

logger = logging.getLogger(__name__)

def run_scraper_task(target_url, config):
    app = create_app()
    with app.app_context():
        logger.info(f"Starting scraper task for {target_url}")
        try:
            with ScraperService() as scraper:
                context = scraper._get_context()
                page = context.new_page()
                
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
                        # Aqui precisaríamos processar os episódios encontrados
                        save_episodes_to_db(result.get("episode_urls", []))

                # Caso 4: Movie
                elif scraper.match_pattern(target_url, url_patterns.get("movie", "")):
                    embed_info = scraper.extract_embed(page, target_url, config)
                    if "embed_url" in embed_info:
                        save_episodes_to_db([embed_info])
                # A lógica de scraping precisa ser movida para cá,
                # adaptando a estrutura complexa que estava no embed.py
                
                logger.info(f"Scraper task finished for {target_url}")
        except Exception as e:
            logger.error(f"Error scraping {target_url}: {e}")
            raise
