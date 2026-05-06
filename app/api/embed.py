import logging
from datetime import timedelta

from flask import current_app, jsonify, request

from app import limiter
from app.models.embed import Anime
from app.services.scraper import ScraperService
from app.services.site_manager import site_manager
from app.utils.helpers import clean_name
from app.api.routes import bp
from app.api.utils import (
    _is_valid_http_url,
    _parse_bool,
    _utcnow,
    check_api_key,
)
from app.api.db_utils import (
    _cleanup_expired_embed_cache_if_needed,
    _load_embed_cache,
    _save_to_embed_cache,
    save_animes_to_db,
    save_episodes_to_db,
)

logger = logging.getLogger(__name__)

@bp.route("/get-embed", methods=["GET"])
@limiter.limit("10 per minute")
def get_embed():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    target_url = request.args.get("url", "").strip()
    force_refresh = _parse_bool(request.args.get("force"))

    if not target_url:
        return jsonify({"error": 'Parameter "url" is required'}), 400

    if not _is_valid_http_url(target_url):
        return jsonify({"error": "Invalid URL"}), 400

    site_key, config = site_manager.get_config_for_url(target_url)
    if not site_key:
        return jsonify({"error": "URL domain not supported"}), 400

    _cleanup_expired_embed_cache_if_needed()

    if not force_refresh:
    if not force_refresh:
        cached_payload, status = get_embed_with_swr(target_url)
        if status == "fresh":
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "embed_requests"
            return jsonify(cached_payload), 200
        elif status == "stale":
            # Enfileira tarefa para atualizar em background
            from app import scraper_queue
            from app.tasks.scraper import run_scraper_task
            scraper_queue.enqueue(run_scraper_task, target_url, config)
            
            # Retorna o dado obsoleto para o usuário
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "embed_requests (stale)"
            return jsonify(cached_payload), 200

    url_patterns = config.get("url_patterns", {})

    try:
        with ScraperService() as scraper:
    # Enfileirar tarefa de scraping
    from app import scraper_queue
    from app.tasks.scraper import run_scraper_task
    scraper_queue.enqueue(run_scraper_task, target_url, config)
    return jsonify({"message": "Scraping task enqueued"}), 202

    # Removendo lógica síncrona temporariamente (a ser substituída pelo worker)
    '''
    with ScraperService() as scraper:
        # ...
    '''
            context = scraper._get_context()
            page = context.new_page()

            try:
                response_payload = {}

                if scraper.match_pattern(target_url, url_patterns.get("home", "")):
                    result = scraper.extract_episodes(page, target_url, config, selector_key="home")
                    if "error" in result:
                        return jsonify(result), 502

                    items = result.get("episode_urls", [])
                    embeds = []
                    for item in items:
                        ep_url = item["url"]
                        if scraper.match_pattern(ep_url, url_patterns.get("episode", "")):
                            embed_info = scraper.extract_embed(page, ep_url, config)
                            if "title" not in embed_info:
                                embed_info["title"] = item.get("title")
                            embed_info["cover_url"] = item.get("cover_url")
                            embed_info["item_type"] = "episode"
                            embeds.append(embed_info)
                        elif scraper.match_pattern(ep_url, url_patterns.get("anime_main", "")):
                            embeds.append(
                                {
                                    "title": item.get("title"),
                                    "url": ep_url,
                                    "cover_url": item.get("cover_url"),
                                    "item_type": "series",
                                    "note": "Main page link",
                                }
                            )
                        elif scraper.match_pattern(ep_url, url_patterns.get("movie", "")):
                            embed_info = scraper.extract_embed(page, ep_url, config)
                            if "title" not in embed_info:
                                embed_info["title"] = item.get("title")
                            embed_info["cover_url"] = item.get("cover_url")
                            embed_info["item_type"] = "movie"
                            embeds.append(embed_info)

                    save_episodes_to_db(embeds)

                    response_payload = {
                        "type": "home",
                        "source": site_key,
                        "url": target_url,
                        "total_scraped": len(embeds),
                        "results": embeds,
                        "cached": False,
                    }

                elif scraper.match_pattern(target_url, url_patterns.get("anime_main", "")):
                    anime = Anime.query.filter_by(url=target_url).first()
                    ttl_hours = current_app.config.get("EMBED_CACHE_TTL_HOURS", 24)

                    if not force_refresh and anime and anime.episodes:
                        max_age = timedelta(hours=ttl_hours)
                        if anime.last_scanned and (_utcnow() - anime.last_scanned) < max_age:
                            logger.info("Returning cached episodes for: %s", anime.name)
                            response_payload = {
                                "type": "anime_series",
                                "anime_title": anime.name,
                                "source_url": target_url,
                                "total_episodes": len(anime.episodes),
                                "episodes": [ep.to_dict() for ep in anime.episodes],
                                "cached": True,
                                "cache_source": "animes/episodes",
                            }
                            _save_to_embed_cache(target_url, response_payload)
                            return jsonify(response_payload), 200

                    result = scraper.extract_episodes(page, target_url, config)
                    if "error" in result:
                        return jsonify(result), 502

                    items = result.get("episode_urls", [])
                    embeds = []
                    for item in items:
                        ep_url = item["url"]
                        embed_info = scraper.extract_embed(page, ep_url, config)
                        if "title" not in embed_info:
                            embed_info["title"] = item.get("title")
                        embeds.append(embed_info)

                    save_episodes_to_db(
                        embeds,
                        anime_url=target_url,
                        anime_title=result.get("title"),
                    )

                    response_payload = {
                        "type": "anime_series",
                        "anime_title": clean_name(result.get("title")),
                        "source_url": target_url,
                        "total_episodes": result.get("total_items"),
                        "episodes": embeds,
                        "cached": False,
                    }

                elif scraper.match_pattern(
                    target_url,
                    config.get("selectors", {}).get("directory", {}).get("url_pattern", ""),
                ):
                    result = scraper.extract_directory(page, target_url, config)
                    if "error" in result:
                        return jsonify(result), 502

                    cleaned_animes = [
                        {"name": clean_name(a.get("name")), "url": a.get("url")}
                        for a in result.get("animes", [])
                    ]

                    save_animes_to_db(cleaned_animes)

                    response_payload = {
                        "type": "directory",
                        "source": site_key,
                        "url": target_url,
                        "total_pages": result.get("total_pages"),
                        "count": len(cleaned_animes),
                        "animes": cleaned_animes,
                        "cached": False,
                    }

                elif scraper.match_pattern(target_url, url_patterns.get("episode", "")):
                    embed_info = scraper.extract_embed(page, target_url, config)
                    save_episodes_to_db([embed_info])

                    response_payload = {
                        "type": "single_episode",
                        "title": clean_name(embed_info.get("title")),
                        "url": target_url,
                        "embed_url": embed_info.get("embed_url"),
                        "cached": False,
                    }

                elif scraper.match_pattern(target_url, url_patterns.get("movie", "")):
                    embed_info = scraper.extract_embed(page, target_url, config)
                    save_episodes_to_db(
                        [embed_info],
                        anime_url=target_url,
                        anime_title=embed_info.get("title"),
                        item_type="movie"
                    )

                    response_payload = {
                        "type": "single_movie",
                        "title": clean_name(embed_info.get("title")),
                        "url": target_url,
                        "embed_url": embed_info.get("embed_url"),
                        "cached": False,
                    }

                else:
                    return jsonify({"error": "URL pattern not recognized"}), 400

                if response_payload:
                    _save_to_embed_cache(target_url, response_payload)

                return jsonify(response_payload), 200
            finally:
                context.close()

    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        return jsonify({"error": "Internal server error"}), 500
