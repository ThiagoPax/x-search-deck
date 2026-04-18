"""
Email alert module for X Search Deck.
Reads config from env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, ALERT_EMAILS
"""
from __future__ import annotations
import asyncio, logging, os, smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import pytz

log = logging.getLogger(__name__)

SMTP_HOST    = os.environ.get("SMTP_HOST", "")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASS", "")
ALERT_EMAILS = [e.strip() for e in os.environ.get("ALERT_EMAILS", "").split(",") if e.strip()]

INTERVAL_MINUTES = 15
SPIKE_REPLIES    = 500
SPIKE_MINUTES    = 10


def _parse_replies(val: str) -> int:
    if not val:
        return 0
    v = val.strip().upper().replace(",", "").replace(".", "")
    try:
        if v.endswith("K"):
            return int(float(v[:-1]) * 1000)
        if v.endswith("M"):
            return int(float(v[:-1]) * 1_000_000)
        return int(v)
    except Exception:
        return 0


def send_alert_email(subject: str, body_html: str) -> bool:
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and ALERT_EMAILS):
        log.warning("SMTP not configured — skipping alert email")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"]    = SMTP_USER
        msg["To"]      = ", ".join(ALERT_EMAILS)
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html"))
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, ALERT_EMAILS, msg.as_string())
        server.quit()
        log.info(f"Alert email sent: {subject}")
        return True
    except Exception as e:
        log.error(f"Failed to send alert email: {e}")
        return False


def _build_email_html(title: str, sections: list[dict]) -> str:
    rows = ""
    for sec in sections:
        rows += f"<h3 style='color:#1d9bf0;margin:16px 0 6px'>{sec['header']}</h3>"
        for t in sec["tweets"]:
            replies = t.get("replies", "0")
            rows += f"""
<div style='border:1px solid #2f3336;border-radius:8px;padding:10px 14px;margin-bottom:8px;background:#16181c'>
  <div style='font-weight:700;color:#e7e9ea'>{t.get('author_name','')} <span style='color:#71767b'>{t.get('author_handle','')}</span></div>
  <div style='color:#e7e9ea;margin:6px 0;font-size:13px'>{t.get('text','')}</div>
  <div style='color:#71767b;font-size:11px'>💬 {replies} replies · <a href='{t.get('url','')}' style='color:#1d9bf0'>Ver tweet</a></div>
</div>"""
    return f"""<html><body style='background:#000;color:#e7e9ea;font-family:sans-serif;padding:20px'>
<h2 style='color:#1d9bf0'>{title}</h2>
{rows}
</body></html>"""


class AlertScheduler:
    def __init__(self):
        self.tz = pytz.timezone("America/Sao_Paulo")
        self.default_windows = [
            {"days": [0, 1, 2, 3, 4], "start": "17:30", "end": "19:00"},
            {"days": [6],              "start": "20:30", "end": "23:00"},
        ]
        self._last_sent: Optional[datetime]    = None
        self._tweet_first_seen: dict[str, datetime] = {}
        self._alerted_spikes:   set[str]       = set()

    def is_within_window(self) -> bool:
        from datetime import time as dtime
        now = datetime.now(self.tz)
        wd  = now.weekday()
        ct  = now.time()
        for w in self.default_windows:
            if wd in w["days"]:
                sh, sm = map(int, w["start"].split(":"))
                eh, em = map(int, w["end"].split(":"))
                if dtime(sh, sm) <= ct <= dtime(eh, em):
                    return True
        return False

    def _should_send_periodic(self) -> bool:
        if self._last_sent is None:
            return True
        now  = datetime.now(self.tz)
        diff = (now - self._last_sent).total_seconds() / 60
        return diff >= INTERVAL_MINUTES

    def ingest(self, col_id: int, col_label: str, tweets: list[dict]):
        """Called after each successful column refresh."""
        if not ALERT_EMAILS:
            return
        now = datetime.now(self.tz)

        # Track first-seen for spike detection
        for t in tweets:
            url = t.get("url", "")
            if url and url not in self._tweet_first_seen:
                self._tweet_first_seen[url] = now

        # Spike alert: tweet >500 replies seen within 10 min of first appearance
        spike_tweets = []
        for t in tweets:
            url = t.get("url", "")
            if not url or url in self._alerted_spikes:
                continue
            if _parse_replies(t.get("replies", "0")) >= SPIKE_REPLIES:
                first = self._tweet_first_seen.get(url, now)
                if (now - first).total_seconds() / 60 <= SPIKE_MINUTES:
                    spike_tweets.append(t)
                    self._alerted_spikes.add(url)

        if spike_tweets:
            html = _build_email_html(
                f"🚨 Spike de replies — Coluna {col_label}",
                [{"header": f"Coluna {col_label} — tweets viralizando", "tweets": spike_tweets}]
            )
            asyncio.get_event_loop().run_in_executor(
                None, send_alert_email,
                f"🚨 Spike: {len(spike_tweets)} tweet(s) com +{SPIKE_REPLIES} replies em <{SPIKE_MINUTES}min",
                html
            )

        # Periodic window alert
        if not self.is_within_window() or not self._should_send_periodic():
            return

        top = [t for t in tweets if _parse_replies(t.get("replies", "0")) >= 200]
        top = sorted(top, key=lambda t: _parse_replies(t.get("replies", "0")), reverse=True)[:5]
        if not top:
            return

        self._last_sent = now
        html = _build_email_html(
            f"📊 Top tweets — Coluna {col_label}",
            [{"header": f"Top 5 tweets com mais replies — {col_label}", "tweets": top}]
        )
        asyncio.get_event_loop().run_in_executor(
            None, send_alert_email,
            f"📊 X Search Deck — Top tweets coluna {col_label}",
            html
        )


_scheduler = AlertScheduler()


def get_scheduler() -> AlertScheduler:
    return _scheduler
