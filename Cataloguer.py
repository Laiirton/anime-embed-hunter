import requests
import json
import time
import os

# ====================================================
#                  CONFIGURAÇÕES
# ====================================================
BASE_URL = "http://localhost:5000/get-embed"
API_KEY = "123" # Sua chave de API
DIRECTORY_URL_TEMPLATE = "https://animesdigital.org/animes-legendados-online?filter_letter=0&type_url=animes&filter_audio=legendado&filter_order=name&filter_genre_add=&filter_genre_del=&pagina={page}&search=0&limit=30"
OUTPUT_FILE = "anime_catalog.json"

def fetch_page(page_num):
    url = DIRECTORY_URL_TEMPLATE.format(page=page_num)
    headers = {"X-API-KEY": API_KEY}
    params = {"url": url, "force": "true"}
    
    print(f"[*] Buscando página {page_num}...")
    try:
        response = requests.get(BASE_URL, headers=headers, params=params, timeout=60)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"[!] Erro na página {page_num}: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"[!] Erro de conexão na página {page_num}: {e}")
        return None

def main():
    all_animes = []
    current_page = 1
    total_pages = 1 # Será atualizado na primeira busca

    # Primeira busca para pegar o total de páginas
    first_page_data = fetch_page(current_page)
    if not first_page_data or 'animes' not in first_page_data:
        print("[!] Não foi possível iniciar o catálogo.")
        return

    total_pages = first_page_data.get('total_pages', 1)
    all_animes.extend(first_page_data['animes'])
    print(f"[+] Página 1 carregada. Total de páginas detectadas: {total_pages}")

    # Loop para buscar as demais páginas
    for p in range(2, total_pages + 1):
        time.sleep(2) # Delay para evitar bloqueios
        data = fetch_page(p)
        if data and 'animes' in data:
            all_animes.extend(data['animes'])
            print(f"[+] Página {p}/{total_pages} carregada. Total acumulado: {len(all_animes)}")
            
            # Salva parcialmente para não perder dados em caso de erro
            with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(all_animes, f, indent=2, ensure_ascii=False)
        else:
            print(f"[!] Falha ao carregar página {p}. Continuando...")

    print(f"\n[SUCCESS] Catálogo completo! Total de {len(all_animes)} animes salvos em {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
