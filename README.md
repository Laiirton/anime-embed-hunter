# Anime Embed Hunter API 🚀

Uma API profissional e modular para extração de links de embed de animes utilizando Flask e Playwright.

## ✨ Funcionalidades

- **Scraping Modular**: Suporte a múltiplos sites via arquivo de configuração JSON.
- **Cache Inteligente**: Armazenamento de resultados no SQLite para respostas instantâneas.
- **Segurança**: Proteção via API Key e Rate Limiting.
- **Arquitetura Profissional**: Código organizado em camadas (API, Services, Models, Core).
- **Robusto**: Tratamento de erros e tentativas (retries) automáticas.

## 🛠️ Tecnologias

- [Python 3.10+](https://www.python.org/)
- [Flask](https://flask.palletsprojects.com/)
- [Playwright](https://playwright.dev/python/)
- [SQLAlchemy](https://www.sqlalchemy.org/)
- [Flask-Caching](https://flask-caching.readthedocs.io/)
- [Flask-Limiter](https://flask-limiter.readthedocs.io/)

## 🚀 Como Iniciar

### 1. Instalação

```bash
# Clone o repositório
git clone https://github.com/seu-usuario/anime-embed-hunter.git
cd anime-embed-hunter

# Crie um ambiente virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Instale as dependências
pip install -r requirements.txt

# Instale os navegadores do Playwright
playwright install chromium
```

### 2. Configuração

1. Copie o arquivo `.env.example` para `.env`:
   ```bash
   cp .env.example .env
   ```
2. Edite o `.env` com suas chaves e configurações.

### 3. Execução

```bash
python run.py
```

## 🔌 Endpoints

### `GET /search`

Busca animes no catálogo local por nome.

**Parâmetros:**
- `q` (obrigatório): Nome ou parte do nome do anime.

**Exemplo:**
```bash
curl -H "X-API-KEY: 123" "http://localhost:5000/search?q=hack"
```

### `GET /get-embed`

Retorna os links de embed para uma URL fornecida (Suporta Home, Séries e Episódios).
Possui sistema de **Cache On-Demand** de 24 horas.

**Parâmetros:**
- `url` (obrigatório): URL do site alvo.
- `force` (opcional): Se `true`, ignora o cache e força um novo scrape.

---

## 🛠️ Scripts de Automação

Localizados na pasta `/scripts`:

- **`cataloguer.py`**: Indexa o site alvo inteiro e salva os animes no banco de dados.
- **`check_db.py`**: Mostra estatísticas e dados atuais do banco de dados.
- **`clean_db.py`**: Limpa e sanitiza todos os nomes no banco de dados.

Uso:
```bash
python scripts/cataloguer.py
```

## 📁 Estrutura do Projeto

```text
app/
├── api/        # Rotas e controladores
├── core/       # Configurações globais
├── models/     # Modelos de banco de dados
├── services/   # Lógica do Scraper
└── utils/      # Funções utilitárias (Limpeza, etc)
scripts/        # Automação e manutenção
data/           # Exportações de arquivos
instance/       # Banco de dados SQLite
```

---
Desenvolvido com ❤️ por Antigravity.
