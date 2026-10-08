FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cortes ./cortes
# config.yaml, .env, client_secret.json, token.json e dados/ entram por volume (veja docker-compose.yml)
CMD ["python", "-m", "cortes", "daemon"]
