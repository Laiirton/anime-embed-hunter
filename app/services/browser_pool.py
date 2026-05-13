"""
Browser Pool for Playwright.

Reuses Chromium browser instances across requests to avoid
the ~2-4 second startup cost per scrape.

Pool sizing is environment-aware:
  - Render (free tier, 512 MB RAM):  max 2 browsers, 50 page contexts each
  - Local/staging:                    max 4 browsers, 50 page contexts each

All browsers carry a per-instance page-context budget to prevent memory
leaks on long-running free-tier instances.  Dead / crashed browsers are
detected on acquire() and removed automatically.

Metrics are emitted via structured logging at each lifecycle step.
"""

import asyncio
import logging
import os
import threading
import time
from typing import Optional

from playwright.sync_api import sync_playwright, Browser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment detection
# ---------------------------------------------------------------------------

def _is_render_free_tier() -> bool:
    """Detect whether we are running on Render free tier."""
    # Render sets RENDER=true and provides IS_ON_RENDER
    return os.environ.get("IS_ON_RENDER", "").lower() == "true" \
        or os.environ.get("RENDER", "").lower() == "true"


# Pool defaults per environment
_MAX_BROWSERS_RENDER = 2
_MAX_BROWSERS_LOCAL = 4
_MAX_CONTEXTS_PER_BROWSER = 50   # pages created across the lifetime of one browser


# ---------------------------------------------------------------------------
# Metrics tracking
# ---------------------------------------------------------------------------

class _PoolMetrics:
    """Lightweight, thread-safe metrics counters for the browser pool."""

    def __init__(self):
        self._lock = threading.Lock()
        self.browsers_created = 0
        self.browsers_closed = 0
        self.browsers_acquired = 0
        self.browsers_released = 0
        self.acquire_failures = 0
        self.total_contexts_created = 0
        self.browsers_replaced_for_health = 0
        self.browsers_replaced_for_context_limit = 0

    def bump(self, field: str):
        with self._lock:
            setattr(self, field, getattr(self, field) + 1)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "browsers_created": self.browsers_created,
                "browsers_closed": self.browsers_closed,
                "browsers_acquired": self.browsers_acquired,
                "browsers_released": self.browsers_released,
                "acquire_failures": self.acquire_failures,
                "total_contexts_created": self.total_contexts_created,
                "browsers_replaced_for_health": self.browsers_replaced_for_health,
                "browsers_replaced_for_context_limit": self.browsers_replaced_for_context_limit,
            }


# ---------------------------------------------------------------------------
# BrowserPool
# ---------------------------------------------------------------------------

class BrowserPool:
    """
    A thread-safe pool of Playwright Chromium browsers.

    Features:
    - Lazy initialization (Playwright starts on first use)
    - Environment-aware pool sizing (Render vs local)
    - Per-browser page-context budget to prevent memory leaks
    - Health checks on acquire — dead browsers are culled
    - Structured metric counters and lifecycle logging
    """

    def __init__(
        self,
        max_browsers: Optional[int] = None,
        browser_ttl_seconds: int = 300,
        max_contexts_per_browser: int = _MAX_CONTEXTS_PER_BROWSER,
        is_render: Optional[bool] = None,
    ):
        if is_render is None:
            is_render = _is_render_free_tier()
        self.is_render = is_render

        if max_browsers is None:
            max_browsers = _MAX_BROWSERS_RENDER if is_render else _MAX_BROWSERS_LOCAL
        self.max_browsers = max_browsers
        self.browser_ttl_seconds = browser_ttl_seconds
        self.max_contexts_per_browser = max_contexts_per_browser

        # Pool storage:  [{"browser": Browser, "created_at": float,
        #                   "in_use": bool, "contexts_created": int}]
        self._browsers: list[dict] = []
        self._lock = threading.Lock()
        self._playwright = None
        self._metrics = _PoolMetrics()

    # -- metrics helper ------------------------------------------------------

    def get_metrics(self) -> dict:
        """Return a snapshot of pool metrics and current state."""
        snapshot = self._metrics.snapshot()
        snapshot.update({
            "pool_size": len(self._browsers),
            "pool_size_max": self.max_browsers,
            "is_render": self.is_render,
        })
        return snapshot

    # -- Playwright lifecycle ------------------------------------------------

    def _ensure_playwright(self):
        """Initialize Playwright if not already done."""
        if self._playwright is None:
            try:
                asyncio.set_event_loop(asyncio.new_event_loop())
            except Exception:
                pass
            self._playwright = sync_playwright().start()
            logger.info("Playwright instance started (render=%s)", self.is_render)

    def _create_browser(self) -> Browser:
        """Create a new Chromium browser instance."""
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
                "--no-zygote",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-default-apps",
                "--disable-translate",
                "--disable-component-update",
                "--mute-audio",
                "--hide-scrollbars",
                "--js-flags=--max-old-space-size=256",
            ],
        )
        self._metrics.bump("browsers_created")
        logger.info(
            "browser_pool: browser created (pool_size=%d, render=%s)",
            len(self._browsers),
            self.is_render,
        )
        return browser

    def _close_browser(self, browser: Browser, reason: str = "explicit") -> None:
        """Close a browser instance and log the reason."""
        try:
            browser.close()
        except Exception as exc:
            logger.warning(
                "browser_pool: error closing browser (%s): %s", reason, exc
            )
        self._metrics.bump("browsers_closed")
        logger.info(
            "browser_pool: browser closed (reason=%s)", reason
        )

    # -- Health check --------------------------------------------------------

    def _is_alive(self, browser: Browser) -> bool:
        """Check if a browser process is still responsive."""
        try:
            # Accessing .contexts is a lightweight round-trip to the browser
            # process; if it has crashed this will raise a TargetClosed or
            # similar exception.
            _ = browser.contexts
            return True
        except Exception as exc:
            logger.warning("browser_pool: health-check failed: %s", exc)
            return False

    # -- Acquire / Release ---------------------------------------------------

    def acquire(self) -> Optional[Browser]:
        """
        Acquire a browser from the pool.

        - Removes dead browsers from the pool.
        - Replaces browsers that have exceeded their page-context budget.
        - Returns None if the pool is exhausted and no new browser can be
          created.
        """
        with self._lock:
            now = time.time()

            # 1. Purge TTL-expired and unhealthy browsers -----------------
            self._cleanup_unhealthy(now)

            # 2. Find an available browser that is still alive and
            #    under its context budget --------------------------------
            for entry in self._browsers:
                if entry["in_use"]:
                    continue
                # Health check (extra guard beyond the cleanup above)
                if not self._is_alive(entry["browser"]):
                    logger.info(
                        "browser_pool: acquired browser was dead, removing"
                    )
                    self._close_browser(entry["browser"], "dead-on-acquire")
                    self._metrics.bump("browsers_replaced_for_health")
                    entry["_closed"] = True  # mark for removal
                    continue
                # Context-budget check
                if entry["contexts_created"] >= self.max_contexts_per_browser:
                    logger.info(
                        "browser_pool: browser reached context budget (%d), replacing",
                        entry["contexts_created"],
                    )
                    self._close_browser(entry["browser"], "context-budget")
                    self._metrics.bump("browsers_replaced_for_context_limit")
                    entry["_closed"] = True
                    continue

                entry["in_use"] = True
                self._metrics.bump("browsers_acquired")
                logger.info(
                    "browser_pool: browser acquired (pool_size=%d, contexts_this_browser=%d)",
                    len(self._browsers),
                    entry["contexts_created"],
                )
                return entry["browser"]

            # 3. Remove dead/closed entries we flagged above -------------
            self._browsers = [e for e in self._browsers if not e.get("_closed", False)]

            # 4. Create a new browser if under limit ---------------------
            if len(self._browsers) < self.max_browsers:
                try:
                    browser = self._create_browser()
                    entry = {
                        "browser": browser,
                        "created_at": now,
                        "in_use": True,
                        "contexts_created": 0,
                    }
                    self._browsers.append(entry)
                    self._metrics.bump("browsers_acquired")
                    logger.info(
                        "browser_pool: new browser created and acquired (total_in_pool=%d)",
                        len(self._browsers),
                    )
                    return browser
                except Exception as exc:
                    self._metrics.bump("acquire_failures")
                    logger.error("browser_pool: failed to create browser: %s", exc)
                    return None

            # 5. Pool is fully exhausted ---------------------------------
            self._metrics.bump("acquire_failures")
            logger.warning(
                "browser_pool: pool exhausted (max=%d, active_in_use=%d)",
                self.max_browsers,
                sum(1 for e in self._browsers if e["in_use"]),
            )
            return None

    def release(self, browser: Browser) -> None:
        """Mark a browser as no longer in use."""
        with self._lock:
            for entry in self._browsers:
                if entry["browser"] is browser:
                    entry["in_use"] = False
                    self._metrics.bump("browsers_released")
                    logger.info(
                        "browser_pool: browser released (contexts_created=%d, pool_size=%d)",
                        entry["contexts_created"],
                        len(self._browsers),
                    )
                    return
            logger.warning(
                "browser_pool: release called for unknown browser (pool_size=%d)",
                len(self._browsers),
            )

    def record_context_created(self, browser: Browser) -> None:
        """
        Call this after creating a new page/context from a browser so the pool
        can track the per-browser context budget.
        """
        with self._lock:
            for entry in self._browsers:
                if entry["browser"] is browser:
                    entry["contexts_created"] += 1
                    self._metrics.bump("total_contexts_created")
                    return

    # -- Internal cleanup ----------------------------------------------------

    def _cleanup_unhealthy(self, now: float) -> None:
        """Remove browsers that are expired, dead, or already closed."""
        to_remove = []
        for entry in self._browsers:
            if entry.get("_closed"):
                to_remove.append(entry)
                continue

            age = now - entry["created_at"]

            # TTL expired and not in use
            if not entry["in_use"] and age > self.browser_ttl_seconds:
                to_remove.append(entry)
                continue

            # Health check failed
            if not self._is_alive(entry["browser"]):
                to_remove.append(entry)
                continue

            # Force close browsers that have been stuck in_use for too long
            if entry["in_use"] and age > self.browser_ttl_seconds * 3:
                logger.warning(
                    "browser_pool: force-closing stuck browser (in_use for %.0fs)", age
                )
                to_remove.append(entry)
                continue

            # Context budget exhausted (even if still "in_use" we log it;
            # the actual purge happens when it gets released)
            if not entry["in_use"] and entry["contexts_created"] >= self.max_contexts_per_browser:
                to_remove.append(entry)
                continue

        for entry in to_remove:
            try:
                self._close_browser(entry["browser"], "cleanup")
            except Exception:
                pass
            try:
                self._browsers.remove(entry)
            except ValueError:
                pass

        if to_remove:
            logger.info(
                "browser_pool: cleanup removed %d browser(s), remaining=%d",
                len(to_remove),
                len(self._browsers),
            )

    # -- Shutdown ------------------------------------------------------------

    def shutdown(self) -> None:
        """Close all browsers and stop Playwright."""
        with self._lock:
            for entry in self._browsers:
                try:
                    self._close_browser(entry["browser"], "shutdown")
                except Exception:
                    pass
            self._browsers.clear()

            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception as exc:
                    logger.warning("browser_pool: error stopping Playwright: %s", exc)
                self._playwright = None

            logger.info(
                "browser_pool: shutdown complete (metrics=%s)",
                self._metrics.snapshot(),
            )

    # -- repr for debugging --------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"BrowserPool(max={self.max_browsers}, size={len(self._browsers)}, "
            f"render={self.is_render})"
        )


# ---------------------------------------------------------------------------
# Thread-local singleton helpers
# ---------------------------------------------------------------------------

_local = threading.local()


def get_browser_pool(max_browsers: Optional[int] = None) -> BrowserPool:
    """Get the thread-local browser pool instance.

    If *max_browsers* is not provided, the pool size is chosen automatically
    based on the hosting environment (Render vs local).
    """
    if not hasattr(_local, "pool"):
        _local.pool = BrowserPool(
            max_browsers=max_browsers,
            browser_ttl_seconds=120,
        )
    return _local.pool


def shutdown_browser_pool() -> None:
    """Shutdown the thread-local browser pool for the current thread."""
    if hasattr(_local, "pool"):
        try:
            _local.pool.shutdown()
        except Exception as exc:
            logger.warning("browser_pool: error shutting down thread-local pool: %s", exc)
        finally:
            del _local.pool
