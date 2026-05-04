# Endpoints da API

## Autenticacao

Todos os endpoints exigem:

- Header `X-API-KEY: <sua_api_key>`

Para favoritos/historico por usuario:

- Header `X-USER-ID: <id_do_usuario>` (recomendado)

---

## 1) `GET /catalog`

O que faz:

- Lista animes do banco com paginacao e filtros.

Query params:

- `page` ou `pagina` (opcional, default `1`)
- `limit` (opcional, default config)
- `search` (opcional)
- `filter_letter` (opcional, ex: `a`)
- `filter_audio` (opcional: `dublado` ou `legendado`)
- `order` ou `filter_order` (opcional: `name`, `name_desc`, `recent`)

---

## 2) `GET /catalog/search`

O que faz:

- Busca animes por nome no banco.

Query params:

- `q` (obrigatorio)
- `page` (opcional)
- `limit` (opcional)

---

## 3) `GET /anime/:slug`

O que faz:

- Retorna detalhes do anime + episodios paginados.

Exemplo de slug:

- `a/one-piece`

Query params:

- `page` (opcional)
- `limit` (opcional)

---

## 4) `GET /home/featured`

O que faz:

- Raspa os destaques da home do site e retorna os itens.

Query params:

- `force` (opcional, `true` para ignorar cache)
- `url` (opcional, default `https://animesdigital.org/home`)

---

## 5) `GET /episode/:id/players`

O que faz:

- Raspa os players disponiveis de um episodio e retorna os embeds.

Path param:

- `id` (obrigatorio, numerico)

Query params:

- `prefix` (opcional, default `a`)

---

## 6) `GET /lancamentos`

O que faz:

- Lista episodios mais recentes (`last_updated` desc).

Query params:

- `page` (opcional)
- `limit` (opcional)

---

## 7) `GET /favorites`

O que faz:

- Lista favoritos do usuario (`X-USER-ID`).

Query params:

- `page` (opcional)
- `limit` (opcional)
- `user_id` (opcional se nao usar `X-USER-ID`)

---

## 8) `POST /favorites`

O que faz:

- Cria/atualiza favorito do usuario.

Body JSON:

- `url` (obrigatorio)
- `name` (obrigatorio se anime nao estiver no banco)
- `image_url` (opcional)
- `user_id` (opcional se nao usar `X-USER-ID`)

---

## 9) `GET /history`

O que faz:

- Lista historico do usuario (`X-USER-ID`).

Query params:

- `page` (opcional)
- `limit` (opcional)
- `user_id` (opcional se nao usar `X-USER-ID`)

---

## 10) `POST /history`

O que faz:

- Cria/atualiza item de historico e incrementa `watch_count` se ja existir.

Body JSON:

- `url` (obrigatorio)
- `title` (obrigatorio se item nao estiver no banco)
- `anime_url` (opcional)
- `image_url` (opcional)
- `user_id` (opcional se nao usar `X-USER-ID`)

---

## 11) `GET /search`

O que faz:

- Busca rapida por nome no catalogo local com cache curto.

Query params:

- `q` (obrigatorio)

---

## 12) `GET /get-embed`

O que faz:

- Extrai embed(s) baseado na URL enviada (home, anime, diretorio ou episodio).

Query params:

- `url` (obrigatorio)
- `force` (opcional, `true` para ignorar cache)

---

## 13) `POST /reload-config`

O que faz:

- Recarrega `configs.json` sem reiniciar a API.

Body:

- Sem body obrigatorio.

---

## 14) `POST /maintenance/cleanup-cache`

O que faz:

- Remove cache expirado da tabela `embed_requests`.

Body:

- Sem body obrigatorio.
