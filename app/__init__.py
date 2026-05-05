from flask import Flask, request
from flask_caching import Cache
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_cors import CORS
from app.core.config import Config
from app.models.embed import db
import os

cache = Cache()
migrate = Migrate()


def _rate_limit_key():
    api_key = request.headers.get("X-API-KEY", "anonymous")
    return f"{api_key}:{get_remote_address()}"


limiter = Limiter(key_func=_rate_limit_key)


def _validate_required_config(app):
    missing = []
    if not app.config.get("SECRET_KEY"):
        missing.append("SECRET_KEY")
    if not app.config.get("API_KEY"):
        missing.append("API_KEY")
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = config_class.build_engine_options(
        app.config.get("SQLALCHEMY_DATABASE_URI")
    )
    _validate_required_config(app)

    # Initialize extensions
    db.init_app(app)
    cache.init_app(app)
    limiter.init_app(app)
    CORS(app)
    migrate.init_app(app, db)

    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)
    screenshots_path = os.path.abspath(os.path.join(app.root_path, "..", "screenshots"))
    os.makedirs(screenshots_path, exist_ok=True)
    
    # Register blueprints
    from app.api.routes import bp as api_bp
    app.register_blueprint(api_bp)

    return app
