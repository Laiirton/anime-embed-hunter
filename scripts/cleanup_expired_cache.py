import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from app import create_app
from app.services.cache_maintenance import delete_expired_embed_cache


def main():
    app = create_app()
    batch_size = int(os.getenv("EMBED_CACHE_CLEANUP_BATCH_SIZE", "1000"))

    with app.app_context():
        deleted = delete_expired_embed_cache(batch_size=batch_size)
        print(f"[SUCCESS] Linhas removidas de embed_requests: {deleted}")


if __name__ == "__main__":
    main()
