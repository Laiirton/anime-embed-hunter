import logging
from flask import jsonify, request

from app import limiter, get_scraper_queue
from app.tasks.scraper import run_scraper_task
from app.services.site_manager import site_manager
from app.api.routes import bp
from app.api.utils import check_api_key
from app.api.validators import EmbedRequestModel
from app.services.unified_cache import persistent_get

logger = logging.getLogger(__name__)


@bp.route("/get-embed", methods=["GET"])
@limiter.limit("10 per minute")
def get_embed():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    try:
        # Validate request parameters with Pydantic
        embed_req = EmbedRequestModel(
            url=request.args.get("url"),
            force=request.args.get("force")
        )
        target_url = embed_req.url
        force_refresh = embed_req.force
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    site_key, config = site_manager.get_config_for_url(target_url)
    if not site_key:
        return jsonify({"error": "URL domain not supported"}), 400

    # Estratégia de cache com Stale-While-Revalidate usando cache unificado
    if not force_refresh:
        cached_payload, status = persistent_get(target_url)
        
        if status == "fresh":
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "persistent_cache"
            return jsonify(cached_payload), 200
        
        elif status == "stale":
            # Enfileira tarefa para atualizar em background
            get_scraper_queue().enqueue(run_scraper_task, target_url, config)
            
            # Retorna o dado obsoleto para o usuário
            cached_payload["cached"] = True
            cached_payload["cache_source"] = "persistent_cache (stale)"
            return jsonify(cached_payload), 200
    
    # Se chegamos aqui, é um "miss" ou "force_refresh"
    get_scraper_queue().enqueue(run_scraper_task, target_url, config)
    return jsonify({"message": "Scraping task enqueued", "target": target_url}), 202
