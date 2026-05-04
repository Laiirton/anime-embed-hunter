import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


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


def check():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_url = build_database_url(base_dir)

    engine = create_engine(db_url, pool_pre_ping=True)

    try:
        with engine.connect() as conn:
            print("\n" + "=" * 40)
            print("       RELATÓRIO DO BANCO DE DADOS")
            print("=" * 40)
            print(f"[*] Banco conectado: {db_url.split('@')[-1]}")

            total_animes = conn.execute(text("SELECT COUNT(*) FROM animes")).scalar_one()
            total_episodes = conn.execute(text("SELECT COUNT(*) FROM episodes")).scalar_one()

            print(f"[*] Total de Animes no Catálogo: {total_animes}")
            print(f"[*] Total de Episódios/Embeds:   {total_episodes}")

            if total_animes > 0:
                print("\n--- ÚLTIMOS ANIMES ADICIONADOS ---")
                rows = conn.execute(
                    text("SELECT id, name, last_scanned FROM animes ORDER BY id DESC LIMIT 5")
                ).fetchall()
                for row in rows:
                    print(f"ID: {row.id} | {row.name} (em {row.last_scanned})")

            if total_episodes > 0:
                print("\n--- ÚLTIMOS EMBEDS (VIDEOS) CAPTURADOS ---")
                rows = conn.execute(
                    text(
                        """
                        SELECT e.title, e.embed_url, a.name
                        FROM episodes e
                        LEFT JOIN animes a ON e.anime_id = a.id
                        ORDER BY e.id DESC LIMIT 5
                        """
                    )
                ).fetchall()
                for row in rows:
                    anime_name = row.name if row.name else "Desconhecido"
                    preview = (row.embed_url or "")[:80]
                    print(f"Anime:  {anime_name}")
                    print(f"Vídeo:  {row.title}")
                    print(f"Embed:  {preview}...")
                    print("-" * 20)

    except SQLAlchemyError as exc:
        print(f"[!] Erro ao consultar banco: {exc}")


if __name__ == "__main__":
    check()
