from flask import Blueprint, request, jsonify, current_app
from app.models.embed import db, EmbedRequest, Anime, Episode
from app.services.scraper import ScraperService
from app.services.site_manager import site_manager
from app.__init__ import cache, limiter
import json
import logging
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime

import re

bp = Blueprint('api', __name__)
logger = logging.getLogger(__name__)

def clean_name(name):
    if not name:
        return name
    # Remove prefix "Assistir "
    name = re.sub(r'^Assistir\s+', '', name, flags=re.IGNORECASE)
    # Remove suffixes like " Online em HD", " Online FHD", " Todos Episódios", etc.
    suffixes = [
        r'\s+Online\s+em\s+HD$',
        r'\s+Online\s+FHD$',
        r'\s+Todos\s+Episódios.*$',
        r'\s+Online$',
        r'\s+Dublado\s+Online.*$',
        r'\s+Legendado\s+Online.*$'
    ]
    for suffix in suffixes:
        name = re.sub(suffix, '', name, flags=re.IGNORECASE)
    
    return name.strip()

def check_api_key():
    key = request.headers.get('X-API-KEY')
    if not key or key != current_app.config['API_KEY']:
        return False
    return True

def save_animes_to_db(anime_list):
    try:
        for item in anime_list:
            name = clean_name(item.get('name'))
            url = item.get('url')
            
            if not name or not url:
                continue

            # Check if already exists
            anime = Anime.query.filter_by(url=url).first()
            if anime:
                anime.name = name
                anime.last_scanned = datetime.utcnow()
            else:
                anime = Anime(name=name, url=url, last_scanned=datetime.utcnow())
                db.session.add(anime)
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error saving animes to DB: {e}")

def save_episodes_to_db(episode_list, anime_url=None):
    try:
        anime = None
        if anime_url:
            anime = Anime.query.filter_by(url=anime_url).first()

        for item in episode_list:
            title = clean_name(item.get('title'))
            url = item.get('episode_url') or item.get('url')
            embed_url = item.get('embed_url')
            
            if not url or not embed_url:
                continue

            ep = Episode.query.filter_by(url=url).first()
            if ep:
                ep.title = title
                ep.embed_url = embed_url
                ep.last_updated = datetime.utcnow()
            else:
                ep = Episode(
                    title=title, 
                    url=url, 
                    embed_url=embed_url, 
                    anime_id=anime.id if anime else None,
                    last_updated=datetime.utcnow()
                )
                db.session.add(ep)
        
        if anime:
            anime.last_scanned = datetime.utcnow()
        
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error saving episodes to DB: {e}")

@bp.route('/search', methods=['GET'])
@limiter.limit("60 per minute")
def search_animes():
    if not check_api_key():
        return jsonify({'error': 'Unauthorized'}), 401

    query = request.args.get('q', '').strip()
    if not query:
        return jsonify({'error': 'Query parameter "q" is required'}), 400

    # Search in DB using ILIKE (case-insensitive)
    try:
        results = Anime.query.filter(Anime.name.ilike(f"%{query}%")).limit(50).all()
        return jsonify({
            'query': query,
            'total_found': len(results),
            'results': [anime.to_dict() for anime in results]
        }), 200
    except Exception as e:
        logger.error(f"Search error: {e}")
        return jsonify({'error': 'Search failed'}), 500

@bp.route('/get-embed', methods=['GET'])
@limiter.limit("10 per minute")
def get_embed():
    if not check_api_key():
        return jsonify({'error': 'Unauthorized'}), 401

    target_url = request.args.get('url')
    force_refresh = request.args.get('force', 'false').lower() == 'true'
    
    if not target_url:
        return jsonify({'error': 'Parameter "url" is required'}), 400

    site_key, config = site_manager.get_config_for_url(target_url)
    if not site_key:
        return jsonify({'error': 'URL domain not supported'}), 400

    # The professional on-demand system now handles caching for animes and episodes.
    # The old EmbedRequest cache is kept only as a fallback or for non-anime URLs.
    # But for animesdigital, we want to use the new logic below.

    # Scrape
    url_patterns = config.get('url_patterns', {})
    
    try:
        with ScraperService() as scraper:
            page = scraper._get_context().new_page()
            response_payload = {}

            if scraper.match_pattern(target_url, url_patterns.get('home', '')):
                result = scraper.extract_episodes(page, target_url, config, selector_key='home')
                if 'error' in result:
                    return jsonify(result), 502
                
                items = result.get('episode_urls', [])
                embeds = []
                for item in items:
                    ep_url = item['url']
                    if scraper.match_pattern(ep_url, url_patterns.get('episode', '')):
                        embed_info = scraper.extract_embed(page, ep_url, config)
                        if 'title' not in embed_info:
                            embed_info['title'] = item.get('title')
                        embed_info['item_type'] = 'episode'
                        embeds.append(embed_info)
                    elif scraper.match_pattern(ep_url, url_patterns.get('anime_main', '')):
                        embeds.append({
                            'title': item.get('title'),
                            'url': ep_url,
                            'item_type': 'series_or_movie',
                            'note': 'Main page link'
                        })
                
                # Save episodes to DB
                save_episodes_to_db(embeds)

                response_payload = {
                    'type': 'home',
                    'source': 'AnimesDigital',
                    'url': target_url,
                    'total_scraped': len(embeds),
                    'results': embeds
                }

            elif scraper.match_pattern(target_url, url_patterns.get('anime_main', '')):
                # PROFESSIONAL ON-DEMAND SYSTEM:
                # 1. Check if we already have this anime and its episodes in DB
                anime = Anime.query.filter_by(url=target_url).first()
                if anime and anime.episodes:
                    # Check how old the data is (e.g., 24 hours)
                    from datetime import timedelta
                    if anime.last_scanned and (datetime.utcnow() - anime.last_scanned) < timedelta(hours=24):
                        logger.info(f"Returning cached episodes for: {anime.name}")
                        response_payload = {
                            'type': 'anime_series',
                            'anime_title': anime.name,
                            'source_url': target_url,
                            'total_episodes': len(anime.episodes),
                            'episodes': [ep.to_dict() for ep in anime.episodes],
                            'cached': True
                        }
                        return jsonify(response_payload), 200

                # 2. If not in DB or too old, SCRAPE it
                result = scraper.extract_episodes(page, target_url, config)
                if 'error' in result:
                    return jsonify(result), 502
                
                items = result.get('episode_urls', [])
                embeds = []
                for item in items:
                    ep_url = item['url']
                    embed_info = scraper.extract_embed(page, ep_url, config)
                    if 'title' not in embed_info:
                        embed_info['title'] = item.get('title')
                    embeds.append(embed_info)
                
                # 3. Save episodes and link to anime
                save_episodes_to_db(embeds, anime_url=target_url)

                response_payload = {
                    'type': 'anime_series',
                    'anime_title': clean_name(result.get('title')),
                    'source_url': target_url,
                    'total_episodes': result.get('total_items'),
                    'episodes': embeds,
                    'cached': False
                }

            elif scraper.match_pattern(target_url, config.get('selectors', {}).get('directory', {}).get('url_pattern', '')):
                result = scraper.extract_directory(page, target_url, config)
                if 'error' in result:
                    return jsonify(result), 502
                
                # Each item in directory already cleaned by save_animes_to_db if we wanted, 
                # but let's clean the objects in results too for consistency.
                cleaned_animes = []
                for a in result.get('animes', []):
                    cleaned_animes.append({
                        'name': clean_name(a.get('name')),
                        'url': a.get('url')
                    })
                
                save_animes_to_db(cleaned_animes)

                response_payload = {
                    'type': 'directory',
                    'source': 'AnimesDigital',
                    'url': target_url,
                    'total_pages': result.get('total_pages'),
                    'count': len(cleaned_animes),
                    'animes': cleaned_animes
                }

            elif scraper.match_pattern(target_url, url_patterns.get('episode', '')):
                embed_info = scraper.extract_embed(page, target_url, config)
                
                # Save single episode to DB
                save_episodes_to_db([embed_info])

                response_payload = {
                    'type': 'single_episode',
                    'title': clean_name(embed_info.get('title')),
                    'url': target_url,
                    'embed_url': embed_info.get('embed_url')
                }

            else:
                return jsonify({'error': 'URL pattern not recognized'}), 400

            # Save to DB
            if response_payload:
                save_to_db(target_url, response_payload)
            
            return jsonify(response_payload), 200

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({'error': 'Internal server error'}), 500

def save_to_db(url, data):
    try:
        entry = EmbedRequest.query.filter_by(url=url).first()
        if entry:
            entry.response_data = json.dumps(data)
        else:
            entry = EmbedRequest(url=url, response_data=json.dumps(data))
            db.session.add(entry)
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"DB Error: {e}")

@bp.route('/reload-config', methods=['POST'])
def reload_config():
    if not check_api_key():
        return jsonify({'error': 'Unauthorized'}), 401
    
    if site_manager.reload_configs():
        return jsonify({'message': 'Configs reloaded'}), 200
    return jsonify({'error': 'Failed to reload configs'}), 500
