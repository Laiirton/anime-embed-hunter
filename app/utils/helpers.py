import re

def clean_name(name):
    """
    Limpa nomes de animes e episódios removendo termos desnecessários.
    """
    if not name:
        return name
    
    # Remove prefix "Assistir "
    name = re.sub(r'^Assistir\s+', '', name, flags=re.IGNORECASE)
    
    # Remove sufixos comuns
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
