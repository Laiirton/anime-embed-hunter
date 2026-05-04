# Anime Embed Hunter API

API modular para extração de links de embed de animes com Flask + Playwright.

## Funcionalidades

- Scraping modular via `configs.json`
- Cache de respostas com TTL no banco (`embed_requests`)
- Limpeza periódica de cache expirado em lote
- Cache de busca em memória (`Flask-Caching`)
- Segurança por API Key + Rate Limit por `API_KEY + IP`
- Persistência compatível com SQLite local e Postgres/Supabase
- Migrações versionadas com Alembic/Flask-Migrate

## Stack

- Python 3.10+
- Flask / Flask-SQLAlchemy
- Flask-Migrate (Alembic)
- Playwright
- Flask-Caching
- Flask-Limiter

## Setup

```bash
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
playwright install chromium
```

Copie `.env.example` para `.env` e configure pelo menos:

- `SECRET_KEY`
- `API_KEY`
- `DATABASE_URL` ou `SUPABASE_DB_URL` (opcional, se vazio usa SQLite local)

## Banco e Migrações

A aplicação não executa mais `create_all()` automaticamente em runtime.

```bash
flask --app run.py db upgrade
```

Para criar nova migration:

```bash
flask --app run.py db revision -m "descricao"
```

## Executar API

```bash
python run.py
```

## Endpoints

### `GET /search`

Busca no catálogo local/remoto (`animes.name`) com cache curto.

Parâmetros:
- `q` obrigatório

### `GET /get-embed`

Retorna embeds para URL alvo.

Parâmetros:
- `url` obrigatório
- `force` opcional (`true|false`) para ignorar cache e forçar novo scrape

### `POST /reload-config`

Recarrega `configs.json` e limpa cache de busca.

### `POST /maintenance/cleanup-cache`

Executa limpeza manual de cache expirado (`embed_requests.expires_at`).

## Scripts

- `python scripts/cataloguer.py`
- `python scripts/check_db.py`
- `python scripts/clean_db.py`
- `python scripts/cleanup_expired_cache.py`

Os scripts respeitam `DATABASE_URL` / `SUPABASE_DB_URL`; se não existir, usam SQLite local.
O `cataloguer.py` tem retry/backoff para `429/5xx` e deduplica animes por URL.

## Supabase (Postgres)

Exemplo de `DATABASE_URL`:

```text
postgresql://postgres:<senha>@<host>.supabase.co:5432/postgres
```

O projeto normaliza automaticamente `postgres://` e `postgresql://` para `postgresql+psycopg://`.

## Testes

```bash
pytest -q
```

CI em GitHub Actions: `.github/workflows/ci.yml`.
