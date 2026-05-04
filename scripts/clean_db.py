import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from app.utils.helpers import clean_name


def normalize_database_url(raw_url):
    if not raw_url:
        return None
    url = raw_url.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def build_database_url(base_dir):
    load_dotenv(os.path.join(base_dir, ".env"))
    remote = normalize_database_url(os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL"))
    if remote:
        return remote
    return f"sqlite:///{os.path.join(base_dir, 'instance', 'anime_embeds.db')}"


def main():
    base_dir = BASE_DIR
    db_url = build_database_url(base_dir)

    engine = create_engine(db_url, pool_pre_ping=True)

    print("[*] Iniciando limpeza dos nomes no banco de dados...")

    updated_animes = 0
    updated_episodes = 0

    try:
        with engine.begin() as conn:
            animes = conn.execute(text("SELECT id, name FROM animes")).fetchall()
            for anime in animes:
                new_name = clean_name(anime.name)
                if new_name != anime.name:
                    conn.execute(
                        text("UPDATE animes SET name = :name WHERE id = :id"),
                        {"name": new_name, "id": anime.id},
                    )
                    updated_animes += 1

            episodes = conn.execute(text("SELECT id, title FROM episodes")).fetchall()
            for episode in episodes:
                new_title = clean_name(episode.title)
                if new_title != episode.title:
                    conn.execute(
                        text("UPDATE episodes SET title = :title WHERE id = :id"),
                        {"title": new_title, "id": episode.id},
                    )
                    updated_episodes += 1

    except SQLAlchemyError as exc:
        print(f"[!] Erro durante limpeza: {exc}")
        return

    print("[SUCCESS] Limpeza concluída!")
    print(f" -> {updated_animes} animes limpos.")
    print(f" -> {updated_episodes} episódios limpos.")


if __name__ == "__main__":
    main()
