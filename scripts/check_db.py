import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from app import create_app
from app.services.scraper import ScraperService
from app.models.embed import Anime
from app.services.site_manager import site_manager

app = create_app()
with app.app_context():
    anime = Anime.query.filter(Anime.name.ilike('%Kami no Niwatsuki%')).first()
    site_key, config = site_manager.get_config_for_url(anime.url)
    
    with ScraperService() as scraper:
        context = scraper._get_context()
        page = context.new_page()
        try:
            scraper._goto_with_retry(page, anime.url, wait_until="load")
            data = scraper.extract_episodes(page, anime.url, config)
            print(f"Scraped data: {data}")
        finally:
            page.close()
            context.close()
