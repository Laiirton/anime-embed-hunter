"""
Browser Pool for Playwright. 

Reuses Chromium browser instances across requests to avoid
the ~2-4 second startup cost per scrape.

Pool sizing is environment-aware:
  - Render (free tier, 512 MB RAM):  max 1 browser, 20 page contexts each
  - Local/staging:                    max 4 browsers, 50 page contexts each

All browsers carry a per-instance page-context budget to prevent memory
leaks on long-running free-tier instances.  Dead / crashed browsers are
detected on acquire() and removed automatically.

Metrics are emitted via structured logging at each lifecycle step.

IMPORTANT: Playwright is imported LAZILY to avoid loading native libraries
at module import time. On Render free tier (512MB RAM), even the Playwright
driver can push memory too close to the limit.
"""

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------
_RENDER = (
    os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").startswith("/opt/render")
    or os.environ.get("RENDER", "").lower() == "true"
)

# ---------------------------------------------------------------------------
# Pool configuration
# ---------------------------------------------------------------------------
# Render free tier: very conservative to stay within 512 MB
_MAX_BROWSERS_RENDER = 1
_MAX_CONTEXTS_PER_BROWSER_RENDER = 20

# Local/dev: generous
_MAX_BROWSERS_LOCAL = 4
_MAX_CONTEXTS_PER_BROWSER_LOCAL = 50

_BROWSER_IDLE_TTL = 600  # seconds — close browsers unused for 10 min
_MAX_BROWSERS = _MAX_BROWSERS_RENDER if _RENDER else _MAX_BROWSERS_LOCAL
_MAX_CONTEXTS = _MAX_CONTEXTS_PER_BROWSER_RENDER if _RENDER else _MAX_CONTEXTS_PER_BROWSER_LOCAL


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
class _PoolMetrics:
    def __init__(self):
        self.browsers_created = 0
        self.browsers_closed = 0
        self.acquires = 0
        self.releases = 0
        self.acquire_failures = 0
        self.total_contexts_created = 0
        self.browsers_replaced_dead = 0
        self.browsers_replaced_budget = 0

    def snapshot(self):
        return {
            "browsers_created": self.browsers_created,
            "browsers_closed": self.browsers_closed,
            "acquires": self.acquires,
            "releases": self.releases,
            "acquire_failures": self.acquire_failures,
            "total_contexts_created": self.total_contexts_created,
            "browsers_replaced_dead": self.browsers_replaced_dead,
            "browsers_replaced_budget": self.browsers_replaced_budget,
        }


_metrics = _PoolMetrics()


# ---------------------------------------------------------------------------
# Lazy Playwright import
# ---------------------------------------------------------------------------
_playwright = None

def _get_playwright():
    """Lazy import of Playwright to avoid loading at module import time."""
    global _playwright
    if _playwright is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
    return _playwright


# ---------------------------------------------------------------------------
# BrowserPool
# ---------------------------------------------------------------------------
class BrowserPool:
    """
    A thread-safe pool of Playwright Chromium browsers.

    Browsers are lazily created on first acquire().  Each browser has a
    page-context budget; once exceeded it is gracefully replaced.
    Dead / crashed browsers are detected on acquire().
    """

    def __init__(
        self,
        max_browsers: Optional[int] = None,
        max_contexts_per_browser: Optional[int] = None,
    ):
        self._lock = threading.RLock()
        self._browsers: list[dict] = []
        self.max_browsers = max_browsers or _MAX_BROWSERS
        self.max_contexts_per_browser = max_contexts_per_browser or _MAX_CONTEXTS
        self._idle_ttl = _BROWSER_IDLE_TTL
        logger.info(
            "browser_pool: initialized (max_browsers=%d, max_contexts=%d, render=%s)",
            self.max_browsers,
            self.max_contexts_per_browser,
            _RENDER,
        )

    def get_metrics(self):
        return _metrics.snapshot()

    # -- internal helpers ------------------------------------------------
    def _create_browser(self):
        """Create a fresh Chromium browser."""
        pw = _get_playwright()
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
                # Render free tier memory limits
                "--js-flags='--max-old-space-size=128'",
            ],
        )
        _metrics.browsers_created += 1
        logger.info(
            "browser_pool: browser created (pool_size=%d, render=%s)",
            len(self._browsers),
            _RENDER,
        )
        return browser

    def _is_alive(self, browser) -> bool:
        try:
            # browser.contexts is a fast health probe
            _ = browser.contexts
            return browser.is_connected()
        except Exception:
            return False

    def _cleanup_unhealthy(self, now: float):
        i = 0
        while i < len(self._browsers):
            entry = self._browsers[i]
            if entry["in_use"]:
                i += 1
                continue
            # TTL expiry
            if (now - entry["last_used"]) > self._idle_ttl:
                try:
                    entry["browser"].close()
                except Exception:
                    pass
                _metrics.browsers_closed += 1
                self._browsers.pop(i)
                continue
            # Health check
            if not self._is_alive(entry["browser"]):
                try:
                    entry["browser"].close()
                except Exception:
                    pass
                _metrics.browsers_closed += 1
                _metrics.browsers_replaced_dead += 1
                self._browsers.pop(i)
                continue
            i += 1

    # -- public API ------------------------------------------------------
    def acquire(self) -> Optional:
        """
        Acquire a browser from the pool.

        - Removes dead browsers.
        - Replaces browsers that exceeded their page-context budget.
        - Creates new browser if none available and pool not full.
        - Returns None if pool exhausted.
        """
        with self._lock:
            now = time.time()
            self._cleanup_unhealthy(now)

            for entry in self._browsers:
                if entry["in_use"]:
                    continue
                if not self._is_alive(entry["browser"]):
                    _metrics.browsers_replaced_dead += 1
                    continue
                if entry["contexts_created"] >= self.max_contexts_per_browser:
                    try:
                        entry["browser"].close()
                    except Exception:
                        pass
                    _metrics.browsers_closed += 1
                    _metrics.browsers_replaced_budget += 1
                    self._browsers.remove(entry)
                    continue

                entry["in_use"] = True
                entry["contexts_created"] += 1
                entry["last_used"] = now
                _metrics.acquires += 1
                _metrics.total_contexts_created += 1
                return entry["browser"]

            # No available browser — create one if room left
            if len(self._browsers) < self.max_browsers:
                browser = self._create_browser()
                entry = {
                    "browser": browser,
                    "in_use": True,
                    "contexts_created": 1,
                    "last_used": now,
                }
                self._browsers.append(entry)
                _metrics.acquires += 1
                _metrics.total_contexts_created += 1
                return browser

            _metrics.acquire_failures += 1
            logger.warning("browser_pool: exhausted (size=%d)", len(self._browsers))
            return None

    def release(self, browser) -> None:
        with self._lock:
            for entry in self._browsers:
                if entry["browser"] is browser:
                    entry["in_use"] = False
                    entry["last_used"] = time.time()
                    _metrics.releases += 1
                    logger.info(
                        "browser_pool: browser released (contexts=%d, pool_size=%d)",
                        entry["contexts_created"],
                        len(self._browsers),
                    )
                    return
            logger.debug(
                "browser_pool: release for unknown browser (pool_size=%d)",
                len(self._browsers),
            )

    def shutdown(self):
        with self._lock:
            for entry in self._browsers:
                try:
                    entry["browser"].close()
                except Exception:
                    pass
            self._browsers.clear()
            _metrics.browsers_closed += len(self._browsers)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
_pool: Optional[BrowserPool] = None
_pool_lock = threading.Lock()


def get_browser_pool() -> BrowserPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = BrowserPool()
    return _pool


def shutdown_browser_pool():
    global _pool
    if _pool is not None:
        _pool.shutdown()
        _pool = None
