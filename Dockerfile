FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

RUN playwright install chromium

COPY . .

ENV PORT=8765
EXPOSE 8765

CMD ["python", "server.py"]
