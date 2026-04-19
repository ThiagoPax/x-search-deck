"""
Email alert module for X Search Deck.

SMTP credentials stay in environment variables. Operational alert settings
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
import smtplib
from copy import deepcopy
from datetime import datetime, time as dtime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import pytz

log = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
ALERT_EMAILS_ENV = [e.strip() for e in os.environ.get("ALERT_EMAILS", "").split(",") if e.strip()]
DATA_DIR = Path(os.environ.get("DATA_DIR", ".data"))
ALERT_CONFIG_PATH = Path(os.environ.get("ALERT_CONFIG_PATH", DATA_DIR / "alert_config.json"))

DEFAULT_CONFIG = {
    "enabled": True,
    "recipients": ALERT_EMAILS_ENV or ["tssouza@tssouza.com"],
    "frequency_minutes": 15,
    "engagement_threshold": 200,
    "spike_replies": 500,
    "spike_minutes": 10,
    "preview_minutes": 30,
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
    cfg["enabled"] = bool(cfg.get("enabled", True))
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


def send_alert_email(subject: str, body_html: str, recipients: list[str]) -> bool:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and recipients):
        log.warning("SMTP or alert recipients not configured - skipping alert email")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, recipients, msg.as_string())
        server.quit()
        log.info("Alert email sent: %s", subject)
        return True
    except Exception as e:
        log.error("Failed to send alert email: %s", e)
        return False


def _build_email_html(title: str, sections: list[dict], deck_url: str = "") -> str:
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
    return f"""<html><body style="background:#000;color:#e7e9ea;font-family:Arial,sans-serif;padding:20px">
<h2 style="color:#1d9bf0">{html.escape(title)}</h2>
{deck_button}
{rows or '<p style="color:#71767b">Sem tweets acima do threshold no momento.</p>'}
</body></html>"""


class AlertScheduler:
    def __init__(self):
        self.tz = pytz.timezone("America/Sao_Paulo")
        self.config = self.load_config()
        self._last_sent: Optional[datetime] = None
        self._tweet_first_seen: dict[str, datetime] = {}
        self._alerted_spikes: set[str] = set()
        self._latest_by_col: dict[int, dict] = {}
        self._sent_previews: set[str] = set()

    def load_config(self) -> dict:
        if ALERT_CONFIG_PATH.exists():
            try:
                return _merge_config(json.loads(ALERT_CONFIG_PATH.read_text(encoding="utf-8")))
            except Exception as e:
                log.warning("Could not load alert config: %s", e)
        return _merge_config({})

    def save_config(self, cfg: dict) -> dict:
        self.config = _merge_config(cfg)
        ALERT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ALERT_CONFIG_PATH.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.config

    def get_config(self) -> dict:
        return deepcopy(self.config)

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

        self._send_spikes(col_label, tweets, recipients, now)

    def dispatch_scheduled(self) -> bool:
        if not self.config.get("enabled", True):
            return False
        recipients = self.config.get("recipients") or []
        if not recipients:
            return False
        now = datetime.now(self.tz)
        due_preview = self.preview_due_window(now)
        if due_preview:
            self.send_digest(
                title=f"Preview X Search Deck - {due_preview.get('label', 'programa')}",
                subject=f"[ALERTA X] Preview - {due_preview.get('label', 'programa')} - {now.strftime('%H:%M')}",
            )

        if self.is_within_window(now) and self._should_send_periodic():
            if self.send_digest(
                title=f"Top repercussao - {now.strftime('%H:%M')}",
                subject=f"[ALERTA X] Top repercussao - {now.strftime('%H:%M')}",
            ):
                self._last_sent = now
                return True
        return False

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

    def send_digest(self, title: str, subject: str) -> bool:
        recipients = self.config.get("recipients") or []
        sections = self.build_sections()
        if not sections:
            return False
        html_body = _build_email_html(title, sections, self.config.get("deck_url", ""))
        asyncio.get_event_loop().run_in_executor(None, send_alert_email, subject, html_body, recipients)
        return True


_scheduler = AlertScheduler()


def get_scheduler() -> AlertScheduler:
    return _scheduler
