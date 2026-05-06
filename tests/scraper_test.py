import pytest
from app import create_app
from app.services.scraper import ScraperService
from app.core.config import Config

@pytest.fixture
def app():
    app = create_app()
    app.config.update({"TESTING": True})
    return app

def test_scraper_real_connection():
    # Testa se o scraper consegue iniciar o navegador e navegar
    with ScraperService() as scraper:
        context = scraper._get_context()
        page = context.new_page()
        
        # Navega para o Google como um teste simples de conectividade
        response = page.goto("https://www.google.com")
        assert response.status == 200
        
        page.close()
        context.close()
