import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

# Adiciona a raiz do projeto ao path para possíveis imports futuros
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ====================================================
#                  CONFIGURAÇÕES
# ====================================================
BASE_URL = os.getenv("CATALOGUER_API_BASE_URL", "http://localhost:5000/get-embed")
API_KEY = os.getenv("API_KEY")
DIRECTORY_URL_TEMPLATE = (
    "https://animesdigital.org/animes-legendados-online?filter_letter=0&type_url=animes"
    "&filter_audio=legendado&filter_order=name&filter_genre_add=&filter_genre_del="
    "&pagina={page}&search=0&limit=30"
)
OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "anime_catalog.json")

REQUEST_DELAY_SECONDS = float(os.getenv("CATALOGUER_REQUEST_DELAY_SECONDS", "1.2"))
MAX_RETRIES = int(os.getenv("CATALOGUER_MAX_RETRIES", "4"))
BACKOFF_BASE_SECONDS = float(os.getenv("CATALOGUER_BACKOFF_BASE_SECONDS", "1.5"))
TIMEOUT_SECONDS = int(os.getenv("CATALOGUER_TIMEOUT_SECONDS", "60"))
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _parse_retry_after(header_value):
    if not header_value:
        return None
    try:
        return float(header_value)
    except ValueError:
        return None


def fetch_page(session, page_num):
    url = DIRECTORY_URL_TEMPLATE.format(page=page_num)
    params = {"url": url, "force": "true"}

    for attempt in range(MAX_RETRIES + 1):
        print(f"[*] Buscando página {page_num} (tentativa {attempt + 1}/{MAX_RETRIES + 1})...")
        try:
            response = session.get(BASE_URL, params=params, timeout=TIMEOUT_SECONDS)

            if response.status_code == 200:
                return response.json()

            if response.status_code in RETRYABLE_STATUS and attempt < MAX_RETRIES:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                backoff = retry_after if retry_after is not None else BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(
                    f"[!] Página {page_num}: status {response.status_code}. "
                    f"Retry em {backoff:.1f}s"
                )
                time.sleep(backoff)
                continue

            print(f"[!] Erro na página {page_num}: {response.status_code} - {response.text[:300]}")
            return None

        except requests.RequestException as exc:
            if attempt < MAX_RETRIES:
                backoff = BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(f"[!] Erro de conexão na página {page_num}: {exc}. Retry em {backoff:.1f}s")
                time.sleep(backoff)
                continue

            print(f"[!] Erro de conexão na página {page_num}: {exc}")
            return None

    return None


def _merge_catalog(target_map, animes):
    for anime in animes:
        name = (anime.get("name") or "").strip()
        url = (anime.get("url") or "").strip()
        if not name or not url:
            continue
        target_map[url] = {"name": name, "url": url}


def _save_catalog(catalog_map):
    ordered = sorted(catalog_map.values(), key=lambda item: item["name"].lower())
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
    return ordered


def main():
    if not API_KEY:
        print("[!] API_KEY não encontrado no ambiente/.env")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    catalog_map = {}

    with requests.Session() as session:
        session.headers.update({"X-API-KEY": API_KEY})

        # Primeira busca para pegar o total de páginas
        first_page_data = fetch_page(session, 1)
        if not first_page_data or "animes" not in first_page_data:
            print("[!] Não foi possível iniciar o catálogo.")
            return

        _merge_catalog(catalog_map, first_page_data["animes"])
        total_pages = int(first_page_data.get("total_pages", 1))
        print(
            f"[+] Página 1 carregada. Total de páginas detectadas: {total_pages}. "
            f"Animes únicos: {len(catalog_map)}"
        )
        _save_catalog(catalog_map)

        # Loop para buscar as demais páginas
        for page_num in range(2, total_pages + 1):
            time.sleep(max(0.0, REQUEST_DELAY_SECONDS))
            data = fetch_page(session, page_num)

            if data and "animes" in data:
                _merge_catalog(catalog_map, data["animes"])
                _save_catalog(catalog_map)
                print(
                    f"[+] Página {page_num}/{total_pages} carregada. "
                    f"Animes únicos acumulados: {len(catalog_map)}"
                )
            else:
                print(f"[!] Falha ao carregar página {page_num}. Continuando...")

    final_catalog = _save_catalog(catalog_map)
    print(
        f"\n[SUCCESS] Catálogo completo! Total de {len(final_catalog)} animes únicos "
        f"salvos em {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
