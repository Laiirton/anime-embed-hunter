import os
import sys
import re
import requests
from bs4 import BeautifulSoup
import time

# Adiciona o diretório raiz do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models.embed import Anime, db

# Forçar UTF-8 no Windows para evitar erro de Unicode com emojis
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass


# Mapeamento de status do Kitsu (inglês) para PT-BR
STATUS_MAP = {
    "finished": "Finalizado",
    "current": "Em Exibição",
    "upcoming": "Em Breve",
}

KITSU_API_BASE = "https://kitsu.io/api/edge"
KITSU_HEADERS = {
    "Accept": "application/vnd.api+json",
    "Content-Type": "application/vnd.api+json",
}


def _search_kitsu_anime(name: str, session: requests.Session) -> dict | None:
    """
    Busca um anime na API do Kitsu pelo nome.
    Retorna o primeiro resultado como dict (attributes) ou None se não encontrar.
    """
    try:
        resp = session.get(
            f"{KITSU_API_BASE}/anime",
            params={
                "filter[text]": name,
                "page[limit]": 1,
            },
            headers=KITSU_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        entries = data.get("data", [])
        if not entries:
            return None

        return entries[0]
    except Exception:
        return None


def _fetch_kitsu_genres(anime_id: str, session: requests.Session) -> list[str]:
    """Busca os gêneros de um anime no Kitsu pelo ID."""
    try:
        resp = session.get(
            f"{KITSU_API_BASE}/anime/{anime_id}/genres",
            headers=KITSU_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        return [g["attributes"]["name"] for g in data.get("data", []) if g.get("attributes", {}).get("name")]
    except Exception:
        return []


def _fetch_kitsu_data(anime: Anime, session: requests.Session) -> dict:
    """Busca dados do Kitsu para um anime e retorna um dict com os campos a serem atualizados."""
    updates = {}

    # Tenta buscar pelo nome completo
    entry = _search_kitsu_anime(anime.name, session)

    # Se não achou, tenta simplificar o nome (remover caracteres especiais, pegar só a parte principal)
    if not entry:
        simple_name = re.sub(r'[\(\[].*?[\)\]]', '', anime.name).strip()
        if simple_name and simple_name != anime.name:
            entry = _search_kitsu_anime(simple_name, session)

    if not entry:
        return updates

    attrs = entry.get("attributes", {})
    kitsu_id = entry.get("id")

    # --- cover_url ---
    cover_image = attrs.get("coverImage") or {}
    if cover_image.get("original"):
        updates["cover_url"] = cover_image["original"]

    # --- rating (averageRating é string como "84.06") ---
    avg_rating = attrs.get("averageRating")
    if avg_rating is not None:
        try:
            rating_float = float(avg_rating)
            updates["rating"] = f"{rating_float:.1f}"
        except (ValueError, TypeError):
            pass

    # --- status (mapear para PT-BR) ---
    kitsu_status = attrs.get("status")
    if kitsu_status:
        updates["status"] = STATUS_MAP.get(kitsu_status, kitsu_status)

    # --- total_episodes ---
    ep_count = attrs.get("episodeCount")
    if ep_count is not None:
        try:
            updates["total_episodes"] = int(ep_count)
        except (ValueError, TypeError):
            pass

    # --- genres (via endpoint separado) ---
    if kitsu_id:
        genres = _fetch_kitsu_genres(kitsu_id, session)
        if genres:
            updates["genres"] = ", ".join(genres)

    # --- synopsis (fallback se o scrape do site não tiver sinopse) ---
    synopsis = attrs.get("synopsis") or attrs.get("description")
    if synopsis and not anime.synopsis:
        updates["synopsis"] = synopsis

    return updates


def refresh_all_metadata_fast():
    app = create_app()
    with app.app_context():
        print("--- Iniciando Atualização de Metadados (PT-BR + Kitsu) ---")
        print("Objetivo: Sobreescrever dados do site em PT-BR e complementar com Kitsu API.")

        animes = Anime.query.all()
        total = len(animes)
        print(f"Encontrados {total} animes para processar.")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'pt-BR,pt;q=0.9,en;q=0.8',
        }

        success_count = 0
        error_count = 0
        kitsu_count = 0

        session = requests.Session()

        try:
            for i, anime in enumerate(animes, 1):
                print(f"[{i}/{total}] {anime.name}...", end=" ", flush=True)
                try:
                    changed = False

                    # --- PASSO 1: Scrape do site original (sinopse/gêneros/ano em PT-BR) ---
                    res = session.get(anime.url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        soup = BeautifulSoup(res.text, 'html.parser')

                        # Extrair Sinopse (sempre sobrescrever para garantir PT-BR)
                        synopsis_el = soup.select_one('.sinopse')
                        if synopsis_el:
                            synopsis_text = synopsis_el.get_text(strip=True)
                            if synopsis_text and synopsis_text != anime.synopsis:
                                anime.synopsis = synopsis_text
                                changed = True

                        # Extrair Gêneros (sempre sobrescrever para garantir PT-BR)
                        genre_els = soup.select('.genre a')
                        if genre_els:
                            genres_text = ", ".join([g.get_text(strip=True) for g in genre_els])
                            if genres_text and genres_text != anime.genres:
                                anime.genres = genres_text
                                changed = True

                        # Extrair Ano
                        info_els = soup.select('.info')
                        for el in info_els:
                            text = el.get_text()
                            if "Ano" in text:
                                match = re.search(r'\d{4}', text)
                                if match:
                                    new_year = int(match.group(0))
                                    if new_year != anime.year:
                                        anime.year = new_year
                                        changed = True

                    # --- PASSO 2: Buscar dados complementares do Kitsu API ---
                    kitsu_updates = _fetch_kitsu_data(anime, session)
                    if kitsu_updates:
                        for field, value in kitsu_updates.items():
                            setattr(anime, field, value)
                        changed = True
                        kitsu_count += 1

                    # Commit se houve alguma alteração
                    if changed:
                        db.session.commit()
                        print("✅")
                        success_count += 1
                    else:
                        db.session.commit()
                        print("➖ (sem alterações)")
                        success_count += 1

                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    db.session.rollback()
                    print(f"❌ (Erro: {str(e)[:50]})")
                    error_count += 1

                # Delay entre requisições para não sobrecarregar
                if i % 10 == 0:
                    time.sleep(1)
                else:
                    time.sleep(0.2)

        except KeyboardInterrupt:
            print("\n\nInterrompido pelo usuário. Progresso salvo até o último commit bem-sucedido.")

        print(f"\n--- Atualização Concluída! ---")
        print(f"Sucessos: {success_count}")
        print(f"Erros: {error_count}")
        print(f"Animes com dados do Kitsu: {kitsu_count}")
        print(f"Total processado: {success_count + error_count}/{total}")


if __name__ == "__main__":
    refresh_all_metadata_fast()