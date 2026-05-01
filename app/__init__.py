from flask import Flask
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from app.core.config import Config
from app.models.embed import db
import os

cache = Cache()
limiter = Limiter(key_func=get_remote_address)

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    cache.init_app(app)
    limiter.init_app(app)

    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)

    with app.app_context():
        # Register blueprints
        from app.api.routes import bp as api_bp
        app.register_blueprint(api_bp)

        # Create database tables
        db.create_all()

    return app
