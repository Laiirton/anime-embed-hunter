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


def refresh_all_metadata_fast():
    app = create_app()
    with app.app_context():
        print("--- Iniciando Atualização de Metadados (PT-BR) ---")
        print("Objetivo: Sobreescrever todos os dados (Sinopse/Gêneros) para garantir PT-BR.")

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

        try:
            for i, anime in enumerate(animes, 1):
                print(f"[{i}/{total}] {anime.name}...", end=" ", flush=True)
                try:
                    res = requests.get(anime.url, headers=headers, timeout=10)
                    if res.status_code == 200:
                        soup = BeautifulSoup(res.text, 'html.parser')

                        # Extrair Sinopse
                        synopsis_el = soup.select_one('.sinopse')
                        if synopsis_el:
                            anime.synopsis = synopsis_el.get_text(strip=True)

                        # Extrair Gêneros
                        genre_els = soup.select('.genre a')
                        if genre_els:
                            anime.genres = ", ".join([g.get_text(strip=True) for g in genre_els])

                        # Extrair Ano
                        info_els = soup.select('.info')
                        for el in info_els:
                            text = el.get_text()
                            if "Ano" in text:
                                match = re.search(r'\d{4}', text)
                                if match:
                                    anime.year = int(match.group(0))

                        db.session.commit()
                        print("✅")
                        success_count += 1
                    else:
                        db.session.rollback()
                        print(f"❌ (Status {res.status_code})")
                        error_count += 1
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    db.session.rollback()
                    print(f"❌ (Erro: {str(e)[:50]})")
                    error_count += 1

                if i % 10 == 0:
                    time.sleep(1)
                else:
                    time.sleep(0.2)

        except KeyboardInterrupt:
            print("\n\nInterrompido pelo usuário. Progresso salvo até o último commit bem-sucedido.")

        print(f"\n--- Atualização Concluída! ---")
        print(f"Sucessos: {success_count}")
        print(f"Erros: {error_count}")
        print(f"Total processado: {success_count + error_count}/{total}")


if __name__ == "__main__":
    refresh_all_metadata_fast()
