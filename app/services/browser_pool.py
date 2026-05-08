"""
Browser Pool for Playwright.

Reuses Chromium browser instances across requests to avoid
the ~2-4 second startup cost per scrape.
"""

import asyncio
import logging
import threading
import time
from typing import Optional

from playwright.sync_api import sync_playwright, Browser

logger = logging.getLogger(__name__)

_local = threading.local()


class BrowserPool:
    """
    A thread-safe pool of Playwright browsers.
    
    Features:
    - Lazy initialization (only starts when first needed)
    - Browser reuse across requests
    - Automatic cleanup of old/unhealthy browsers
    - Configurable pool size
    """
    
    def __init__(self, max_browsers: int = 2, browser_ttl_seconds: int = 300):
        self.max_browsers = max_browsers
        self.browser_ttl_seconds = browser_ttl_seconds
        self._browsers: list[dict] = []  # [{"browser": Browser, "created_at": timestamp, "in_use": bool}]
        self._lock = threading.Lock()
        self._playwright = None
        
    def _ensure_playwright(self):
        """Initialize Playwright if not already done."""
        if self._playwright is None:
            try:
                asyncio.set_event_loop(asyncio.new_event_loop())
            except Exception:
                pass
            self._playwright = sync_playwright().start()
            
    def _create_browser(self) -> Browser:
        """Create a new browser instance."""
        self._ensure_playwright()
        browser = self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--no-first-run",
                "--no-zygote"
            ]
        )
        logger.info("Created new browser instance")
        return browser
    
    def _is_browser_healthy(self, browser: Browser) -> bool:
        """Check if a browser is still usable."""
        try:
            # Simple check - try to get browser contexts
            browser.contexts
            return True
        except Exception as e:
            logger.warning("Browser health check failed: %s", e)
            return False
    
    def acquire(self) -> Optional[Browser]:
        """
        Acquire a browser from the pool.
        Returns None if pool is full and all browsers are in use.
        """
        with self._lock:
            now = time.time()
            
            # Cleanup old browsers first
            self._cleanup_old_browsers(now)
            
            # Try to find an available browser
            for entry in self._browsers:
                if not entry["in_use"] and self._is_browser_healthy(entry["browser"]):
                    entry["in_use"] = True
                    logger.debug("Acquired existing browser from pool")
                    return entry["browser"]
                    
            # No available browser - create new one if under limit
            if len(self._browsers) < self.max_browsers:
                try:
                    browser = self._create_browser()
                    entry = {
                        "browser": browser,
                        "created_at": now,
                        "in_use": True
                    }
                    self._browsers.append(entry)
                    logger.info("Created new browser for pool (total: %d)", len(self._browsers))
                    return browser
                except Exception as e:
                    logger.error("Failed to create browser: %s", e)
                    return None
                    
            logger.warning("Browser pool exhausted (max: %d)", self.max_browsers)
            return None
    
    def release(self, browser: Browser):
        """Mark a browser as no longer in use."""
        with self._lock:
            for entry in self._browsers:
                if entry["browser"] == browser:
                    entry["in_use"] = False
                    logger.debug("Released browser back to pool")
                    break
                    
    def _cleanup_old_browsers(self, now: float):
        """Remove browsers that have exceeded their TTL or are unhealthy."""
        to_remove = []
        for entry in self._browsers:
            age = now - entry["created_at"]
            if age > self.browser_ttl_seconds:
                to_remove.append(entry)
            elif not entry["in_use"] and not self._is_browser_healthy(entry["browser"]):
                to_remove.append(entry)
                
        for entry in to_remove:
            if not entry["in_use"]:  # Only remove if not in use
                try:
                    entry["browser"].close()
                    self._browsers.remove(entry)
                    logger.info("Cleaned up old browser from pool (total: %d)", len(self._browsers))
                except Exception as e:
                    logger.warning("Error closing browser during cleanup: %s", e)
                    
    def shutdown(self):
        """Close all browsers and stop Playwright."""
        with self._lock:
            for entry in self._browsers:
                try:
                    entry["browser"].close()
                except Exception as e:
                    logger.warning("Error closing browser during shutdown: %s", e)
            self._browsers.clear()
            
            if self._playwright:
                try:
                    self._playwright.stop()
                    self._playwright = None
                except Exception as e:
                    logger.warning("Error stopping Playwright: %s", e)
                    
            logger.info("Browser pool shut down")


def get_browser_pool(max_browsers: int = 1) -> BrowserPool:
    """Get the thread-local browser pool instance."""
    if not hasattr(_local, 'pool'):
        _local.pool = BrowserPool(max_browsers=max_browsers)
    return _local.pool


def shutdown_browser_pool():
    """Shutdown the thread-local browser pool for the current thread."""
    if hasattr(_local, 'pool'):
        try:
            _local.pool.shutdown()
        except Exception as e:
            logger.warning("Error shutting down thread-local pool: %s", e)
        finally:
            del _local.pool
