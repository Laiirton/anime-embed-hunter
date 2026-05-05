from flask import jsonify

from app import cache, limiter
from app.services.site_manager import site_manager
from app.api.routes import bp
from app.api.utils import _utcnow, check_api_key
from app.api.db_utils import _cleanup_expired_embed_cache_if_needed

@bp.route("/reload-config", methods=["POST"])
@limiter.limit("5 per minute")
def reload_config():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    if site_manager.reload_configs():
        cache.clear()
        return jsonify({"message": "Configs reloaded"}), 200
    return jsonify({"error": "Failed to reload configs"}), 500

@bp.route("/maintenance/cleanup-cache", methods=["POST"])
@limiter.limit("2 per minute")
def cleanup_cache():
    if not check_api_key():
        return jsonify({"error": "Unauthorized"}), 401

    deleted = _cleanup_expired_embed_cache_if_needed(force=True)
    return jsonify({"deleted_rows": deleted}), 200

@bp.route("/health", methods=["GET"])
@limiter.exempt
def health():
    return jsonify({
        "status": "ok",
        "timestamp": _utcnow().isoformat(),
    }), 200
