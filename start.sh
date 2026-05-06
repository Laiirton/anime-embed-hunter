#!/bin/bash

# Inicia o worker em segundo plano
# --url é opcional se a variável REDIS_URL estiver definida no ambiente
rq worker scraper-queue &

# Inicia o Gunicorn (serviço web) em primeiro plano
# O Gunicorn PRECISA ser o último comando para que o Render saiba que o serviço está rodando
gunicorn -b 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 120 run:app
