FROM python:3.12-slim-bookworm

# ffmpeg + fontes + bibliotecas que o Chrome do HyperFrames precisa + Node.js 22
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-liberation curl ca-certificates gnupg \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libxkbcommon0 libxcomposite1 \
        libxdamage1 libxrandr2 libxfixes3 libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# HyperFrames (motor de edição): dependências, GSAP e o Chrome de renderização
COPY hyperframes/package.json hyperframes/package-lock.json hyperframes/
COPY hyperframes/short hyperframes/short
RUN cd hyperframes && npm ci --no-fund --no-audit && npx hyperframes browser ensure \
    && npx hyperframes telemetry disable

COPY cortes ./cortes
# config.yaml, .env, client_secret.json, token.json e dados/ entram por volume (veja docker-compose.yml)
CMD ["python", "-m", "cortes", "daemon"]
