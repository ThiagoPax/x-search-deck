FROM mcr.microsoft.com/playwright/python:v1.50.0-noble

# Reap orphaned browser/driver children and forward container shutdown signals.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

RUN playwright install chromium

COPY . .

ENV PORT=8765
EXPOSE 8765

ENTRYPOINT ["/usr/bin/tini", "-s", "-g", "--"]
CMD ["python", "server.py"]
