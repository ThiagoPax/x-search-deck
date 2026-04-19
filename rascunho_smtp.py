import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

def send_alert_email(smtp_host, smtp_port, smtp_user, smtp_pass, recipients, subject, body_html):
    """
    Envia um e-mail de alerta formatado em HTML.
    
    - Porta 465: Utiliza SMTP_SSL.
    - Outras portas: Utiliza SMTP com STARTTLS.
    """
    try:
        # 1. Construção da mensagem
        message = MIMEMultipart()
        message["From"] = smtp_user
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        
        # Anexa o corpo da mensagem como HTML
        message.attach(MIMEText(body_html, "html"))

        # 2. Estabelecimento da conexão baseada na porta
        if smtp_port == 465:
            # Conexão SSL implícita
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        else:
            # Conexão padrão com upgrade para TLS (ex: porta 587)
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls()

        # 3. Autenticação e envio
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, message.as_string())
        server.quit()
        
        return True

    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")
        return False

# --- Exemplo de teste ---
if __name__ == "__main__":
    # Configurações fictícias
    sucesso = send_alert_email(
        smtp_host="smtp.gmail.com",
        smtp_port=587, 
        smtp_user="seu_email@gmail.com",
        smtp_pass="sua_senha_de_app",
        recipients=["destino@example.com", "alerta@example.com"],
        subject="🚨 Alerta de Sistema",
        body_html="<h1>Erro Detectado</h1><p>O serviço <b>API</b> apresentou instabilidade.</p>"
    )
    print(f"E-mail enviado com sucesso? {sucesso}")