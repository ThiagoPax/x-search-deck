/*
 * rascunho_interface.js — Trechos JS/CSS para interface.html
 * Gerado externamente; adaptar ao projeto X Search Deck
 *
 * Contém:
 *   1. localStorage — persistência de queries, nomes de colunas e sort
 *   2. Nomes editáveis por coluna (campo input acima da textarea)
 *   3. Badge de contador de novos tweets por coluna
 */

/* ─── 1. PERSISTÊNCIA COM localStorage ────────────────────────────────── */

/** Salva o estado atual de todas as colunas */
function saveColumnsState(numCols) {
  const state = [];
  for (let i = 0; i < numCols; i++) {
    state.push({
      query: document.getElementById(`q${i}`)?.value ?? '',
      sort:  document.getElementById(`sort${i}`)?.value ?? 'live',
      name:  document.getElementById(`colname${i}`)?.value ?? '',
    });
  }
  try {
    localStorage.setItem('xdeck_columns', JSON.stringify(state));
    localStorage.setItem('xdeck_numcols', String(numCols));
  } catch (_) {}
}

/** Restaura o estado salvo; retorna { numCols, columns } ou null */
function loadColumnsState() {
  try {
    const raw = localStorage.getItem('xdeck_columns');
    const nc  = localStorage.getItem('xdeck_numcols');
    if (\!raw) return null;
    return {
      numCols: nc ? parseInt(nc, 10) : null,
      columns: JSON.parse(raw),
    };
  } catch (_) {
    return null;
  }
}

/** Aplica o estado restaurado aos elementos do DOM */
function applyColumnsState(state) {
  if (\!state) return;
  state.columns.forEach((col, i) => {
    const q = document.getElementById(`q${i}`);
    const s = document.getElementById(`sort${i}`);
    const n = document.getElementById(`colname${i}`);
    if (q && col.query \!== undefined) q.value = col.query;
    if (s && col.sort  \!== undefined) s.value = col.sort;
    if (n && col.name  \!== undefined) n.value = col.name;
  });
}

/* ─── 2. CAMPO DE NOME EDITÁVEL POR COLUNA ────────────────────────────── */
/*
 * HTML a inserir ACIMA da <textarea> dentro de .ch-top:
 *
 *   <input type="text"
 *          id="colname${i}"
 *          class="col-name-input"
 *          placeholder="Nome da coluna..."
 *          value="${esc(p.name || '')}"
 *          oninput="saveColumnsState(numCols)" />
 *
 * CSS:
 */
const COL_NAME_CSS = `
.col-name-input {
  width: 100%;
  background: transparent;
  border: none;
  border-bottom: .5px solid var(--border);
  color: var(--text);
  font-size: 11px;
  font-weight: 700;
  padding: 2px 4px 4px;
  outline: none;
  margin-bottom: 4px;
  letter-spacing: .3px;
  transition: border-color .15s;
}
.col-name-input:focus { border-bottom-color: var(--blue); }
.col-name-input::placeholder { color: #333; font-weight: 400; }
`;

/* ─── 3. BADGE DE NOVOS TWEETS ────────────────────────────────────────── */
/*
 * HTML do badge (inserir dentro de .ch-bot, ao lado do .rbtn):
 *
 *   <span class="new-badge" id="badge${i}" style="display:none">0</span>
 *
 * CSS:
 */
const BADGE_CSS = `
.new-badge {
  min-width: 16px;
  height: 16px;
  background: var(--blue);
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  border-radius: 8px;
  padding: 0 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: transform .15s;
}
.new-badge:hover { transform: scale(1.15); }
`;

/**
 * Incrementa o badge de uma coluna com `count` novos tweets.
 * @param {number} colIdx  — índice da coluna
 * @param {number} count   — quantidade de novos tweets detectados
 */
function incrementBadge(colIdx, count) {
  const badge = document.getElementById(`badge${colIdx}`);
  if (\!badge || count <= 0) return;
  const current = parseInt(badge.textContent, 10) || 0;
  badge.textContent = current + count;
  badge.style.display = 'inline-flex';
}

/**
 * Limpa o badge ao clicar (chamar no onclick do badge).
 * @param {Event} e
 * @param {number} colIdx
 */
function clearBadge(e, colIdx) {
  e.stopPropagation();
  const badge = document.getElementById(`badge${colIdx}`);
  if (\!badge) return;
  badge.textContent = '0';
  badge.style.display = 'none';
}
