import logging
import random
import time
import re
import os
import threading
import asyncio
from typing import List, Dict, Any
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
from app.core.config import Config
from pydantic import BaseModel


class ScraperService:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.logger = logging.getLogger(__name__)

    def __enter__(self):
        # Resolve o erro "Playwright Sync API inside the asyncio loop"
        # Garante que esta thread não tenha um loop de eventos ativo que conflite com o Sync do Playwright
        try:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
        except Exception:
            pass

        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=Config.HEADLESS,
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
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def _get_context(self) -> BrowserContext:
        context = self.browser.new_context(
            user_agent=random.choice(Config.USER_AGENTS),
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            timezone_id="America/Sao_Paulo"
        )
        # Bloqueia recursos pesados para economizar RAM no Render
        context.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,otf}", lambda route: route.abort())
        return context

    def match_pattern(self, url: str, pattern: str) -> bool:
        if not pattern:
            return False
        return re.match(pattern, url) is not None

    def extract_episodes(self, page: Page, url: str, config: Any, selector_key: str = 'anime_main') -> Dict[str, Any]:
        if isinstance(config, BaseModel):
            config = config.model_dump()
        bypass_js = config.get("bypass_javascript", False)
        selectors = config.get("selectors", {}).get(selector_key, {})
        episodes_selector = selectors.get('episodes_section')

        if not episodes_selector:
            return {'error': f"Selector '{selector_key}.episodes_section' not found in config."}

        try:
            if bypass_js:
                return self._extract_via_iframe_bypass(page, url, episodes_selector)
            
            page.goto(url, timeout=Config.BROWSER_TIMEOUT)
            page.wait_for_selector(episodes_selector, timeout=30000)

            # Extract page title as anime name
            title_selector = selectors.get('title')
            if title_selector:
                try:
                    title = page.inner_text(title_selector, timeout=5000).strip()
                except PlaywrightTimeoutError:
                    title = page.title().split('-')[0].strip()
            else:
                title = page.title().split('-')[0].strip()
            
            elements = page.query_selector_all(episodes_selector)
            
            urls = []
            for elem in elements:
                href = elem.get_attribute('href')
                link_title = elem.get_attribute('title') or elem.inner_text().strip()
                if href:
                    urls.append({
                        'url': urljoin(url, href),
                        'title': link_title
                    })
            
            return {
                'url': url, 
                'title': title,
                'total_items': len(urls),
                'episode_urls': urls
            }
            
        except Exception as e:
            self.logger.error(f"Error extracting episodes from {url}: {e}")
            self.capture_screenshot(page, "error_episodes")
            return {'error': str(e)}

    def extract_home_sections(self, page: Page, url: str, config: Any) -> Dict[str, Any]:
        if isinstance(config, BaseModel):
            config = config.model_dump()
        
        home_sections_config = config.get("selectors", {}).get("home", {}).get("home_sections", {})
        if not home_sections_config:
            # Fallback to default extraction if no sections defined
            return self.extract_episodes(page, url, config, selector_key="home")

        try:
            page.goto(url, timeout=Config.BROWSER_TIMEOUT)
            # Wait for some common element
            page.wait_for_selector("div.header_title", timeout=30000)

            # Use JS to extract sections based on text content
            info_selector = config.get("selectors", {}).get("home", {}).get("item_info_selector", ".number")
            data = page.evaluate("""
                ({sectionsConfig, infoSelector}) => {
                    const results = {};
                    const headers = document.querySelectorAll('div.header_title');
                    headers.forEach(header => {
                        const title = header.innerText.trim().toUpperCase();
                        
                        for (const [key, searchTitle] of Object.entries(sectionsConfig)) {
                            if (title.includes(searchTitle.toUpperCase())) {
                                const container = header.nextElementSibling;
                                if (container) {
                                    const links = Array.from(container.querySelectorAll('a')).map(a => {
                                        const infoElem = a.querySelector(infoSelector);
                                        return {
                                            title: a.getAttribute('title') || a.innerText.trim(),
                                            url: a.href,
                                            info: infoElem ? infoElem.innerText.trim() : null
                                        };
                                    });
                                    results[key] = links;
                                }
                                break;
                            }
                        }
                    });
                    return results;
                }
            """, {"sectionsConfig": home_sections_config, "infoSelector": info_selector})

            return {
                "url": url,
                "sections": data
            }

        except Exception as e:
            self.logger.error(f"Error extracting home sections from {url}: {e}")
            self.capture_screenshot(page, "error_home_sections")
            return {'error': str(e)}

    def capture_screenshot(self, page: Page, name: str):
        try:
            os.makedirs('screenshots', exist_ok=True)
            path = f"screenshots/{name}_{int(time.time())}.png"
            page.screenshot(path=path)
            self.logger.info(f"Screenshot saved: {path}")
        except Exception as e:
            self.logger.error(f"Failed to capture screenshot: {e}")

    def extract_embed(self, page: Page, episode_url: str, config: Any, retries: int = 2) -> Dict[str, Any]:
        if isinstance(config, BaseModel):
            config = config.model_dump()
        attempt = 0
        bypass_js = config.get("bypass_javascript", False)
        iframe_selectors = config.get("selectors", {}).get("episode", {}).get("iframe_selectors", [])

        while attempt <= retries:
            try:
                if bypass_js:
                    links = self._extract_player_via_iframe_bypass(page, episode_url)
                    if links:
                        return {'episode_url': episode_url, 'embed_url': links[0]}
                else:
                    page.goto(episode_url, timeout=Config.BROWSER_TIMEOUT)
                    
                    # Try to get episode title
                    title_selector = config.get("selectors", {}).get("episode", {}).get("title")
                    if title_selector:
                        try:
                            ep_title = page.inner_text(title_selector, timeout=5000).strip()
                        except PlaywrightTimeoutError:
                            ep_title = page.title().split('-')[0].strip()
                    else:
                        ep_title = page.title().split('-')[0].strip()
                    
                    for selector in iframe_selectors:
                        try:
                            iframe_el = page.wait_for_selector(selector, timeout=10000)
                            src = iframe_el.get_attribute("src")
                            if src:
                                return {
                                    'episode_url': episode_url, 
                                    'title': ep_title,
                                    'embed_url': src
                                }
                        except PlaywrightTimeoutError:
                            continue
                
                attempt += 1
                if attempt <= retries:
                    time.sleep(2 ** attempt)
            except Exception as e:
                self.logger.warning(f"Attempt {attempt} failed for {episode_url}: {e}")
                attempt += 1
                if attempt <= retries:
                    time.sleep(2 ** attempt)
        
        self.capture_screenshot(page, "error_embed")
        return {'episode_url': episode_url, 'error': 'Could not find embed URL'}

    def extract_episode_players(self, page: Page, episode_url: str, config: Any) -> Dict[str, Any]:
        if isinstance(config, BaseModel):
            config = config.model_dump()
        iframe_selectors = config.get("selectors", {}).get("episode", {}).get("iframe_selectors", [])

        try:
            page.goto(episode_url, timeout=Config.BROWSER_TIMEOUT)
            ep_title = self._extract_episode_title(page, config)

            ordered_sources = []
            seen = set()

            for selector in iframe_selectors:
                for iframe_el in page.query_selector_all(selector):
                    src = (iframe_el.get_attribute("src") or "").strip()
                    if src and src not in seen:
                        ordered_sources.append({"selector": selector, "embed_url": src})
                        seen.add(src)

            generic_sources = page.evaluate(
                """
                () => {
                    const candidates = Array.from(document.querySelectorAll("iframe[src]"));
                    const score = (el) => {
                        const text = ((el.id || "") + " " + (el.className || "")).toLowerCase();
                        if (text.includes("player") || text.includes("tab-video")) return 2;
                        const src = (el.getAttribute("src") || "").toLowerCase();
                        if (src.includes("embed") || src.includes("player")) return 1;
                        return 0;
                    };
                    return candidates
                        .sort((a, b) => score(b) - score(a))
                        .map((el) => (el.getAttribute("src") || "").trim())
                        .filter(Boolean);
                }
                """
            )

            for src in generic_sources:
                if src not in seen:
                    ordered_sources.append({"selector": "iframe[src]", "embed_url": src})
                    seen.add(src)

            players = []
            for index, item in enumerate(ordered_sources, start=1):
                players.append(
                    {
                        "position": index,
                        "label": f"Player {index}",
                        "embed_url": item["embed_url"],
                        "source_selector": item["selector"],
                    }
                )

            return {
                "episode_url": episode_url,
                "title": ep_title,
                "players": players,
                "total_players": len(players),
            }
        except Exception as exc:
            self.logger.error(f"Error extracting episode players from {episode_url}: {exc}")
            self.capture_screenshot(page, "error_players")
            return {"episode_url": episode_url, "error": str(exc), "players": [], "total_players": 0}

    def extract_directory(self, page: Page, url: str, config: Dict[str, Any]) -> Dict[str, Any]:
        selectors = config.get("selectors", {}).get("directory", {})
        item_selector = selectors.get('item_selector')
        pagination_selector = selectors.get('pagination_info')

        try:
            page.goto(url, timeout=Config.BROWSER_TIMEOUT)
            page.wait_for_selector(item_selector, timeout=30000)

            # Extract pagination info
            total_pages = 1
            if pagination_selector:
                try:
                    text = page.inner_text(pagination_selector).strip()
                    match = re.search(r'de (\d+)', text)
                    if match:
                        total_pages = int(match.group(1))
                except (PlaywrightTimeoutError, ValueError):
                    pass

            elements = page.query_selector_all(item_selector)
            animes = []
            for elem in elements:
                href = elem.get_attribute('href')
                name = elem.get_attribute('title') or elem.inner_text().strip()
                if href:
                    animes.append({
                        'name': name,
                        'url': urljoin(url, href)
                    })

            return {
                'url': url,
                'total_pages': total_pages,
                'items_per_page': len(animes),
                'animes': animes
            }
        except Exception as e:
            self.logger.error(f"Error extracting directory from {url}: {e}")
            self.capture_screenshot(page, "error_directory")
            return {'error': str(e)}

    def _extract_episode_title(self, page: Page, config: Dict[str, Any]) -> str:
        title_selector = config.get("selectors", {}).get("episode", {}).get("title")
        if title_selector:
            try:
                return page.inner_text(title_selector, timeout=5000).strip()
            except PlaywrightTimeoutError:
                pass
        return page.title().split("-")[0].strip()

    def _extract_via_iframe_bypass(self, page: Page, url: str, selector: str) -> Dict[str, Any]:
        page.set_content(f'<html><body><iframe src="{url}" sandbox></iframe></body></html>')
        page.wait_for_load_state("domcontentloaded")
        
        urls = []
        for frame in page.frames:
            if frame.url == url:
                try:
                    frame.wait_for_selector(selector, timeout=30000)
                    elements = frame.query_selector_all(selector)
                    for elem in elements:
                        href = elem.get_attribute('href')
                        if href:
                            urls.append(urljoin(url, href))
                except PlaywrightTimeoutError:
                    continue
        return {'url': url, 'episode_urls': urls}

    def _extract_player_via_iframe_bypass(self, page: Page, url: str) -> List[str]:
        page.set_content(f'<html><body><iframe src="{url}" sandbox></iframe></body></html>')
        page.wait_for_load_state("domcontentloaded")
        
        srcs = []
        for frame in page.frames:
            if frame.url == url:
                try:
                    found = frame.evaluate("() => Array.from(document.querySelectorAll('iframe[src]')).map(i => i.src)")
                    if found:
                        srcs.extend(found)
                except Exception as exc:
                    self.logger.debug("Bypass extraction failed for frame %s: %s", frame.url, exc)
                    continue
        return list(set(srcs))
