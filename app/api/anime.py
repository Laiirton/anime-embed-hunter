import logging
import re

from flask import current_app, jsonify, request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app import limiter
from app.models.embed import Anime, Episode
from app.api.routes import bp
from app.api.utils import (
    _parse_positive_int,
    _serialize_anime,
    _serialize_episode,
    check_api_key,
)

logger = logging.getLogger(__name__)

@bp.route("/anime/<path:slug>", methods=["GET"])
@limiter.limit("120 per minute")
def get_anime(slug):
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    if not slug or not re.match(r"^[a-zA-Z0-9._~/-]{1,255}$", slug):
        return jsonify({"error": "Invalid slug"}), 400

    # Carrega anime com episódios via selectinload para evitar N+1
    anime = (
        Anime.query
        .options(selectinload(Anime.episodes))
        .filter(
            (Anime.url.ilike(f"%/anime/%/{slug}")) |
            (Anime.url == f"https://animesdigital.org/anime/{slug}")
        )
        .first()
    )
    if not anime:
        return jsonify({"error": "Anime not found"}), 404

    from app.services.metadata_service import populate_anime_metadata_single
    populate_anime_metadata_single(anime)

    # Trigger background scrape to get PT-BR metadata if missing
    if not anime.synopsis or not anime.genres:
        from app import get_scraper_queue
        from app.tasks.scraper import run_scraper_task
        from app.services.site_manager import site_manager
        site_key, config = site_manager.get_config_for_url(anime.url)
        if site_key:
            get_scraper_queue().enqueue(run_scraper_task, anime.url, config)

    default_limit = max(1, int(current_app.config.get("DEFAULT_PAGE_SIZE", 30)))
    max_limit = max(default_limit, int(current_app.config.get("MAX_PAGE_SIZE", 100)))
    page = _parse_positive_int(request.args.get("page"), 1, 1, 100000)
    limit = _parse_positive_int(request.args.get("limit"), default_limit, 1, max_limit)

    try:
        episode_query = Episode.query.options(selectinload(Episode.anime)).filter_by(anime_id=anime.id)
        total_episodes = episode_query.count()
        total_pages = max(1, (total_episodes + limit - 1) // limit)
        if page > total_pages:
            page = total_pages

        episodes = (
            episode_query.order_by(Episode.id.asc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        # Usa episódios já carregados via selectinload, sem N+1
        payload = _serialize_anime(anime, include_episodes_count=True)
        payload.update(
            {
                "page": page,
                "limit": limit,
                "episodes_total_pages": total_pages,
                "episodes_total_results": total_episodes,
                "episodes": [_serialize_episode(ep) for ep in episodes],
            }
        )
        return jsonify(payload), 200
    except SQLAlchemyError as exc:
        logger.error("Anime lookup failed: %s", exc)
        return jsonify({"error": "Anime lookup failed"}), 500

@bp.route("/anime/full", methods=["GET"])
@limiter.limit("20 per minute")
def get_anime_full():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    name = request.args.get("name")
    if not name:
        return jsonify({"error": "Name parameter is required"}), 400

    from app.api.utils import _escape_like_pattern
    escaped_name = _escape_like_pattern(name.strip())
    
    try:
        anime = (
            Anime.query
            .filter(Anime.name.ilike(f"%{escaped_name}%", escape="\\"))
            .first()
        )
        
        if not anime:
            return jsonify({"error": "Anime not found"}), 404

        from app.services.metadata_service import populate_anime_metadata_single
        populate_anime_metadata_single(anime)

        from app.models.embed import db, Episode
        from datetime import datetime, timedelta
        
        episodes = Episode.query.filter_by(anime_id=anime.id).order_by(Episode.id.asc()).all()

        # Evita raspar toda hora se já foi raspado recentemente (nas últimas 2 horas)
        is_recent = False
        if anime.last_scanned:
            if isinstance(anime.last_scanned, str):
                last_scanned_dt = datetime.fromisoformat(anime.last_scanned.replace('Z', '+00:00'))
            else:
                last_scanned_dt = anime.last_scanned
                
            if last_scanned_dt.tzinfo:
                from datetime import timezone
                is_recent = (datetime.now(timezone.utc) - last_scanned_dt) < timedelta(hours=2)
            else:
                is_recent = (datetime.utcnow() - last_scanned_dt) < timedelta(hours=2)

        # Só raspa se não tiver episódios OU se estiver em exibição e não for recente
        should_scrape = (not episodes) or (anime.status and "exib" in anime.status.lower() and not is_recent)

        if should_scrape:
            from app.services.scraper import ScraperService
            from app.services.site_manager import site_manager
            site_key, config = site_manager.get_config_for_url(anime.url)
            
            if site_key:
                try:
                    with ScraperService() as scraper:
                        context = scraper._get_context()
                        page = context.new_page()
                        try:
                            # Usa 'load' para garantir que a lista de episódios seja carregada
                            scraper._goto_with_retry(page, anime.url, wait_until="load")
                            data = scraper.extract_episodes(page, anime.url, config)
                            
                            if 'episode_urls' in data and data['episode_urls']:
                                for item in data['episode_urls']:
                                    ep = Episode.query.filter_by(url=item['url']).first()
                                    if not ep:
                                        ep = Episode(title=item['title'], url=item['url'], anime_id=anime.id)
                                        db.session.add(ep)
                                    else:
                                        ep.anime_id = anime.id # Atualiza o vínculo
                                
                                anime.last_scanned = datetime.utcnow()
                                db.session.commit()
                                # Recarrega os episódios agora vinculados
                                episodes = Episode.query.filter_by(anime_id=anime.id).order_by(Episode.id.asc()).all()
                        finally:
                            page.close()
                            context.close()
                except Exception as e:
                    logger.warning(f"Failed to scrape episodes for {anime.name}: {e}")

        # Se mesmo depois do scrape (ou se não precisou raspar) ainda estiver vazio,
        # tenta o Fallback 1 (buscar por nome no banco)
        if not episodes:
            episodes = Episode.query.filter(Episode.title.ilike(f"%{escaped_name}%")).order_by(Episode.id.asc()).all()
            if episodes:
                # Vincula os episódios encontrados ao anime para as próximas consultas
                for ep in episodes:
                    ep.anime_id = anime.id
                db.session.commit()

        payload = _serialize_anime(anime, include_episodes_count=True)
        payload["episodes"] = [_serialize_episode(ep) for ep in episodes]
        # Corrige o episodes_count para refletir a quantidade de episódios reais na lista
        payload["episodes_count"] = len(episodes)

        return jsonify(payload), 200
    except SQLAlchemyError as exc:
        logger.error("Anime full lookup failed: %s", exc)
        return jsonify({"error": "Anime lookup failed"}), 500
