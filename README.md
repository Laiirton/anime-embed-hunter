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

### `GET /get-embed`

Retorna os links de embed para uma URL fornecida.

**Parâmetros:**
- `url` (obrigatório): URL do anime, episódio ou página inicial.
- `force` (opcional): Se `true`, ignora o cache e força um novo scrape.

**Headers:**
- `X-API-KEY`: Sua chave de API configurada no `.env`.

**Exemplo:**
```bash
curl -H "X-API-KEY: 123" "http://localhost:5000/get-embed?url=https://site.com/anime/one-piece"
```

### `POST /reload-config`

Recarrega o arquivo `configs.json` sem reiniciar o servidor.

## 📁 Estrutura do Projeto

```text
app/
├── core/       # Configurações e segurança
├── models/     # Modelos de banco de dados
├── services/   # Lógica de scraping e utilitários
└── api/        # Definição das rotas
```

---
Desenvolvido com ❤️ por Antigravity.
