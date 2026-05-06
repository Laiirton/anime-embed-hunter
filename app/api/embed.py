import logging
from flask import current_app, jsonify, request

from app import limiter, scraper_queue
from app.tasks.scraper import run_scraper_task
from app.services.site_manager import site_manager
from app.api.routes import bp
from app.api.utils import (
    _is_valid_http_url,
    _parse_bool,
    check_api_key,
)
from app.api.db_utils import (
    _cleanup_expired_embed_cache_if_needed,
    get_embed_with_swr,
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

    # Estratégia de cache com Stale-While-Revalidate
    if not force_refresh:
        cached_payload, status = get_embed_with_swr(target_url)
        
        if status == "fresh":
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "embed_requests"
            return jsonify(cached_payload), 200
        
        elif status == "stale":
            # Enfileira tarefa para atualizar em background
            scraper_queue.enqueue(run_scraper_task, target_url, config)
            
            # Retorna o dado obsoleto para o usuário
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "embed_requests (stale)"
            return jsonify(cached_payload), 200

    # Se chegamos aqui, é um "miss" ou "force_refresh"
    scraper_queue.enqueue(run_scraper_task, target_url, config)
    return jsonify({"message": "Scraping task enqueued", "target": target_url}), 202
