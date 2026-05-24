"""
Operational mode helpers for critical editorial windows.
"""
from __future__ import annotations

import os
from datetime import datetime, time as dtime
from typing import Optional

import pytz

OPERATIONAL_TIMEZONE = os.environ.get("OPERATIONAL_TIMEZONE", "America/Sao_Paulo").strip() or "America/Sao_Paulo"
CRITICAL_WEEKDAY_WINDOWS = os.environ.get(
    "CRITICAL_WEEKDAY_WINDOWS",
    "12:00-12:05,15:00-15:05,17:30-19:00",
).strip()
CRITICAL_SUNDAY_WINDOWS = os.environ.get(
    "CRITICAL_SUNDAY_WINDOWS",
    "12:00-12:05,15:00-15:05,20:30-23:00",
).strip()
CRITICAL_SATURDAY_WINDOWS = os.environ.get("CRITICAL_SATURDAY_WINDOWS", "").strip()


def _parse_hhmm(value: str) -> Optional[dtime]:
    try:
        hh_raw, mm_raw = [x.strip() for x in value.split(":", 1)]
        hh, mm = int(hh_raw), int(mm_raw)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return dtime(hh, mm)
    except Exception:
        return None
    return None


def _parse_windows(raw: str, fallback: str) -> list[tuple[dtime, dtime]]:
    source = raw if raw is not None else fallback
    source = source.strip()
    if not source:
        return []
    windows: list[tuple[dtime, dtime]] = []
    for token in source.split(","):
        piece = token.strip()
        if not piece or "-" not in piece:
            continue
        start_raw, end_raw = [x.strip() for x in piece.split("-", 1)]
        start = _parse_hhmm(start_raw)
        end = _parse_hhmm(end_raw)
        if start and end:
            windows.append((start, end))
    if windows:
        return windows
    if source == fallback:
        return []
    return _parse_windows(fallback, fallback)


def now_in_operational_tz(now: Optional[datetime] = None) -> datetime:
    tz = pytz.timezone(OPERATIONAL_TIMEZONE)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return tz.localize(now)
    return now.astimezone(tz)


def is_critical_window_now(now: Optional[datetime] = None) -> bool:
    current = now_in_operational_tz(now)
    weekday = current.weekday()  # Mon=0 ... Sun=6
    clock = current.time()

    if weekday <= 4:
        windows = _parse_windows(CRITICAL_WEEKDAY_WINDOWS, "12:00-12:05,15:00-15:05,17:30-19:00")
    elif weekday == 5:
        windows = _parse_windows(CRITICAL_SATURDAY_WINDOWS, "")
    else:
        windows = _parse_windows(CRITICAL_SUNDAY_WINDOWS, "12:00-12:05,15:00-15:05,20:30-23:00")

    return any(start <= clock <= end for start, end in windows)


def get_operational_mode(now: Optional[datetime] = None) -> str:
    return "critical" if is_critical_window_now(now) else "manual_only"
