# X Search Deck — Deploy no Render

Painel de buscas do X acessível online, de qualquer lugar.

---

## Passo 1 — Exportar seus cookies do X

Os cookies permitem que o servidor no Render acesse o X como se fosse você.

1. Abra o Chrome e acesse **https://x.com** (certifique-se de estar logado)
2. Pressione **F12** → aba **Console**
3. Cole o conteúdo do arquivo `exportar_cookies.js` e pressione **Enter**
4. O JSON será copiado automaticamente para a área de transferência
5. Guarde esse JSON — você vai precisar no Passo 3

---

## Passo 2 — Colocar o código no GitHub

O Render puxa o código de um repositório Git.

1. Crie uma conta em **github.com** (se não tiver)
2. Clique em **New repository** → nome: `x-search-deck` → **Create**
3. Faça upload de todos os arquivos desta pasta para o repositório
   (botão **uploading an existing file** na página do repositório)

---

## Passo 3 — Criar o serviço no Render

1. Crie uma conta em **render.com**
2. Clique em **New → Web Service**
3. Conecte seu repositório GitHub (`x-search-deck`)
4. Configure:
   - **Name:** x-search-deck
   - **Region:** Ohio (US East)
   - **Branch:** main
   - **Build Command:**
     ```
     pip install -r requirements.txt && playwright install chromium --with-deps
     ```
   - **Start Command:**
     ```
     python server.py
     ```
   - **Plan:** Starter ($7/mês) — necessário para não dormir após 15min

5. Clique em **Advanced → Add Environment Variable:**
   - Key: `X_COOKIES_JSON`
   - Value: cole o JSON de cookies que você copiou no Passo 1

6. Clique em **Create Web Service**

O deploy leva ~5 minutos na primeira vez (instala o Chromium).

---

## Passo 4 — Acessar

Após o deploy, o Render vai te dar uma URL no formato:

```
https://x-search-deck.onrender.com
```

Acesse essa URL de qualquer browser, em qualquer dispositivo.

---

## Renovar cookies (quando a sessão expirar)

Os cookies do X expiram periodicamente (~30 dias). Quando parar de funcionar:

1. Repita o **Passo 1** para exportar novos cookies
2. No painel do Render → seu serviço → **Environment**
3. Edite `X_COOKIES_JSON` com o novo JSON
4. O serviço reinicia automaticamente

---

## Estrutura dos arquivos

```
x-search-deck-render/
├── server.py              Servidor Python (aiohttp + Playwright)
├── interface.html         Interface web das colunas
├── requirements.txt       Dependências Python
├── render.yaml            Configuração do Render
├── exportar_cookies.js    Script para exportar cookies do X
└── README.md              Este arquivo
```

---

## Custo estimado

| Item | Valor |
|------|-------|
| Render Starter | $7/mês |
| GitHub | Grátis |
| **Total** | **~R$ 40/mês** |
