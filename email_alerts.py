"""
Email alert module for X Search Deck.

Resend credentials stay in environment variables. Operational alert settings
(recipients, windows, frequency and thresholds) are editable through the app
and persisted as JSON.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional

import pytz

log = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


RESEND_API_URL = "https://api.resend.com/emails"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "").strip()
RESEND_TIMEOUT = max(1, _env_int("RESEND_TIMEOUT", 20))
ALERT_EMAILS_ENV = [e.strip() for e in os.environ.get("ALERT_EMAILS", "").split(",") if e.strip()]
DATA_DIR = Path(os.environ.get("DATA_DIR", ".data"))
ALERT_CONFIG_PATH = Path(os.environ.get("ALERT_CONFIG_PATH", DATA_DIR / "alert_config.json"))
ALERT_STATE_PATH = Path(os.environ.get("ALERT_STATE_PATH", DATA_DIR / "alert_state.json"))

DEFAULT_CONFIG = {
    "enabled": True,
    "recipients": ALERT_EMAILS_ENV or ["tssouza@tssouza.com"],
    "frequency_minutes": 15,
    "engagement_threshold": 200,
    "spike_replies": 500,
    "spike_minutes": 10,
    "preview_minutes": 30,
    "silence_alert_enabled": False,
    "silence_minutes": 30,
    "final_digest_enabled": False,
    "deck_url": os.environ.get("DECK_URL", ""),
    "windows": [
        {
            "id": "gazeta-esportiva",
            "label": "Gazeta Esportiva",
            "days": [0, 1, 2, 3, 4],
            "start": "17:30",
            "end": "19:00",
            "enabled": True,
        },
        {
            "id": "mesa-redonda",
            "label": "Mesa Redonda",
            "days": [6],
            "start": "20:30",
            "end": "23:00",
            "enabled": True,
        },
    ],
}


def _merge_config(raw: dict) -> dict:
    cfg = deepcopy(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return cfg
    for key in (
        "enabled",
        "recipients",
        "frequency_minutes",
        "engagement_threshold",
        "spike_replies",
        "spike_minutes",
        "preview_minutes",
        "silence_alert_enabled",
        "silence_minutes",
        "final_digest_enabled",
        "deck_url",
        "windows",
    ):
        if key in raw:
            cfg[key] = raw[key]
    return _sanitize_config(cfg)


def _sanitize_config(cfg: dict) -> dict:
    recipients = cfg.get("recipients") or []
    if isinstance(recipients, str):
        recipients = [e.strip() for e in re.split(r"[,\n;]+", recipients) if e.strip()]
    cfg["recipients"] = [str(e).strip() for e in recipients if str(e).strip()]

    def int_between(key: str, default: int, lo: int, hi: int) -> None:
        try:
            val = int(cfg.get(key, default))
        except Exception:
            val = default
        cfg[key] = max(lo, min(hi, val))

    int_between("frequency_minutes", 15, 1, 240)
    int_between("engagement_threshold", 200, 1, 1_000_000)
    int_between("spike_replies", 500, 1, 1_000_000)
    int_between("spike_minutes", 10, 1, 240)
    int_between("preview_minutes", 30, 0, 240)
    int_between("silence_minutes", 30, 1, 240)
    cfg["enabled"] = bool(cfg.get("enabled", True))
    cfg["silence_alert_enabled"] = bool(cfg.get("silence_alert_enabled", False))
    cfg["final_digest_enabled"] = bool(cfg.get("final_digest_enabled", False))
    cfg["deck_url"] = str(cfg.get("deck_url") or "").strip()

    windows = []
    for idx, raw in enumerate(cfg.get("windows") or []):
        if not isinstance(raw, dict):
            continue
        try:
            days = sorted({int(d) for d in raw.get("days", []) if 0 <= int(d) <= 6})
        except Exception:
            days = []
        start = _valid_hhmm(raw.get("start")) or "17:30"
        end = _valid_hhmm(raw.get("end")) or "19:00"
        windows.append({
            "id": str(raw.get("id") or f"window-{idx + 1}"),
            "label": str(raw.get("label") or f"Janela {idx + 1}"),
            "days": days,
            "start": start,
            "end": end,
            "enabled": bool(raw.get("enabled", True)),
        })
    cfg["windows"] = windows or deepcopy(DEFAULT_CONFIG["windows"])
    return cfg


def _valid_hhmm(value: object) -> Optional[str]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value.strip()):
        return None
    hh, mm = map(int, value.strip().split(":"))
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return None


def parse_metric(val: object) -> int:
    if not val:
        return 0
    v = str(val).strip().upper().replace(" ", "")
    v = v.replace(",", ".")
    mult = 1
    had_suffix = False
    if v.endswith("K"):
        mult = 1_000
        v = v[:-1]
        had_suffix = True
    elif v.endswith("M"):
        mult = 1_000_000
        v = v[:-1]
        had_suffix = True
    v = re.sub(r"[^0-9.]", "", v)
    if v.count(".") > 1 or (not had_suffix and re.fullmatch(r"\d{1,3}\.\d{3}", v)):
        v = v.replace(".", "")
    try:
        return int(float(v) * mult)
    except Exception:
        return 0


def engagement_score(tweet: dict) -> int:
    return (
        parse_metric(tweet.get("replies"))
        + parse_metric(tweet.get("retweets"))
        + parse_metric(tweet.get("likes"))
    )


def _clean_recipients(recipients: list[str] | str | None) -> list[str]:
    if isinstance(recipients, str):
        recipients = re.split(r"[,\n;]+", recipients)
    return [str(e).strip() for e in (recipients or []) if str(e).strip()]


def validate_resend_config(recipients: list[str] | str | None) -> dict:
    clean = _clean_recipients(recipients)
    missing = []
    if not RESEND_API_KEY:
        missing.append("RESEND_API_KEY")
    if not RESEND_FROM_EMAIL:
        missing.append("RESEND_FROM_EMAIL")
    if not clean:
        missing.append("destinatarios")
    if missing:
        return {
            "ok": False,
            "message": "Configuracao Resend incompleta: " + ", ".join(missing),
            "recipients": clean,
        }
    return {"ok": True, "message": "Configuracao Resend valida", "recipients": clean}


def _resend_error_detail(raw_body: str) -> str:
    try:
        data = json.loads(raw_body)
    except Exception:
        data = {}
    if isinstance(data, dict):
        for key in ("message", "error"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:240]
            if isinstance(value, dict):
                nested = value.get("message")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()[:240]
    return raw_body.strip()[:240]


def _truncate_log_value(value: object, limit: int = 1200) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]"


def _resend_safe_payload_for_log(payload: dict) -> dict:
    html_body = str(payload.get("html") or "")
    return {
        "from": payload.get("from"),
        "to": payload.get("to") or [],
        "recipient_count": len(payload.get("to") or []),
        "subject": payload.get("subject"),
        "html_length": len(html_body),
        "html_omitted": True,
    }


def _resend_http_error_message(status: int, raw_body: str) -> str:
    detail = _resend_error_detail(raw_body)
    detail_lower = detail.lower()
    if status == 401:
        if detail:
            return f"Falha de autenticacao na Resend: {detail}."
        return "Falha de autenticacao na Resend. Verifique RESEND_API_KEY."
    if status == 403:
        if detail:
            return f"Resend retornou 403 Forbidden: {detail}."
        if any(term in detail_lower for term in ("domain", "sender", "from", "remetente")):
            return "Resend recusou o dominio ou remetente configurado em RESEND_FROM_EMAIL."
        return "Resend retornou 403 Forbidden sem mensagem detalhada."
    if status in (400, 422):
        if any(term in detail_lower for term in ("domain", "sender", "from", "remetente")):
            return f"Resend recusou o dominio ou remetente configurado em RESEND_FROM_EMAIL: {detail}."
        return f"Resend recusou a requisicao de envio: {detail or 'dados invalidos'}."
    if status == 429:
        return "Resend limitou temporariamente o envio. Tente novamente em instantes."
    if 500 <= status <= 599:
        return "Falha temporaria na API da Resend."
    return f"Falha HTTP ao enviar pela Resend: status {status}."


def _resend_transport_error_message(exc: Exception) -> str:
    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
        return "Timeout ao chamar a API da Resend."
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError) or isinstance(reason, socket.timeout):
            return "Timeout ao chamar a API da Resend."
        return f"Erro de rede ao chamar a API da Resend: {reason or exc.__class__.__name__}."
    if isinstance(exc, OSError):
        return f"Erro de rede ao chamar a API da Resend: {exc.strerror or exc.__class__.__name__}."
    return f"Erro inesperado ao enviar pela Resend: {exc.__class__.__name__}."


def send_alert_email_result(subject: str, body_html: str, recipients: list[str] | str | None) -> dict:
    validation = validate_resend_config(recipients)
    timestamp = datetime.now(pytz.timezone("America/Sao_Paulo")).isoformat(timespec="seconds")
    clean_recipients = validation["recipients"]
    if not validation["ok"]:
        log.warning("Alert email skipped: %s", validation["message"])
        return {
            "ok": False,
            "message": validation["message"],
            "recipients": clean_recipients,
            "timestamp": timestamp,
        }

    try:
        payload = {
            "from": RESEND_FROM_EMAIL,
            "to": clean_recipients,
            "subject": subject,
            "html": body_html,
        }
        safe_payload = _resend_safe_payload_for_log(payload)
        log.info(
            "Sending alert email via Resend: %s",
            json.dumps(
                {
                    "resend_payload": safe_payload,
                    "from_source": "RESEND_FROM_EMAIL",
                    "api_url": RESEND_API_URL,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        request = urllib.request.Request(
            RESEND_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=RESEND_TIMEOUT) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            status = getattr(response, "status", 0)
        if not 200 <= status <= 299:
            message = _resend_http_error_message(status, raw_body)
            log.error(
                "Failed to send alert email via Resend: %s %s",
                message,
                json.dumps(
                    {
                        "resend_payload": safe_payload,
                        "status": status,
                        "response_body": _truncate_log_value(raw_body),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            return {
                "ok": False,
                "message": message,
                "recipients": clean_recipients,
                "timestamp": timestamp,
            }
        try:
            response_data = json.loads(raw_body or "{}")
        except Exception:
            response_data = {}
        if not isinstance(response_data, dict) or not response_data.get("id"):
            log.error(
                "Unexpected Resend response while sending alert email: %s",
                json.dumps(
                    {
                        "resend_payload": safe_payload,
                        "status": status,
                        "response_body": _truncate_log_value(raw_body),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )
            return {
                "ok": False,
                "message": "Resposta inesperada da API da Resend.",
                "recipients": clean_recipients,
                "timestamp": timestamp,
            }
        log.info(
            "Alert email sent via Resend: %s",
            json.dumps(
                {
                    "resend_payload": safe_payload,
                    "status": status,
                    "provider_id": response_data.get("id"),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return {
            "ok": True,
            "message": "E-mail enviado com sucesso.",
            "recipients": clean_recipients,
            "timestamp": timestamp,
        }
    except urllib.error.HTTPError as e:
        raw_body = e.read().decode("utf-8", errors="replace")
        message = _resend_http_error_message(e.code, raw_body)
        log.error(
            "Failed to send alert email via Resend: %s %s",
            message,
            json.dumps(
                {
                    "resend_payload": safe_payload if "safe_payload" in locals() else {},
                    "status": e.code,
                    "response_body": _truncate_log_value(raw_body),
                    "error_type": e.__class__.__name__,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return {
            "ok": False,
            "message": message,
            "recipients": clean_recipients,
            "timestamp": timestamp,
        }
    except Exception as e:
        message = _resend_transport_error_message(e)
        log.error(
            "Failed to send alert email via Resend: %s %s",
            message,
            json.dumps(
                {
                    "resend_payload": safe_payload if "safe_payload" in locals() else {},
                    "error_type": e.__class__.__name__,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return {
            "ok": False,
            "message": message,
            "recipients": clean_recipients,
            "timestamp": timestamp,
        }


def send_alert_email(subject: str, body_html: str, recipients: list[str]) -> bool:
    return bool(send_alert_email_result(subject, body_html, recipients).get("ok"))


def _build_email_html(title: str, sections: list[dict], deck_url: str = "", intro: str = "") -> str:
    rows = ""
    for sec in sections:
        tweets = sec.get("tweets") or []
        if not tweets:
            continue
        rows += f"<h3 style='color:#1d9bf0;margin:18px 0 8px'>{html.escape(sec.get('header', 'Coluna'))}</h3>"
        for t in tweets:
            url = html.escape(t.get("url", ""))
            rows += f"""
<div style="border:1px solid #2f3336;border-radius:8px;padding:10px 14px;margin-bottom:8px;background:#16181c">
  <div style="font-weight:700;color:#e7e9ea">{html.escape(t.get('author_name',''))} <span style="color:#71767b">{html.escape(t.get('author_handle',''))}</span></div>
  <div style="color:#e7e9ea;margin:8px 0;font-size:13px;line-height:1.5;white-space:pre-wrap">{html.escape(t.get('text',''))}</div>
  <div style="color:#71767b;font-size:11px">Replies: {html.escape(str(t.get('replies','0')))} · RTs: {html.escape(str(t.get('retweets','0')))} · Likes: {html.escape(str(t.get('likes','0')))} · Score: {engagement_score(t)}</div>
  {f'<div style="margin-top:7px"><a href="{url}" style="color:#1d9bf0">Ver tweet</a></div>' if url else ''}
</div>"""
    deck_button = ""
    if deck_url:
        deck_button = f"""<p style="margin:18px 0"><a href="{html.escape(deck_url)}" style="background:#1d9bf0;color:#fff;text-decoration:none;border-radius:18px;padding:9px 16px;font-weight:700">Abrir Deck</a></p>"""
    intro_html = ""
    if intro:
        intro_html = f"""<p style="color:#e7e9ea;font-size:13px;line-height:1.5">{html.escape(intro)}</p>"""
    return f"""<html><body style="background:#000;color:#e7e9ea;font-family:Arial,sans-serif;padding:20px">
<h2 style="color:#1d9bf0">{html.escape(title)}</h2>
{deck_button}
{intro_html}
{rows or '<p style="color:#71767b">Sem tweets acima do threshold no momento.</p>'}
</body></html>"""


class AlertScheduler:
    def __init__(self):
        self.tz = pytz.timezone("America/Sao_Paulo")
        self.config = self.load_config()
        self._state = self.load_state()
        self._last_sent: Optional[datetime] = None
        self._tweet_first_seen: dict[str, datetime] = {}
        self._alerted_spikes: set[str] = set()
        self._latest_by_col: dict[int, dict] = {}
        self._sent_previews: set[str] = set()
        self._sent_silence_alerts: set[str] = set(self._state.get("sent_silence_alerts", []))
        self._sent_final_digests: set[str] = set(self._state.get("sent_final_digests", []))
        self._window_tweets: dict[str, dict[int, dict]] = {}
        self._window_seen_tweets: dict[str, set[str]] = {}
        self._window_last_relevant_at: dict[str, datetime] = {}

    def load_config(self) -> dict:
        if ALERT_CONFIG_PATH.exists():
            try:
                return _merge_config(json.loads(ALERT_CONFIG_PATH.read_text(encoding="utf-8")))
            except Exception as e:
                log.warning("Could not load alert config: %s", e)
        return _merge_config({})

    def load_state(self) -> dict:
        if ALERT_STATE_PATH.exists():
            try:
                raw = json.loads(ALERT_STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception as e:
                log.warning("Could not load alert state: %s", e)
        return {}

    def save_state(self) -> None:
        state = {
            "sent_silence_alerts": sorted(self._sent_silence_alerts)[-200:],
            "sent_final_digests": sorted(self._sent_final_digests)[-200:],
        }
        ALERT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ALERT_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def save_config(self, cfg: dict) -> dict:
        self.config = _merge_config(cfg)
        ALERT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ALERT_CONFIG_PATH.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.config

    def get_config(self) -> dict:
        return deepcopy(self.config)

    def send_test_email(self, cfg_override: Optional[dict] = None) -> dict:
        cfg = _merge_config({**self.config, **(cfg_override or {})})
        now = datetime.now(self.tz)
        deck_url = cfg.get("deck_url", "")
        intro = (
            f"Este e-mail foi disparado pelo botao de teste do X Search Deck em "
            f"{now.strftime('%d/%m/%Y %H:%M:%S')}."
        )
        if deck_url:
            intro += f" URL do deck: {deck_url}"
        html_body = _build_email_html(
            "Teste de e-mail - X Search Deck",
            [],
            deck_url,
            intro=intro,
        )
        result = send_alert_email_result(
            "[TESTE X SEARCH DECK] envio de e-mail",
            html_body,
            cfg.get("recipients") or [],
        )
        result["timestamp"] = now.isoformat(timespec="seconds")
        return result

    def is_within_window(self, now: Optional[datetime] = None) -> bool:
        return self.current_window(now) is not None

    def current_window(self, now: Optional[datetime] = None) -> Optional[dict]:
        now = now or datetime.now(self.tz)
        wd = now.weekday()
        ct = now.time()
        for w in self.config.get("windows", []):
            if not w.get("enabled", True) or wd not in w.get("days", []):
                continue
            sh, sm = map(int, w["start"].split(":"))
            eh, em = map(int, w["end"].split(":"))
            if dtime(sh, sm) <= ct <= dtime(eh, em):
                return w
        return None

    def _window_datetimes(self, window: dict, now: datetime) -> tuple[datetime, datetime]:
        sh, sm = map(int, window["start"].split(":"))
        eh, em = map(int, window["end"].split(":"))
        start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end_dt = now.replace(hour=eh, minute=em, second=0, microsecond=0)
        return start_dt, end_dt

    def _window_key(self, window: dict, now: datetime) -> str:
        return f"{now.date().isoformat()}:{window.get('id')}"

    def _tweet_key(self, tweet: dict) -> str:
        url = str(tweet.get("url") or "").strip()
        if url:
            return url
        return f"{tweet.get('author_handle', '')}:{tweet.get('text', '')}"[:280]

    def preview_due_window(self, now: Optional[datetime] = None) -> Optional[dict]:
        now = now or datetime.now(self.tz)
        preview_minutes = int(self.config.get("preview_minutes", 30))
        if preview_minutes <= 0:
            return None
        for w in self.config.get("windows", []):
            if not w.get("enabled", True) or now.weekday() not in w.get("days", []):
                continue
            sh, sm = map(int, w["start"].split(":"))
            start_dt = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
            preview_dt = start_dt - timedelta(minutes=preview_minutes)
            if preview_dt <= now < start_dt:
                key = f"{now.date().isoformat()}:{w.get('id')}"
                if key not in self._sent_previews:
                    self._sent_previews.add(key)
                    return w
        return None

    def _should_send_periodic(self) -> bool:
        if self._last_sent is None:
            return True
        now = datetime.now(self.tz)
        diff = (now - self._last_sent).total_seconds() / 60
        return diff >= int(self.config.get("frequency_minutes", 15))

    def ingest(self, col_id: int, col_label: str, tweets: list[dict]):
        """Called after each successful column refresh."""
        self._latest_by_col[col_id] = {"label": col_label, "tweets": tweets}
        if not self.config.get("enabled", True):
            return
        recipients = self.config.get("recipients") or []
        if not recipients:
            return
        now = datetime.now(self.tz)

        for t in tweets:
            url = t.get("url", "")
            if url and url not in self._tweet_first_seen:
                self._tweet_first_seen[url] = now

        self._record_window_tweets(col_id, col_label, tweets, now)
        self._send_spikes(col_label, tweets, recipients, now)

    def dispatch_scheduled(self) -> bool:
        if not self.config.get("enabled", True):
            return False
        recipients = self.config.get("recipients") or []
        if not recipients:
            return False
        now = datetime.now(self.tz)
        sent = False

        due_preview = self.preview_due_window(now)
        if due_preview:
            self.send_digest(
                title=f"Preview X Search Deck - {due_preview.get('label', 'programa')}",
                subject=f"[ALERTA X] Preview - {due_preview.get('label', 'programa')} - {now.strftime('%H:%M')}",
            )

        current = self.current_window(now)
        if current:
            sent = self._send_silence_alert_if_due(current, now) or sent

        if current and self._should_send_periodic():
            if self.send_digest(
                title=f"Top repercussao - {now.strftime('%H:%M')}",
                subject=f"[ALERTA X] Top repercussao - {now.strftime('%H:%M')}",
            ):
                self._last_sent = now
                sent = True

        sent = self._send_final_digests_if_due(now) or sent
        return sent

    def _record_window_tweets(self, col_id: int, col_label: str, tweets: list[dict], now: datetime) -> None:
        window = self.current_window(now)
        if not window:
            return
        key = self._window_key(window, now)
        start_dt, _ = self._window_datetimes(window, now)
        self._window_last_relevant_at.setdefault(key, start_dt)
        self._window_seen_tweets.setdefault(key, set())
        columns = self._window_tweets.setdefault(key, {})
        col_bucket = columns.setdefault(col_id, {"label": col_label, "tweets": {}})
        col_bucket["label"] = col_label

        threshold = int(self.config.get("engagement_threshold", 200))
        for t in tweets:
            if engagement_score(t) < threshold:
                continue
            tweet_key = self._tweet_key(t)
            if not tweet_key:
                continue
            if tweet_key not in self._window_seen_tweets[key]:
                self._window_last_relevant_at[key] = now
            self._window_seen_tweets[key].add(tweet_key)
            col_bucket["tweets"][tweet_key] = t

    def _send_silence_alert_if_due(self, window: dict, now: datetime) -> bool:
        if not self.config.get("silence_alert_enabled", False):
            return False
        key = self._window_key(window, now)
        if key in self._sent_silence_alerts:
            return False
        start_dt, _ = self._window_datetimes(window, now)
        last_relevant = self._window_last_relevant_at.get(key, start_dt)
        silence_minutes = int(self.config.get("silence_minutes", 30))
        if (now - last_relevant).total_seconds() / 60 < silence_minutes:
            return False
        title = f"Silencio editorial - {window.get('label', 'programa')}"
        intro = (
            f"Nenhum tweet novo acima do threshold de engajamento "
            f"({self.config.get('engagement_threshold')}) foi visto nos ultimos "
            f"{silence_minutes} minutos da janela ativa."
        )
        html_body = _build_email_html(title, [], self.config.get("deck_url", ""), intro=intro)
        self._sent_silence_alerts.add(key)
        self.save_state()
        asyncio.get_event_loop().run_in_executor(
            None,
            send_alert_email,
            f"[ALERTA X] Silencio - {window.get('label', 'programa')} - {now.strftime('%H:%M')}",
            html_body,
            self.config.get("recipients") or [],
        )
        return True

    def _send_final_digests_if_due(self, now: datetime) -> bool:
        if not self.config.get("final_digest_enabled", False):
            return False
        sent = False
        for window in self.config.get("windows", []):
            if not window.get("enabled", True) or now.weekday() not in window.get("days", []):
                continue
            _, end_dt = self._window_datetimes(window, now)
            if now <= end_dt:
                continue
            key = self._window_key(window, now)
            if key in self._sent_final_digests:
                continue
            if key not in self._window_last_relevant_at:
                continue
            sections = self.build_window_sections(key)
            title = f"Digest final - {window.get('label', 'programa')}"
            subject = f"[ALERTA X] Digest final - {window.get('label', 'programa')} - {end_dt.strftime('%H:%M')}"
            if self.send_digest(title=title, subject=subject, sections=sections, allow_empty=True):
                self._sent_final_digests.add(key)
                self.save_state()
                sent = True
        return sent

    def _send_spikes(self, col_label: str, tweets: list[dict], recipients: list[str], now: datetime) -> None:
        spike_replies = int(self.config.get("spike_replies", 500))
        spike_minutes = int(self.config.get("spike_minutes", 10))
        spike_tweets = []
        for t in tweets:
            url = t.get("url", "")
            if not url or url in self._alerted_spikes:
                continue
            if parse_metric(t.get("replies")) >= spike_replies:
                first = self._tweet_first_seen.get(url, now)
                if (now - first).total_seconds() / 60 <= spike_minutes:
                    spike_tweets.append(t)
                    self._alerted_spikes.add(url)
        if not spike_tweets:
            return
        html_body = _build_email_html(
            f"Spike de replies - {col_label}",
            [{"header": f"{col_label} - tweets viralizando", "tweets": spike_tweets}],
            self.config.get("deck_url", ""),
        )
        asyncio.get_event_loop().run_in_executor(
            None,
            send_alert_email,
            f"[ALERTA X] Spike - {len(spike_tweets)} tweet(s) com +{spike_replies} replies",
            html_body,
            recipients,
        )

    def build_sections(self) -> list[dict]:
        threshold = int(self.config.get("engagement_threshold", 200))
        sections = []
        for col_id in sorted(self._latest_by_col):
            item = self._latest_by_col[col_id]
            top = [t for t in item["tweets"] if engagement_score(t) >= threshold]
            top = sorted(top, key=engagement_score, reverse=True)[:5]
            if top:
                sections.append({"header": item["label"], "tweets": top})
        return sections

    def build_window_sections(self, window_key: str) -> list[dict]:
        sections = []
        for col_id in sorted(self._window_tweets.get(window_key, {})):
            item = self._window_tweets[window_key][col_id]
            tweets = list((item.get("tweets") or {}).values())
            top = sorted(tweets, key=engagement_score, reverse=True)[:5]
            if top:
                sections.append({"header": item["label"], "tweets": top})
        return sections

    def send_digest(
        self,
        title: str,
        subject: str,
        sections: Optional[list[dict]] = None,
        allow_empty: bool = False,
    ) -> bool:
        recipients = self.config.get("recipients") or []
        sections = self.build_sections() if sections is None else sections
        if not sections and not allow_empty:
            return False
        html_body = _build_email_html(title, sections, self.config.get("deck_url", ""))
        asyncio.get_event_loop().run_in_executor(None, send_alert_email, subject, html_body, recipients)
        return True


_scheduler = AlertScheduler()


def get_scheduler() -> AlertScheduler:
    return _scheduler
