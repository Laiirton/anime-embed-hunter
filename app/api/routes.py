from flask import Blueprint, jsonify
import os
import time

bp = Blueprint("api", __name__)

@bp.route("/health")
def health_check():
    """Health check endpoint for Render."""
    return jsonify({
        "status": "ok",
        "timestamp": time.time(),
        "render_free_tier": os.getenv("RENDER", "false")
    }), 200

# Import modularized routes to register them with the blueprint
from app.api import (
    catalog,
    anime,
    home,
    episode,
    user,
    search,
    embed,
    maintenance
)
