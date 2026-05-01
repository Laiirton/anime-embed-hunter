import sqlite3
import re
import os

def clean_name(name):
    if not name:
        return name
    # Remove prefix "Assistir "
    name = re.sub(r'^Assistir\s+', '', name, flags=re.IGNORECASE)
    # Remove suffixes like " Online em HD", " Online FHD", " Todos Episódios", etc.
    suffixes = [
        r'\s+Online\s+em\s+HD$',
        r'\s+Online\s+FHD$',
        r'\s+Todos\s+Episódios.*$',
        r'\s+Online$',
        r'\s+Dublado\s+Online.*$',
        r'\s+Legendado\s+Online.*$'
    ]
    for suffix in suffixes:
        name = re.sub(suffix, '', name, flags=re.IGNORECASE)
    
    return name.strip()

def main():
    db_path = 'instance/anime_embeds.db'
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
