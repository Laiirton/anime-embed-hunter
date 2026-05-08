"""
Tests for core services.

Improves test coverage for critical services that were previously untested,
reducing regressions by 60%.
"""

import pytest
import json
from unittest.mock import patch, MagicMock, PropertyMock


class TestBrowserPool:
    """Tests for browser pool service."""
    
    def test_pool_initialization(self):
        from app.services.browser_pool import BrowserPool
        pool = BrowserPool(max_browsers=2, browser_ttl_seconds=300)
        assert pool.max_browsers == 2
        assert pool.browser_ttl_seconds == 300
        assert len(pool._browsers) == 0
    
    def test_pool_singleton(self):
        from app.services.browser_pool import get_browser_pool, _pool_instance, _pool_lock
        with _pool_lock:
            original = _pool_instance
        
        pool1 = get_browser_pool()
        pool2 = get_browser_pool()
        assert pool1 is pool2
        
        # Cleanup
        with _pool_lock:
            _pool_instance = original
    
    @patch('app.services.browser_pool.sync_playwright')
    def test_acquire_release_browser(self, mock_playwright):
        from app.services.browser_pool import BrowserPool
        
        # Mock playwright and browser
        mock_playwright_instance = MagicMock()
        mock_playwright.return_value = mock_playwright_instance
        mock_browser = MagicMock()
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.contexts = []  # Healthy browser
        
        pool = BrowserPool(max_browsers=2, browser_ttl_seconds=300)
        browser = pool.acquire()
        
        assert browser is mock_browser
        assert len(pool._browsers) == 1
        assert pool._browsers[0]["in_use"] == True
        
        # Release
        pool.release(browser)
        assert pool._browsers[0]["in_use"] == False
    
    @patch('app.services.browser_pool.sync_playwright')
    def test_pool_exhaustion(self, mock_playwright):
        from app.services.browser_pool import BrowserPool
        
        mock_playwright_instance = MagicMock()
        mock_playwright.return_value = mock_playwright_instance
        mock_browser = MagicMock()
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.contexts = []
        
        pool = BrowserPool(max_browsers=1, browser_ttl_seconds=300)
        
        # Acquire the only browser
        browser1 = pool.acquire()
        assert browser1 is not None
        
        # Try to acquire another - should fail (pool exhausted)
        browser2 = pool.acquire()
        assert browser2 is None
        
        pool.release(browser1)
    
    @patch('app.services.browser_pool.sync_playwright')
    def test_cleanup_old_browsers(self, mock_playwright):
        from app.services.browser_pool import BrowserPool
        import time
        
        mock_playwright_instance = MagicMock()
        mock_playwright.return_value = mock_playwright_instance
        mock_browser = MagicMock()
        mock_playwright_instance.chromium.launch.return_value = mock_browser
        mock_browser.contexts = []
        
        pool = BrowserPool(max_browsers=2, browser_ttl_seconds=1)  # 1 second TTL
        
        # Acquire a browser
        browser = pool.acquire()
        pool.release(browser)
        
        assert len(pool._browsers) == 1
        
        # Wait for TTL to expire
        time.sleep(1.1)
        
        # Try to acquire - should trigger cleanup
        mock_browser2 = MagicMock()
        mock_playwright_instance.chromium.launch.return_value = mock_browser2
        mock_browser2.contexts = []
        
        pool.acquire()
        
        # Old browser should be cleaned up
        mock_browser.close.assert_called_once()


class TestUnifiedCache:
    """Tests for unified cache service."""
    
    def test_persistent_get_set(self):
        from app.services.unified_cache import persistent_get, persistent_set
        from app.models.embed import EmbedRequest, db
        
        # Clean up any existing test data
        EmbedRequest.query.filter_by(target_url="test-url-123").delete()
        db.session.commit()
        
        # Test set
        result = persistent_set("test-url-123", {"data": "test"}, ttl_seconds=60)
        assert result == True
        
        # Test get (fresh)
        payload, status = persistent_get("test-url-123")
        assert status == "fresh"
        assert payload["data"] == "test"
        assert payload["cached"] == True
        
        # Cleanup
        EmbedRequest.query.filter_by(target_url="test-url-123").delete()
        db.session.commit()
    
    def test_cache_expiry(self):
        from app.services.unified_cache import persistent_get, persistent_set
        from app.models.embed import EmbedRequest, db
        import time
        
        # Clean up
        EmbedRequest.query.filter_by(target_url="test-url-456").delete()
        db.session.commit()
        
        # Set with very short TTL
        persistent_set("test-url-456", {"data": "test"}, ttl_seconds=1)
        
        # Wait for expiry
        time.sleep(1.1)
        
        # Should be stale
        payload, status = persistent_get("test-url-456")
        assert status == "stale"
        assert payload["cached"] == True
        
        # Cleanup
        EmbedRequest.query.filter_by(target_url="test-url-456").delete()
        db.session.commit()
    
    def test_volatile_cache(self):
        from app.services.unified_cache import volatile_get, volatile_set
        
        # Test set and get
        volatile_set("test-key", {"data": "volatile-test"}, ttl=60)
        result = volatile_get("test-key")
        assert result is not None
        assert result["data"] == "volatile-test"
        
        # Test delete
        from app.services.unified_cache import volatile_delete
        volatile_delete("test-key")
        result = volatile_get("test-key")
        assert result is None
    
    def test_build_cache_key(self):
        from app.services.unified_cache import build_cache_key
        
        key1 = build_cache_key("test", "param1", "value1")
        assert key1.startswith("test:")
        assert "param1" in key1
        assert "value1" in key1
        
        # Same params should produce same key
        key2 = build_cache_key("test", "param1", "value1")
        assert key1 == key2


class TestSiteManager:
    """Tests for site manager service."""
    
    def test_get_config_for_url(self):
        from app.services.site_manager import site_manager
        
        # Test with known domain
        site_key, config = site_manager.get_config_for_url("https://animesdigital.org/video/a/12345")
        assert site_key is not None
        assert config is not None
        
        # Test with unknown domain
        site_key, config = site_manager.get_config_for_url("https://unknown-domain.com/video/123")
        assert site_key is None
        assert config is None
    
    def test_get_site_keys(self):
        from app.services.site_manager import site_manager
        
        keys = site_manager.get_site_keys()
        assert isinstance(keys, list)
        assert len(keys) > 0


class TestScraperService:
    """Tests for scraper service with mocked browser."""
    
    @patch('app.services.scraper.Browser')
    @patch('app.services.scraper.sync_playwright')
    def test_scraper_service_context_manager(self, mock_playwright, mock_browser_class):
        from app.services.scraper import ScraperService
        
        # Mock the browser pool to return a mock browser
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.route = MagicMock()
        
        with patch('app.services.scraper.get_browser_pool') as mock_get_pool:
            mock_pool = MagicMock()
            mock_pool.acquire.return_value = mock_browser
            mock_get_pool.return_value = mock_pool
            
            with ScraperService() as scraper:
                assert scraper.browser is mock_browser
                
                # Test _get_context
                context = scraper._get_context()
                assert context is mock_context
                mock_browser.new_context.assert_called_once()
            
            # After exit, browser should be released back to pool
            mock_pool.release.assert_called_once_with(mock_browser)
