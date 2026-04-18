"""
rascunho_smtp.py — Função de envio via smtplib (sem lógica de negócio)
Gerado externamente para uso como base no email_alerts.py
"""
import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def send_email(subject: str, body_html: str, body_text: str = "") -> bool:
    """
    Envia e-mail via SMTP usando variáveis de ambiente.
    Retorna True se enviado com sucesso, False caso contrário.

    Variáveis de ambiente necessárias:
        SMTP_HOST  — ex: smtp.gmail.com
        SMTP_PORT  — ex: 587
        SMTP_USER  — endereço remetente
        SMTP_PASS  — senha ou app-password
        ALERT_EMAILS — destinatários separados por vírgula
    """
    host  = os.environ.get("SMTP_HOST", "")
    port  = int(os.environ.get("SMTP_PORT", 587))
    user  = os.environ.get("SMTP_USER", "")
    pwd   = os.environ.get("SMTP_PASS", "")
    dests = [e.strip() for e in os.environ.get("ALERT_EMAILS", "").split(",") if e.strip()]

    if not all([host, user, pwd, dests]):
        return False  # configuração incompleta — silencia

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = ", ".join(dests)

    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=15) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(user, pwd)
            srv.sendmail(user, dests, msg.as_string())
        return True
    except Exception as exc:
        # log será feito pelo módulo chamador
        print(f"[smtp] Erro ao enviar: {exc}")
        return False
