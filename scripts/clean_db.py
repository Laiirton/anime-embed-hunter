import sqlite3
import os
import sys

# Adiciona a raiz do projeto ao path para importar o helper de limpeza
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from app.utils.helpers import clean_name

def main():
    db_path = os.path.join(base_dir, 'instance', 'anime_embeds.db')
    if not os.path.exists(db_path):
        print(f"[!] Banco de dados não encontrado em {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("[*] Iniciando limpeza dos nomes no banco de dados...")

    # Clean Animes
    cursor.execute("SELECT id, name FROM animes")
    animes = cursor.fetchall()
    updated_animes = 0
    for id, name in animes:
        new_name = clean_name(name)
        if new_name != name:
            cursor.execute("UPDATE animes SET name = ? WHERE id = ?", (new_name, id))
            updated_animes += 1

    # Clean Episodes
    cursor.execute("SELECT id, title FROM episodes")
    episodes = cursor.fetchall()
    updated_episodes = 0
    for id, title in episodes:
        new_title = clean_name(title)
        if new_title != title:
            cursor.execute("UPDATE episodes SET title = ? WHERE id = ?", (new_title, id))
            updated_episodes += 1

    conn.commit()
    conn.close()

    print(f"[SUCCESS] Limpeza concluída!")
    print(f" -> {updated_animes} animes limpos.")
    print(f" -> {updated_episodes} episódios limpos.")

if __name__ == "__main__":
    main()
