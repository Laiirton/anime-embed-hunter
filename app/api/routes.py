from flask import Blueprint

bp = Blueprint("api", __name__)

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
