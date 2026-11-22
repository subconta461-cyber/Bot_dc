FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# O yt-dlp quebra com frequência quando os sites mudam: esta linha roda de novo
# a cada deploy (depois do COPY) e garante a versão mais recente.
RUN pip install --no-cache-dir -U yt-dlp

CMD ["python", "bot.py"]
