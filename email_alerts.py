"""
email_alerts.py — Módulo de alertas por e-mail para X Search Deck Pro
Baseado nos rascunhos gerados externamente (rascunho_smtp.py, rascunho_scheduler.py)

Variáveis de ambiente:
    SMTP_HOST       — servidor SMTP (ex: smtp.gmail.com)
    SMTP_PORT       — porta (padrão: 587)
    SMTP_USER       — e-mail remetente
    SMTP_PASS       — senha ou app-password
    ALERT_EMAILS    — destinatários separados por vírgula
    ALERT_WINDOWS   — janelas de envio (padrão seg-sex 17:30-19:00, dom 20:30-23:00)
                      Formato: "0,1,2,3,4 17:30-19:00;6 20:30-23:00"
    ALERT_TZ        — fuso horário (padrão: America/Sao_Paulo)
    ALERT_INTERVAL  — minutos entre envios periódicos (padrão: 15)
    ALERT_MIN_REPLIES       — threshold de replies para incluir tweet no digest (padrão: 200)
    ALERT_SPIKE_REPLIES     — threshold para alerta imediato (padrão: 500)
    ALERT_SPIKE_MINUTES     — janela em minutos para detecção de spike (padrão: 10)
    ALERT_TOP_N             — top N tweets por coluna no digest (padrão: 5)
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

# ─── Configurações via env ────────────────────────────────────────────────────

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


SMTP_HOST       = os.environ.get("SMTP_HOST", "")
SMTP_PORT       = _env_int("SMTP_PORT", 587)
SMTP_USER       = os.environ.get("SMTP_USER", "")
SMTP_PASS       = os.environ.get("SMTP_PASS", "")
ALERT_EMAILS    = [e.strip() for e in os.environ.get("ALERT_EMAILS", "").split(",") if e.strip()]
ALERT_TZ        = os.environ.get("ALERT_TZ", "America/Sao_Paulo")
ALERT_INTERVAL  = _env_int("ALERT_INTERVAL", 15)           # minutos entre digests
MIN_REPLIES     = _env_int("ALERT_MIN_REPLIES", 200)
SPIKE_REPLIES   = _env_int("ALERT_SPIKE_REPLIES", 500)
SPIKE_MINUTES   = _env_int("ALERT_SPIKE_MINUTES", 10)
TOP_N           = _env_int("ALERT_TOP_N", 5)


# ─── Janelas de horário ───────────────────────────────────────────────────────

def _parse_windows(raw: str) -> list[tuple[list[int], int, int, int, int]]:
    """
    Parseia janelas de envio.
    Formato por entrada (separadas por ';'):  "DIAS HH:MM-HH:MM"
    DIAS = inteiros 0-6 separados por vírgula (seg=0, dom=6)
    Exemplo: "0,1,2,3,4 17:30-19:00;6 20:30-23:00"
    """
    windows: list[tuple[list[int], int, int, int, int]] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        try:
            days_part, time_part = entry.split(" ", 1)
            days = [int(d) for d in days_part.split(",")]
            start_str, end_str = time_part.split("-")
            sh, sm = map(int, start_str.split(":"))
            eh, em = map(int, end_str.split(":"))
            windows.append((days, sh, sm, eh, em))
        except Exception:
            pass
    return windows


def _default_windows() -> list[tuple[list[int], int, int, int, int]]:
    return [
        ([0, 1, 2, 3, 4], 17, 30, 19, 0),  # seg-sex 17:30-19:00
        ([6],              20, 30, 23, 0),  # dom 20:30-23:00
    ]


def _is_within_send_window() -> bool:
    tz  = ZoneInfo(ALERT_TZ)
    now = datetime.now(tz)
    hm  = now.hour * 60 + now.minute
    wd  = now.weekday()

    raw = os.environ.get("ALERT_WINDOWS", "")
    windows = _parse_windows(raw) if raw else _default_windows()

    for days, sh, sm, eh, em in windows:
        if wd not in days:
            continue
        if sh * 60 + sm <= hm < eh * 60 + em:
            return True
    return False


# ─── SMTP ─────────────────────────────────────────────────────────────────────

def _configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS and ALERT_EMAILS)


def _send_email(subject: str, body_html: str) -> bool:
    """Envia e-mail via SMTP. Nunca levanta exceção — apenas retorna False."""
    if not _configured():
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ", ".join(ALERT_EMAILS)
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_USER, ALERT_EMAILS, msg.as_string())
        log.info(f"[alerts] E-mail enviado: {subject}")
        return True
    except Exception as exc:
        log.error(f"[alerts] Falha SMTP: {exc}")
        return False


# ─── Formatação HTML ──────────────────────────────────────────────────────────

def _tweet_html(t: dict) -> str:
    text = (t.get("text") or "").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    handle = t.get("author_handle") or ""
    name   = t.get("author_name") or ""
    url    = t.get("url") or "#"
    replies = t.get("replies", "0")
    likes   = t.get("likes", "0")
    return f"""
<div style="border:1px solid #2f3336;border-radius:12px;padding:12px 16px;margin-bottom:10px;background:#16181c;">
  <p style="margin:0 0 4px;font-size:13px;color:#e7e9ea;">
    <strong>{name}</strong>
    <span style="color:#71767b;font-size:11px;"> {handle}</span>
  </p>
  <p style="margin:0 0 8px;font-size:13px;color:#e7e9ea;line-height:1.5;">{text}</p>
  <p style="margin:0;font-size:11px;color:#71767b;">
    💬 {replies} &nbsp; ❤️ {likes}
    &nbsp; <a href="{url}" style="color:#1d9bf0;">Ver tweet</a>
  </p>
</div>"""


def _build_digest_html(col_summaries: list[dict]) -> str:
    tz  = ZoneInfo(ALERT_TZ)
    now = datetime.now(tz).strftime("%d/%m/%Y %H:%M")
    blocks = ""
    for cs in col_summaries:
        name   = cs.get("name", f"Coluna {cs['col_id']+1}")
        tweets = cs.get("tweets", [])
        if not tweets:
            continue
        rows = "".join(_tweet_html(t) for t in tweets)
        blocks += f"""
<h3 style="color:#1d9bf0;margin:20px 0 8px;font-size:14px;">{name}</h3>
{rows}"""
    return f"""
<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="background:#000;color:#e7e9ea;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;max-width:640px;margin:auto;">
  <h2 style="font-size:18px;margin-bottom:4px;">𝕏 Search Deck — Digest</h2>
  <p style="color:#71767b;font-size:12px;margin-bottom:16px;">{now} (Brasília)</p>
  {blocks}
</body></html>"""


def _build_spike_html(col_name: str, tweet: dict) -> str:
    tz  = ZoneInfo(ALERT_TZ)
    now = datetime.now(tz).strftime("%d/%m/%Y %H:%M")
    return f"""
<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="background:#000;color:#e7e9ea;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:24px;max-width:640px;margin:auto;">
  <h2 style="font-size:18px;color:#f4212e;margin-bottom:4px;">🚨 Spike detectado — {col_name}</h2>
  <p style="color:#71767b;font-size:12px;margin-bottom:16px;">{now} (Brasília)</p>
  {_tweet_html(tweet)}
</body></html>"""


# ─── Parse de número de engajamento (ex: "1.2K" → 1200) ──────────────────────

def _parse_num(s: str) -> int:
    s = (s or "0").strip().upper().replace(",", "").replace(".", "")
    if s.endswith("K"):
        try:
            return int(float(s[:-1]) * 1000)
        except ValueError:
            return 0
    if s.endswith("M"):
        try:
            return int(float(s[:-1]) * 1_000_000)
        except ValueError:
            return 0
    try:
        return int(s)
    except ValueError:
        return 0


# ─── Gestor de estado de alerta ───────────────────────────────────────────────

class AlertManager:
    """
    Recebe resultados de colunas e dispara alertas por e-mail conforme as
    janelas configuradas e os thresholds de engajamento.
    """

    def __init__(self):
        self._last_digest: Optional[datetime] = None
        # tweet_url → (timestamp_first_seen, max_replies)
        self._spike_tracker: dict[str, tuple[datetime, int]] = {}
        self._alerted_spikes: set[str] = set()
        self._lock = asyncio.Lock()

    async def process_column(self, col_id: int, col_name: str, tweets: list[dict]):
        """
        Chamado após cada refresh de coluna.
        Verifica spikes imediatos e agenda digest se necessário.
        """
        if not _configured():
            return
        async with self._lock:
            await self._check_spikes(col_id, col_name, tweets)
            await self._maybe_send_digest_single(col_id, col_name, tweets)

    async def _check_spikes(self, col_id: int, col_name: str, tweets: list[dict]):
        """Dispara alerta imediato se tweet ultrapassar SPIKE_REPLIES em ≤ SPIKE_MINUTES."""
        now = datetime.now(timezone.utc)
        for t in tweets:
            url     = t.get("url", "")
            replies = _parse_num(t.get("replies", "0"))
            if not url or replies < SPIKE_REPLIES:
                continue
            if url in self._alerted_spikes:
                continue

            first_seen, prev_replies = self._spike_tracker.get(url, (now, 0))
            self._spike_tracker[url] = (first_seen, max(prev_replies, replies))

            elapsed = (now - first_seen).total_seconds() / 60
            if elapsed <= SPIKE_MINUTES:
                log.warning(f"[alerts] 🚨 Spike! {url} — {replies} replies em {elapsed:.1f}min")
                self._alerted_spikes.add(url)
                name = col_name or f"Coluna {col_id+1}"
                subject = f"🚨 Spike X: {replies} replies — {name}"
                asyncio.get_event_loop().run_in_executor(
                    None, _send_email, subject, _build_spike_html(name, t)
                )

    async def _maybe_send_digest_single(
        self, col_id: int, col_name: str, tweets: list[dict]
    ):
        """
        Envia digest periódico (a cada ALERT_INTERVAL minutos dentro da janela).
        O digest por coluna só é enviado se houver tweets com replies ≥ MIN_REPLIES.
        """
        if not _is_within_send_window():
            return
        now = datetime.now(timezone.utc)
        if self._last_digest is not None:
            elapsed = (now - self._last_digest).total_seconds() / 60
            if elapsed < ALERT_INTERVAL:
                return

        top = sorted(
            [t for t in tweets if _parse_num(t.get("replies", "0")) >= MIN_REPLIES],
            key=lambda t: _parse_num(t.get("replies", "0")),
            reverse=True,
        )[:TOP_N]

        if not top:
            return

        self._last_digest = now
        name = col_name or f"Coluna {col_id+1}"
        col_summaries = [{"col_id": col_id, "name": name, "tweets": top}]
        subject = f"𝕏 Digest — {name} ({len(top)} tweets)"
        html = _build_digest_html(col_summaries)
        asyncio.get_event_loop().run_in_executor(None, _send_email, subject, html)

    async def send_digest_all(self, columns: dict[int, tuple[str, list[dict]]]):
        """
        Envia um digest consolidado com todas as colunas.
        Chamado externamente quando se quer forçar envio.
        """
        if not _configured() or not _is_within_send_window():
            return
        col_summaries = []
        for col_id, (col_name, tweets) in sorted(columns.items()):
            top = sorted(
                [t for t in tweets if _parse_num(t.get("replies", "0")) >= MIN_REPLIES],
                key=lambda t: _parse_num(t.get("replies", "0")),
                reverse=True,
            )[:TOP_N]
            if top:
                col_summaries.append({"col_id": col_id, "name": col_name, "tweets": top})
        if not col_summaries:
            return
        subject = f"𝕏 Digest — {len(col_summaries)} colunas"
        html = _build_digest_html(col_summaries)
        asyncio.get_event_loop().run_in_executor(None, _send_email, subject, html)
