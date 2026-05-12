import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from app import create_app
from app.models.embed import Episode

app = create_app()
with app.app_context():
    count = Episode.query.filter_by(anime_id=2194).count()
    print(f"Episodes linked to anime 2194: {count}")
