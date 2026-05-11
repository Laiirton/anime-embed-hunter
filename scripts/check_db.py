import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from app import create_app
from app.models.embed import Episode

app = create_app()
with app.app_context():
    episodes = Episode.query.filter(Episode.title.ilike('%Kami no Niwatsuki%')).all()
    print(f"Found {len(episodes)} episodes")
    for ep in episodes:
        print(f" - {ep.title} (ID: {ep.id}, Anime ID: {ep.anime_id})")
