import pytest
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from app import create_app
from app.core.config import Config
from app.models.embed import db
from unittest.mock import MagicMock



class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    API_KEY = "test-api-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RATELIMIT_ENABLED = False
    CACHE_TYPE = "flask_caching.backends.simplecache.SimpleCache"
    SEARCH_CACHE_TTL_SECONDS = 60
    EMBED_CACHE_TTL_HOURS = 24
    REDIS_URL = "redis://localhost:6379" # Valor dummy, será mockado


class SyncQueueMock:
    def enqueue(self, func, *args, **kwargs):
        # Nos testes de API, não executamos a tarefa real para evitar dependências de infra (Playwright, Redis)
        return MagicMock()

@pytest.fixture
def app():
    app = create_app(TestConfig)
    
    # Mock da fila do RQ
    from app import scraper_queue
    scraper_queue.enqueue = SyncQueueMock().enqueue
    
    with app.app_context():
        db.create_all()





    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers():
    return {"X-API-KEY": "test-api-key"}
