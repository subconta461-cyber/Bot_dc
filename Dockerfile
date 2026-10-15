FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DENO_INSTALL=/opt/deno \
    PATH="/opt/deno/bin:${PATH}" \
    FFMPEG_PATH=/usr/bin/ffmpeg

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libopus0 libsodium23 ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deno.land/install.sh -o /tmp/install-deno.sh \
    && sh /tmp/install-deno.sh \
    && rm /tmp/install-deno.sh \
    && deno --version

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY dc.py panel.py moderation.py ./
COPY site/ ./site/
RUN python -c "import dc, panel; from pathlib import Path; assert all((Path('site') / p).is_file() for p in ('index.html','app.js','style.css','moon.svg'))"

CMD ["python", "dc.py"]
