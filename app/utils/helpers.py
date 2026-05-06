import re

def extract_audio_type(name):
    """
    Extrai o tipo de áudio (Dublado ou Legendado) do nome.
    """
    if not name:
        return "Legendado"
    
    name_lower = name.lower()
    if "dublado" in name_lower or " dub" in name_lower:
        return "Dublado"
    
    return "Legendado"

def format_info(info):
    """
    Formata a string de informação (ex: "EPISÓDIO 849" -> "Episódio 849").
    """
    if not info:
        return info
    return info.strip().capitalize()

def clean_name(name):

    """
    Limpa nomes de animes e episódios removendo termos desnecessários.
    """
    if not name:
        return name
    
    # Remove prefix "Assistir "
    name = re.sub(r'^Assistir\s+', '', name, flags=re.IGNORECASE)

    # Limpa quebras de linha e múltiplos espaços
    name = name.replace('\n', ' ')
    name = re.sub(r'\s+', ' ', name)
    
    # Remove "HD" isolado no início ou fim
    name = re.sub(r'^\s*HD\s+', '', name, flags=re.IGNORECASE)
    name = re.sub(r'\s+HD\s*$', '', name, flags=re.IGNORECASE)
    
    # Remove padrões de episódio (ex: "Episódio 05", "Ep 05", "Ep. 05")
    name = re.sub(r'\s+(?:Episódio|Ep\.?)\s+\d+\b.*$', '', name, flags=re.IGNORECASE)

    # Remove sufixos comuns e variações de áudio
    suffixes = [
        r'\s+Online\s+em\s+HD$',
        r'\s+Online\s+FHD$',
        r'\s+Todos\s+Episódios.*$',
        r'\s+Online$',
        r'\s+Dublado\s+Online.*$',
        r'\s+Legendado\s+Online.*$',
        r'\s+Dublado$',
        r'\s+Legendado$',
        r'\s+Dub$',
        r'\s+Sub$'
    ]
    for suffix in suffixes:
        name = re.sub(suffix, '', name, flags=re.IGNORECASE)
    
    # Remove caracteres residuais de separação (como " - " no final)
    name = re.sub(r'\s+-\s*$', '', name)
    
    return name.strip()
