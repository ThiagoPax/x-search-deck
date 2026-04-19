# X Search Deck

Painel de buscas do X com múltiplas colunas, coleta via Playwright autenticado, atualização em tempo real por WebSocket e alertas editoriais por e-mail.

## Arquitetura

```
x-search-deck/
├── server.py              Servidor aiohttp, WebSocket e coleta Playwright
├── openai_service.py      Integração backend com OpenAI Responses API
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
| `MAX_TWEETS` | `100` | Máximo de tweets coletados por coluna |
| `MAX_SCROLLS` | `12` | Número máximo de rolagens por coleta |
| `SCROLL_WAIT` | `1.1` | Pausa em segundos entre rolagens da busca |
| `PAGE_WAIT` | `7` | Tempo de espera após abrir a busca |
| `DATA_DIR` | `.data` | Diretório de persistência local |
| `ALERT_CONFIG_PATH` | `.data/alert_config.json` | Arquivo de configuração dos alertas |
| `ALERT_STATE_PATH` | `.data/alert_state.json` | Estado leve de alertas já enviados |
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
O estado operacional de envios únicos por janela, como alerta de silêncio e digest final, é salvo em `ALERT_STATE_PATH`.

OpenAI para recursos editoriais sob demanda:

| Variável | Padrão | Descrição |
|---|---:|---|
| `OPENAI_API_KEY` | vazio | Chave usada somente pelo backend |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Modelo usado na Responses API; precisa estar disponível para a chave configurada |

Sem `OPENAI_API_KEY`, a aplicação continua funcionando; apenas o resumo IA retorna aviso de configuração. Se `OPENAI_MODEL` estiver vazio, inválido ou sem acesso, o endpoint retorna uma mensagem específica para ajuste do ambiente.

## Uso

- Edite a query diretamente em cada coluna.
- Use o seletor `Recentes`/`Top` para mudar o modo da busca.
  - `Recentes` usa o modo `f=live` do próprio X.
  - `Top` usa o modo `f=top` do próprio X; o deck não reordena localmente por engajamento.
- Use os campos de data por coluna para injetar `since:` e `until:` automaticamente.
- Marque `sem RT` para adicionar `-filter:retweets` à busca daquela coluna.
- Use `likes`, `replies`, `RTs`, `mídia` e `verificado` para adicionar `min_faves:`, `min_replies:`, `min_retweets:`, `filter:media` e `filter:verified`.
- Clique no nome da coluna para renomear; nomes, queries, ordenação, filtros e número de colunas persistem no navegador.
- Use o seletor `Templates salvos...` em cada coluna para aplicar, salvar ou excluir templates de query persistidos no navegador.
- Arraste o handle `⋮⋮` no cabeçalho para reorganizar colunas preservando o estado salvo de cada uma.
- O botão `Ao vivo` mantém as colunas inscritas no auto-refresh do backend. Ao pausar, as subscriptions são removidas até uma nova busca.
- O badge azul na coluna mostra quantos tweets novos chegaram desde o último refresh visualizado.
- O texto do tweet é exibido completo, sem truncamento visual.
- Termos relevantes da query são destacados no texto quando aparecem no tweet.
- Quando o X expõe fotos, GIF thumbs ou thumbs de vídeo no DOM, o card tenta exibir mídia inline.

## Fase 3 Editorial

### Coleta ampliada

Cada coluna tenta coletar até `MAX_TWEETS` resultados, com padrão de 100. Em `Recentes`, a busca usa `f=live`; em `Top`, usa `f=top`. O backend rola a página até atingir o limite, parar de receber tweets novos ou bater `MAX_SCROLLS`.

Limitações:

- o X pode não renderizar 100 itens em todas as queries, sessões ou momentos. Nesses casos, o sistema entrega o máximo deduplicado que apareceu no DOM sem travar a aba;
- `Recentes` e `Top` dependem da ordenação entregue pelo X. O deck não aplica uma segunda ordenação local por data ou engajamento;
- quando uma coleta de uma coluna já populada volta vazia de forma transitória, o backend preserva os últimos tweets daquela coluna e marca o status como erro para evitar regressão visual para zero.

### Resumo IA da coluna

O botão `IA` em cada coluna envia até os 20 primeiros tweets carregados daquela coluna para o backend, que chama a OpenAI Responses API. Esse limite é aplicado também no backend para manter a chamada barata, rápida e mais robusta. A resposta é em português, curta e voltada para redação: principais assuntos, sinais de pauta/controvérsia e o que monitorar.

A chamada é manual para evitar custo em todo refresh. A chave nunca vai para o frontend.

Sem `OPENAI_API_KEY`, o backend retorna uma mensagem clara de configuração ausente e o restante do deck continua funcionando. O endpoint também diferencia modelo inválido/sem acesso, rate limit, timeout, erro temporário da OpenAI e resposta sem texto utilizável. Esses erros aparecem no box do resumo sem alterar o restante da interface.

## Fase 2 Restaurada na Hotfix

Esta hotfix restaurou recursos previstos/entregues na Fase 2 que haviam regredido após a Fase 3:

- templates de query salvos em `localStorage`, com aplicar/salvar/excluir por coluna;
- destaque local de keywords da query no texto do tweet;
- mídia inline para fotos, GIF thumbnails e thumbnails de vídeo quando esses elementos aparecem no DOM renderizado pelo X;
- modo ao vivo explícito no topo, usando as subscriptions WebSocket e o auto-refresh do backend;
- drag-and-drop de colunas por handle, preservando estado salvo;
- preservação dos alertas de spike e silêncio editorial existentes.

Limitações de mídia inline: o deck não baixa mídia diretamente pela API do X. Ele apenas reaproveita URLs de imagens/thumbs que o X renderiza no HTML coletado pelo Playwright. Tweets com vídeo sem thumbnail disponível, cards externos ou mídia bloqueada pela sessão podem aparecer sem mídia.

Observação sobre modo ao vivo: o modo ao vivo depende de WebSocket conectado, cookies válidos do X e ao menos uma coluna com query. Pausar o modo ao vivo limpa as subscriptions do backend para evitar coletas sobrepostas.

### Detector de viralização nascente

Cada tweet recebe uma análise heurística local. O selo `Viralizando` aparece quando há tração recente ou desproporcional, combinando replies, retweets, likes e idade do tweet quando o timestamp está disponível.

Esse detector não usa IA automaticamente. Ele é um sinal editorial, não uma previsão estatística.

### Clip para pauta e fila editorial

O botão `Clip` em cada tweet salva o item na fila editorial persistida em `localStorage`. A fila abre pelo botão `Fila` no topo, permite anotação curta por item, remoção e limpeza.

As anotações ficam apenas no navegador em uso.

### Exportar para WhatsApp

Cada tweet ou item da fila pode ser copiado em formato pronto para WhatsApp. A fila inteira também pode ser copiada em lote pelo botão `Copiar WhatsApp`.

O texto inclui fonte, anotação de pauta quando houver, conteúdo, métricas e link.

### Score de credibilidade da fonte

O selo `Fonte N/100` usa heurística explicável: verificação quando detectada na coleta, sinais de veículo/clube/entidade/jornalista no nome ou handle e alcance observado. O tooltip mostra os motivos.

O score não atesta veracidade; ele só ajuda a priorizar checagem editorial.

## Alertas por E-mail

Abra `Alertas` no topo da interface para editar:

- destinatários;
- janelas de envio;
- frequência em minutos;
- threshold de engajamento;
- regra de spike;
- antecedência do preview antes do programa;
- alerta de silêncio;
- digest final ao fim da janela;
- URL pública do deck.

Configuração padrão:

| Programa | Dias | Janela |
|---|---|---|
| Gazeta Esportiva | Segunda a sexta | 17:30-19:00 |
| Mesa Redonda | Domingo | 20:30-23:00 |

Durante uma janela ativa, o sistema envia um digest a cada N minutos com até 5 tweets de maior engajamento por coluna ativa. O engajamento é calculado como replies + retweets + likes e precisa superar o threshold configurado.

O preview automático é enviado antes do começo da janela, conforme a antecedência configurada. O botão `Enviar preview` permite testar manualmente com os tweets já coletados.

### Alerta de silêncio

Quando ativado, o alerta de silêncio monitora cada janela ativa e envia no máximo um e-mail por janela se nenhum tweet novo acima do threshold configurado aparecer pelo intervalo definido em minutos.

O silêncio é contado a partir do início da janela ou do último tweet relevante novo visto naquela janela. Tweets repetidos em refreshes posteriores não reiniciam o contador. O alerta usa os mesmos destinatários, SMTP, threshold e URL do deck da configuração principal.

### Digest final

Quando ativado, o digest final é enviado automaticamente após o fim de uma janela observada pelo backend. Ele usa os principais tweets relevantes vistos durante aquela janela, com até 5 tweets por coluna, ordenados por engajamento.

Para evitar reenvio, cada digest final é marcado em `ALERT_STATE_PATH` com a data e o ID da janela. Se não houver tweets acima do threshold durante a janela, o digest final ainda pode ser enviado com a indicação de ausência de tweets acima do threshold.

Limitações conhecidas:

- as janelas atuais usam horários no mesmo dia; janelas que atravessam meia-noite não são tratadas como um único bloco;
- alertas agendados dependem do loop de refresh estar ativo com ao menos uma coluna inscrita;
- após reinício do servidor, o sistema preserva alertas finais e de silêncio já enviados, mas não reconstrói tweets vistos em uma janela antes do reinício.

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
Para resumo IA, configure também `OPENAI_API_KEY` e, opcionalmente, `OPENAI_MODEL`. O modelo precisa ser compatível com a Responses API e estar liberado para a chave usada.

## Roadmap Técnico

Implementado nesta rodada:

- hotfix de estabilização pós-Fase 3:
  - refresh global coalescido para evitar subscriptions duplicadas e coletas sobrepostas;
  - proteção contra coleta vazia transitória sobrescrever coluna já populada;
  - restauração de templates, mídia inline, destaque de keywords, modo ao vivo e drag-and-drop;
  - mensagens de erro mais claras para resumo IA;
  - filtros avançados reorganizados para legibilidade;
- coleta ampliada com rolagem segura até 100 tweets por coluna;
- filtros avançados por coluna: likes, replies, retweets, mídia e verificados;
- resumo IA sob demanda por coluna via backend e Responses API;
- detector heurístico de viralização nascente;
- clip para pauta com fila editorial persistente no navegador;
- exportação de tweet ou fila para texto de WhatsApp;
- score simples e explicável de credibilidade da fonte;
- alertas por e-mail com configuração editável e persistente;
- janelas, destinatários, frequência, threshold, spike e preview configuráveis;
- alerta de silêncio e digest final configuráveis;
- filtros de data por coluna;
- opção por coluna para excluir retweets;
- persistência leve de layout/estado no navegador;
- contador de novos tweets por coluna;
- tweet completo sem truncamento visual;
- `.gitignore` para arquivos locais e caches.

Pendências maiores do planejamento:

- adicionar/remover colunas sem limite fixo;
- largura ajustável;
- histórico cronológico de queries além dos templates salvos;
- cards de link externos completos;
- integrações externas além de cópia para WhatsApp.
