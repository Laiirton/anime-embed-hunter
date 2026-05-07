"""
Script para encontrar animes sem cover_url e buscar capas pela API do Kitsu.

Uso:
    python scripts/fix_missing_covers.py              # Lista apenas os animes sem capa
    python scripts/fix_missing_covers.py --fix         # Busca e salva as capas no banco
    python scripts/fix_missing_covers.py --limit 10    # Limita a N animes (com --fix)
"""
import os
import sys
import re
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models.embed import Anime, db

KITSU_API_BASE = "https://kitsu.io/api/edge"
KITSU_HEADERS = {
    "Accept": "application/vnd.api+json",
}


def list_missing_covers():
    """Retorna todos os animes que não têm cover_url."""
    app = create_app()
    with app.app_context():
        missing = Anime.query.filter(
            (Anime.cover_url.is_(None)) | (Anime.cover_url == "")
        ).order_by(Anime.name).all()
        return list(missing)


def search_kitsu_cover(name: str, session) -> str | None:
    """
    Busca um anime no Kitsu pelo nome e retorna a URL da cover image original,
    ou None se não encontrar.
    """
    # Tenta primeiro com o nome completo
    candidates = [name]

    # Se o nome tem algo entre parênteses/colchetes, tenta sem (ex: "(Dublado)")
    simplified = re.sub(r'[\(\[].*?[\)\]]', '', name).strip()
    if simplified and simplified != name:
        candidates.append(simplified)

    # Tenta pegar só a primeira palavra-chave se as outras tentativas falharem
    # (alguns nomes no banco podem ser diferentes do Kitsu)
    for search_name in candidates:
        try:
            resp = session.get(
                f"{KITSU_API_BASE}/anime",
                params={"filter[text]": search_name, "page[limit]": 1},
                headers=KITSU_HEADERS,
                timeout=10,
            )
            if resp.status_code != 200:
                continue

            data = resp.json()
            entries = data.get("data", [])
            if not entries:
                continue

            attrs = entries[0].get("attributes", {})

            # Tenta coverImage primeiro (banner/capa larga)
            cover_image = attrs.get("coverImage") or {}
            if cover_image.get("original"):
                return cover_image["original"]

            # Fallback: posterImage (imagem do pôster)
            poster_image = attrs.get("posterImage") or {}
            if poster_image.get("original"):
                return poster_image["original"]

        except Exception:
            continue

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Encontra e corrige animes sem cover_url usando Kitsu API"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Busca e salva as capas no banco de dados",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limite de animes para processar (0 = todos)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="Delay entre requisições em segundos (padrão: 0.3)",
    )
    args = parser.parse_args()

    missing = list_missing_covers()
    total = len(missing)

    if total == 0:
        print("✅ Todos os animes já possuem cover_url!")
        return

    print(f"📋 Encontrados {total} animes SEM cover_url:\n")

    if not args.fix:
        # Modo lista: só exibe os animes
        for i, anime in enumerate(missing, 1):
            print(f"  {i:4d}. [ID {anime.id:5d}] {anime.name}")
        print(f"\nTotal: {total}")
        print("\n💡 Para buscar e salvar as capas automaticamente, execute:")
        print(f"    python scripts/fix_missing_covers.py --fix")
        print(f"\n   Para limitar a quantidade:")
        print(f"    python scripts/fix_missing_covers.py --fix --limit 10")
        return

    # Modo fix: busca as capas e salva no banco
    print(f"\n🔍 Buscando capas para {total} animes...\n")

    app = create_app()
    with app.app_context():
        import requests as req

        session = req.Session()
        success = 0
        errors = 0
        not_found = 0
        limit = args.limit if args.limit > 0 else total
        processed = 0

        for anime in missing:
            if processed >= limit:
                break

            processed += 1
            print(f"  [{processed}/{limit}] {anime.name}...", end=" ", flush=True)

            try:
                cover_url = search_kitsu_cover(anime.name, session)

                if cover_url:
                    anime.cover_url = cover_url
                    db.session.commit()
                    print(f"✅ {cover_url[:70]}...")
                    success += 1
                else:
                    print("❌ (não encontrado no Kitsu)")
                    db.session.commit()
                    not_found += 1

            except Exception as e:
                db.session.rollback()
                print(f"❌ (erro: {str(e)[:60]})")
                errors += 1

            time.sleep(args.delay)

        print(f"\n--- Resumo ---")
        print(f"  ✅ Capas salvas: {success}")
        print(f"  ❌ Não encontrados no Kitsu: {not_found}")
        print(f"  ⚠️  Erros: {errors}")
        print(f"  📊 Processados: {processed}/{total}")


if __name__ == "__main__":
    main()