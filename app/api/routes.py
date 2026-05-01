from flask import Blueprint, request, jsonify, current_app
from app.models.embed import db, EmbedRequest
from app.services.scraper import ScraperService
from app.services.site_manager import site_manager
from app.__init__ import cache, limiter
import json
import logging
from sqlalchemy.exc import SQLAlchemyError

bp = Blueprint('api', __name__)
logger = logging.getLogger(__name__)

def check_api_key():
    key = request.headers.get('X-API-KEY')
    if not key or key != current_app.config['API_KEY']:
        return False
    return True

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

    # Check cache/DB
    cached_entry = EmbedRequest.query.filter_by(url=target_url).first()
    if cached_entry and not force_refresh:
        return jsonify(cached_entry.to_dict()), 200

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
                
                response_payload = {
                    'type': 'home',
                    'source': 'AnimesDigital',
                    'url': target_url,
                    'total_scraped': len(embeds),
                    'results': embeds
                }

            elif scraper.match_pattern(target_url, url_patterns.get('anime_main', '')):
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
                
                response_payload = {
                    'type': 'anime_series',
                    'anime_title': result.get('title'),
                    'source_url': target_url,
                    'total_episodes': result.get('total_items'),
                    'episodes': embeds
                }

            elif scraper.match_pattern(target_url, url_patterns.get('episode', '')):
                embed_info = scraper.extract_embed(page, target_url, config)
                response_payload = {
                    'type': 'single_episode',
                    'title': embed_info.get('title'),
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
