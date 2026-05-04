import os
from dotenv import load_dotenv

load_dotenv()


def _normalize_database_url(raw_url):
    if not raw_url:
        return None

    url = raw_url.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    SECRET_KEY = os.getenv("SECRET_KEY")
    API_KEY = os.getenv("API_KEY")

    _SQLITE_PATH = os.path.join(BASE_DIR, "instance", "anime_embeds.db")
    _DATABASE_URL = _normalize_database_url(
        os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")
    )
    SQLALCHEMY_DATABASE_URI = _DATABASE_URL or f"sqlite:///{_SQLITE_PATH}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    _DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    _DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "2"))
    _DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "2"))
    _DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    _DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

    SQLALCHEMY_ENGINE_OPTIONS = {}

    CACHE_TYPE = os.getenv("CACHE_TYPE", "flask_caching.backends.simplecache.SimpleCache")
    CACHE_DEFAULT_TIMEOUT = int(os.getenv("CACHE_DEFAULT_TIMEOUT", "300"))
    CACHE_REDIS_URL = os.getenv("CACHE_REDIS_URL")
    SEARCH_CACHE_TTL_SECONDS = int(os.getenv("SEARCH_CACHE_TTL_SECONDS", "120"))
    EMBED_CACHE_TTL_HOURS = int(os.getenv("EMBED_CACHE_TTL_HOURS", "24"))
    EMBED_CACHE_CLEANUP_INTERVAL_SECONDS = int(
        os.getenv("EMBED_CACHE_CLEANUP_INTERVAL_SECONDS", "900")
    )
    EMBED_CACHE_CLEANUP_BATCH_SIZE = int(
        os.getenv("EMBED_CACHE_CLEANUP_BATCH_SIZE", "1000")
    )

    RATELIMIT_DEFAULT = "100 per hour"
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")

    # Scraper settings
    HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
    BROWSER_TIMEOUT = int(os.getenv("BROWSER_TIMEOUT", "60000"))

    SEARCH_LIMIT = int(os.getenv("SEARCH_LIMIT", "50"))

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0.3 Safari/605.1.15",
    ]

    @classmethod
    def build_engine_options(cls, database_uri):
        options = {
            "pool_pre_ping": True,
            "pool_recycle": cls._DB_POOL_RECYCLE,
        }

        if database_uri and database_uri.startswith("postgresql+psycopg://"):
            options.update(
                {
                    "pool_size": cls._DB_POOL_SIZE,
                    "max_overflow": cls._DB_MAX_OVERFLOW,
                    "pool_timeout": cls._DB_POOL_TIMEOUT,
                    "connect_args": {
                        "connect_timeout": cls._DB_CONNECT_TIMEOUT,
                    },
                }
            )

        return options
