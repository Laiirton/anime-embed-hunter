import json
from datetime import datetime, timedelta, timezone

from app.models.embed import Anime, EmbedRequest, Episode, db


class _MockContext:
    def new_page(self):
        return object()

    def close(self):
        return None


class _MockScraper:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def _get_context(self):
        return _MockContext()

    def match_pattern(self, url, pattern):
        return pattern in url

    def extract_episodes(self, page, url, config, selector_key="anime_main"):
        return {
            "url": url,
            "title": "Assistir Test Anime Online em HD",
            "total_items": 1,
            "episode_urls": [
                {
                    "url": "https://animesdigital.org/video/a/1",
                    "title": "Assistir Episode 1 Online em HD",
                }
            ],
        }

    def extract_embed(self, page, episode_url, config, retries=2):
        return {
            "episode_url": episode_url,
            "title": "Assistir Episode 1 Online em HD",
            "embed_url": "https://player.example/embed/1",
        }


class _FailIfCalledScraper:
    def __enter__(self):
        raise AssertionError("Scraper should not be called when cache is valid")

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


def _mock_site_config():
    return {
        "url_patterns": {
            "home": "/home",
            "anime_main": "/anime/",
            "episode": "/video/",
        },
        "selectors": {
            "directory": {
                "url_pattern": "/animes-legendados-online",
            }
        },
    }


def test_force_true_bypasses_embed_cache(client, app, auth_headers, monkeypatch):
    from app.api import routes

    target_url = "https://animesdigital.org/anime/a/test-anime"

    with app.app_context():
        db.session.add(
            EmbedRequest(
                url=target_url,
                response_data=json.dumps({"type": "from_cache", "value": 1}),
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
            )
        )
        db.session.commit()

    monkeypatch.setattr(routes.site_manager, "get_config_for_url", lambda _: ("animesdigital", _mock_site_config()))
    monkeypatch.setattr(routes, "ScraperService", _FailIfCalledScraper)

    response = client.get("/get-embed", query_string={"url": target_url}, headers=auth_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["type"] == "from_cache"
    assert payload["cached"] is True
    assert payload["cache_source"] == "embed_requests"

    monkeypatch.setattr(routes, "ScraperService", _MockScraper)
    response = client.get(
        "/get-embed",
        query_string={"url": target_url, "force": "true"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["type"] == "anime_series"
    assert payload["cached"] is False


def test_anime_main_creates_anime_and_links_episodes(client, app, auth_headers, monkeypatch):
    from app.api import routes

    target_url = "https://animesdigital.org/anime/a/brand-new-anime"

    monkeypatch.setattr(routes.site_manager, "get_config_for_url", lambda _: ("animesdigital", _mock_site_config()))
    monkeypatch.setattr(routes, "ScraperService", _MockScraper)

    response = client.get(
        "/get-embed",
        query_string={"url": target_url, "force": "true"},
        headers=auth_headers,
    )

    assert response.status_code == 200

    with app.app_context():
        anime = Anime.query.filter_by(url=target_url).first()
        assert anime is not None
        assert anime.name == "Test Anime"

        episodes = Episode.query.filter_by(anime_id=anime.id).all()
        assert len(episodes) == 1
        assert episodes[0].embed_url == "https://player.example/embed/1"


def test_search_returns_cached_flag(client, app, auth_headers):
    with app.app_context():
        db.session.add(Anime(name="Hack//Sign", url="https://animesdigital.org/anime/h/hack-sign"))
        db.session.commit()

    response = client.get("/search", query_string={"q": "hack"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.get_json()["cached"] is False

    response = client.get("/search", query_string={"q": "hack"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.get_json()["cached"] is True


def test_cleanup_cache_endpoint_removes_expired_rows(client, app, auth_headers):
    with app.app_context():
        db.session.add(
            EmbedRequest(
                url="https://example.com/expired",
                response_data=json.dumps({"ok": False}),
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
            )
        )
        db.session.add(
            EmbedRequest(
                url="https://example.com/valid",
                response_data=json.dumps({"ok": True}),
                expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
            )
        )
        db.session.commit()

    response = client.post("/maintenance/cleanup-cache", headers=auth_headers)
    assert response.status_code == 200
    assert response.get_json()["deleted_rows"] == 1

    with app.app_context():
        assert EmbedRequest.query.count() == 1
        assert EmbedRequest.query.filter_by(url="https://example.com/valid").first() is not None


def test_directory_upsert_keeps_unique_url(client, app, auth_headers, monkeypatch):
    from app.api import routes

    directory_url = "https://animesdigital.org/animes-legendados-online?pagina=1"

    class _DirectoryScraperV1:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def _get_context(self):
            return _MockContext()

        def match_pattern(self, url, pattern):
            return pattern in url

        def extract_directory(self, page, url, config):
            return {
                "url": url,
                "total_pages": 1,
                "animes": [
                    {"name": "Assistir Anime X Online em HD", "url": "https://animesdigital.org/anime/a/anime-x"}
                ],
            }

    class _DirectoryScraperV2(_DirectoryScraperV1):
        def extract_directory(self, page, url, config):
            return {
                "url": url,
                "total_pages": 1,
                "animes": [
                    {"name": "Anime X Remaster", "url": "https://animesdigital.org/anime/a/anime-x"}
                ],
            }

    monkeypatch.setattr(routes.site_manager, "get_config_for_url", lambda _: ("animesdigital", _mock_site_config()))
    monkeypatch.setattr(routes, "ScraperService", _DirectoryScraperV1)
    response = client.get("/get-embed", query_string={"url": directory_url, "force": "true"}, headers=auth_headers)
    assert response.status_code == 200

    monkeypatch.setattr(routes, "ScraperService", _DirectoryScraperV2)
    response = client.get("/get-embed", query_string={"url": directory_url, "force": "true"}, headers=auth_headers)
    assert response.status_code == 200

    with app.app_context():
        animes = Anime.query.filter_by(url="https://animesdigital.org/anime/a/anime-x").all()
        assert len(animes) == 1
        assert animes[0].name == "Anime X Remaster"
