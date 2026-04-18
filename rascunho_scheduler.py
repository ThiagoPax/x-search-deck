"""
rascunho_scheduler.py — Lógica de janela de horário para envio de alertas
Gerado externamente para uso como base no email_alerts.py
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo


def parse_windows(env_val: str) -> list[tuple[list[int], int, int, int, int]]:
    """
    Parseia janelas de envio de uma variável de ambiente.
    Formato de cada janela (separadas por ';'):
        DIAS HH:MM-HH:MM
    onde DIAS é uma lista de inteiros 0-6 (seg=0, dom=6) separados por vírgula.
    Exemplo: "0,1,2,3,4 17:30-19:00;6 20:30-23:00"
    """
    windows = []
    for entry in env_val.split(";"):
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


def default_windows() -> list[tuple[list[int], int, int, int, int]]:
    """
    Janelas padrão se ALERT_WINDOWS não estiver configurado:
      seg-sex 17:30-19:00
      dom     20:30-23:00
    """
    return [
        ([0, 1, 2, 3, 4], 17, 30, 19, 0),   # seg-sex
        ([6],              20, 30, 23, 0),   # dom
    ]


def is_within_send_window() -> bool:
    """
    Retorna True se o momento atual estiver dentro de alguma
    janela de envio configurada.
    Fuso: America/Sao_Paulo (ou ALERT_TZ se definido).
    """
    tz   = ZoneInfo(os.environ.get("ALERT_TZ", "America/Sao_Paulo"))
    now  = datetime.now(tz)
    wday = now.weekday()  # 0=seg … 6=dom
    hm   = now.hour * 60 + now.minute

    raw = os.environ.get("ALERT_WINDOWS", "")
    windows = parse_windows(raw) if raw else default_windows()

    for days, sh, sm, eh, em in windows:
        if wday not in days:
            continue
        start = sh * 60 + sm
        end   = eh * 60 + em
        if start <= hm < end:
            return True
    return False
