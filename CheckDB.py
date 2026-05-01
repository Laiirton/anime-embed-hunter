import sqlite3
import os

def check():
    db_path = os.path.join('instance', 'anime_embeds.db')
    if not os.path.exists(db_path):
        print(f"[!] Banco de dados não encontrado em {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        print("\n" + "="*40)
        print("       RELATÓRIO DO BANCO DE DADOS")
        print("="*40)

        # Estatísticas
        cursor.execute("SELECT COUNT(*) FROM animes")
        total_animes = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM episodes")
        total_episodes = cursor.fetchone()[0]

        print(f"[*] Total de Animes no Catálogo: {total_animes}")
        print(f"[*] Total de Episódios/Embeds:   {total_episodes}")

        # Animes
        if total_animes > 0:
            print("\n--- ÚLTIMOS ANIMES ADICIONADOS ---")
            cursor.execute("SELECT id, name, last_scanned FROM animes ORDER BY id DESC LIMIT 5")
            for row in cursor.fetchall():
                print(f"ID: {row[0]} | {row[1]} (em {row[2]})")
        
        # Episódios
        if total_episodes > 0:
            print("\n--- ÚLTIMOS EMBEDS (VIDEOS) CAPTURADOS ---")
            cursor.execute("""
                SELECT e.title, e.embed_url, a.name 
                FROM episodes e
                LEFT JOIN animes a ON e.anime_id = a.id
                ORDER BY e.id DESC LIMIT 5
            """)
            for row in cursor.fetchall():
                anime_name = row[2] if row[2] else "Desconhecido"
                print(f"Anime:  {anime_name}")
                print(f"Vídeo:  {row[0]}")
                print(f"Embed:  {row[1][:80]}...")
                print("-" * 20)

    except sqlite3.OperationalError as e:
        print(f"[!] Erro ao ler tabelas: {e}")
        print("[i] Certifique-se de que rodou o servidor (run.py) e fez ao menos uma busca.")
    finally:
        conn.close()

if __name__ == "__main__":
    check()
