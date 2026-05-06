# Adicionar lógica de SWR em app/api/db_utils.py

def get_embed_with_swr(url, ttl_hours=24):
    entry = EmbedRequest.query.filter_by(url=url).first()
    if not entry:
        return None, "miss"
    
    # Verifica se expirou
    max_age = timedelta(hours=ttl_hours)
    if entry.expires_at < _utcnow():
        return entry.data, "stale" # Dados obsoletos, disparar background task
        
    return entry.data, "fresh" # Dados frescos
