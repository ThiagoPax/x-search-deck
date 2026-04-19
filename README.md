# X Search Deck

Painel de buscas do X com múltiplas colunas, coleta via Playwright autenticado, atualização em tempo real por WebSocket e alertas editoriais por e-mail.

## Arquitetura

```
x-search-deck/
├── server.py              Servidor aiohttp, WebSocket e coleta Playwright
├── email_alerts.py        Configuração e envio dos alertas por e-mail
├── interface.html         Interface web das colunas
├── exportar_cookies.js    Script para exportar cookies do X
├── requirements.txt       Dependências Python
├── render.yaml            Configuração do Render
├── Dockerfile             Imagem de deploy
└── docs/                  Planejamento e documentos do projeto
```

O servidor mantém uma única aba Chromium para reduzir consumo de recursos. A interface envia as colunas ativas por WebSocket, o backend monta a URL de busca do X, coleta os tweets e devolve os resultados para todos os clientes conectados.

## Setup Local

1. Instale dependências:

```bash
pip install -r requirements.txt
playwright install chromium
```

2. Exporte seus cookies do X:

- Abra o Chrome em `https://x.com` logado.
- Abra o Console do navegador.
- Cole o conteúdo de `exportar_cookies.js`.
- Guarde o JSON copiado.

3. Rode o servidor:

```bash
export X_COOKIES_JSON='[...]'
python server.py
```

4. Acesse:

```text
http://localhost:8765
```

## Variáveis de Ambiente

Obrigatórias para coleta:

| Variável | Descrição |
|---|---|
| `X_COOKIES_JSON` | JSON dos cookies autenticados do X |

Opcionais de operação:

| Variável | Padrão | Descrição |
|---|---:|---|
| `PORT` | `8765` | Porta HTTP/WebSocket |
| `REFRESH_INTERVAL` | `90` | Auto-refresh global em segundos |
| `STAGGER_SECONDS` | `8` | Pausa entre colunas no refresh |
| `MAX_TWEETS` | `20` | Máximo de tweets coletados por coluna |
| `PAGE_WAIT` | `7` | Tempo de espera após abrir a busca |
| `DATA_DIR` | `.data` | Diretório de persistência local |
| `ALERT_CONFIG_PATH` | `.data/alert_config.json` | Arquivo de configuração dos alertas |
| `DECK_URL` | vazio | URL pública usada no botão "Abrir Deck" |

SMTP para alertas por e-mail:

| Variável | Descrição |
|---|---|
| `SMTP_HOST` | Host SMTP |
| `SMTP_PORT` | Porta SMTP, normalmente `587` ou `465` |
| `SMTP_USER` | Usuário/remetente |
| `SMTP_PASS` | Senha ou app password |
| `ALERT_EMAILS` | Lista inicial de destinatários separados por vírgula |

As credenciais SMTP ficam somente no ambiente. Destinatários, janelas, frequência e thresholds são editáveis na interface e persistidos em `ALERT_CONFIG_PATH`.

## Uso

- Edite a query diretamente em cada coluna.
- Use o seletor `Recentes`/`Top` para mudar o modo da busca.
- Use os campos de data por coluna para injetar `since:` e `until:` automaticamente.
- Marque `sem RT` para adicionar `-filter:retweets` à busca daquela coluna.
- Clique no nome da coluna para renomear; nomes, queries, ordenação, filtros e número de colunas persistem no navegador.
- O badge azul na coluna mostra quantos tweets novos chegaram desde o último refresh visualizado.
- O texto do tweet é exibido completo, sem truncamento visual.

## Alertas por E-mail

Abra `Alertas` no topo da interface para editar:

- destinatários;
- janelas de envio;
- frequência em minutos;
- threshold de engajamento;
- regra de spike;
- antecedência do preview antes do programa;
- URL pública do deck.

Configuração padrão:

| Programa | Dias | Janela |
|---|---|---|
| Gazeta Esportiva | Segunda a sexta | 17:30-19:00 |
| Mesa Redonda | Domingo | 20:30-23:00 |

Durante uma janela ativa, o sistema envia um digest a cada N minutos com até 5 tweets de maior engajamento por coluna ativa. O engajamento é calculado como replies + retweets + likes e precisa superar o threshold configurado.

O preview automático é enviado antes do começo da janela, conforme a antecedência configurada. O botão `Enviar preview` permite testar manualmente com os tweets já coletados.

## Deploy no Render

Build command:

```bash
pip install -r requirements.txt && playwright install chromium --with-deps
```

Start command:

```bash
python server.py
```

Configure pelo menos `X_COOKIES_JSON`. Para alertas, configure também `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` e, opcionalmente, `ALERT_EMAILS` e `DECK_URL`.

## Roadmap Técnico

Implementado nesta rodada:

- alertas por e-mail com configuração editável e persistente;
- janelas, destinatários, frequência, threshold, spike e preview configuráveis;
- filtros de data por coluna;
- opção por coluna para excluir retweets;
- persistência leve de layout/estado no navegador;
- contador de novos tweets por coluna;
- tweet completo sem truncamento visual;
- `.gitignore` para arquivos locais e caches.

Pendências maiores do planejamento:

- adicionar/remover colunas sem limite fixo;
- drag-and-drop e largura ajustável;
- templates e histórico de queries;
- mídia inline, cards de link e thumbnails de vídeo;
- destaque de keywords;
- alerta de silêncio e digest diário final;
- resumo/IA editorial, clip para pauta e integrações externas.
