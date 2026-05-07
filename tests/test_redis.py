"""
Script de diagnostico para testar conexao com Redis no Render.
Uso: python scripts/test_redis.py
"""
import os
import sys
import socket
from urllib.parse import urlparse
from datetime import datetime, timezone

# Carrega .env manualmente
from dotenv import load_dotenv
load_dotenv()

RESULTS = []
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:12]
    line = f"[{ts}] {msg}"
    print(line)
    RESULTS.append(line)

def main():
    log("=" * 60)
    log("TESTE DE CONEXAO REDIS - RENDER")
    log("=" * 60)

    # 1. Verificar variaveis de ambiente
    redis_url = os.environ.get("REDIS_URL", "")
    cache_redis_url = os.environ.get("CACHE_REDIS_URL", "")
    cache_type = os.environ.get("CACHE_TYPE", "")

    log(f"REDIS_URL:        {'SET' if redis_url else 'NOT SET'}")
    log(f"CACHE_REDIS_URL:  {'SET' if cache_redis_url else 'NOT SET'}")
    log(f"CACHE_TYPE:       {cache_type}")

    if not cache_redis_url:
        log("ERRO: CACHE_REDIS_URL nao configurada no .env")
        sys.exit(1)

    # 2. Parse URL
    parsed = urlparse(cache_redis_url)
    host = parsed.hostname
    port = parsed.port or 6379
    scheme = parsed.scheme
    log(f"Host: {host}:{port}")
    log(f"Scheme: {scheme} (SSL={'yes' if 'rediss' in scheme else 'no'})")
    log(f"Password set: {'yes' if parsed.password else 'no'}")

    # 3. Teste TCP basico
    log("\n--- TCP Connect Test ---")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect((host, port))
        log(f"TCP OK - conectou a {host}:{port}")
    except Exception as e:
        log(f"TCP FAIL - {type(e).__name__}: {e}")
        sys.exit(1)
    finally:
        s.close()

    # 4. Teste Redis com SSL
    log("\n--- Redis PING/GET/SET Test ---")
    from redis import Redis
    try:
        r = Redis.from_url(
            cache_redis_url,
            socket_connect_timeout=10,
            socket_timeout=10,
            ssl_cert_reqs=None,  # Render usa SSL auto-assinado
            decode_responses=True,
        )
        pong = r.ping()
        log(f"PING: {pong}")

        info = r.info()
        log(f"Redis version: {info.get('redis_version', 'unknown')}")
        log(f"Redis mode: {info.get('redis_mode', 'unknown')}")
        log(f"Connected clients: {info.get('connected_clients', 'unknown')}")
        log(f"Used memory: {info.get('used_memory_human', 'unknown')}")

        # SET/GET/DEL
        r.set("test:anime-hunter-diag", "funcionando!", ex=300)
        val = r.get("test:anime-hunter-diag")
        log(f"SET/GET test: {val}")
        r.delete("test:anime-hunter-diag")

        log("\n[OK] SUCESSO: Redis do Render esta ONLINE e FUNCIONANDO!")
        log("O cache do flask_caching com Redis esta pronto para uso.")
    except Exception as e:
        log(f"\n[FAIL] ERRO: {type(e).__name__}: {e}")
        log("\nPossiveis causas:")
        log("  - URL com usuario/senha invalidos")
        log("  - IP nao autorizado no Redis do Render")
        log("  - Firewall bloqueando conexao")
        log("  - Certificado SSL")
        sys.exit(1)

    log("\n" + "=" * 60)

if __name__ == "__main__":
    main()