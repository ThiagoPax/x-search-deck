"""
Operational mode helpers for critical editorial windows.
"""
from __future__ import annotations

import os
from datetime import datetime, time as dtime
from typing import Optional

import pytz

OPERATIONAL_TIMEZONE = os.environ.get("OPERATIONAL_TIMEZONE", "America/Sao_Paulo").strip() or "America/Sao_Paulo"
CRITICAL_WEEKDAYS_WINDOW = os.environ.get("CRITICAL_WEEKDAYS_WINDOW", "17:30-19:00").strip() or "17:30-19:00"
CRITICAL_SUNDAY_WINDOW = os.environ.get("CRITICAL_SUNDAY_WINDOW", "20:30-23:00").strip() or "20:30-23:00"


def _parse_window(raw: str, fallback: str) -> tuple[dtime, dtime]:
    value = (raw or "").strip() or fallback
    try:
        start_raw, end_raw = [x.strip() for x in value.split("-", 1)]
        sh, sm = [int(x) for x in start_raw.split(":", 1)]
        eh, em = [int(x) for x in end_raw.split(":", 1)]
        return dtime(sh, sm), dtime(eh, em)
    except Exception:
        sh, sm = [int(x) for x in fallback.split("-", 1)[0].split(":", 1)]
        eh, em = [int(x) for x in fallback.split("-", 1)[1].split(":", 1)]
        return dtime(sh, sm), dtime(eh, em)


def now_in_operational_tz(now: Optional[datetime] = None) -> datetime:
    tz = pytz.timezone(OPERATIONAL_TIMEZONE)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return tz.localize(now)
    return now.astimezone(tz)


def is_critical_window_now(now: Optional[datetime] = None) -> bool:
    current = now_in_operational_tz(now)
    weekday = current.weekday()
    clock = current.time()
    weekday_start, weekday_end = _parse_window(CRITICAL_WEEKDAYS_WINDOW, "17:30-19:00")
    sunday_start, sunday_end = _parse_window(CRITICAL_SUNDAY_WINDOW, "20:30-23:00")
    if weekday <= 4:
        return weekday_start <= clock <= weekday_end
    if weekday == 6:
        return sunday_start <= clock <= sunday_end
    return False


def get_operational_mode(now: Optional[datetime] = None) -> str:
    return "critical" if is_critical_window_now(now) else "manual_only"

