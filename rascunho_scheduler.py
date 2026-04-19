from datetime import datetime, time
import pytz

class AlertScheduler:
    def __init__(self):
        # Define o fuso horário de São Paulo
        self.tz = pytz.timezone("America/Sao_Paulo")
        
        # Janelas padrão: 
        # 0-4 = Segunda a Sexta | 6 = Domingo
        self.default_windows = [
            {"days": [0, 1, 2, 3, 4], "start": "17:30", "end": "19:00"},
            {"days": [6], "start": "20:30", "end": "23:00"}
        ]

    def is_within_window(self, windows: list[dict] = None) -> bool:
        """
        Verifica se o horário atual (São Paulo) está dentro de alguma das janelas fornecidas.
        Se windows for None, utiliza as janelas padrão da classe.
        """
        if windows is None:
            windows = self.default_windows

        # Obtém a data e hora atual no fuso de SP
        now = datetime.now(self.tz)
        current_weekday = now.weekday()  # 0=Segunda, 6=Domingo
        current_time = now.time()

        for window in windows:
            if current_weekday in window["days"]:
                # Converte strings "HH:MM" para objetos datetime.time para comparação
                start_h, start_m = map(int, window["start"].split(":"))
                end_h, end_m = map(int, window["end"].split(":"))
                
                start_time = time(start_h, start_m)
                end_time = time(end_h, end_m)

                if start_time <= current_time <= end_time:
                    return True
        
        return False

    def should_send(self, last_sent: datetime, interval_minutes: int) -> bool:
        """
        Verifica se já passou o intervalo mínimo de minutos desde o último envio.
        """
        if last_sent is None:
            return True

        # Garante que o 'now' esteja no fuso de SP
        now = datetime.now(self.tz)

        # Tratamento para garantir que last_sent seja 'timezone aware' para evitar erro de comparação
        if last_sent.tzinfo is None:
            # Se for naive, assume-se que foi gravado no fuso de SP
            last_sent = self.tz.localize(last_sent)
        else:
            # Se já tiver fuso, converte para o de SP
            last_sent = last_sent.astimezone(self.tz)

        diff = now - last_sent
        minutes_passed = diff.total_seconds() / 60
        
        return minutes_passed >= interval_minutes

# --- Exemplo de Uso ---
if __name__ == "__main__":
    scheduler = AlertScheduler()

    # Testando janela de horário
    if scheduler.is_within_window():
        print("✅ Estamos dentro da janela de envio.")
    else:
        print("❌ Fora da janela de envio.")

    # Testando intervalo de tempo
    from datetime import timedelta
    # Simula que o último e-mail foi enviado há 45 minutos
    last_email_time = datetime.now(pytz.utc) - timedelta(minutes=45)
    
    # Se o intervalo mínimo for 60 min, deve retornar False
    if scheduler.should_send(last_email_time, 60):
        print("🚀 Pode enviar agora!")
    else:
        print("⏳ Intervalo mínimo ainda não atingido.")