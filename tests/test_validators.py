"""
Tests for Pydantic validators.

Validates that input validation catches errors before processing,
reducing 500 errors and improving API reliability.
"""

import pytest
from app.api.validators import (
    CatalogRequest,
    SearchRequest,
    EmbedRequestModel,
    EpisodePlayersRequest,
    HomeFeaturedRequest,
    FavoriteRequest,
    HistoryRequest,
)


class TestCatalogRequest:
    """Tests for catalog request validation."""
    
    def test_default_values(self):
        req = CatalogRequest()
        assert req.page == 1
        assert req.limit == 30
        assert req.search is None
        assert req.filter_letter is None
        assert req.filter_audio is None
        assert req.order == "name"
    
    def test_valid_page_limit(self):
        req = CatalogRequest(page="2", limit="50")
        assert req.page == 2
        assert req.limit == 50
    
    def test_invalid_page_defaults_to_one(self):
        req = CatalogRequest(page="invalid", limit="30")
        assert req.page == 1
    
    def test_invalid_limit_defaults(self):
        req = CatalogRequest(page="1", limit="invalid")
        assert req.limit == 30
    
    def test_limit_bounds(self):
        req = CatalogRequest(limit="1")
        assert req.limit == 1
        
        req = CatalogRequest(limit="100")
        assert req.limit == 100
        
        # Over bounds should be clamped
        req = CatalogRequest(limit="200")
        assert req.limit == 100
    
    def test_audio_filter_normalization(self):
        req = CatalogRequest(filter_audio="Dublado")
        assert req.filter_audio == "dubbed"
        
        req = CatalogRequest(filter_audio="legendado")
        assert req.filter_audio == "legendado"
        
        req = CatalogRequest(filter_audio="")
        assert req.filter_audio is None


class TestSearchRequest:
    """Tests for search request validation."""
    
    def test_required_query(self):
        with pytest.raises(ValueError, match="Query parameter"):
            SearchRequest(q="")
        
        with pytest.raises(ValueError, match="Query parameter"):
            SearchRequest(q="   ")
    
    def test_valid_search(self):
        req = SearchRequest(q="Naruto")
        assert req.q == "Naruto"
        assert req.page == 1
        assert req.limit == 30
    
    def test_search_with_pagination(self):
        req = SearchRequest(q="Naruto", page="2", limit="20")
        assert req.q == "Naruto"
        assert req.page == 2
        assert req.limit == 20


class TestEmbedRequestModel:
    """Tests for embed request validation."""
    
    def test_required_url(self):
        with pytest.raises(ValueError, match="Parameter"):
            EmbedRequestModel(url="", force=False)
    
    def test_invalid_url(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            EmbedRequestModel(url="not-a-url", force=False)
    
    def test_valid_url(self):
        req = EmbedRequestModel(url="https://example.com/video/123", force=False)
        assert req.url == "https://example.com/video/123"
        assert req.force == False
    
    def test_force_parsing(self):
        req = EmbedRequestModel(url="https://example.com", force="true")
        assert req.force == True
        
        req = EmbedRequestModel(url="https://example.com", force="1")
        assert req.force == True
        
        req = EmbedRequestModel(url="https://example.com", force=True)
        assert req.force == True
        
        req = EmbedRequestModel(url="https://example.com", force=False)
        assert req.force == False


class TestEpisodePlayersRequest:
    """Tests for episode players request validation."""
    
    def test_required_episode_id(self):
        with pytest.raises(ValueError, match="Episode id must be provided"):
            EpisodePlayersRequest(episode_id="", prefix="a")
    
    def test_numeric_episode_id(self):
        with pytest.raises(ValueError, match="Episode id must be numeric"):
            EpisodePlayersRequest(episode_id="abc", prefix="a")
    
    def test_valid_episode_id(self):
        req = EpisodePlayersRequest(episode_id="123", prefix="a")
        assert req.episode_id == "123"
        assert req.prefix == "a"
    
    def test_prefix_validation(self):
        with pytest.raises(ValueError):
            EpisodePlayersRequest(episode_id="123", prefix="INVALID!")
        
        req = EpisodePlayersRequest(episode_id="123", prefix="a-b1")
        assert req.prefix == "a-b1"


class TestHomeFeaturedRequest:
    """Tests for home featured request validation."""
    
    def test_default_url(self):
        req = HomeFeaturedRequest()
        assert req.url == "https://animesdigital.org/home"
        assert req.force == False
    
    def test_invalid_url(self):
        with pytest.raises(ValueError, match="Invalid URL"):
            HomeFeaturedRequest(url="not-a-url")
    
    def test_valid_url(self):
        req = HomeFeaturedRequest(url="https://example.com", force="true")
        assert req.url == "https://example.com"
        assert req.force == True


class TestFavoriteRequest:
    """Tests for favorite request validation."""
    
    def test_required_url(self):
        with pytest.raises(ValueError, match="Field"):
            FavoriteRequest(url="", name="Test")
    
    def test_valid_favorite(self):
        req = FavoriteRequest(
            url="https://example.com/anime/test",
            name="Test Anime",
            image_url="https://example.com/image.jpg",
            user_id="user123"
        )
        assert req.url == "https://example.com/anime/test"
        assert req.name == "Test Anime"


class TestHistoryRequest:
    """Tests for history request validation."""
    
    def test_required_url(self):
        with pytest.raises(ValueError, match="Field"):
            HistoryRequest(url="", title="Test")
    
    def test_valid_history(self):
        req = HistoryRequest(
            url="https://example.com/episode/123",
            title="Episode 1",
            image_url="https://example.com/image.jpg",
            user_id="user123"
        )
        assert req.url == "https://example.com/episode/123"
        assert req.title == "Episode 1"
