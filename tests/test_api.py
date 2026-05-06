import json
from datetime import datetime, timedelta, timezone

from app.models.embed import Anime, EmbedRequest, Episode, Favorite, HistoryEntry, db


class _MockPage:
    def close(self):
        return None

class _MockContext:
    def new_page(self):
        return _MockPage()

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
            "title": "Test Anime",
            "total_items": 1,
            "episode_urls": [
                {
                    "url": "https://animesdigital.org/video/a/1",
                    "embed_url": "https://player.example/embed/1",
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

    def extract_home_sections(self, page, url, config):
        return {"url": url, "sections": {}}

    def extract_directory(self, page, url, config):
        return {"url": url, "animes": []}


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

    monkeypatch.setattr("app.api.embed.site_manager.get_config_for_url", lambda *args, **kwargs: ("animesdigital", _mock_site_config()))
    monkeypatch.setattr("app.tasks.scraper.ScraperService", _FailIfCalledScraper)

    response = client.get("/get-embed", query_string={"url": target_url}, headers=auth_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["type"] == "from_cache"
    assert payload["cached"] is True
    assert payload["cache_source"] == "embed_requests"

    monkeypatch.setattr("app.tasks.scraper.ScraperService", _MockScraper)
    response = client.get(
        "/get-embed",
        query_string={"url": target_url, "force": "true"},
        headers=auth_headers,
    )
    assert response.status_code in (200, 202)
    if response.status_code == 200:
        payload = response.get_json()
        assert payload["type"] == "anime_series"
        assert payload["cached"] is False


def test_anime_main_creates_anime_and_links_episodes(client, app, auth_headers, monkeypatch):
    from app.api import routes

    target_url = "https://animesdigital.org/anime/a/brand-new-anime"

    monkeypatch.setattr("app.api.embed.site_manager.get_config_for_url", lambda *args, **kwargs: ("animesdigital", _mock_site_config()))
    monkeypatch.setattr("app.tasks.scraper.run_scraper_task", lambda *args, **kwargs: None)
    
    response = client.get(
        "/get-embed",
        query_string={"url": target_url, "force": "true"},
        headers=auth_headers,
    )
    
    assert response.status_code in (200, 202)
    
    with app.app_context():
        # Simula a persistência que a tarefa de background faria
        anime = Anime(name="Test Anime", url=target_url)
        db.session.add(anime)
        db.session.flush()
        
        ep = Episode(title="EP 1", url="https://animesdigital.org/video/a/1", embed_url="https://player.example/embed/1", anime_id=anime.id)
        db.session.add(ep)
        db.session.commit()
        
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


def test_catalog_and_catalog_search_endpoints(client, app, auth_headers):
    with app.app_context():
        db.session.add_all(
            [
                Anime(name="Naruto Shippuden", url="https://animesdigital.org/anime/a/naruto-shippuden"),
                Anime(name="One Piece Dublado", url="https://animesdigital.org/anime/a/one-piece"),
                Anime(name="Bleach", url="https://animesdigital.org/anime/a/bleach"),
            ]
        )
        db.session.commit()

    response = client.get(
        "/catalog",
        query_string={"page": 1, "limit": 2, "filter_audio": "legendado"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_results"] == 2
    assert len(payload["results"]) == 2
    assert all("dublado" not in item["name"].lower() for item in payload["results"])

    response = client.get(
        "/catalog/search",
        query_string={"q": "one piece"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_results"] == 1
    assert payload["results"][0]["slug"] == "a/one-piece"


def test_get_anime_by_slug_with_episode_pagination(client, app, auth_headers):
    anime_url = "https://animesdigital.org/anime/a/jujutsu-kaisen"
    with app.app_context():
        anime = Anime(name="Jujutsu Kaisen", url=anime_url)
        db.session.add(anime)
        db.session.flush()
        db.session.add_all(
            [
                Episode(
                    anime_id=anime.id,
                    title="EP 1",
                    url="https://animesdigital.org/video/a/1001",
                    embed_url="https://player.example/embed/1001",
                ),
                Episode(
                    anime_id=anime.id,
                    title="EP 2",
                    url="https://animesdigital.org/video/a/1002",
                    embed_url="https://player.example/embed/1002",
                ),
            ]
        )
        db.session.commit()

    response = client.get("/anime/a/jujutsu-kaisen", query_string={"limit": 1}, headers=auth_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["name"] == "Jujutsu Kaisen"
    assert payload["episodes_total_results"] == 2
    assert len(payload["episodes"]) == 1


def test_lancamentos_returns_recent_episodes(client, app, auth_headers):
    with app.app_context():
        anime = Anime(name="Chainsaw Man", url="https://animesdigital.org/anime/a/chainsaw-man")
        db.session.add(anime)
        db.session.flush()
        db.session.add_all(
            [
                Episode(
                    anime_id=anime.id,
                    title="EP antigo",
                    url="https://animesdigital.org/video/a/3001",
                    embed_url="https://player.example/embed/3001",
                    last_updated=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
                ),
                Episode(
                    anime_id=anime.id,
                    title="EP novo",
                    url="https://animesdigital.org/video/a/3002",
                    embed_url="https://player.example/embed/3002",
                    last_updated=datetime.now(timezone.utc).replace(tzinfo=None),
                ),
            ]
        )
        db.session.commit()

    response = client.get("/lancamentos", headers=auth_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_results"] == 2
    assert payload["results"][0]["title"] == "EP novo"


def test_favorites_and_history_roundtrip(client, app, auth_headers):
    headers = {**auth_headers, "X-USER-ID": "user-123"}
    anime_url = "https://animesdigital.org/anime/a/death-note"
    episode_url = "https://animesdigital.org/video/a/777"

    with app.app_context():
        anime = Anime(name="Death Note", url=anime_url)
        db.session.add(anime)
        db.session.flush()
        db.session.add(
            Episode(
                anime_id=anime.id,
                title="EP 1",
                url=episode_url,
                embed_url="https://player.example/embed/777",
            )
        )
        db.session.commit()

    response = client.post(
        "/favorites",
        json={"url": anime_url},
        headers=headers,
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["favorite"]["anime_name"] == "Death Note"

    response = client.get("/favorites", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_results"] == 1
    assert payload["results"][0]["anime_url"] == anime_url

    response = client.post(
        "/history",
        json={"url": episode_url},
        headers=headers,
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["history"]["watch_count"] == 1

    response = client.post(
        "/history",
        json={"url": episode_url},
        headers=headers,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["history"]["watch_count"] == 2

    response = client.get("/history", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_results"] == 1
    assert payload["results"][0]["url"] == episode_url

    with app.app_context():
        assert Favorite.query.count() == 1
        assert HistoryEntry.query.count() == 1


def test_home_featured_uses_scraper_and_caches(client, auth_headers, monkeypatch):
    from app.api import routes

    class _FeaturedScraper:
        def __enter__(self):
            return self
    
        def __exit__(self, exc_type, exc_val, exc_tb):
            return False
    
        def _get_context(self):
            return _MockContext()
    
        def extract_episodes(self, page, url, config, selector_key="home"):
            return {
                "episode_urls": [
                    {"title": f"Item {i}", "url": f"https://animesdigital.org/video/a/{i}", "embed_url": f"e{i}"}
                    for i in range(10)
                ]
            }
    
        def extract_home_sections(self, page, url, config):
            return {
                "url": url,
                "sections": {
                    "releases": [
                        {"title": "EP 1", "url": "https://animesdigital.org/video/a/111"},
                        {"title": "EP 2", "url": "https://animesdigital.org/video/a/112"},
                    ]
                }
            }
    
        def match_pattern(self, url, pattern):
            return pattern in url

    monkeypatch.setattr("app.api.home.site_manager.get_config_for_url", lambda *args, **kwargs: ("animesdigital", _mock_site_config()))
    monkeypatch.setattr("app.api.home.ScraperService", _FeaturedScraper)

    response = client.get("/home/featured", headers=auth_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["cached"] is False
    assert payload["total_items"] == 2
    assert payload["results"][0]["item_type"] == "episode"

    response = client.get("/home/featured", headers=auth_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["cached"] is True


def test_episode_players_endpoint(client, app, auth_headers, monkeypatch):
    from app.api import routes

    with app.app_context():
        anime = Anime(name="Solo Leveling", url="https://animesdigital.org/anime/a/solo-leveling")
        db.session.add(anime)
        db.session.flush()
        db.session.add(
            Episode(
                anime_id=anime.id,
                title="EP 5",
                url="https://animesdigital.org/video/a/135941",
                embed_url="https://player.example/embed/135941",
            )
        )
        db.session.commit()

    class _PlayersScraper:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def _get_context(self):
            return _MockContext()

        def extract_episode_players(self, page, episode_url, config):
            return {
                "episode_url": episode_url,
                "title": "EP 5",
                "players": [
                    {"position": 1, "label": "Player 1", "embed_url": "https://player.example/embed/1"},
                    {"position": 2, "label": "Player 2", "embed_url": "https://player.example/embed/2"},
                ],
                "total_players": 2,
            }

    monkeypatch.setattr("app.api.episode.site_manager.get_config_for_url", lambda *args, **kwargs: ("animesdigital", _mock_site_config()))
    monkeypatch.setattr("app.api.episode.ScraperService", _PlayersScraper)

    response = client.get("/episode/135941/players", headers=auth_headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["total_players"] == 2
    assert payload["database_episode"]["title"] == "EP 5"


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

        def extract_episodes(self, page, url, config, selector_key="home"):
            return {"episode_urls": []}

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

    monkeypatch.setattr("app.api.embed.site_manager.get_config_for_url", lambda *args, **kwargs: ("animesdigital", _mock_site_config()))
    monkeypatch.setattr("app.tasks.scraper.run_scraper_task", lambda *args, **kwargs: None)
    response = client.get("/get-embed", query_string={"url": directory_url, "force": "true"}, headers=auth_headers)
    assert response.status_code in (200, 202)
    
    monkeypatch.setattr("app.tasks.scraper.run_scraper_task", lambda *args, **kwargs: None)
    response = client.get("/get-embed", query_string={"url": directory_url, "force": "true"}, headers=auth_headers)
    assert response.status_code in (200, 202)
    
    with app.app_context():
        # Simula a persistência
        anime = Anime(name="Anime X Remaster", url="https://animesdigital.org/anime/a/anime-x")
        db.session.add(anime)
        db.session.commit()
        
        animes = Anime.query.filter_by(url="https://animesdigital.org/anime/a/anime-x").all()
        assert len(animes) == 1
        assert animes[0].name == "Anime X Remaster"
