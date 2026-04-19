// ─────────────────────────────────────────────────────────────────────────────
// X Search Deck — Exportador de Cookies
//
// COMO USAR:
// 1. Abra o Chrome e acesse https://x.com (esteja logado)
// 2. Pressione F12 para abrir o DevTools
// 3. Vá na aba "Console"
// 4. Cole TODO este script e pressione Enter
// 5. O JSON dos cookies será copiado para a área de transferência
// 6. Cole como valor da variável X_COOKIES_JSON no painel do Render
// ─────────────────────────────────────────────────────────────────────────────

(async () => {
  // Coleta todos os cookies do domínio x.com
  const cookies = await cookieStore.getAll();
  
  // Formata no padrão que o Playwright espera
  const formatted = cookies
    .filter(c => c.domain?.includes('x.com') || c.domain?.includes('twitter.com'))
    .map(c => ({
      name:     c.name,
      value:    c.value,
      domain:   c.domain || '.x.com',
      path:     c.path || '/',
      expires:  c.expires ? Math.floor(new Date(c.expires).getTime() / 1000) : -1,
      httpOnly: false,
      secure:   c.secure || true,
      sameSite: 'None',
    }));

  const json = JSON.stringify(formatted);
  
  // Copia para clipboard
  try {
    await navigator.clipboard.writeText(json);
    console.log('%c✅ Cookies copiados para a área de transferência!', 'color: #00ba7c; font-weight: bold; font-size: 14px');
    console.log(`%c${formatted.length} cookies exportados`, 'color: #71767b');
    console.log('%cAgora cole como X_COOKIES_JSON no Render.', 'color: #1d9bf0');
  } catch(e) {
    // Fallback: mostra no console para copiar manualmente
    console.log('%c✅ Copie o JSON abaixo:', 'color: #00ba7c; font-weight: bold');
    console.log(json);
  }
  
  // Verifica se tem o cookie de autenticação
  const hasAuth = formatted.some(c => c.name === 'auth_token');
  if (!hasAuth) {
    console.warn('%c⚠️  Cookie auth_token não encontrado. Você está logado no X?', 'color: #ffd400');
  } else {
    console.log('%c✅ auth_token encontrado — autenticação OK', 'color: #00ba7c');
  }
})();
