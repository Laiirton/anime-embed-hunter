# Usar a imagem oficial do Playwright com Python pré-instalado e todas as dependências do sistema
FROM mcr.microsoft.com/playwright/python:v1.51.0-jammy

# Definir diretório de trabalho
WORKDIR /app

# Copiar apenas o requirements primeiro para aproveitar o cache do Docker
COPY requirements.txt .

# Instalar dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Instalar o browser Chromium (o Playwright já vem instalado na imagem base, mas precisamos garantir o binário)
RUN playwright install chromium

# Copiar o restante do código
COPY . .

# Definir variáveis de ambiente padrão
ENV FLASK_APP=wsgi.py
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Comando para rodar migrações e iniciar o servidor
# O Render passa a variável $PORT automaticamente
CMD flask db upgrade && gunicorn wsgi:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT
